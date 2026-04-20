/*
 * RISC-V architecture backend: memcage, disassembly, instruction iteration.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#ifdef __linux__
#include <sys/ptrace.h>
#include <sys/uio.h>
#include <elf.h>
#endif

#include "../include/injector.h"
#include "../include/arch.h"

/* Capstone disassembler */
#ifdef USE_CAPSTONE
#include <capstone/capstone.h>
static csh cs_handle;
static cs_insn* cs_insn_ptr;
#endif

extern config_t config;
extern state_t state;

/* Memory management */
static void* guard_region = NULL; /* Base of the guard region */
static void* packet_bufs[2]; /* Double-buffered test pages */
static int active_buf = 0; /* Index of the page about to be executed */
static void* packet_buffer = NULL; /* Points to packet_bufs[active_buf] */
static void* trap_page = NULL; /* R-X ebreak page (after test pages) */
static uint8_t* packet = NULL;
static char alt_stack[ALT_STACK_SIZE];
static stack_t ss;

/* Signal recovery */
static sigjmp_buf jmp_env;
static volatile int caught_signal;
static volatile int caught_sicode;
static volatile uintptr_t caught_addr;

/* Blacklist of dangerous instructions */
static blacklist_entry_t blacklist[] = {
    /* System calls and exceptions */
    { 0x00000073, 0x0000007F, "ecall/ebreak/wfi" },

    /* ECALL - system call */
    { 0x00000073, 0xFFFFFFFF, "ecall" },

    /* EBREAK - breakpoint */
    { 0x00100073, 0xFFFFFFFF, "ebreak" },

    /* WFI - wait for interrupt */
    { 0x10500073, 0xFFFFFFFF, "wfi" },

    /* MRET/SRET/URET - return from trap */
    { 0x30200073, 0xFFFFFFFF, "mret" },
    { 0x10200073, 0xFFFFFFFF, "sret" },
    { 0x00200073, 0xFFFFFFFF, "uret" },

    /* SFENCE.VMA - memory fence */
    { 0x12000073, 0xFE007FFF, "sfence.vma" },

    /* Compressed EBREAK */
    { 0x9002, 0xFFFF, "c.ebreak" },

    /* End marker */
    { 0, 0, NULL },
};

/* ============================================================================
 * Instruction Analysis
 * ============================================================================ */

/*
 * Determine if an instruction encoding is a compressed (16-bit) instruction.
 * RISC-V rule: if bits [1:0] != 0b11, it's a 16-bit instruction.
 */
bool is_compressed_instruction(insn_t insn)
{
    return (insn & 0x3) != 0x3;
}

/*
 * Get the size of an instruction based on its encoding.
 */
int get_instruction_size(insn_t insn)
{
    if ((insn & 0x3) != 0x3) {
        return 2; /* Compressed instruction */
    }
    /* For now, we only handle 32-bit standard instructions */
    /* Future: check for 48-bit, 64-bit, etc. */
    return 4;
}

/*
 * Check if instruction matches any blacklist entry.
 */
bool is_blacklisted(insn_t insn)
{
    for (int i = 0; blacklist[i].reason != NULL; i++) {
        if ((insn & blacklist[i].mask) == blacklist[i].pattern) {
            return true;
        }
    }
    return false;
}

/* ============================================================================
 * HINT Instruction Recognition
 * ============================================================================ */

/*
 * Check if a 32-bit instruction is a HINT (architecturally NOP).
 * RISC-V defines HINTs as valid encodings that write to x0 or have
 * other reserved-but-must-execute-as-NOP semantics.
 */
static bool is_hint_32(insn_t insn)
{
    uint32_t opcode = insn & 0x7F;
    uint32_t rd = (insn >> 7) & 0x1F;
    uint32_t funct3 = (insn >> 12) & 0x7;
    uint32_t rs1 = (insn >> 15) & 0x1F;

    switch (opcode) {
    case 0x37: /* LUI   rd, imm  — HINT when rd == x0 */
    case 0x17: /* AUIPC rd, imm  — HINT when rd == x0 */
        return rd == 0;

    case 0x13: /* OP-IMM (ADDI, SLTI, …) — HINT when rd == x0 */
        return rd == 0;

    case 0x1B: /* OP-IMM-32 (ADDIW, …) — HINT when rd == x0 */
        return rd == 0;

    case 0x0F: /* MISC-MEM / FENCE — HINT variants */
        if (funct3 == 0) {
            /* FENCE: several HINT forms when pred/succ fields are zero */
            uint32_t pred = (insn >> 24) & 0xF;
            uint32_t succ = (insn >> 20) & 0xF;
            return (rd == 0 && rs1 == 0) && (pred == 0 || succ == 0);
        }
        return false;

    default:
        return false;
    }
}

