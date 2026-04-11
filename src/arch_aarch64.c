/*
 * AArch64 backend: memory cage, BRK sentinel, exhaustive +1 iteration.
 */

#define _GNU_SOURCE
#include <errno.h>
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

#ifdef USE_CAPSTONE
#include <capstone/capstone.h>
static csh cs_handle;
static cs_insn* cs_insn_ptr;

#if defined(CS_ARCH_ARM64)
#define SIFTER_CS_ARCH_A64 CS_ARCH_ARM64
#elif defined(CS_ARCH_AARCH64)
#define SIFTER_CS_ARCH_A64 CS_ARCH_AARCH64
#else
/* Capstone variant without exported ARM64/AArch64 alias macros.
 * In Capstone's cs_arch enum, AArch64/ARM64 is architecture id 1.
 */
#define SIFTER_CS_ARCH_A64 ((cs_arch)1)
#endif
#endif

extern config_t config;
extern state_t state;

#define A64_BRK0 0xd4200000u

static void* guard_region = NULL;
static void* packet_bufs[2];
static int active_buf = 0;
static void* packet_buffer = NULL;
static void* trap_page = NULL;
static uint8_t* packet = NULL;
static char alt_stack[ALT_STACK_SIZE];
static stack_t ss;

static sigjmp_buf jmp_env;
static volatile int caught_signal;
static volatile int caught_sicode;
static volatile uintptr_t caught_addr;

uint64_t saved_x18_value;

static bool insn_blacklisted(insn_t insn)
{
    uint32_t u = insn;

    if (u == 0xd69f03e0u)
        return true;

    /*
     * Exception / system instructions in the 0xD4****** space (SVC, HVC, SMC,
     * BRK, HLT, MSR/MRS imm, …) — skip for cage safety and BRK/sentinel clash.
     */
    if ((u & 0xff000000u) == 0xd4000000u)
        return true;

    return false;
}

bool arch_is_blacklisted(insn_t insn)
{
    return insn_blacklisted(insn);
}

int arch_get_instruction_size(insn_t insn)
{
    (void)insn;
    return 4;
}

bool arch_is_hint_instruction(insn_t insn)
{
    (void)insn;
    return false;
}

const char* arch_identify_known_extension(insn_t insn)
{
    (void)insn;
    return NULL;
}

void arch_check_cli_config(void)
{
    if (config.mode != MODE_EXHAUSTIVE) {
        fprintf(stderr, "injector (aarch64): only exhaustive mode (-E) is supported\n");
        exit(1);
    }
    if (config.filter_known_ext || config.strict_filter) {
        fprintf(stderr, "injector (aarch64): --filter-ext / --strict-filter are ignored\n");
    }
}

