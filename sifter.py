#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RISC-V Sifter - Hidden Instruction Analyzer Frontend
Based on sandsifter design by Christopher Domas

This is the main control program that:
1. Launches the C injector process
2. Displays real-time scanning progress
3. Records and filters results
4. Provides an interactive interface
"""

import signal
import sys
import subprocess
import os
import select
import threading
import time
import argparse
import curses
import json
from struct import unpack
from collections import deque
from binascii import hexlify
import copy

from analysis.common import (
    ARM64_MODE_DEFAULT,
    CAPSTONE_V6,
    HAS_CAPSTONE,
    RISCV_MODE_ALL,
    capstone_mode_for_sifter_arch,
    describe_capstone_mode,
    detect_isa_extensions_from_string,
)

if HAS_CAPSTONE:
    from capstone import Cs, CS_ARCH_RISCV
    try:
        from capstone import CS_ARCH_ARM64 as _CS_ARCH_A64
    except ImportError:
        from capstone import CS_ARCH_AARCH64 as _CS_ARCH_A64

ARCH_RISCV = "riscv"
ARCH_AARCH64 = "aarch64"

# Paths
INJECTOR_RISCV = "./injector"
INJECTOR_AARCH64 = "./injector_aarch64"
OUTPUT_DIR = "./data/"
LOG_FILE = OUTPUT_DIR + "log"
SYNC_FILE = OUTPUT_DIR + "sync"
TICK_FILE = OUTPUT_DIR + "tick"
LAST_FILE = OUTPUT_DIR + "last"
RUN_FILE = OUTPUT_DIR + "run.json"

# Reader thread: avoid blocking forever on injector stdout (Unix).
STDOUT_POLL_TIMEOUT_S = 1.0
# If no full result frames arrive for this long while the process stays alive, kill and resume.
DEFAULT_WORKER_STALL_TIMEOUT_S = 120.0


def _stdout_select_supported():
    """select() on subprocess pipes is not reliable on Windows."""
    return os.name == "posix"


def detect_isa_extensions():
    """Parse /proc/cpuinfo and return (extension_tokens_set, raw_isa_string)."""
    isa_string = "(unknown)"
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for raw in f:
                line = raw.strip()
                if not line or ':' not in line:
                    continue
                key, value = [x.strip() for x in line.split(':', 1)]
                if key.lower() != 'isa':
                    continue
                isa_string = value
                break
    except Exception:
        pass
    return detect_isa_extensions_from_string(isa_string), isa_string


class ThreadState:
    """Shared state between threads"""
    pause = False
    run = True


class RawResult:
    """Structure matching the C injector output (12 bytes)"""
    def __init__(self, data=None):
        if data and len(data) >= 12:
            self.worker_id = data[0]
            self.disas_len = data[1]
            self.disas_known = data[2]
            self.disas_illegal = data[3]
            self.encoding = unpack('<I', data[4:8])[0]
            self.valid = data[8]
            self.length = data[9]
            self.signum = data[10]
            self.sicode = data[11]
        else:
            self.worker_id = 0
            self.disas_len = 0
            self.disas_known = 0
            self.disas_illegal = 0
            self.encoding = 0
            self.valid = 0
            self.length = 0
            self.signum = 0
            self.sicode = 0


class WorkerStats:
    """Per-worker progress and result counters"""
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.result = RawResult()
        self.insn_count = 0
        self.hidden_count = 0
        self.disas_bug_count = 0
        self.disas_mismatch_raw_count = 0
        self.disas_mismatch_strict_count = 0
        self.timeout_count = 0
        self.exec_fault_count = 0


def classify_artifact(result):
    """Return the artifact type letter for a recorded result."""
    disas_valid = result.disas_known and not result.disas_illegal
    if result.signum == Poll.SIGALRM:
        return 'T'
    if result.signum == 0 and not disas_valid:
        return 'H'
    if result.signum == Poll.SIGILL and disas_valid:
        return 'D'
    if result.signum not in (0, Poll.SIGILL, Poll.SIGALRM) and not disas_valid:
        return 'X'
    return '?'


def format_artifact_line(result, mnemonic="", operands=""):
    """Format an artifact entry consistently for sync/log output."""
    artifact_type = classify_artifact(result)
    asm = f"{mnemonic} {operands}".strip()
    if asm:
        return f"{artifact_type} 0x{result.encoding:08x} {result.signum} {result.sicode} ; {asm}\n"
    return f"{artifact_type} 0x{result.encoding:08x} {result.signum} {result.sicode}\n"


class Settings:
    """Injector settings"""
    MODE_EXHAUSTIVE = 'E'
    MODE_RANDOM = 'r'
    MODE_TARGETED = 't'
    
    def __init__(self, args, arch=ARCH_RISCV):
        self.arch = arch
        self.mode = self.MODE_EXHAUSTIVE
        self.args = args
        self.seed = int(time.time())
        self.compressed = True
        self.begin = None
        self.end = None
        self.jobs = 1
        
        if '-E' in args or '--exhaustive' in args:
            self.mode = self.MODE_EXHAUSTIVE
        elif '-r' in args or '--random' in args:
            self.mode = self.MODE_RANDOM
        elif '-t' in args or '--targeted' in args:
            self.mode = self.MODE_TARGETED

        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ('-b', '--begin') and i + 1 < len(args):
                self.begin = int(args[i + 1], 16)
                i += 2
                continue
            if arg in ('-e', '--end') and i + 1 < len(args):
                self.end = int(args[i + 1], 16)
                i += 2
                continue
            if arg in ('-j', '--jobs') and i + 1 < len(args):
                self.jobs = max(1, int(args[i + 1]))
                i += 2
                continue
            i += 1


def describe_mode(mode):
    """Return human-readable mode name from Settings mode letter."""
    return {
        Settings.MODE_EXHAUSTIVE: 'exhaustive',
        Settings.MODE_RANDOM: 'random',
        Settings.MODE_TARGETED: 'targeted',
    }.get(mode, 'unknown')


class Tests:
    """Test state and results"""
    INSN_LOG_LEN = 20
    ARTIFACT_LOG_LEN = 10
    
    def __init__(self):
        self.result = RawResult()
        self.insn_log = deque(maxlen=self.INSN_LOG_LEN)
        self.artifact_log = deque(maxlen=self.ARTIFACT_LOG_LEN)
        self.artifact_dict = {}
        self.worker_stats = {}
        self.insn_count = 0
        self.artifact_count = 0
        self.hidden_count = 0
        self.disas_bug_count = 0
        self.disas_mismatch_raw_count = 0
        self.disas_mismatch_strict_count = 0
        self.timeout_count = 0
        self.exec_fault_count = 0
        self.start_time = time.time()
        
    def elapsed(self):
        """Return formatted elapsed time"""
        elapsed = time.time() - self.start_time
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        return "%02d:%02d:%02d" % (h, m, int(s))


class Disassembler:
    """Capstone-backed disassembler for RISC-V or AArch64."""
    def __init__(self, arch=ARCH_RISCV, mode=None):
        self.arch = arch
        self.md = None
        if HAS_CAPSTONE:
            if arch == ARCH_AARCH64:
                self.md = Cs(_CS_ARCH_A64, mode if mode is not None else ARM64_MODE_DEFAULT)
            else:
                self.md = Cs(CS_ARCH_RISCV, mode if mode is not None else RISCV_MODE_ALL)
            
    def disassemble(self, encoding, size=None):
        if not self.md:
            return ("(no disas)", "", 0)

        if self.arch == ARCH_AARCH64:
            size = 4
        elif size is None:
            size = 2 if (encoding & 0x3) != 0x3 else 4

        try:
            insn_bytes = encoding.to_bytes(size, byteorder='little')
            for insn in self.md.disasm(insn_bytes, 0):
                return (insn.mnemonic, insn.op_str, insn.size)
        except Exception:
            pass
        return ("(unk)", "", 0)


class Injector:
    """Injector process manager with auto-restart on crash"""
    MAX_RESTARTS = 500
    RESTART_COOLDOWN = 0.2

    def __init__(self, settings, worker_id=0, cs_mode=None, injector_path=None):
        self.settings = settings
        self.worker_id = worker_id
        self.cs_mode = cs_mode
        arch = getattr(settings, 'arch', ARCH_RISCV)
        self.injector_path = injector_path or (
            INJECTOR_AARCH64 if arch == ARCH_AARCH64 else INJECTOR_RISCV)
        self.process = None
        self.command = None
        self.last_encoding = None
        self.crash_count = 0
        self._resume_begin = None

    def _build_cmd(self):
        cmd_parts = [self.injector_path]
        cmd_parts.append('-R')  # Raw output
        cmd_parts.extend(self.settings.args)
        cmd_parts.extend(['-s', str(self.settings.seed)])
        cmd_parts.extend(['--worker-id', str(self.worker_id)])
        if self.cs_mode is not None:
            cmd_parts.extend(['--cs-mode', str(self.cs_mode)])

        if not self.settings.compressed:
            cmd_parts.append('-C')

        if self._resume_begin is not None:
            filtered = []
            skip_next = False
            for a in cmd_parts:
                if skip_next:
                    skip_next = False
                    continue
                if a in ('-b', '--begin'):
                    skip_next = True
                    continue
                filtered.append(a)
            cmd_parts = filtered
            cmd_parts.extend(['-b', f'{self._resume_begin:x}'])

        return cmd_parts

    def _next_resume_begin(self, last_encoding):
        if last_encoding is None:
            return self.settings.begin

        if self.settings.mode != Settings.MODE_EXHAUSTIVE:
            return (last_encoding + 1) & 0xFFFFFFFF

        if getattr(self.settings, 'arch', ARCH_RISCV) == ARCH_AARCH64:
            step = 1
        else:
            step = 1 if self.settings.compressed else 4
        return (last_encoding + step) & 0xFFFFFFFF

    def start(self):
        """Start the injector process"""
        cmd_parts = self._build_cmd()
        self.command = ' '.join(cmd_parts)

        try:
            self.process = subprocess.Popen(
                cmd_parts,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
        except FileNotFoundError:
            print(f"Error: Injector not found at {self.injector_path}")
            print("Please run 'make' or 'make injector_aarch64' to build the injector.")
            sys.exit(1)

    def restart_from(self, last_encoding):
        """Restart injector scanning from the instruction after last_encoding"""
        self.stop()
        self.crash_count += 1
        self._resume_begin = self._next_resume_begin(last_encoding)
        time.sleep(self.RESTART_COOLDOWN)
        self.start()

    def stop(self):
        """Stop the injector process"""
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.process.wait(timeout=2)
            except Exception:
                pass


def compute_shards(jobs, begin=None, end=None):
    """Compute N exhaustive shard ranges from [begin, end]."""
    start = begin if begin is not None else 0
    stop = end if end is not None else 0xFFFFFFFF
    if start > stop or jobs <= 1:
        return []

    total = stop - start + 1
    base = total // jobs
    rem = total % jobs
    shards = []
    offset = 0

    for i in range(jobs):
        span = base + (1 if i < rem else 0)
        if span <= 0:
            shard_start = 1
            shard_end = 0
        else:
            shard_start = start + offset
            shard_end = shard_start + span - 1
        shards.append((shard_start & 0xFFFFFFFF, shard_end & 0xFFFFFFFF))
        offset += span

    return shards


class Poll:
    """Result polling — manages one reader thread per injector process."""
    SIGILL = 4
    SIGSEGV = 11
    SIGFPE = 8
    SIGBUS = 7
    SIGTRAP = 5
    SIGALRM = 14
    ILL_ILLOPC = 1

    RESULT_SIZE = 12  # Size of raw result struct (v6: +disas_illegal)

    def __init__(self, ts, injectors, tests, sync=False, low_mem=False,
                 search_unk=True, search_dis=False, filter_ext=False,
                 strict_filter=False, isa_extensions=None, cs_mode=None,
                 stall_timeout_s=DEFAULT_WORKER_STALL_TIMEOUT_S,
                 arch=ARCH_RISCV):
        self.ts = ts
        if isinstance(injectors, list):
            self.injectors = injectors
        else:
            self.injectors = [injectors]
        self.tests = tests
        self.poll_threads = []
        self.lock = threading.Lock()
        self.sync = sync
        self.low_mem = low_mem
        self.search_unk = search_unk
        self.search_dis = search_dis
        self.filter_ext = filter_ext
        self.strict_filter = strict_filter
        self.isa_extensions = set(isa_extensions or [])
        self.arch = arch
        self.disas = Disassembler(arch=arch, mode=cs_mode)
        self.stall_timeout_s = float(stall_timeout_s)
        self._use_stdout_select = _stdout_select_supported()

        if self.sync:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(SYNC_FILE, 'w') as f:
                title = "AArch64 Sifter Results" if arch == ARCH_AARCH64 else "RISC-V Sifter Results"
                f.write(f"# {title}\n")
                f.write(f"# Workers: {len(self.injectors)}\n")
                f.write(f"# Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# Format: type encoding signal code\n")
                f.write("#\n")

    def start(self):
        """Start one reader thread per injector."""
        for inj in self.injectors:
            ws = WorkerStats(inj.worker_id)
            self.tests.worker_stats[inj.worker_id] = ws
            t = threading.Thread(target=self._reader_loop, args=(inj, ws),
                                 daemon=True)
            t.start()
            self.poll_threads.append(t)

    def stop(self):
        """Wait for all reader threads to finish."""
        for t in self.poll_threads:
            t.join(timeout=1.0)

    def _recover_stalled_worker(self, injector):
        """Kill a hung injector and resume after last known encoding."""
        last = injector.last_encoding
        if self.sync:
            with self.lock:
                with open(SYNC_FILE, 'a') as f:
                    f.write(
                        f"# STALL W{injector.worker_id} "
                        f"no_progress={self.stall_timeout_s:g}s "
                        f"after=0x{last or 0:08x} "
                        f"restart={injector.crash_count + 1}\n")
        injector.restart_from(last)

    def _reader_loop(self, injector, worker):
        """Read results from a single injector, restart on crash or stall."""
        BATCH = 64
        buf_size = self.RESULT_SIZE * BATCH
        RS = self.RESULT_SIZE
        _is_hint = self._is_hint
        _identify_ext = self._identify_known_extension
        _ext_enabled = self._extension_enabled
        do_filter = self.filter_ext
        search_unk = self.search_unk
        search_dis = self.search_dis
        SIGALRM = self.SIGALRM
        SIGILL = self.SIGILL
        ILL_ILLOPC = self.ILL_ILLOPC
        use_select = self._use_stdout_select
        stall_s = self.stall_timeout_s
        poll_to = STDOUT_POLL_TIMEOUT_S

        pending = b''
        last_progress = time.monotonic()

        while self.ts.run:
            while self.ts.pause:
                last_progress = time.monotonic()
                time.sleep(0.1)

            try:
                proc = injector.process
                if proc is None or proc.stdout is None:
                    break

                chunk = b''
                if use_select:
                    fd = proc.stdout.fileno()
                    try:
                        readable, _, _ = select.select([fd], [], [], poll_to)
                    except (ValueError, OSError):
                        break
                    if not readable:
                        if stall_s > 0 and proc.poll() is None:
                            if time.monotonic() - last_progress > stall_s:
                                if injector.crash_count < injector.MAX_RESTARTS:
                                    self._recover_stalled_worker(injector)
                                    pending = b''
                                    last_progress = time.monotonic()
                                else:
                                    break
                        continue
                    try:
                        chunk = os.read(fd, buf_size)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break
                else:
                    chunk = proc.stdout.read(buf_size)

                if not chunk:
                    pending = b''
                    if proc.poll() is not None:
                        exit_code = proc.returncode
                        is_sigterm = exit_code == -signal.SIGTERM
                        is_crash = exit_code != 0 and not is_sigterm
                        if is_crash and self.ts.run and \
                           injector.crash_count < injector.MAX_RESTARTS:
                            last = injector.last_encoding
                            if self.sync:
                                with self.lock:
                                    with open(SYNC_FILE, 'a') as f:
                                        f.write(
                                            f"# CRASH W{injector.worker_id} "
                                            f"exit={exit_code} "
                                            f"after=0x{last or 0:08x} "
                                            f"restart="
                                            f"{injector.crash_count+1}\n")
                            injector.restart_from(last)
                            continue
                        if is_sigterm or not self.ts.run:
                            break
                        break
                    if use_select:
                        continue
                    continue

                pending += chunk
                n_full = len(pending) // RS
                if n_full == 0:
                    continue

                batch = pending[: n_full * RS]
                pending = pending[n_full * RS :]
                last_progress = time.monotonic()

                local_insn = 0
                local_hidden = 0
                local_disas_bug = 0
                local_disas_raw = 0
                local_disas_strict = 0
                local_timeout = 0
                local_exec_fault = 0
                artifacts = []
                last_result = None

                for i in range(n_full):
                    off = i * RS
                    frame = batch[off:off + RS]
                    result = RawResult(frame)
                    last_result = result

                    local_insn += 1
                    worker.insn_count += 1

                    signum = result.signum
                    disas_known = result.disas_known
                    disas_illegal = result.disas_illegal
                    disas_valid = disas_known and not disas_illegal

                    if signum == SIGALRM:
                        local_timeout += 1
                        worker.timeout_count += 1
                        artifacts.append(result)
                        continue

                    is_hidden = (signum == 0) and not disas_valid
                    is_disas_bug = (signum == SIGILL) and disas_valid
                    is_disas_bug_strict = is_disas_bug and (result.sicode == ILL_ILLOPC)
                    is_exec_fault = (signum not in (0, SIGILL, SIGALRM)) and \
                        not disas_valid

                    if is_hidden:
                        if self.arch != ARCH_AARCH64 and _is_hint(result.encoding):
                            continue
                        if do_filter:
                            ext = _identify_ext(result.encoding)
                            if ext and _ext_enabled(ext):
                                continue
                        if search_unk:
                            local_hidden += 1
                            worker.hidden_count += 1
                            artifacts.append(result)

                    if is_disas_bug and search_dis:
                        local_disas_raw += 1
                        worker.disas_mismatch_raw_count += 1
                        if self.arch == ARCH_AARCH64:
                            if is_disas_bug_strict:
                                local_disas_bug += 1
                                local_disas_strict += 1
                                worker.disas_bug_count += 1
                                worker.disas_mismatch_strict_count += 1
                                artifacts.append(result)
                        else:
                            local_disas_bug += 1
                            worker.disas_bug_count += 1
                            artifacts.append(result)

                    if is_exec_fault and search_unk:
                        local_exec_fault += 1
                        worker.exec_fault_count += 1
                        artifacts.append(result)

                if last_result is not None:
                    injector.last_encoding = last_result.encoding
                    worker.result = last_result

                if local_insn > 0 or artifacts:
                    with self.lock:
                        self.tests.insn_count += local_insn
                        self.tests.hidden_count += local_hidden
                        self.tests.disas_bug_count += local_disas_bug
                        self.tests.disas_mismatch_raw_count += local_disas_raw
                        self.tests.disas_mismatch_strict_count += local_disas_strict
                        self.tests.timeout_count += local_timeout
                        self.tests.exec_fault_count += local_exec_fault
                        if last_result is not None:
                            self.tests.result = last_result
                        for art in artifacts:
                            self._record_artifact(art)

            except Exception:
                if self.ts.run:
                    if injector.crash_count < injector.MAX_RESTARTS:
                        last = injector.last_encoding
                        injector.restart_from(last)
                        pending = b''
                        last_progress = time.monotonic()
                        continue
                break

    @staticmethod
    def _is_hint(encoding):
        """Check if instruction is a HINT (architecturally NOP)."""
        if (encoding & 0x3) != 0x3:
            # Compressed instruction — check 16-bit value
            insn = encoding & 0xFFFF
            quadrant = insn & 0x3
            funct3 = (insn >> 13) & 0x7
            rd = (insn >> 7) & 0x1F

            if quadrant == 1:
                imm5 = (insn >> 12) & 0x1
                imm40 = (insn >> 2) & 0x1F
                if funct3 == 0 and rd == 0 and (imm5 | imm40) != 0:
                    return True   # C.NOP with nzimm → HINT
                if funct3 == 0 and rd != 0 and (imm5 | imm40) == 0:
                    return True   # C.ADDI rd, 0 → HINT
                if funct3 == 2 and rd == 0:
                    return True   # C.LI x0, imm → HINT
                if funct3 == 3 and rd == 0:
                    return True   # C.LUI x0, imm → HINT
            if quadrant == 2:
                if funct3 == 0 and rd == 0:
                    return True   # C.SLLI x0 → HINT
                if funct3 == 4:
                    bit12 = (insn >> 12) & 0x1
                    rs2 = (insn >> 2) & 0x1F
                    if rd == 0 and rs2 != 0:
                        return True  # C.MV/C.ADD x0, rs2 → HINT
            return False
        else:
            # 32-bit instruction
            opcode = encoding & 0x7F
            rd = (encoding >> 7) & 0x1F
            if opcode in (0x37, 0x17, 0x13, 0x1B) and rd == 0:
                return True   # LUI/AUIPC/OP-IMM/OP-IMM-32 with rd=x0
            return False

    @staticmethod
    def _identify_known_extension(encoding):
        """Return extension name if encoding belongs to a known extension
        that Capstone may not decode, or None otherwise.
        With Capstone 6 most of these are natively handled; this is a fallback."""
        if (encoding & 0x3) != 0x3:
            c = encoding & 0xFFFF
            q, f3 = c & 0x3, (c >> 13) & 0x7
            if q == 0 and f3 == 4:
                return "Zcb"
            if q == 1 and f3 == 4:
                b12, b1110, b65 = (c>>12)&1, (c>>10)&3, (c>>5)&3
                if b1110 == 3 and b12 == 1 and b65 >= 2:
                    return "Zcb"
            if q == 1 and f3 == 0 and ((c >> 7) & 0x1F) == 0:
                return "Zcmop"
            return None

        opc = encoding & 0x7F
        f3  = (encoding >> 12) & 0x7
        f7  = (encoding >> 25) & 0x7F
        fmt = (encoding >> 25) & 0x3
        f5  = f7 >> 2

        if opc in (0x07, 0x27) and f3 == 1:
            return "Zfh"
        if opc in (0x07, 0x27) and f3 in (0, 7):
            return "V"
        if opc in (0x43, 0x47, 0x4B, 0x4F) and fmt == 2:
            return "Zfh"
        if opc == 0x53:
            if fmt == 2:        return "Zfh"
            if (f7 & 0x7C) == 0x78: return "Zfa"
            if f7 in (0x14, 0x15, 0x20, 0x21): return "Zfa"
        if opc == 0x57:
            return "V"
        if opc == 0x33:
            if f7 == 0x05:            return "Zba"
            if f7 == 0x04 and f3 >= 4: return "Zbb"
            if f7 == 0x20 and f3 in (1,4,5,6,7): return "Zbb"
            if f7 == 0x30:            return "Zbb"
            if f7 in (0x14, 0x24, 0x34): return "Zbs"
            if f7 == 0x07 and f3 in (5,7): return "Zicond"
            if f7 == 0x08:            return "Zbc"
            if f7 in (0x48, 0x18, 0x10): return "Zbkb"
            if f7 in (0x19, 0x1A, 0x1F, 0x7A): return "Zb*"
        if opc == 0x3B:
            if f7 == 0x30:            return "Zbb"
            if f7 in (0x04, 0x05):    return "Zba"
            if f7 == 0x10 and f3 in (2, 4, 6): return "Zb*"
        if opc == 0x13:
            if f7 == 0x30 and f3 in (1,5): return "Zbb"
            if (f7 & 0x3E) == 0x24:       return "Zbs"
            if f7 == 0x34 and f3 == 1:     return "Zbs"
            if f7 == 0x31 and f3 == 5:     return "Zbb"
        if opc == 0x1B:
            if f7 == 0x30:            return "Zbb"
            if f7 == 0x04 and f3 == 0: return "Zba"
            if f7 == 0x05 and f3 == 1: return "Zba"
        if opc == 0x2F:
            if f3 in (0, 1):          return "Zabha"
            if f5 == 5:               return "Zacas"
        if opc == 0x73 and f3 != 0 and f7 >= 0x40:
            return "Zimop"
        if opc == 0x0F:
            if f3 == 2: return "Zicbom"
            if f3 == 6: return "Zicbop"
            if f3 in (0, 1): return "Zihintntl"
        return None

    def _extension_enabled(self, ext_name):
        """Check whether a known-extension label is enabled in detected ISA.

        In --strict-filter mode:
          - ISA extensions MUST be available; if not, return False (do not filter).
          - Wildcard labels like 'Zb*' and 'Zk' are NOT expanded; only exact
            matches count, so ambiguous encodings are never silently dropped.
        """
        if not self.isa_extensions:
            return not self.strict_filter

        ext = (ext_name or "").lower()
        exts = self.isa_extensions

        if self.strict_filter:
            return ext in exts

        if ext == 'v':
            return ('v' in exts) or any(x.startswith('zv') for x in exts)
        if ext == 'zb*':
            return any(x.startswith('zb') for x in exts)
        if ext == 'zk':
            return any(x.startswith('zk') for x in exts)

        return ext in exts

    def _record_artifact(self, result):
        """Record an interesting artifact (must be called with self.lock held)."""
        key = result.encoding

        if key not in self.tests.artifact_dict:
            self.tests.artifact_count += 1
            self.tests.artifact_log.appendleft(copy.deepcopy(result))

            if not self.low_mem:
                self.tests.artifact_dict[key] = result

            if self.sync:
                mne, ops, _ = self.disas.disassemble(result.encoding)
                with open(SYNC_FILE, 'a') as f:
                    f.write(format_artifact_line(result, mne, ops))


class Gui:
    """Terminal GUI using curses"""
    TIME_SLICE = 0.05
    TICK_MASK = 0xff
    
    # Colors
    WHITE = 1
    RED = 2
    GREEN = 3
    BLUE = 4
    GRAY = 5
    
    def __init__(self, ts, injectors, settings, tests, do_tick=False,
                 cs_mode=None, isa_string="(unknown)", isa_extensions=None,
                 arch=ARCH_RISCV):
        self.ts = ts
        if isinstance(injectors, list):
            self.injectors = injectors
        else:
            self.injectors = [injectors]
        self.settings = settings
        self.tests = tests
        self.gui_thread = None
        self.do_tick = do_tick
        self.ticks = 0
        self.cs_mode = cs_mode
        self.isa_string = isa_string
        self.isa_extensions = isa_extensions or set()
        self.arch = arch
        self.disas = Disassembler(arch=arch, mode=cs_mode)
        
        self.last_count = 0
        self.rate_samples = deque(maxlen=100)
        self.time_samples = deque(maxlen=100)
        self.last_time = time.time()
        
        # Initialize curses
        self.stdscr = curses.initscr()
        curses.start_color()
        curses.use_default_colors()
        curses.noecho()
        curses.cbreak()
        curses.curs_set(0)
        self.stdscr.nodelay(1)
        
        self.init_colors()
        
    def init_colors(self):
        """Initialize color pairs"""
        curses.init_pair(self.WHITE, curses.COLOR_WHITE, -1)
        curses.init_pair(self.RED, curses.COLOR_RED, -1)
        curses.init_pair(self.GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(self.BLUE, curses.COLOR_BLUE, -1)
        curses.init_pair(self.GRAY, curses.COLOR_WHITE, -1)
        
    def start(self):
        """Start GUI thread"""
        self.gui_thread = threading.Thread(target=self.render_loop, daemon=True)
        self.gui_thread.start()
        
    def stop(self):
        """Stop GUI and cleanup"""
        if self.gui_thread:
            self.gui_thread.join(timeout=1.0)
        curses.nocbreak()
        curses.echo()
        curses.endwin()
        
    def render_loop(self):
        """Main render loop"""
        while self.ts.run:
            while self.ts.pause:
                self.check_key()
                time.sleep(0.1)
                
            try:
                self.draw()
                self.check_key()
            except curses.error:
                pass
                
            if self.do_tick:
                self.ticks += 1
                if self.ticks & self.TICK_MASK == 0:
                    with open(TICK_FILE, 'w') as f:
                        f.write(f"0x{self.tests.result.encoding:08x}")
                        
            time.sleep(self.TIME_SLICE)
            
    def check_key(self):
        """Handle keyboard input"""
        try:
            c = self.stdscr.getch()
            if c == ord('q'):
                self.ts.run = False
            elif c == ord('p'):
                self.ts.pause = not self.ts.pause
        except curses.error:
            pass
            
    def draw(self):
        """Draw the interface"""
        try:
            self.stdscr.erase()
            maxy, maxx = self.stdscr.getmaxyx()
            
            # Title
            title = ("═══ AArch64 Sifter ═══" if self.arch == ARCH_AARCH64
                     else "═══ RISC-V Sifter ═══")
            self.stdscr.addstr(0, max(0, (maxx - len(title)) // 2), 
                              title, curses.color_pair(self.GREEN) | curses.A_BOLD)
            
            # Current instruction info
            y = 2
            result = self.tests.result
            
            self.stdscr.addstr(y, 2, "Current:", curses.color_pair(self.WHITE))
            self.stdscr.addstr(y, 12, f"0x{result.encoding:08x}", 
                              curses.color_pair(self.BLUE) | curses.A_BOLD)
            
            # Disassembly
            mne, ops, size = self.disas.disassemble(result.encoding)
            self.stdscr.addstr(y, 26, f"{mne} {ops}", curses.color_pair(self.WHITE))
            
            # Signal info
            y += 1
            sig_names = {4: 'SIGILL', 11: 'SIGSEGV', 8: 'SIGFPE', 
                        7: 'SIGBUS', 5: 'SIGTRAP', 0: 'OK'}
            sig_name = sig_names.get(result.signum, f'SIG{result.signum}')
            self.stdscr.addstr(y, 2, "Signal:", curses.color_pair(self.WHITE))
            color = self.GREEN if result.signum == 0 else self.RED
            self.stdscr.addstr(y, 12, f"{sig_name} ({result.sicode})", 
                              curses.color_pair(color))
            
            # Statistics
            y += 2
            self.stdscr.addstr(y, 2, "─" * (min(maxx - 4, 70)), 
                              curses.color_pair(self.GRAY))
            
            y += 1
            self.stdscr.addstr(y, 2, "Statistics:", 
                              curses.color_pair(self.WHITE) | curses.A_BOLD)
            
            y += 1
            self.stdscr.addstr(y, 4, f"Tested:      {self.tests.insn_count:,}", 
                              curses.color_pair(self.WHITE))
            
            y += 1
            self.stdscr.addstr(y, 4, f"Hidden:      {self.tests.hidden_count:,}", 
                              curses.color_pair(self.RED))
            
            y += 1
            disas_label = "D(strict):   " if self.arch == ARCH_AARCH64 else "Disas Bugs:  "
            self.stdscr.addstr(y, 4, f"{disas_label}{self.tests.disas_bug_count:,}",
                              curses.color_pair(self.BLUE))
            
            y += 1
            self.stdscr.addstr(y, 4, f"Timeouts:    {self.tests.timeout_count:,}",
                              curses.color_pair(self.RED))

            y += 1
            self.stdscr.addstr(y, 4, f"Exec Faults: {self.tests.exec_fault_count:,}",
                              curses.color_pair(self.RED))

            y += 1
            self.stdscr.addstr(y, 4, f"Time:        {self.tests.elapsed()}", 
                              curses.color_pair(self.WHITE))
            
            # Calculate rate
            now = time.time()
            delta = self.tests.insn_count - self.last_count
            dt = now - self.last_time
            self.rate_samples.append(delta)
            self.time_samples.append(dt)
            self.last_count = self.tests.insn_count
            self.last_time = now
            
            if sum(self.time_samples) > 0:
                rate = int(sum(self.rate_samples) / sum(self.time_samples))
            else:
                rate = 0
                
            y += 1
            self.stdscr.addstr(y, 4, f"Rate:        {rate:,}/s", 
                              curses.color_pair(self.GREEN))

            total_restarts = sum(inj.crash_count for inj in self.injectors)
            if total_restarts > 0:
                y += 1
                self.stdscr.addstr(y, 4, f"Restarts:    {total_restarts}",
                                  curses.color_pair(self.RED))

            # Mode & ISA
            y += 2
            mode_map = {'E': 'Exhaustive', 'r': 'Random', 't': 'Targeted'}
            mode_name = mode_map.get(self.settings.mode, 'Unknown')
            self.stdscr.addstr(y, 2, f"Mode: {mode_name}",
                              curses.color_pair(self.WHITE))
            num_jobs = len(self.injectors)
            self.stdscr.addstr(y, 26, f"Jobs: {num_jobs}",
                              curses.color_pair(self.WHITE))

            y += 1
            cs_desc = describe_capstone_mode(self.cs_mode, self.arch)
            self.stdscr.addstr(y, 2, f"Caps: {cs_desc}"[:maxx-4],
                              curses.color_pair(self.WHITE))

            y += 1
            isa_full = self.isa_string
            line_width = max(10, maxx - 8)
            self.stdscr.addstr(y, 2, "ISA:", curses.color_pair(self.GRAY))
            pos = 0
            while pos < len(isa_full) and y < maxy - 2:
                y += 1
                chunk = isa_full[pos:pos + line_width]
                self.stdscr.addstr(y, 6, chunk, curses.color_pair(self.GRAY))
                pos += line_width

            if num_jobs > 1:
                y += 2
                self.stdscr.addstr(y, 2, "Workers:",
                                  curses.color_pair(self.WHITE) | curses.A_BOLD)

                for wid in range(num_jobs):
                    y += 1
                    if y >= maxy - 7:
                        break
                    worker = self.tests.worker_stats.get(wid)
                    if worker is None or worker.insn_count == 0:
                        line = f"W{wid:02d}  starting..."
                    else:
                        disas_count = (worker.disas_mismatch_strict_count
                                       if self.arch == ARCH_AARCH64
                                       else worker.disas_bug_count)
                        line = (f"W{wid:02d}  0x{worker.result.encoding:08x}  "
                                f"{worker.insn_count:,} tested  "
                                f"H{worker.hidden_count} D{disas_count} "
                                f"T{worker.timeout_count} X{worker.exec_fault_count}")
                    self.stdscr.addstr(y, 4, line[:maxx - 8], curses.color_pair(self.WHITE))
            
            # Recent artifacts
            if self.tests.artifact_log:
                y += 2
                self.stdscr.addstr(y, 2, "Recent Anomalies:", 
                                  curses.color_pair(self.RED) | curses.A_BOLD)
                
                for i, artifact in enumerate(list(self.tests.artifact_log)[:5]):
                    y += 1
                    if y >= maxy - 2:
                        break
                    mne, ops, _ = self.disas.disassemble(artifact.encoding)
                    self.stdscr.addstr(y, 4, f"0x{artifact.encoding:08x}  {mne} {ops}", 
                                      curses.color_pair(self.RED))
            
            # Help
            self.stdscr.addstr(maxy - 1, 2, 
                              "[Q]uit  [P]ause", 
                              curses.color_pair(self.GRAY))
            
            self.stdscr.refresh()
            
        except curses.error:
            pass


def get_cpu_info():
    """Get CPU information"""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            return [l.strip() for l in f.readlines()[:10]]
    except Exception:
        return ["CPU info not available"]


def dump_run_metadata(tests, injectors, command_line, isa_string, isa_extensions,
                      cs_mode, settings):
    """Persist scan context for offline analysis."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    s_arch = getattr(settings, 'arch', ARCH_RISCV)
    metadata = {
        "command": command_line,
        "sifter_arch": s_arch,
        "isa_string": isa_string,
        "isa_extensions": sorted(isa_extensions),
        "cs_mode": cs_mode,
        "capstone_flags": describe_capstone_mode(cs_mode, s_arch),
        "jobs": settings.jobs,
        "mode": describe_mode(settings.mode),
        "filter_ext": bool('-F' in settings.args or '--filter-ext' in settings.args),
        "strict_filter": '--strict-filter' in settings.args,
        "rwx": '--rwx' in settings.args,
        "no_compressed": not settings.compressed,
        "search_unk": '--unk' in command_line.split(),
        "search_dis": '--dis' in command_line.split(),
        "started_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "runtime": tests.elapsed(),
        "workers": [
            {
                "worker_id": inj.worker_id,
                "command": inj.command,
                "last_encoding": inj.last_encoding,
                "crash_count": inj.crash_count,
            }
            for inj in injectors
        ],
    }
    if s_arch == ARCH_AARCH64:
        metadata["disas_mismatch_raw"] = tests.disas_mismatch_raw_count
        metadata["disas_mismatch_strict"] = tests.disas_mismatch_strict_count

    with open(RUN_FILE, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)

    with open(LAST_FILE, 'w') as f:
        f.write(command_line + "\n")


