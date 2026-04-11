/*
 * Signal handler trampoline for RISC-V.
 *
 * When a test instruction clobbers gp (x3) or tp (x4), the C signal
 * handler cannot access globals or TLS, and siglongjmp will crash.
 *
 * This trampoline restores gp and tp from saved values using PC-relative
 * addressing (which does NOT depend on gp), then tail-calls the C handler.
 *
 * Signature matches sa_sigaction:
 *   void asm_fault_handler(int signo, siginfo_t *info, void *ucontext);
 */

    .text
    .globl  asm_fault_handler
    .type   asm_fault_handler, @function
    .align  2

asm_fault_handler:
    /* Restore gp from PC-relative address (works even if gp is trashed) */
.option push
.option norelax
1:  auipc   gp, %pcrel_hi(saved_gp_value)
    ld      gp, %pcrel_lo(1b)(gp)

2:  auipc   tp, %pcrel_hi(saved_tp_value)
    ld      tp, %pcrel_lo(2b)(tp)
.option pop

    /* a0/a1/a2 still hold the original signal handler arguments */
    tail    fault_handler

    .size   asm_fault_handler, .-asm_fault_handler
