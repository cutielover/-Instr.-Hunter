/*
 * Shared injector orchestration: configuration, buffered output, main loop.
 * Architecture-specific execution lives in arch_riscv.c / arch_aarch64.c.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <getopt.h>
#include <pthread.h>
#include <sched.h>
#include <stdarg.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "../include/injector.h"
#include "../include/arch.h"

config_t config = {
    .mode = MODE_EXHAUSTIVE,
    .exec_method = EXEC_CAGE,
    .output = OUTPUT_RAW,
    .scan_compressed = true,
    .show_tick = false,
    .filter_known_ext = false,
    .strict_filter = false,
    .allow_rwx = false,
    .jobs = 1,
    .core = 0,
    .force_core = false,
    .seed = 0,
    .resume_shards = NULL,
    .range = {
        .start = 0x00000000,
        .end = 0xFFFFFFFF,
        .started = false,
    },
};

state_t state = {
    .current = { .encoding = 0, .size = 4 },
    .count = 0,
    .hidden_count = 0,
    .disas_bug_count = 0,
};

int worker_id = 0;
uint32_t cs_mode_override = 0;

static pthread_mutex_t* pool_mutex = NULL;
static pthread_mutex_t* output_mutex = NULL;
static pthread_mutexattr_t mutex_attr;
static insn_t* shared_marker = NULL;

static char stdout_buffer[LINE_BUFFER_SIZE * BUFFER_LINES];
static char* stdout_pos = stdout_buffer;
static int sync_counter = 0;

static void sync_printf(const char* format, ...)
{
    va_list args;
    va_start(args, format);
    stdout_pos += vsprintf(stdout_pos, format, args);
    va_end(args);
}

static void sync_write(const void* data, size_t size)
{
    memcpy(stdout_pos, data, size);
    stdout_pos += size;
}

static void sync_flush(bool force)
{
    sync_counter++;
    if (sync_counter >= SYNC_LINES || force) {
        sync_counter = 0;
        if (output_mutex)
            pthread_mutex_lock(output_mutex);
        fwrite(stdout_buffer, stdout_pos - stdout_buffer, 1, stdout);
        fflush(stdout);
        if (output_mutex)
            pthread_mutex_unlock(output_mutex);
        stdout_pos = stdout_buffer;
    }
}

void report_result(void)
{
    arch_disassemble_instruction(&state.current, &state.disas);

    bool disas_ok = state.disas.known && !state.disas.illegal;
    bool is_timeout = (state.result.signum == SIGALRM);
    bool is_hidden = (state.result.signum == 0) && !disas_ok;
    bool is_disas_bug = (state.result.signum == SIGILL) && disas_ok;
    bool is_exec_fault_unknown = (state.result.signum != 0 && state.result.signum != SIGILL && state.result.signum != SIGALRM && !disas_ok);

    if (config.output == OUTPUT_TEXT) {
        if (is_timeout) {
            sync_printf("T 0x%08x %d %d\n",
                state.current.encoding,
                state.result.signum,
                state.result.si_code);
        } else if (is_hidden) {
            if (arch_is_hint_instruction(state.current.encoding)) {
            } else if (config.filter_known_ext && !config.strict_filter
                && arch_identify_known_extension(state.current.encoding)) {
            } else {
                sync_printf("H 0x%08x %d %d\n",
                    state.current.encoding,
                    state.result.signum,
                    state.result.si_code);
                state.hidden_count++;
            }
        } else if (is_disas_bug) {
            sync_printf("D 0x%08x %d %d\n",
                state.current.encoding,
                state.result.signum,
                state.result.si_code);
            state.disas_bug_count++;
        } else if (is_exec_fault_unknown) {
            sync_printf("X 0x%08x %d %d\n",
                state.current.encoding,
                state.result.signum,
                state.result.si_code);
        }
    } else {
        struct __attribute__((packed)) {
            uint8_t wid;
            uint8_t disas_len;
            uint8_t disas_known;
            uint8_t disas_illegal;
            uint32_t encoding;
            uint8_t valid;
            uint8_t length;
            uint8_t signum;
            uint8_t sicode;
        } raw_result = {
            .wid = (uint8_t)worker_id,
            .disas_len = (uint8_t)state.disas.length,
            .disas_known = (uint8_t)state.disas.known,
            .disas_illegal = (uint8_t)state.disas.illegal,
            .encoding = state.current.encoding,
            .valid = (uint8_t)state.result.valid,
            .length = (uint8_t)state.result.insn_size,
            .signum = (uint8_t)state.result.signum,
            .sicode = (uint8_t)state.result.si_code,
        };
        sync_write(&raw_result, sizeof(raw_result));
    }

    sync_flush(false);
}

void report_tick(void)
{
    static uint64_t tick_counter = 0;
    tick_counter++;

    if (config.show_tick && (tick_counter & TICK_MASK) == 0) {
        fprintf(stderr, "Progress: 0x%08x (%llu tested, %llu hidden, %llu disas bugs)\r",
            state.current.encoding,
            (unsigned long long)state.count,
            (unsigned long long)state.hidden_count,
            (unsigned long long)state.disas_bug_count);
    }
}

void print_usage(void)
{
    printf("Usage: injector [OPTIONS]\n");
    printf("  -E, --exhaustive     Exhaustive search mode (default)\n");
    printf("  -r, --random         Random sampling mode\n");
    printf("  -t, --targeted       Targeted opcode-group search mode\n");
    printf("  -p, --ptrace         Use ptrace single-step execution method\n");
    printf("  -R, --raw            Raw binary output\n");
    printf("  -T, --text           Text output (default for human)\n");
    printf("  -c, --compressed     Include compressed (16-bit) instructions\n");
    printf("  -C, --no-compressed  Skip compressed instructions\n");
    printf("  -F, --filter-ext     Filter out known-extension hidden instructions\n");
    printf("      --strict-filter  Strict extension filter (exact match, require ISA)\n");
    printf("      --rwx            Allow RWX pages (legacy mode for QEMU)\n");
    printf("  -x, --tick           Show progress ticks\n");
    printf("  -s, --seed SEED      Random seed\n");
    printf("  -b, --begin INSN     Start instruction (hex)\n");
    printf("  -e, --end INSN       End instruction (hex)\n");
    printf("  -j, --jobs N         Number of parallel jobs\n");
    printf("      --resume-shards  Internal per-worker resume ranges\n");
    printf("      --worker-id N   Worker ID written into raw output\n");
    printf("      --cs-mode N     Capstone cs_mode bitmask for disassembler\n");
    printf("  -a, --affinity N     CPU core affinity\n");
    printf("  -h, --help           Show this help\n");
}

enum {
    OPT_STRICT_FILTER = 256,
    OPT_RWX,
    OPT_RESUME_SHARDS,
    OPT_WORKER_ID,
    OPT_CS_MODE,
};

void init_config(int argc, char** argv)
{
    int opt;
    static struct option long_options[] = {
        { "exhaustive", no_argument, 0, 'E' },
        { "random", no_argument, 0, 'r' },
        { "targeted", no_argument, 0, 't' },
        { "ptrace", no_argument, 0, 'p' },
        { "raw", no_argument, 0, 'R' },
        { "text", no_argument, 0, 'T' },
        { "compressed", no_argument, 0, 'c' },
        { "no-compressed", no_argument, 0, 'C' },
        { "filter-ext", no_argument, 0, 'F' },
        { "strict-filter", no_argument, 0, OPT_STRICT_FILTER },
        { "rwx", no_argument, 0, OPT_RWX },
        { "tick", no_argument, 0, 'x' },
        { "seed", required_argument, 0, 's' },
        { "begin", required_argument, 0, 'b' },
        { "end", required_argument, 0, 'e' },
        { "jobs", required_argument, 0, 'j' },
        { "resume-shards", required_argument, 0, OPT_RESUME_SHARDS },
        { "worker-id", required_argument, 0, OPT_WORKER_ID },
        { "cs-mode", required_argument, 0, OPT_CS_MODE },
        { "affinity", required_argument, 0, 'a' },
        { "help", no_argument, 0, 'h' },
        { 0, 0, 0, 0 }
    };

    while ((opt = getopt_long(argc, argv, "ErtpRTcCFxs:b:e:j:a:h",
                long_options, NULL))
        != -1) {
        switch (opt) {
        case 'E':
            config.mode = MODE_EXHAUSTIVE;
            break;
        case 'r':
            config.mode = MODE_RANDOM;
            break;
        case 't':
            config.mode = MODE_TARGETED;
            break;
        case 'p':
            config.exec_method = EXEC_PTRACE;
            break;
        case 'R':
            config.output = OUTPUT_RAW;
            break;
        case 'T':
            config.output = OUTPUT_TEXT;
            break;
        case 'F':
            config.filter_known_ext = true;
            break;
        case OPT_STRICT_FILTER:
            config.strict_filter = true;
            config.filter_known_ext = true;
            break;
        case OPT_RWX:
            config.allow_rwx = true;
            break;
        case 'c':
            config.scan_compressed = true;
            break;
        case 'C':
            config.scan_compressed = false;
            break;
        case 'x':
            config.show_tick = true;
            break;
        case 's':
            config.seed = strtol(optarg, NULL, 0);
            break;
        case 'b':
            config.range.start = strtoul(optarg, NULL, 16);
            break;
        case 'e':
            config.range.end = strtoul(optarg, NULL, 16);
            break;
        case 'j':
            config.jobs = atoi(optarg);
            break;
        case OPT_RESUME_SHARDS:
            config.resume_shards = optarg;
            break;
        case OPT_WORKER_ID:
            worker_id = atoi(optarg);
            break;
        case OPT_CS_MODE:
            cs_mode_override = (uint32_t)strtoul(optarg, NULL, 0);
            break;
        case 'a':
            config.core = atoi(optarg);
            config.force_core = true;
            break;
        case 'h':
        default:
            print_usage();
            exit(opt == 'h' ? 0 : 1);
        }
    }

    if (config.seed == 0) {
        config.seed = time(NULL);
    }
    srand(config.seed);
    arch_check_cli_config();
}

static void init_multiprocess(void)
{
    pthread_mutexattr_init(&mutex_attr);
    pthread_mutexattr_setpshared(&mutex_attr, PTHREAD_PROCESS_SHARED);

    pool_mutex = mmap(NULL, sizeof(pthread_mutex_t),
        PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    output_mutex = mmap(NULL, sizeof(pthread_mutex_t),
        PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    shared_marker = mmap(NULL, sizeof(insn_t),
        PROT_READ | PROT_WRITE,
        MAP_SHARED | MAP_ANONYMOUS, -1, 0);

    pthread_mutex_init(pool_mutex, &mutex_attr);
    pthread_mutex_init(output_mutex, &mutex_attr);
    *shared_marker = config.range.start;
}

static void cleanup_multiprocess(void)
{
    if (pool_mutex) {
        pthread_mutex_destroy(pool_mutex);
        munmap(pool_mutex, sizeof(pthread_mutex_t));
    }
    if (output_mutex) {
        pthread_mutex_destroy(output_mutex);
        munmap(output_mutex, sizeof(pthread_mutex_t));
    }
    if (shared_marker) {
        munmap(shared_marker, sizeof(insn_t));
    }
}

static void pin_to_core(void)
{
    if (config.force_core) {
#ifdef __linux__
        cpu_set_t mask;
        CPU_ZERO(&mask);
        CPU_SET(config.core, &mask);
        if (sched_setaffinity(0, sizeof(mask), &mask) != 0) {
            fprintf(stderr, "Warning: failed to set CPU affinity\n");
        }
#else
        fprintf(stderr, "Warning: CPU affinity is only supported on Linux\n");
#endif
    }
}

static void partition_exhaustive_range(int job)
{
    uint64_t start = config.range.start;
    uint64_t end = config.range.end;

    if (config.mode != MODE_EXHAUSTIVE || config.jobs <= 1 || start > end) {
        return;
    }

    uint64_t total = end - start + 1;
    uint64_t base = total / (uint64_t)config.jobs;
    uint64_t rem = total % (uint64_t)config.jobs;
    uint64_t extra = ((uint64_t)job < rem) ? 1 : 0;
    uint64_t offset = (uint64_t)job * base + ((uint64_t)job < rem ? (uint64_t)job : rem);
    uint64_t span = base + extra;

    if (span == 0) {
        config.range.start = 1;
        config.range.end = 0;
        config.range.started = false;
        return;
    }

    config.range.start = (insn_t)(start + offset);
    config.range.end = (insn_t)(start + offset + span - 1);
    config.range.started = false;
}

static bool apply_resume_shard_range(int job)
{
    if (!config.resume_shards || config.mode != MODE_EXHAUSTIVE || config.jobs <= 1) {
        return false;
    }

    char* spec = strdup(config.resume_shards);
    if (!spec) {
        return false;
    }

    bool applied = false;
    char* saveptr = NULL;
    char* token = strtok_r(spec, ",", &saveptr);

    for (int i = 0; token; i++, token = strtok_r(NULL, ",", &saveptr)) {
        if (i != job) {
            continue;
        }

        char* dash = strchr(token, '-');
        if (!dash) {
            break;
        }

        *dash = '\0';
        char* start_str = token;
        char* end_str = dash + 1;
        char* endptr = NULL;
        unsigned long start = strtoul(start_str, &endptr, 16);
        if (*start_str == '\0' || *endptr != '\0') {
            break;
        }
        unsigned long end = strtoul(end_str, &endptr, 16);
        if (*end_str == '\0' || *endptr != '\0') {
            break;
        }

        config.range.start = (insn_t)start;
        config.range.end = (insn_t)end;
        config.range.started = false;
        applied = true;
        break;
    }

    free(spec);
    return applied;
}

int main(int argc, char** argv)
{
    pid_t pid;
    int job = 0;

    init_config(argc, argv);

    if (config.exec_method == EXEC_PTRACE) {
        arch_init_disassembler();
        int rc = ptrace_scan_loop();
        arch_cleanup_disassembler();
        return rc;
    }

    arch_init_memory();
    arch_init_disassembler();
    arch_init_signal_handlers();

    if (config.jobs > 1) {
        init_multiprocess();

        for (int i = 0; i < config.jobs - 1; i++) {
            pid = fork();
            if (pid < 0) {
                perror("fork failed");
                exit(1);
            }
            if (pid == 0) {
                job = i + 1;
                break;
            }
        }
    }

    if (config.jobs > 1)
        worker_id = job;
    if (!apply_resume_shard_range(job)) {
        partition_exhaustive_range(job);
    }
    pin_to_core();

    while (arch_move_next_instruction()) {
        arch_inject_instruction(&state.current);
        report_result();
        report_tick();
        state.count++;
    }

    sync_flush(true);

    if (config.jobs > 1 && job == 0) {
        for (int i = 0; i < config.jobs - 1; i++) {
            wait(NULL);
        }
        cleanup_multiprocess();
    }

    arch_cleanup_disassembler();
    arch_cleanup_memory();

    if (config.show_tick) {
        fprintf(stderr, "\nCompleted: %llu instructions tested, %llu hidden, %llu disas bugs\n",
            (unsigned long long)state.count,
            (unsigned long long)state.hidden_count,
            (unsigned long long)state.disas_bug_count);
    }

    return 0;
}
