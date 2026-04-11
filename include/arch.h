/*
 * Architecture-specific backend for the instruction injector.
 * Each target links exactly one arch_*.c + matching trampoline .S.
 */

#ifndef ARCH_H
#define ARCH_H

#include "injector.h"
#include <sys/types.h>

extern config_t config;
extern state_t state;
extern int worker_id;
extern uint32_t cs_mode_override;

void arch_check_cli_config(void);

void arch_init_memory(void);
void arch_cleanup_memory(void);
void arch_init_signal_handlers(void);
void arch_inject_instruction(insn_info_t* insn);

bool arch_move_next_instruction(void);

void arch_init_disassembler(void);
void arch_cleanup_disassembler(void);
bool arch_disassemble_instruction(insn_info_t* insn, disas_t* result);

bool arch_is_hint_instruction(insn_t insn);
const char* arch_identify_known_extension(insn_t insn);

bool arch_is_blacklisted(insn_t insn);
int arch_get_instruction_size(insn_t insn);

typedef struct arch_ptrace_ops {
    const char* name;
    uint32_t sentinel_insn;
    size_t regs_size;
    int (*get_regs)(pid_t pid, void* regs);
    int (*set_regs)(pid_t pid, const void* regs);
    void (*seed_regs)(void* regs, uintptr_t test_pc, uint64_t safe_val);
    uintptr_t (*get_pc)(const void* regs);
    void (*classify_stop)(int wstatus,
                          uintptr_t test_addr,
                          int insn_size,
                          const void* regs_before,
                          const void* regs_after,
                          uint32_t* signum,
                          uint32_t* sicode);
    void (*report_reg_diff)(const void* regs_before,
                            const void* regs_after,
                            insn_t encoding);
} arch_ptrace_ops_t;

const arch_ptrace_ops_t* arch_get_ptrace_ops(void);

/* Called from assembly trampoline before C fault handling */
void fault_handler(int signo, siginfo_t* info, void* context);

#endif
