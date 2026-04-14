/*
 * RISC-V Instruction Injector
 * Header file defining types and interfaces
 */

#ifndef INJECTOR_H
#define INJECTOR_H

#define _GNU_SOURCE
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <signal.h>

/* RISC-V specific constants */
#define RV_INSN_SIZE_32     4       /* Standard 32-bit instruction */
#define RV_INSN_SIZE_16     2       /* Compressed 16-bit instruction */
#define RV_MAX_INSN_SIZE    4       /* Maximum instruction size we handle */
#define RV_NUM_GP_REGS      32      /* Number of general purpose registers */
#define RV_REG_SIZE         8       /* Register size in bytes (RV64) */

/* Memory page size */
#define PAGE_SIZE           4096

/* Guard region: 2MB+ total, test page(s) in the center */
#define GUARD_PAGES         256
#define GUARD_REGION_SIZE   ((GUARD_PAGES * 2 + 3) * PAGE_SIZE)

/* Alternate stack size for signal handling */
#define ALT_STACK_SIZE      16384

/* Maximum number of blacklisted instructions */
#define MAX_BLACKLIST       128

/* Output buffer sizes */
#define LINE_BUFFER_SIZE    256
#define BUFFER_LINES        16
#define SYNC_LINES          BUFFER_LINES

/* Tick output mask */
#define TICK_MASK           0xffff

/* Search modes */
typedef enum {
    MODE_EXHAUSTIVE,    /* Exhaustive enumeration */
    MODE_RANDOM,        /* Random sampling */
} search_mode_t;

/* Execution methods */
typedef enum {
    EXEC_CAGE,          /* Memory cage with signal handler (default) */
    EXEC_PTRACE,        /* ptrace single-step in child process */
} exec_method_t;

/* Output modes */
typedef enum {
    OUTPUT_TEXT,        /* Human-readable text */
    OUTPUT_RAW,         /* Binary raw output */
} output_mode_t;

/* Instruction type - 32 bits can hold both 16-bit and 32-bit instructions */
typedef uint32_t insn_t;

/* Register type for RV64 */
typedef uint64_t reg_t;

/* Instruction encoding information */
typedef struct {
    insn_t encoding;        /* Raw instruction encoding */
    int size;               /* Instruction size (2 or 4 bytes) */
} insn_info_t;

/* Execution result */
typedef struct __attribute__((packed)) {
    uint32_t valid;         /* Whether result is valid */
    uint32_t insn_size;     /* Detected instruction size */
    uint32_t signum;        /* Signal number received */
    uint32_t si_code;       /* Signal code */
    uint64_t fault_addr;    /* Faulting address (if applicable) */
} result_t;

/* Disassembly result */
typedef struct {
    int known;              /* Whether disassembler recognizes it */
    int illegal;            /* Capstone 6: decoded but ISA-illegal */
    int length;             /* Expected length from disassembler */
    char mnemonic[32];      /* Instruction mnemonic */
    char operands[64];      /* Operand string */
} disas_t;

/* Search range */
typedef struct {
    insn_t start;           /* Start of search range */
    insn_t end;             /* End of search range */
    bool started;           /* Whether search has started */
} range_t;

/* Blacklist entry */
typedef struct {
    insn_t pattern;         /* Instruction pattern to match */
    insn_t mask;            /* Mask for pattern matching */
    const char* reason;     /* Reason for blacklisting */
} blacklist_entry_t;

/* Configuration */
typedef struct {
    search_mode_t mode;             /* Search mode */
    exec_method_t exec_method;      /* Execution method (cage or ptrace) */
    output_mode_t output;           /* Output mode */
    bool scan_compressed;           /* Whether to scan compressed instructions */
    bool show_tick;                 /* Show progress ticks */
    bool filter_known_ext;          /* Filter out known-extension H entries */
    bool strict_filter;             /* Strict extension filter (no wildcards, require ISA) */
    bool allow_rwx;                 /* Allow RWX pages (legacy mode for QEMU) */
    int jobs;                       /* Number of parallel jobs */
    int core;                       /* CPU core to pin to */
    bool force_core;                /* Whether to force core pinning */
    long seed;                      /* Random seed */
    const char* resume_shards;      /* Internal exhaustive per-worker resume spec */
    range_t range;                  /* Search range */
} config_t;

/* Global state */
typedef struct {
    insn_info_t current;            /* Current instruction being tested */
    result_t result;                /* Last execution result */
    disas_t disas;                  /* Last disassembly result */
    uint64_t count;                 /* Instructions tested */
    uint64_t hidden_count;          /* Hidden instructions found */
    uint64_t disas_bug_count;       /* Disassembler bugs found */
} state_t;

/* Function declarations */

void init_config(int argc, char** argv);
void report_result(void);
void report_tick(void);
int ptrace_scan_loop(void);
void print_usage(void);

#endif /* INJECTOR_H */
