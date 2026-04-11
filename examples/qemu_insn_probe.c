// Minimal RISC-V instruction probe for QEMU/Docker debugging.
// Tries to reproduce sifter-like execution more closely than inline asm.

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define PAGE_SIZE 4096

static sigjmp_buf jmp_env;
static volatile int caught_signal;
static volatile int caught_code;
static volatile uintptr_t caught_addr;

static void signal_handler(int signo, siginfo_t* info, void* context)
{
    (void)context;
    caught_signal = signo;
    caught_code = info ? info->si_code : 0;
    caught_addr = info ? (uintptr_t)info->si_addr : 0;
    siglongjmp(jmp_env, 1);
}

static void install_handlers(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = signal_handler;
    sa.sa_flags = SA_SIGINFO;
    sigfillset(&sa.sa_mask);

    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
    sigaction(SIGTRAP, &sa, NULL);
    sigaction(SIGALRM, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
}

static int is_compressed(uint32_t word)
{
    return (word & 0x3) != 0x3;
}

static void dump_result(const char* label, uint32_t word, int size, void* packet)
{
    uintptr_t test_addr = (uintptr_t)packet;
    uintptr_t sentinel = test_addr + (uintptr_t)size;

    printf("[%s] word=0x%08" PRIx32 " size=%d\n", label, word, size);
    printf("  signal=%d code=%d addr=0x%016" PRIxPTR "\n",
        caught_signal, caught_code, caught_addr);
    printf("  test=0x%016" PRIxPTR " sentinel=0x%016" PRIxPTR "\n",
        test_addr, sentinel);

    if (caught_signal == 0) {
        printf("  outcome=no signal returned\n");
    } else if (caught_signal == SIGALRM) {
        printf("  outcome=timeout\n");
    } else if (caught_signal == SIGTRAP) {
        printf("  outcome=hit ebreak sentinel (success path)\n");
    } else if (caught_signal == SIGILL && caught_addr == test_addr) {
        printf("  outcome=illegal at test PC\n");
    } else if (caught_signal == SIGILL && caught_addr != test_addr) {
        printf("  outcome=SIGILL away from test PC (often sentinel on QEMU user-mode)\n");
    } else {
        printf("  outcome=accepted then faulted during execution\n");
    }
}

static int run_page_probe(uint32_t word, int sandbox_regs)
{
    int size = is_compressed(word) ? 2 : 4;
    uint8_t* packet = mmap(NULL, PAGE_SIZE, PROT_READ | PROT_WRITE | PROT_EXEC,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    uint8_t* trap = mmap(NULL, PAGE_SIZE, PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (packet == MAP_FAILED || trap == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    uint32_t ebreak = 0x00100073;
    for (size_t i = 0; i < PAGE_SIZE / 4; i++) {
        ((uint32_t*)packet)[i] = ebreak;
        ((uint32_t*)trap)[i] = ebreak;
    }
    if (mprotect(trap, PAGE_SIZE, PROT_READ | PROT_EXEC) != 0) {
        perror("mprotect trap");
        return 1;
    }

    memcpy(packet, &word, size);
    __builtin___clear_cache((char*)packet, (char*)packet + size + 4);

    caught_signal = 0;
    caught_code = 0;
    caught_addr = 0;
    alarm(1);

    if (sigsetjmp(jmp_env, 1) == 0) {
        if (sandbox_regs) {
#if defined(__riscv) && (__riscv_xlen == 64)
            register uintptr_t r_safe __asm__("t0") = (uintptr_t)trap + PAGE_SIZE / 2;
            register uintptr_t r_target __asm__("t1") = (uintptr_t)packet;
            __asm__ volatile(
                "mv ra,  t0\n"
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
            fprintf(stderr, "sandbox mode requires rv64 target\n");
            return 2;
#endif
        } else {
            void (*exec_fn)(void) = (void (*)(void))packet;
            exec_fn();
        }
    }

    alarm(0);
    dump_result(sandbox_regs ? "sandbox" : "plain-page", word, size, packet);

    munmap(packet, PAGE_SIZE);
    munmap(trap, PAGE_SIZE);
    return 0;
}

int main(int argc, char** argv)
{
    uint32_t word = 0x80019f82u;
    int sandbox_only = 0;
    int plain_only = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--sandbox-only") == 0) {
            sandbox_only = 1;
        } else if (strcmp(argv[i], "--plain-only") == 0) {
            plain_only = 1;
        } else {
            word = (uint32_t)strtoul(argv[i], NULL, 0);
        }
    }

    install_handlers();
    printf("Probing 0x%08" PRIx32 "\n", word);
    fflush(stdout);

    if (!sandbox_only) {
        run_page_probe(word, 0);
        fflush(stdout);
    }
    if (!plain_only) {
        run_page_probe(word, 1);
        fflush(stdout);
    }
    return 0;
}