/*
 * Check if a 16-bit compressed instruction is a HINT.
 */
static bool is_hint_compressed(uint16_t insn)
{
    uint32_t quadrant = insn & 0x3;
    uint32_t funct3 = (insn >> 13) & 0x7;
    uint32_t rd = (insn >> 7) & 0x1F;

    if (quadrant == 0x1) {
        /* C1 quadrant */
        if (funct3 == 0 && rd == 0) {
            /* C.NOP — HINT when imm != 0 */
            uint32_t imm5 = (insn >> 12) & 0x1;
            uint32_t imm40 = (insn >> 2) & 0x1F;
            return (imm5 | imm40) != 0;
        }
        if (funct3 == 0 && rd != 0) {
            /* C.ADDI rd, imm — HINT when imm == 0 */
            uint32_t imm5 = (insn >> 12) & 0x1;
            uint32_t imm40 = (insn >> 2) & 0x1F;
            return (imm5 | imm40) == 0;
        }
        if (funct3 == 2 && rd == 0) /* C.LI x0, imm  → HINT */
            return true;
        if (funct3 == 3 && rd == 0) /* C.LUI x0, imm → HINT */
            return true;
    }
    if (quadrant == 0x2) {
        /* C2 quadrant */
        if (funct3 == 0 && rd == 0) /* C.SLLI x0, …  → HINT */
            return true;
        if (funct3 == 4) {
            uint32_t bit12 = (insn >> 12) & 0x1;
            uint32_t rs2 = (insn >> 2) & 0x1F;
            if (bit12 == 0 && rd == 0 && rs2 != 0) /* C.MV x0, rs2 → HINT */
                return true;
            if (bit12 == 1 && rd == 0 && rs2 != 0) /* C.ADD x0, rs2 → HINT */
                return true;
        }
    }
    return false;
}

/*
 * Unified HINT check for any instruction encoding.
 */
bool is_hint_instruction(insn_t insn)
{
    if (is_compressed_instruction(insn))
        return is_hint_compressed((uint16_t)(insn & 0xFFFF));
    return is_hint_32(insn);
}

/* ============================================================================
 * Known-Extension Filter
 * ============================================================================ */

/*
 * Identify instructions that belong to RISC-V extensions which QEMU
 * implements but Capstone may not fully decode.
 * With Capstone 6 most of these are natively handled; this is a fallback.
 *
 * Returns the extension name (static string) or NULL if the encoding
 * does not match any known-extension pattern.
 */