void arch_init_disassembler(void)
{
#ifdef USE_CAPSTONE
    cs_mode m = (cs_mode)cs_mode_override;
    if (cs_open(SIFTER_CS_ARCH_A64, m, &cs_handle) != CS_ERR_OK) {
        fprintf(stderr, "Failed to initialize Capstone (ARM64)\n");
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
    uint8_t buf[4];
    memcpy(buf, &insn->encoding, 4);
    const uint8_t* code = buf;
    size_t code_size = 4;
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

void arch_init_memory(void)
{
    size_t total = GUARD_REGION_SIZE;

    guard_region = mmap(NULL, total,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (guard_region == MAP_FAILED) {
        perror("mmap guard region failed");
        exit(1);
    }

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

    trap_page = (uint8_t*)packet_bufs[1] + PAGE_SIZE;
    if (mprotect(trap_page, PAGE_SIZE, PROT_READ | PROT_EXEC) != 0) {
        perror("mprotect trap page failed");
        exit(1);
    }

    uint32_t brk = A64_BRK0;
    for (int b = 0; b < 2; b++)
        for (int i = 0; i < PAGE_SIZE / 4; i++)
            ((uint32_t*)packet_bufs[b])[i] = brk;

    if (mprotect(trap_page, PAGE_SIZE, PROT_READ | PROT_WRITE) != 0) {
        perror("mprotect trap page rw failed");
        exit(1);
    }
    for (int i = 0; i < PAGE_SIZE / 4; i++)
        ((uint32_t*)trap_page)[i] = brk;
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

void fault_handler(int signo, siginfo_t* info, void* context)
{
    (void)context;
    caught_signal = signo;
    caught_sicode = info->si_code;
    caught_addr = (uintptr_t)info->si_addr;
    siglongjmp(jmp_env, 1);
}

extern void asm_fault_handler(int signo, siginfo_t* info, void* context);

void arch_init_signal_handlers(void)
{
    struct sigaction sa;

    ss.ss_sp = alt_stack;
    ss.ss_size = ALT_STACK_SIZE;
    ss.ss_flags = 0;
    sigaltstack(&ss, NULL);

    sa.sa_sigaction = asm_fault_handler;
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

void arch_inject_instruction(insn_info_t* insn)
{
    if (!config.allow_rwx) {
        packet_buffer = packet_bufs[active_buf];
    }
    packet = (uint8_t*)packet_buffer;

    uint32_t brk = A64_BRK0;
    for (int i = 0; i < PAGE_SIZE / 4; i++)
        ((uint32_t*)packet)[i] = brk;

    memcpy(packet, &insn->encoding, 4);
    memcpy(packet + 4, &brk, 4);

    if (!config.allow_rwx) {
        mprotect(packet_buffer, PAGE_SIZE, PROT_READ | PROT_EXEC);
    }

    __builtin___clear_cache((char*)packet, (char*)packet + 8);

    memset(&state.result, 0, sizeof(state.result));
    caught_signal = 0;
    caught_sicode = 0;
    caught_addr = 0;

    alarm(1);

    if (sigsetjmp(jmp_env, 1) == 0) {
        register uintptr_t r_safe __asm__("x11") = (uintptr_t)trap_page + PAGE_SIZE / 2;
        register uintptr_t r_tgt __asm__("x12") = (uintptr_t)packet;

        __asm__ volatile("str x18, %0" : "=m"(saved_x18_value) : : "memory");

        __asm__ volatile(
            "mov x0, x11\n"
            "mov x1, x11\n"
            "mov x2, x11\n"
            "mov x3, x11\n"
            "mov x4, x11\n"
            "mov x5, x11\n"
            "mov x6, x11\n"
            "mov x7, x11\n"
            "mov x8, x11\n"
            "mov x9, x11\n"
            "mov x10, x11\n"
            "mov x13, x11\n"
            "mov x14, x11\n"
            "mov x15, x11\n"
            "mov x16, x11\n"
            "mov x17, x11\n"
            "mov x18, x11\n"
            "mov x19, x11\n"
            "mov x20, x11\n"
            "mov x21, x11\n"
            "mov x22, x11\n"
            "mov x23, x11\n"
            "mov x24, x11\n"
            "mov x25, x11\n"
            "mov x26, x11\n"
            "mov x27, x11\n"
            "mov x28, x11\n"
            "mov x29, x11\n"
            "mov x30, x11\n"
            "mov sp, x11\n"
            "br x12\n"
            :
            : "r"(r_safe), "r"(r_tgt)
            : "memory", "x0", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10",
            "x13", "x14", "x15", "x16", "x17", "x18", "x19", "x20", "x21", "x22", "x23",
            "x24", "x25", "x26", "x27", "x28", "x29", "x30", "sp");
    }

    alarm(0);

    state.result.valid = 1;
    state.result.insn_size = 4;

    uintptr_t test_addr = (uintptr_t)packet;
    uintptr_t sentinel_addr = test_addr + 4;

    if (caught_signal == SIGALRM) {
        state.result.signum = SIGALRM;
        state.result.si_code = caught_sicode;
    } else if (caught_signal == SIGTRAP) {
        if (caught_addr == sentinel_addr) {
            state.result.signum = 0;
            state.result.si_code = 0;
        } else if (caught_addr == test_addr) {
            state.result.signum = SIGILL;
            state.result.si_code = caught_sicode;
        } else {
            state.result.signum = 0;
            state.result.si_code = 0;
        }
    } else if (caught_signal == SIGILL) {
        if (caught_addr == test_addr) {
            state.result.signum = SIGILL;
            state.result.si_code = caught_sicode;
        } else {
            state.result.signum = 0;
            state.result.si_code = 0;
        }
    } else {
        state.result.signum = caught_signal;
        state.result.si_code = caught_sicode;
    }

    if (!config.allow_rwx) {
        mprotect(packet_buffer, PAGE_SIZE, PROT_READ | PROT_WRITE);
        active_buf ^= 1;
    }
}

bool arch_move_next_instruction(void)
{
    insn_t next;

    if (config.mode != MODE_EXHAUSTIVE) {
        return false;
    }

    if (config.range.start > config.range.end) {
        return false;
    }

    if (!config.range.started) {
        config.range.started = true;
        next = config.range.start;

        while (next <= config.range.end && insn_blacklisted(next)) {
            uint64_t n = (uint64_t)next + 1u;
            if (n > UINT32_MAX) {
                return false;
            }
            next = (insn_t)n;
        }

        if (next > config.range.end) {
            return false;
        }

        state.current.encoding = next;
        state.current.size = 4;
        return true;
    }

    if (state.current.encoding >= config.range.end) {
        return false;
    }

    uint64_t n = (uint64_t)state.current.encoding + 1u;
    if (n > UINT32_MAX) {
        return false;
    }
    next = (insn_t)n;

    while (next <= config.range.end && insn_blacklisted(next)) {
        uint64_t nn = (uint64_t)next + 1u;
        if (nn > UINT32_MAX) {
            return false;
        }
        next = (insn_t)nn;
    }

    if (next > config.range.end) {
        return false;
    }

    state.current.encoding = next;
    state.current.size = 4;
    return true;
}

#ifdef __linux__
struct aarch64_ptrace_regs {
    uint64_t regs[31];
    uint64_t sp;
    uint64_t pc;
    uint64_t pstate;
};

static int a64_ptrace_get_regs(pid_t pid, void* regs)
{
    struct iovec iov = { .iov_base = regs, .iov_len = sizeof(struct aarch64_ptrace_regs) };
    return ptrace(PTRACE_GETREGSET, pid, (void*)NT_PRSTATUS, &iov) < 0 ? -1 : 0;
}

static int a64_ptrace_set_regs(pid_t pid, const void* regs)
{
    struct iovec iov = { .iov_base = (void*)regs, .iov_len = sizeof(struct aarch64_ptrace_regs) };
    return ptrace(PTRACE_SETREGSET, pid, (void*)NT_PRSTATUS, &iov) < 0 ? -1 : 0;
}

static void a64_ptrace_seed_regs(void* regs, uintptr_t test_pc, uint64_t safe_val)
{
    struct aarch64_ptrace_regs* r = (struct aarch64_ptrace_regs*)regs;
    for (int i = 0; i < 31; i++)
        r->regs[i] = safe_val;
    r->sp = safe_val;
    r->pc = (uint64_t)test_pc;
}

static uintptr_t a64_ptrace_get_pc(const void* regs)
{
    return (uintptr_t)((const struct aarch64_ptrace_regs*)regs)->pc;
}

static void a64_ptrace_classify_stop(int wstatus,
                                     uintptr_t test_addr,
                                     int insn_size,
                                     const void* regs_before,
                                     const void* regs_after,
                                     uint32_t* signum,
                                     uint32_t* sicode)
{
    (void)regs_before;
    const struct aarch64_ptrace_regs* after = (const struct aarch64_ptrace_regs*)regs_after;
    *signum = 0;
    *sicode = 0;

    if (!WIFSTOPPED(wstatus))
        return;

    int sig = WSTOPSIG(wstatus);
    if (sig == SIGTRAP) {
        if ((uintptr_t)after->pc == test_addr + (uintptr_t)insn_size) {
            *signum = 0;
        } else if ((uintptr_t)after->pc == test_addr) {
            *signum = SIGILL;
            *sicode = 1;
        }
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

static void a64_ptrace_report_reg_diff(const void* regs_before,
                                       const void* regs_after,
                                       insn_t encoding)
{
    const struct aarch64_ptrace_regs* before = (const struct aarch64_ptrace_regs*)regs_before;
    const struct aarch64_ptrace_regs* after = (const struct aarch64_ptrace_regs*)regs_after;
    bool any = false;
    for (int i = 0; i < 31; i++) {
        if (before->regs[i] != after->regs[i]) {
            if (!any) {
                fprintf(stderr, "  regdiff 0x%08x:", encoding);
                any = true;
            }
            fprintf(stderr, " x%d=0x%lx->0x%lx",
                    i,
                    (unsigned long)before->regs[i],
                    (unsigned long)after->regs[i]);
        }
    }
    if (before->sp != after->sp) {
        if (!any) {
            fprintf(stderr, "  regdiff 0x%08x:", encoding);
            any = true;
        }
        fprintf(stderr, " sp=0x%lx->0x%lx",
                (unsigned long)before->sp,
                (unsigned long)after->sp);
    }
    if (before->pc + 4 != after->pc) {
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

static const arch_ptrace_ops_t a64_ptrace_ops = {
    .name = "aarch64",
    .sentinel_insn = A64_BRK0,
    .regs_size = sizeof(struct aarch64_ptrace_regs),
    .get_regs = a64_ptrace_get_regs,
    .set_regs = a64_ptrace_set_regs,
    .seed_regs = a64_ptrace_seed_regs,
    .get_pc = a64_ptrace_get_pc,
    .classify_stop = a64_ptrace_classify_stop,
    .report_reg_diff = a64_ptrace_report_reg_diff,
};
#else
static const arch_ptrace_ops_t a64_ptrace_ops = { 0 };
#endif

const arch_ptrace_ops_t* arch_get_ptrace_ops(void)
{
    return &a64_ptrace_ops;
}