def dump_results(tests, injectors, command_line, isa_string, cs_mode, settings):
    """Dump final results to log file"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    s_arch = getattr(settings, 'arch', ARCH_RISCV)
    disas = Disassembler(arch=s_arch, mode=cs_mode)

    with open(LOG_FILE, 'w') as f:
        hdr = "AArch64 Sifter Results" if s_arch == ARCH_AARCH64 else "RISC-V Sifter Results"
        f.write(f"# {hdr}\n")
        f.write(f"# Command: {command_line}\n")
        f.write(f"# Architecture: {s_arch}\n")
        f.write(f"# ISA: {isa_string}\n")
        f.write(f"# Capstone Mode: {cs_mode}\n")
        f.write(f"# Capstone Flags: {describe_capstone_mode(cs_mode, s_arch)}\n")
        f.write(f"# Mode: {describe_mode(settings.mode)}\n")
        f.write(f"# Filter Ext: {bool('-F' in settings.args or '--filter-ext' in settings.args)}\n")
        f.write(f"# Strict Filter: {'--strict-filter' in settings.args}\n")
        f.write(f"# RWX: {'--rwx' in settings.args}\n")
        f.write(f"# Workers: {len(injectors)}\n")
        for inj in injectors:
            if inj.command:
                f.write(f"# Injector W{inj.worker_id}: {inj.command}\n")
        f.write(f"# Tested: {tests.insn_count}\n")
        f.write(f"# Hidden: {tests.hidden_count}\n")
        f.write(f"# Disas Bugs: {tests.disas_bug_count}\n")
        if s_arch == ARCH_AARCH64:
            f.write(f"# Disas Mismatch Raw: {tests.disas_mismatch_raw_count}\n")
            f.write(f"# Disas Mismatch Strict: {tests.disas_mismatch_strict_count}\n")
        f.write(f"# Timeouts: {tests.timeout_count}\n")
        f.write(f"# Exec Faults: {tests.exec_fault_count}\n")
        f.write(f"# Runtime: {tests.elapsed()}\n")
        f.write(f"# Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("#\n")
        f.write("# CPU:\n")
        for line in get_cpu_info():
            f.write(f"# {line}\n")
        f.write("#\n")
        f.write("# Format: type encoding signal code\n")
        f.write("# Artifacts:\n")

        for _, result in sorted(tests.artifact_dict.items()):
            mne, ops, _ = disas.disassemble(result.encoding)
            f.write(format_artifact_line(result, mne, ops))

    print(f"\nResults saved to {LOG_FILE}")


def cleanup(gui, poll, injectors, ts, tests, command_line, isa_string,
            isa_extensions, cs_mode, settings):
    """Cleanup resources"""
    ts.run = False

    if gui:
        gui.stop()
    if poll:
        poll.stop()
    for inj in injectors:
        inj.stop()

    dump_run_metadata(tests, injectors, command_line, isa_string,
                      isa_extensions, cs_mode, settings)
    dump_results(tests, injectors, command_line, isa_string, cs_mode, settings)


def main():
    """Main entry point"""
    command_line = ' '.join(sys.argv)

    parser = argparse.ArgumentParser(
        description='ISA Sifter — hidden instruction analyzer (RISC-V / AArch64)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --unk --dis --sync --tick
  %(prog)s --unk --exhaustive -j 10
  %(prog)s --unk --no-compressed -b 0x00000000 -e 0x10000000
  %(prog)s --unk -j 4 --random
  %(prog)s --arch aarch64 --unk --dis --no-gui -j 8
        """
    )

    parser.add_argument('--arch', choices=(ARCH_RISCV, ARCH_AARCH64), default=ARCH_RISCV,
                       help='Target architecture: riscv (default) or aarch64 (Linux AArch64 injector)')

    parser.add_argument('--unk', action='store_true',
                       help='Search for unknown/hidden instructions')
    parser.add_argument('--dis', action='store_true',
                       help='Search for disassembler bugs')
    parser.add_argument('--sync', action='store_true',
                       help='Write results to disk in real-time')
    parser.add_argument('--tick', action='store_true',
                       help='Show progress ticks')
    parser.add_argument('--low-mem', action='store_true',
                       help='Low memory mode (do not store all results)')
    parser.add_argument('--no-compressed', action='store_true',
                       help='Skip compressed (16-bit) instructions')
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--exhaustive', action='store_true',
                            help='Exhaustive enumeration mode (default)')
    mode_group.add_argument('--random', action='store_true',
                            help='Random sampling mode')
    mode_group.add_argument('--targeted', action='store_true',
                            help='Targeted opcode-group search mode')
    parser.add_argument('--filter-ext', action='store_true',
                       help='Filter out hidden instructions from known extensions')
    parser.add_argument('--strict-filter', action='store_true',
                       help='Strict extension filter (exact match, require ISA)')
    parser.add_argument('--ptrace', action='store_true',
                       help='Use ptrace single-step execution method')
    parser.add_argument('--rwx', action='store_true',
                       help='Allow RWX pages (legacy mode for QEMU)')
    parser.add_argument('-b', '--begin', type=str, default=None,
                       help='Start instruction (hex)')
    parser.add_argument('-e', '--end', type=str, default=None,
                       help='End instruction (hex)')
    parser.add_argument('-j', '--jobs', type=int, default=1,
                       help='Number of parallel jobs')
    parser.add_argument('--stall-timeout', type=float,
                       default=DEFAULT_WORKER_STALL_TIMEOUT_S,
                       metavar='SEC',
                       help='POSIX: if a worker emits no stdout for SEC seconds while '
                            'still running, kill and resume (0 disables). '
                            'Default: %(default)s')
    parser.add_argument('-s', '--seed', type=int, default=None,
                       help='Random seed')
    parser.add_argument('--no-gui', action='store_true',
                       help='Disable graphical interface')
    parser.add_argument('injector_args', nargs=argparse.REMAINDER,
                       help='Additional arguments for injector')

    args = parser.parse_args()

    if args.arch == ARCH_AARCH64:
        if args.random or args.targeted:
            parser.error('--arch aarch64 only supports exhaustive search (omit --random / --targeted)')
        if args.filter_ext or args.strict_filter:
            print("Note: --filter-ext / --strict-filter are ignored for AArch64 scans.")

    if not args.unk and not args.dis:
        print("Warning: no search type (--unk, --dis) specified, "
              "results may not be recorded.")

    # Build base injector arguments (no -j, no -b/-e — those are per-worker)
    base_injector_args = []
    if args.random:
        base_injector_args.append('-r')
    elif args.targeted:
        base_injector_args.append('-t')
    else:
        base_injector_args.append('-E')
    if args.tick:
        base_injector_args.append('-x')
    if args.no_compressed:
        base_injector_args.append('-C')
    if args.arch != ARCH_AARCH64:
        if args.filter_ext:
            base_injector_args.append('-F')
        if args.strict_filter:
            base_injector_args.append('--strict-filter')
    if args.ptrace:
        base_injector_args.append('-p')
    if args.rwx:
        base_injector_args.append('--rwx')

    if args.injector_args:
        extra = list(args.injector_args)
        if '--' in extra:
            extra.remove('--')
        base_injector_args.extend(extra)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ts = ThreadState()
    tests = Tests()
    isa_extensions, isa_string = detect_isa_extensions()
    cs_mode = capstone_mode_for_sifter_arch(args.arch, isa_extensions)

    base_seed = args.seed if args.seed else int(time.time())
    compressed = not args.no_compressed
    begin_val = int(args.begin, 16) if args.begin else None
    end_val = int(args.end, 16) if args.end else None

    # Determine mode letter for Settings
    if args.random:
        mode_letter = Settings.MODE_RANDOM
    elif args.targeted:
        mode_letter = Settings.MODE_TARGETED
    else:
        mode_letter = Settings.MODE_EXHAUSTIVE

    injectors = []
    num_jobs = max(1, args.jobs)

    if num_jobs > 1 and mode_letter == Settings.MODE_EXHAUSTIVE:
        shards = compute_shards(num_jobs, begin_val, end_val)
        for worker_id, (shard_start, shard_end) in enumerate(shards):
            worker_args = list(base_injector_args)
            worker_args.extend(['-b', f'{shard_start:x}'])
            worker_args.extend(['-e', f'{shard_end:x}'])
            ws = Settings(worker_args, arch=args.arch)
            ws.compressed = compressed
            ws.seed = base_seed + worker_id
            ws.jobs = 1
            inj = Injector(ws, worker_id=worker_id, cs_mode=cs_mode)
            inj.start()
            injectors.append(inj)
    else:
        single_args = list(base_injector_args)
        if begin_val is not None:
            single_args.extend(['-b', f'{begin_val:x}'])
        if end_val is not None:
            single_args.extend(['-e', f'{end_val:x}'])
        ws = Settings(single_args, arch=args.arch)
        ws.compressed = compressed
        ws.seed = base_seed
        ws.jobs = 1
        inj = Injector(ws, worker_id=0, cs_mode=cs_mode)
        inj.start()
        injectors.append(inj)

    # Master settings for GUI display
    master_settings = Settings(base_injector_args, arch=args.arch)
    master_settings.compressed = compressed
    master_settings.seed = base_seed
    master_settings.jobs = num_jobs

    stall_timeout = max(0.0, float(args.stall_timeout))
    use_filter = args.arch != ARCH_AARCH64 and (args.filter_ext or args.strict_filter)
    poll = Poll(ts, injectors, tests,
                sync=args.sync,
                low_mem=args.low_mem,
                search_unk=args.unk,
                search_dis=args.dis,
                filter_ext=use_filter,
                strict_filter=args.strict_filter and args.arch != ARCH_AARCH64,
                isa_extensions=isa_extensions,
                cs_mode=cs_mode,
                stall_timeout_s=stall_timeout,
                arch=args.arch)
    poll.start()

    gui = None
    if not args.no_gui:
        try:
            gui = Gui(ts, injectors, master_settings, tests, do_tick=args.tick,
                      cs_mode=cs_mode, isa_string=isa_string,
                      isa_extensions=isa_extensions, arch=args.arch)
            gui.start()
        except Exception as e:
            print(f"GUI initialization failed: {e}")
            print("Running in headless mode...")

    def exit_handler(signum, frame):
        cleanup(gui, poll, injectors, ts, tests, command_line,
                isa_string, isa_extensions, cs_mode, master_settings)
        sys.exit(0)

    signal.signal(signal.SIGINT, exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)

    try:
        while ts.run:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup(gui, poll, injectors, ts, tests, command_line,
                isa_string, isa_extensions, cs_mode, master_settings)


if __name__ == '__main__':
    main()