static const char* identify_known_extension(insn_t insn)
{
    if (is_compressed_instruction(insn)) {
        uint16_t c = (uint16_t)(insn & 0xFFFF);
        uint32_t q = c & 0x3;
        uint32_t f3 = (c >> 13) & 0x7;

        /* Zcb: C0 quadrant, funct3 = 4 (c.lbu / c.lhu / c.lh / c.sb / c.sh) */
        if (q == 0 && f3 == 4)
            return "Zcb";

        if (q == 1 && f3 == 4) {
            uint32_t b12 = (c >> 12) & 1;
            uint32_t b11_10 = (c >> 10) & 3;
            uint32_t b6_5 = (c >> 5) & 3;
            /* Zcb: c.mul, c.not, c.zext.b, c.zext.h, c.sext.b, c.sext.h */
            if (b11_10 == 3 && b12 == 1 && b6_5 >= 2)
                return "Zcb";
        }
        /* Zcmop: C1 quadrant, funct3 = 0, rd = 0, specific immediate patterns */
        if (q == 1 && f3 == 0) {
            uint32_t rd = (c >> 7) & 0x1F;
            if (rd == 0)
                return "Zcmop";
        }
        return NULL;
    }

    /* ── 32-bit instructions ── */
    uint32_t opc = insn & 0x7F;
    uint32_t f3 = (insn >> 12) & 0x7;
    uint32_t f7 = (insn >> 25) & 0x7F;
    uint32_t fmt = (insn >> 25) & 0x3; /* FP format field */
    uint32_t f5 = f7 >> 2; /* funct5 for AMO */

    switch (opc) {
    /* ── Floating-point half-precision (Zfh / Zfbfmin) ── */
    case 0x07: /* LOAD-FP  */
    case 0x27: /* STORE-FP */
        if (f3 == 1)
            return "Zfh";
        if (f3 == 0 || f3 == 7)
            return "V";
        break;
    case 0x43: /* MADD  */
    case 0x47: /* MSUB  */
    case 0x4B: /* NMSUB */
    case 0x4F: /* NMADD */
        if (fmt == 2)
            return "Zfh";
        break;
    case 0x53: /* OP-FP */
        if (fmt == 2)
            return "Zfh";
        /* Zfa: additional FP ops (fli, fminm, fmaxm, …) */
        if ((f7 & 0x7C) == 0x78)
            return "Zfa";
        if (f7 == 0x14 || f7 == 0x15 || f7 == 0x20 || f7 == 0x21)
            return "Zfa";
        break;

    /* ── Vector (V / Zv*) ── */
    case 0x57: /* OP-V */
        return "V";

    /* ── Bit manipulation (Zba, Zbb, Zbc, Zbs, Zbk*) ── */
    case 0x33: /* OP */
        if (f7 == 0x05)
            return "Zba";
        if (f7 == 0x04 && f3 >= 4)
            return "Zbb"; /* min/max */
        if (f7 == 0x20 && (f3 == 1 || f3 == 5 || f3 == 4 || f3 == 6 || f3 == 7))
            return "Zbb"; /* andn/orn/xnor */
        if (f7 == 0x30)
            return "Zbb"; /* rol */
        if (f7 == 0x14 || f7 == 0x24 || f7 == 0x34)
            return "Zbs"; /* bclr/bext/binv/bset */
        if (f7 == 0x07 && (f3 == 5 || f3 == 7))
            return "Zicond";
        if (f7 == 0x08)
            return "Zk"; /* clmul (Zbc/Zbkc) */
        if (f7 == 0x48 || f7 == 0x18 || f7 == 0x10)
            return "Zbkb";
        if (f7 == 0x19 || f7 == 0x1A || f7 == 0x1F || f7 == 0x7A)
            return "Zb*";
        break;
    case 0x3B: /* OP-32 */
        if (f7 == 0x30)
            return "Zbb"; /* rolw/roriw */
        if (f7 == 0x04 || f7 == 0x05)
            return "Zba"; /* sh*add.uw / add.uw */
        if (f7 == 0x20 && f3 == 5)
            return "Zbb"; /* sraw? */
        if (f7 == 0x10 && (f3 == 2 || f3 == 4 || f3 == 6))
            return "Zb*";
        break;
    case 0x13: /* OP-IMM */
        if (f7 == 0x30 && f3 == 1)
            return "Zbb"; /* clz/ctz/cpop */
        if (f7 == 0x30 && f3 == 5)
            return "Zbb"; /* rori/rev8/orc.b */
        if ((f7 & 0x3E) == 0x24 && f3 == 1)
            return "Zbs"; /* bclri/bexti */
        if ((f7 & 0x3E) == 0x24 && f3 == 5)
            return "Zbs"; /* bseti/binvi */
        if (f7 == 0x34 && f3 == 1)
            return "Zbs";
        if (f7 == 0x31 && f3 == 5)
            return "Zbb";
        break;
    case 0x1B: /* OP-IMM-32 */
        if (f7 == 0x30)
            return "Zbb"; /* roriw/clzw/ctzw */
        if (f7 == 0x04 && f3 == 0)
            return "Zba"; /* slli.uw */
        if (f7 == 0x05 && f3 == 1)
            return "Zba";
        break;

    /* ── Atomic extensions (Zabha, Zacas) ── */
    case 0x2F: /* AMO */
        if (f3 == 0 || f3 == 1)
            return "Zabha";
        if (f5 == 5)
            return "Zacas";
        break;

    /* ── Zimop (may-be-operations) ── */
    case 0x73: /* SYSTEM */
        if (f3 != 0 && f7 >= 0x40)
            return "Zimop";
        break;

    /* ── MISC-MEM extensions (Zicbom, Zicbop, Zicboz, Zihintntl) ── */
    case 0x0F: /* MISC-MEM */
        if (f3 == 2)
            return "Zicbom";
        if (f3 == 6)
            return "Zicbop";
        /* FENCE / FENCE.I with non-standard fields: Zihintntl hints */
        if (f3 == 0 || f3 == 1)
            return "Zihintntl";
        break;
    }
    return NULL;
}

