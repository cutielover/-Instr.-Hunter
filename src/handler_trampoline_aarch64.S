/*
 * Restore x18 (platform register) before entering the C signal handler.
 * AArch64 Linux keeps ABI state in x18; test instructions may clobber it.
 */
    .text
    .globl  asm_fault_handler
    .type   asm_fault_handler, %function
    .align  4
asm_fault_handler:
    adrp    x16, saved_x18_value
    add     x16, x16, :lo12:saved_x18_value
    ldr     x18, [x16]
    b       fault_handler
    .size   asm_fault_handler, .-asm_fault_handler
