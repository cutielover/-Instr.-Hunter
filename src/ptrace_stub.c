/*
 * Ptrace scan is only implemented for the RISC-V build.
 */

#include <stdio.h>

#include "../include/arch.h"

int ptrace_scan_loop(void)
{
    fprintf(stderr, "injector: ptrace mode is not supported in this build\n");
    return 1;
}