/* ============================================================================
 * Disassembler Interface
 * ============================================================================ */

void arch_init_disassembler(void)
{
#ifdef USE_CAPSTONE
    cs_mode rv_mode;
    if (cs_mode_override != 0) {
        rv_mode = (cs_mode)cs_mode_override;
    } else {
        rv_mode = CS_MODE_RISCV64
                        | CS_MODE_RISCV_C
                        | CS_MODE_RISCV_FD
                        | CS_MODE_RISCV_V
                        | CS_MODE_RISCV_A
                        | CS_MODE_RISCV_ZICFISS
                        | CS_MODE_RISCV_ZCMP_ZCMT_ZCE
                        | CS_MODE_RISCV_ZBA
                        | CS_MODE_RISCV_ZBB
                        | CS_MODE_RISCV_ZBC
                        | CS_MODE_RISCV_ZBKB
                        | CS_MODE_RISCV_ZBKC
                        | CS_MODE_RISCV_ZBKX
                        | CS_MODE_RISCV_ZBS;
    }
    if (cs_open(CS_ARCH_RISCV, rv_mode, &cs_handle) != CS_ERR_OK) {
        fprintf(stderr, "Failed to initialize Capstone disassembler\n");
        exit(1);
    }
    cs_insn_ptr = cs_malloc(cs_handle);
#endif
}

void arch_cleanup_disassembler(void)
{
#ifdef USE_CAPSTONE
    if (cs_insn_ptr)
        cs_free(cs_insn_ptr, 1);
    cs_close(&cs_handle);
#endif
}

bool arch_disassemble_instruction(insn_info_t* insn, disas_t* result)
{
    result->known = 0;
    result->illegal = 0;
    result->length = 0;
    result->mnemonic[0] = '\0';
    result->operands[0] = '\0';

#ifdef USE_CAPSTONE
    const uint8_t* code = (const uint8_t*)&insn->encoding;
    size_t code_size = insn->size;
    uint64_t address = 0;

    if (cs_disasm_iter(cs_handle, &code, &code_size, &address, cs_insn_ptr)) {
        result->known = 1;
        result->illegal = cs_insn_ptr->illegal ? 1 : 0;
        result->length = (int)address;
        strncpy(result->mnemonic, cs_insn_ptr->mnemonic, sizeof(result->mnemonic) - 1);
        strncpy(result->operands, cs_insn_ptr->op_str, sizeof(result->operands) - 1);
        return true;
    }
#endif
    return false;
}

/* ============================================================================
 * Memory Setup
 * ============================================================================ */

