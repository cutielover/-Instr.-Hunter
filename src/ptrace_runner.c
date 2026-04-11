/*
 * Shared ptrace-based instruction scanner (Linux).
 *
 * Instead of the memory-cage + signal-handler approach, this module
 * forks a child process, writes test instructions via PTRACE_POKETEXT,
 * single-steps them with PTRACE_SINGLESTEP, and inspects the result
 * through PTRACE_GETREGSET.  This gives:
 *
 *   - Full register diff before/after each instruction
 *   - No need for RWX pages (parent writes via ptrace, child page is RX)
 *   - No need for sigaltstack / signal handler trampoline
 *   - Ability to intercept ecall via PTRACE_SYSCALL
 *
 * Trade-off: ~10-100x slower than the cage method due to syscall overhead.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <sys/mman.h>
#include <sys/wait.h>

#ifdef __linux__
#include <sys/ptrace.h>
#include <sys/uio.h>
#include <elf.h>
#endif

#include "../include/injector.h"
#include "../include/arch.h"

#ifndef __linux__
/*
 * Ptrace-based scanning requires Linux ptrace(2).  On other platforms
 * we provide a stub that prints an error and returns.
 */
int ptrace_scan_loop(void) {
    fprintf(stderr, "ptrace mode is only supported on Linux\n");
    return 1;
}
#else /* __linux__ */

/* ── Helpers shared with arch backends / injector_core.c ──────────────── */

extern bool arch_move_next_instruction(void);
extern bool arch_is_blacklisted(insn_t insn);
extern bool arch_disassemble_instruction(insn_info_t* insn, disas_t* result);
extern bool arch_is_hint_instruction(insn_t insn);
extern int  arch_get_instruction_size(insn_t insn);
extern void report_tick(void);

/* ── Child process code ──────────────────────────────────────────────── */

/*
 * The child mmaps an RX page filled with architecture sentinel instructions and then
 * stops itself.  The parent will overwrite the first instruction slot
 * via PTRACE_POKETEXT before each single-step.
 */
static void child_main(void) {
    /* Request tracing */
    if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) < 0) {
        perror("ptrace TRACEME");
        _exit(1);
    }

    const arch_ptrace_ops_t* ops = arch_get_ptrace_ops();

    /* Allocate the test page as RW, fill with sentinel, then flip to RX */
    void* page = mmap(NULL, 4096,
                      PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) {
        perror("child mmap");
        _exit(1);
    }

    uint32_t ebreak = ops->sentinel_insn;
    for (int i = 0; i < 4096 / 4; i++)
        ((uint32_t*)page)[i] = ebreak;

    if (mprotect(page, 4096, PROT_READ | PROT_EXEC) != 0) {
        perror("child mprotect");
        _exit(1);
    }

    /*
     * Write the page address to stdout so the parent knows where to
     * POKETEXT.  Then raise SIGSTOP to hand control to the parent.
     */
    uint64_t addr = (uint64_t)(uintptr_t)page;
    if (write(STDOUT_FILENO, &addr, sizeof(addr)) != sizeof(addr))
        _exit(1);

    raise(SIGSTOP);

    /* Should never reach here — parent controls execution via ptrace */
    _exit(0);
}

/* ── Output helpers (match injector_core raw format) ─────────────────── */

static char ptrace_stdout_buf[4096];
static char* ptrace_stdout_pos;
static int   ptrace_sync_ctr;

static void ptrace_sync_write(const void* data, size_t sz) {
    memcpy(ptrace_stdout_pos, data, sz);
    ptrace_stdout_pos += sz;
}

static void ptrace_sync_flush(bool force) {
    ptrace_sync_ctr++;
    if (ptrace_sync_ctr >= 16 || force) {
        ptrace_sync_ctr = 0;
        size_t n = ptrace_stdout_pos - ptrace_stdout_buf;
        if (n > 0) {
            fwrite(ptrace_stdout_buf, n, 1, stdout);
            fflush(stdout);
        }
        ptrace_stdout_pos = ptrace_stdout_buf;
    }
}