void arch_init_memory(void)
{
    /*
     * Memory layout (guard region):
     *
     *   [GUARD_PAGES * 4K no-exec] [4K testA] [4K testB] [4K R-X trap] [GUARD * 4K]
     *
     * Two test pages enable double-buffering in W^X mode: while one page
     * is RX (being executed), the other is RW (being prepared).  In --rwx
     * mode both pages are permanently RWX and only page A is used.
     *
     * - Trap page (R-X): all ebreak. Registers point here.
     * - Guard pages (no-exec): PC-relative jumps land here → SIGSEGV.
     */
    size_t total = GUARD_REGION_SIZE;

    guard_region = mmap(NULL, total,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (guard_region == MAP_FAILED) {
        perror("mmap guard region failed");
        exit(1);
    }

    /* Two test pages sit at the center */
    packet_bufs[0] = (uint8_t*)guard_region + GUARD_PAGES * PAGE_SIZE;
    packet_bufs[1] = (uint8_t*)packet_bufs[0] + PAGE_SIZE;
    active_buf = 0;
    packet_buffer = packet_bufs[0];

    if (config.allow_rwx) {
        for (int b = 0; b < 2; b++) {
            if (mprotect(packet_bufs[b], PAGE_SIZE,
                    PROT_READ | PROT_WRITE | PROT_EXEC)
                != 0) {
                perror("mprotect test page RWX failed");
                exit(1);
            }
        }
    }

    /* Trap page right after the two test pages */
    trap_page = (uint8_t*)packet_bufs[1] + PAGE_SIZE;
    if (mprotect(trap_page, PAGE_SIZE, PROT_READ | PROT_EXEC) != 0) {
        perror("mprotect trap page failed");
        exit(1);
    }

    /* Fill both test pages and trap page with EBREAK */
    uint32_t ebreak = 0x00100073;
    for (int b = 0; b < 2; b++)
        for (int i = 0; i < PAGE_SIZE / 4; i++)
            ((uint32_t*)packet_bufs[b])[i] = ebreak;

    /* Trap page: write ebreaks before making it read-only */
    if (mprotect(trap_page, PAGE_SIZE, PROT_READ | PROT_WRITE) != 0) {
        perror("mprotect trap page rw failed");
        exit(1);
    }
    for (int i = 0; i < PAGE_SIZE / 4; i++)
        ((uint32_t*)trap_page)[i] = ebreak;
    if (mprotect(trap_page, PAGE_SIZE, PROT_READ | PROT_EXEC) != 0) {
        perror("mprotect trap page rx failed");
        exit(1);
    }
}

void arch_cleanup_memory(void)
{
    if (guard_region) {
        munmap(guard_region, GUARD_REGION_SIZE);
    }
}

/* ============================================================================
 * Signal Handling
 * ============================================================================ */

void fault_handler(int signo, siginfo_t* info, void* context)
{
    (void)context;
    caught_signal = signo;
    caught_sicode = info->si_code;
    /*
     * For SIGILL/SIGTRAP, si_addr is the PC of the faulting instruction.
     * This lets us distinguish whether the signal came from the test
     * instruction itself or the ebreak sentinel that follows it.
     */
    caught_addr = (uintptr_t)info->si_addr;
    siglongjmp(jmp_env, 1);
}

#ifdef __riscv
/* Assembly trampoline that restores gp/tp before calling fault_handler */
extern void asm_fault_handler(int signo, siginfo_t* info, void* context);
#endif

void arch_init_signal_handlers(void)
{
    struct sigaction sa;

    ss.ss_sp = alt_stack;
    ss.ss_size = ALT_STACK_SIZE;
    ss.ss_flags = 0;
    sigaltstack(&ss, NULL);

#ifdef __riscv
    sa.sa_sigaction = asm_fault_handler;
#else
    sa.sa_sigaction = fault_handler;
#endif
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigfillset(&sa.sa_mask);

    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGTRAP, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGALRM, &sa, NULL);
}

/* ============================================================================
 * Instruction Injection
 * ============================================================================ */

/*
 * Saved gp/tp for the assembly signal handler trampoline to restore.
 * These are written before each test instruction and read by
 * asm_fault_handler (in handler_trampoline.S) via PC-relative addressing.
 */
uint64_t saved_gp_value;
uint64_t saved_tp_value;

void arch_inject_instruction(insn_info_t* insn)
{
    if (!config.allow_rwx) {
        /*
         * Double-buffer W^X strategy:
         *   packet_bufs[active_buf]     is currently RW — we write here.
         *   packet_bufs[active_buf ^ 1] was the previous RX page.
         *
         * After writing, flip the active page to RX (one mprotect) and
         * swap the index.  The old page becomes the next write target;
         * it is already RW because we flipped it back at the end of the
         * previous call (or it was never changed from the initial RW).
         */
        packet_buffer = packet_bufs[active_buf];
    }
    packet = (uint8_t*)packet_buffer;

    /*
     * Place ebreak sentinels so that a successfully-executed test instruction
     * always lands on ebreak → SIGTRAP, never on a zero word → SIGILL.
     * This avoids relying on si_addr comparison to distinguish "SIGILL from
     * the test instruction" vs "SIGILL from the sentinel", which is fragile
     * on certain real-hardware platforms (observed on JH7110 / U74-MC).
     */
    uint32_t ebreak = 0x00100073;
    memcpy(packet, &ebreak, 4);
    memcpy(packet + 4, &ebreak, 4);
    if (insn->size == 2)
        memcpy(packet + 2, &ebreak, 4);

    /* Write test instruction at the start of the page */
    memcpy(packet, &insn->encoding, insn->size);

    /* Flip to RX before execution (W^X mode) */
    if (!config.allow_rwx) {
        mprotect(packet_buffer, PAGE_SIZE, PROT_READ | PROT_EXEC);
    }

#ifdef __riscv
    __builtin___clear_cache((char*)packet, (char*)packet + insn->size + 4);
#endif

    memset(&state.result, 0, sizeof(state.result));
    caught_signal = 0;
    caught_sicode = 0;
    caught_addr = 0;

    alarm(1);

    if (sigsetjmp(jmp_env, 1) == 0) {
#ifdef __riscv
        /*
         * Register sandbox: set ALL GP registers to point into the
         * read-only ebreak trap page before executing the test
         * instruction.  gp and tp are included — the assembly
         * trampoline (handler_trampoline.S) restores them via
         * PC-relative addressing before entering the C handler.
         *
         * Trap page is R-X (read + exec, NO write):
         *  - stores via any register → SIGSEGV (no write perm)
         *  - jumps via any register → ebreak → SIGTRAP
         *  - PC-relative jumps → guard region (no exec) → SIGSEGV
         *
         * Without this, store instructions using gp/tp as base
         * silently corrupt the injector's data segment (state,
         * stdout_buffer, etc.), causing position-dependent output
         * corruption (len=0 records, wrong encodings).
         */
        register uintptr_t r_safe __asm__("t0") = (uintptr_t)trap_page + PAGE_SIZE / 2;
        register uintptr_t r_target __asm__("t1") = (uintptr_t)packet;

        __asm__ volatile("sd gp, %0" : "=m"(saved_gp_value));
        __asm__ volatile("sd tp, %0" : "=m"(saved_tp_value));

        __asm__ volatile(
            "mv ra,  t0\n"
            "mv gp,  t0\n"
            "mv tp,  t0\n"
            "mv t2,  t0\n"
            "mv s0,  t0\n"
            "mv s1,  t0\n"
            "mv a0,  t0\n"
            "mv a1,  t0\n"
            "mv a2,  t0\n"
            "mv a3,  t0\n"
            "mv a4,  t0\n"
            "mv a5,  t0\n"
            "mv a6,  t0\n"
            "mv a7,  t0\n"
            "mv s2,  t0\n"
            "mv s3,  t0\n"
            "mv s4,  t0\n"
            "mv s5,  t0\n"
            "mv s6,  t0\n"
            "mv s7,  t0\n"
            "mv s8,  t0\n"
            "mv s9,  t0\n"
            "mv s10, t0\n"
            "mv s11, t0\n"
            "mv t3,  t0\n"
            "mv t4,  t0\n"
            "mv t5,  t0\n"
            "mv t6,  t0\n"
            "mv sp,  t0\n"
            "jalr zero, t1, 0\n"
            :
            : "r"(r_safe), "r"(r_target)
            : "memory", "ra",
            "t2", "t3", "t4", "t5", "t6",
            "s0", "s1", "s2", "s3", "s4", "s5", "s6",
            "s7", "s8", "s9", "s10", "s11",
            "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7");
#else
        void (*exec)(void) = (void (*)(void))packet;
        exec();
#endif
    }

#ifdef __riscv
    __asm__ volatile("ld gp, %0" : : "m"(saved_gp_value) : "memory");
    __asm__ volatile("ld tp, %0" : : "m"(saved_tp_value) : "memory");
#endif

    alarm(0);

    state.result.valid = 1;
    state.result.insn_size = insn->size;

    /*
     * Determine whether the signal came from the test instruction or the
     * ebreak sentinel.  For SIGILL and SIGTRAP, si_addr is the PC of the
     * faulting instruction, so we can compare it to the test instruction
     * address (packet) and the sentinel address (packet + insn->size).
     *
     * On QEMU user-mode, ebreak may produce SIGILL instead of SIGTRAP,
     * so relying purely on signal type is unreliable.  Checking si_addr
     * handles both native hardware and QEMU correctly.
     */
    uintptr_t test_addr = (uintptr_t)packet;

    if (caught_signal == SIGALRM) {
        /* Keep timeout distinct; do not fold into SIGILL. */
        state.result.signum = SIGALRM;
        state.result.si_code = caught_sicode;
    } else if (caught_signal == SIGTRAP) {
        /* SIGTRAP: ebreak sentinel hit → test instruction succeeded */
        state.result.signum = 0;
        state.result.si_code = 0;
    } else if (caught_signal == SIGILL) {
        /*
         * With an explicit ebreak sentinel at packet + insn_size, a
         * successfully-executed test instruction always triggers SIGTRAP
         * (handled above), never SIGILL.  Therefore any SIGILL must
         * originate from the test instruction itself — the CPU rejected
         * the encoding.  No si_addr comparison is needed.
         *
         * The si_addr heuristic (caught_addr != test_addr) was previously
         * used when the sentinel was an implicit zero-word that also
         * produced SIGILL; it is no longer required and was found to be
         * unreliable on certain real-hardware platforms (JH7110 / U74-MC)
         * where si_addr exhibits position-dependent inaccuracies.
         *
         * For QEMU user-mode (where ebreak may produce SIGILL instead of
         * SIGTRAP), a future build flag can re-enable the si_addr path.
         */
        state.result.signum = SIGILL;
        state.result.si_code = caught_sicode;
    } else {
        /*
         * Any other signal (SIGSEGV, SIGBUS, SIGFPE, …) means the CPU
         * decoded the instruction (it was a valid encoding) but execution
         * caused a fault.  Record the actual signal for analysis.
         */
        state.result.signum = caught_signal;
        state.result.si_code = caught_sicode;
    }

    /*
     * Double-buffer swap: flip the just-executed page back to RW (so it
     * is ready for the next-next write) and advance the active index.
     * The other page is already RW from init or the previous swap.
     */
    if (!config.allow_rwx) {
        mprotect(packet_buffer, PAGE_SIZE, PROT_READ | PROT_WRITE);
        active_buf ^= 1;
    }
}