static void ptrace_sync_printf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    ptrace_stdout_pos += vsprintf(ptrace_stdout_pos, fmt, ap);
    va_end(ap);
}

/* ── Main ptrace scan loop (runs in the parent process) ──────────────── */

/* Defined in injector_core.c (non-static, shared across translation units) */
extern config_t config;
extern state_t  state;

int ptrace_scan_loop(void) {
    const arch_ptrace_ops_t* ops = arch_get_ptrace_ops();
    if (!ops || !ops->get_regs || !ops->set_regs || !ops->seed_regs
            || !ops->classify_stop || !ops->get_pc || ops->regs_size == 0) {
        fprintf(stderr, "ptrace: architecture backend does not support ptrace\n");
        return 1;
    }

    int pipefd[2];
    if (pipe(pipefd) < 0) {
        perror("pipe");
        return 1;
    }

    ptrace_stdout_pos = ptrace_stdout_buf;
    ptrace_sync_ctr = 0;

    pid_t child = fork();
    if (child < 0) {
        perror("fork");
        return 1;
    }

    if (child == 0) {
        /* Child: redirect stdout to pipe write end */
        close(pipefd[0]);
        dup2(pipefd[1], STDOUT_FILENO);
        close(pipefd[1]);
        child_main();
        _exit(0);
    }

    /* Parent */
    close(pipefd[1]);

    /* Read the test page address from the child */
    uint64_t test_page_addr;
    if (read(pipefd[0], &test_page_addr, sizeof(test_page_addr))
            != sizeof(test_page_addr)) {
        fprintf(stderr, "ptrace: failed to read test page address\n");
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
        return 1;
    }
    close(pipefd[0]);

    /* Wait for the child's SIGSTOP */
    int wstatus;
    waitpid(child, &wstatus, 0);
    if (!WIFSTOPPED(wstatus) || WSTOPSIG(wstatus) != SIGSTOP) {
        fprintf(stderr, "ptrace: child did not stop as expected\n");
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
        return 1;
    }

    /*
     * Set PTRACE_O_TRACESYSGOOD so we can distinguish syscall-stops
     * from signal-delivery-stops (bit 7 set in WSTOPSIG).
     */
    ptrace(PTRACE_SETOPTIONS, child, NULL, PTRACE_O_TRACESYSGOOD);

    uint8_t regs_before_buf[512];
    uint8_t regs_after_buf[512];
    if (ops->regs_size > sizeof(regs_before_buf)) {
        fprintf(stderr, "ptrace: register context too large for backend %s\n",
                ops->name ? ops->name : "(unknown)");
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
        return 1;
    }
    void* regs_before = regs_before_buf;
    void* regs_after = regs_after_buf;
    uint64_t safe_val = test_page_addr + 2048;  /* middle of ebreak page */

    disas_t disas_result;

    while (arch_move_next_instruction()) {
        insn_info_t* insn = &state.current;

        /* 1. Write the test instruction + ebreak sentinel via POKETEXT */
        uint64_t word = ((uint64_t)ops->sentinel_insn << 32) | ops->sentinel_insn;
        memcpy(&word, &insn->encoding, insn->size);
        if (ptrace(PTRACE_POKETEXT, child, (void*)test_page_addr, (void*)word) < 0) {
            if (errno == ESRCH)
                break;
            continue;
        }

        /* 2. Set up registers: PC → test slot, all GP regs → safe value */
        if (ops->get_regs(child, regs_before) < 0)
            break;

        ops->seed_regs(regs_before, (uintptr_t)test_page_addr, safe_val);

        if (ops->set_regs(child, regs_before) < 0)
            break;

        /* 3. Single-step */
        if (ptrace(PTRACE_SINGLESTEP, child, NULL, NULL) < 0)
            break;

        waitpid(child, &wstatus, 0);

        siginfo_t stop_info;
        memset(&stop_info, 0, sizeof(stop_info));
        bool has_stop_info = false;
        if (WIFSTOPPED(wstatus)) {
            if (ptrace(PTRACE_GETSIGINFO, child, NULL, &stop_info) == 0) {
                has_stop_info = true;
            }
        }

        /* 4. Read post-execution registers */
        if (ops->get_regs(child, regs_after) < 0)
            break;

        /* 5. Classify the result */
        uint32_t signum = 0;
        uint32_t sicode = 0;

        if (WIFSTOPPED(wstatus)) {
            ops->classify_stop(wstatus, (uintptr_t)test_page_addr, insn->size,
                               regs_before, regs_after, &signum, &sicode);
            if (has_stop_info && signum != 0) {
                /* Prefer kernel-reported si_code over backend fallback values. */
                sicode = (uint32_t)stop_info.si_code;
            }
        } else if (WIFSIGNALED(wstatus)) {
            /* Child terminated — need to restart */
            fprintf(stderr, "ptrace: child killed by signal %d\n",
                    WTERMSIG(wstatus));
            break;
        }

        /* 6. Disassemble */
        arch_disassemble_instruction(insn, &disas_result);

        /* 7. Emit result */
        if (config.output == OUTPUT_RAW) {
            struct __attribute__((packed)) {
                uint8_t  wid;
                uint8_t  disas_len;
                uint8_t  disas_known;
                uint8_t  disas_illegal;
                uint32_t encoding;
                uint8_t  valid;
                uint8_t  length;
                uint8_t  signum;
                uint8_t  sicode;
            } raw = {
                .wid         = (uint8_t)worker_id,
                .disas_len   = disas_result.length,
                .disas_known = disas_result.known,
                .disas_illegal = disas_result.illegal,
                .encoding    = insn->encoding,
                .valid       = 1,
                .length      = insn->size,
                .signum      = (uint8_t)signum,
                .sicode      = (uint8_t)sicode,
            };
            ptrace_sync_write(&raw, sizeof(raw));
        } else {
            bool disas_ok = disas_result.known;
            bool is_hidden = (signum == 0) && !disas_ok;
            bool is_disas_bug = (signum == SIGILL) && disas_ok;
            bool is_exec_fault = (signum != 0 && signum != SIGILL) && !disas_ok;

            if (is_hidden) {
                if (!arch_is_hint_instruction(insn->encoding)) {
                    ptrace_sync_printf("H 0x%08x %u %u\n",
                                       insn->encoding, signum, sicode);
                    state.hidden_count++;
                    if (ops->report_reg_diff) {
                        ops->report_reg_diff(regs_before, regs_after, insn->encoding);
                    }
                }
            } else if (is_disas_bug) {
                ptrace_sync_printf("D 0x%08x %u %u\n",
                                   insn->encoding, signum, sicode);
                state.disas_bug_count++;
            } else if (is_exec_fault) {
                ptrace_sync_printf("X 0x%08x %u %u\n",
                                   insn->encoding, signum, sicode);
            }
        }

        ptrace_sync_flush(false);
        report_tick();
        state.count++;

        /*
         * If the child received a signal other than SIGTRAP, we need to
         * suppress it before continuing (pass 0 as the signal to deliver).
         * The next iteration's PTRACE_SINGLESTEP will handle this.
         */
    }

    ptrace_sync_flush(true);

    /* Clean up child */
    kill(child, SIGKILL);
    waitpid(child, NULL, 0);

    if (config.show_tick) {
        fprintf(stderr,
                "\n[ptrace] Completed: %llu tested, %llu hidden, %llu disas bugs\n",
                (unsigned long long)state.count,
                (unsigned long long)state.hidden_count,
                (unsigned long long)state.disas_bug_count);
    }

    return 0;
}

#endif /* __linux__ */