/* ============================================================================
 * Search Iteration
 * ============================================================================ */

bool arch_move_next_instruction(void)
{
    insn_t next;

    if (config.range.start > config.range.end) {
        return false;
    }

    if (!config.range.started) {
        config.range.started = true;
        next = config.range.start;
        if (config.mode == MODE_EXHAUSTIVE && !config.scan_compressed) {
            next |= 0x3;
        }
        while (next <= config.range.end && is_blacklisted(next)) {
            if (config.mode == MODE_EXHAUSTIVE && !config.scan_compressed) {
                if ((uint64_t)next + 4 > UINT32_MAX) {
                    return false;
                }
                next += 4;
            } else {
                if (next == UINT32_MAX) {
                    return false;
                }
                next++;
            }
        }
        if (next > config.range.end) {
            return false;
        }
        state.current.encoding = next;
        state.current.size = get_instruction_size(state.current.encoding);
        return true;
    }

    if (config.mode == MODE_EXHAUSTIVE) {
        /* Simple increment */
        if (state.current.encoding >= config.range.end) {
            return false;
        }

        if (!config.scan_compressed) {
            if ((uint64_t)state.current.encoding + 4 > UINT32_MAX) {
                return false;
            }
            next = state.current.encoding + 4;
        } else {
            next = state.current.encoding + 1;
        }

        /* Skip blacklisted */
        while (next <= config.range.end && is_blacklisted(next)) {
            if (!config.scan_compressed) {
                if ((uint64_t)next + 4 > UINT32_MAX) {
                    return false;
                }
                next += 4;
            } else {
                if (next == UINT32_MAX) {
                    return false;
                }
                next++;
            }
        }

        if (next > config.range.end) {
            return false;
        }

        state.current.encoding = next;
        state.current.size = get_instruction_size(next);
    } else if (config.mode == MODE_RANDOM) {
        /* Random sampling */
        next = (insn_t)rand();
        if (!config.scan_compressed) {
            next |= 0x3; /* Force 32-bit encoding */
        }

        if (is_blacklisted(next)) {
            return arch_move_next_instruction();
        }

        state.current.encoding = next;
        state.current.size = get_instruction_size(next);
    } else {
        /* Only MODE_EXHAUSTIVE and MODE_RANDOM are valid now. */
        return false;
    }

    return true;
}

void arch_check_cli_config(void) { }

bool arch_is_hint_instruction(insn_t insn)
{
    return is_hint_instruction(insn);
}

const char* arch_identify_known_extension(insn_t insn)
{
    return identify_known_extension(insn);
}

bool arch_is_blacklisted(insn_t insn)
{
    return is_blacklisted(insn);
}

int arch_get_instruction_size(insn_t insn)
{
    return get_instruction_size(insn);
}

#ifdef __linux__
struct riscv_ptrace_regs {
    uint64_t x[32];
    uint64_t pc;
};

static const char* const ptrace_reg_names[33] = {
    "zero", "ra",  "sp",  "gp",  "tp",  "t0",  "t1",  "t2",
    "s0",   "s1",  "a0",  "a1",  "a2",  "a3",  "a4",  "a5",
    "a6",   "a7",  "s2",  "s3",  "s4",  "s5",  "s6",  "s7",
    "s8",   "s9",  "s10", "s11", "t3",  "t4",  "t5",  "t6",
    "pc"
};

static int rv_ptrace_get_regs(pid_t pid, void* regs)
{
    struct iovec iov = { .iov_base = regs, .iov_len = sizeof(struct riscv_ptrace_regs) };
    return ptrace(PTRACE_GETREGSET, pid, (void*)NT_PRSTATUS, &iov) < 0 ? -1 : 0;
}

static int rv_ptrace_set_regs(pid_t pid, const void* regs)
{
    struct iovec iov = { .iov_base = (void*)regs, .iov_len = sizeof(struct riscv_ptrace_regs) };
    return ptrace(PTRACE_SETREGSET, pid, (void*)NT_PRSTATUS, &iov) < 0 ? -1 : 0;
}

static void rv_ptrace_seed_regs(void* regs, uintptr_t test_pc, uint64_t safe_val)
{
    struct riscv_ptrace_regs* r = (struct riscv_ptrace_regs*)regs;
    r->pc = (uint64_t)test_pc;
    r->x[0] = 0;
    for (int i = 1; i < 32; i++)
        r->x[i] = safe_val;
}

static uintptr_t rv_ptrace_get_pc(const void* regs)
{
    return (uintptr_t)((const struct riscv_ptrace_regs*)regs)->pc;
}

static void rv_ptrace_classify_stop(int wstatus,
                                    uintptr_t test_addr,
                                    int insn_size,
                                    const void* regs_before,
                                    const void* regs_after,
                                    uint32_t* signum,
                                    uint32_t* sicode)
{
    (void)regs_before;
    (void)insn_size;
    const struct riscv_ptrace_regs* after = (const struct riscv_ptrace_regs*)regs_after;
    *signum = 0;
    *sicode = 0;

    if (!WIFSTOPPED(wstatus)) {
        return;
    }

    int sig = WSTOPSIG(wstatus);
    if (sig == SIGTRAP) {
        *signum = 0;
    } else if (sig == SIGILL) {
        if ((uintptr_t)after->pc == test_addr) {
            *signum = SIGILL;
            *sicode = 1;
        }
    } else {
        *signum = (uint32_t)sig;
        *sicode = 1;
    }
}

static void rv_ptrace_report_reg_diff(const void* regs_before,
                                      const void* regs_after,
                                      insn_t encoding)
{
    const struct riscv_ptrace_regs* before = (const struct riscv_ptrace_regs*)regs_before;
    const struct riscv_ptrace_regs* after = (const struct riscv_ptrace_regs*)regs_after;
    bool any = false;
    for (int i = 1; i < 32; i++) {
        if (before->x[i] != after->x[i]) {
            if (!any) {
                fprintf(stderr, "  regdiff 0x%08x:", encoding);
                any = true;
            }
            fprintf(stderr, " %s=0x%lx->0x%lx",
                    ptrace_reg_names[i],
                    (unsigned long)before->x[i],
                    (unsigned long)after->x[i]);
        }
    }
    if (before->pc + arch_get_instruction_size(encoding) != after->pc) {
        if (!any) {
            fprintf(stderr, "  regdiff 0x%08x:", encoding);
            any = true;
        }
        fprintf(stderr, " pc=0x%lx->0x%lx",
                (unsigned long)before->pc,
                (unsigned long)after->pc);
    }
    if (any)
        fprintf(stderr, "\n");
}

static const arch_ptrace_ops_t rv_ptrace_ops = {
    .name = "riscv",
    .sentinel_insn = 0x00100073u,
    .regs_size = sizeof(struct riscv_ptrace_regs),
    .get_regs = rv_ptrace_get_regs,
    .set_regs = rv_ptrace_set_regs,
    .seed_regs = rv_ptrace_seed_regs,
    .get_pc = rv_ptrace_get_pc,
    .classify_stop = rv_ptrace_classify_stop,
    .report_reg_diff = rv_ptrace_report_reg_diff,
};
#else
static const arch_ptrace_ops_t rv_ptrace_ops = { 0 };
#endif

const arch_ptrace_ops_t* arch_get_ptrace_ops(void)
{
    return &rv_ptrace_ops;
}
