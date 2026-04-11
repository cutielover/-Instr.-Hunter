#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shared analysis helpers for ISA Sifter (RISC-V / AArch64).
"""

from __future__ import annotations

import json
import os
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from capstone import Cs, CS_ARCH_RISCV, CS_MODE_RISCV64, CS_MODE_RISCV_C
    try:
        from capstone import CS_ARCH_ARM64 as CS_ARCH_A64
    except ImportError:
        from capstone import CS_ARCH_AARCH64 as CS_ARCH_A64
    from capstone import (
        CS_MODE_RISCV_A,
        CS_MODE_RISCV_COREV,
        CS_MODE_RISCV_FD,
        CS_MODE_RISCV_SIFIVE,
        CS_MODE_RISCV_THEAD,
        CS_MODE_RISCV_V,
        CS_MODE_RISCV_ZBA,
        CS_MODE_RISCV_ZBB,
        CS_MODE_RISCV_ZBC,
        CS_MODE_RISCV_ZBKB,
        CS_MODE_RISCV_ZBKC,
        CS_MODE_RISCV_ZBKX,
        CS_MODE_RISCV_ZBS,
        CS_MODE_RISCV_ZCMP_ZCMT_ZCE,
        CS_MODE_RISCV_ZFINX,
        CS_MODE_RISCV_ZICFISS,
    )

    HAS_CAPSTONE = True
    CAPSTONE_V6 = True

    ISA_TO_CS_MODE = {
        "a": CS_MODE_RISCV_A,
        "f": CS_MODE_RISCV_FD,
        "d": CS_MODE_RISCV_FD,
        "c": CS_MODE_RISCV_C,
        "v": CS_MODE_RISCV_V,
        "zfinx": CS_MODE_RISCV_ZFINX,
        "zicfiss": CS_MODE_RISCV_ZICFISS,
        "zcb": CS_MODE_RISCV_ZCMP_ZCMT_ZCE,
        "zcmp": CS_MODE_RISCV_ZCMP_ZCMT_ZCE,
        "zcmt": CS_MODE_RISCV_ZCMP_ZCMT_ZCE,
        "zce": CS_MODE_RISCV_ZCMP_ZCMT_ZCE,
        "zba": CS_MODE_RISCV_ZBA,
        "zbb": CS_MODE_RISCV_ZBB,
        "zbc": CS_MODE_RISCV_ZBC,
        "zbs": CS_MODE_RISCV_ZBS,
        "zbkb": CS_MODE_RISCV_ZBKB,
        "zbkc": CS_MODE_RISCV_ZBKC,
        "zbkx": CS_MODE_RISCV_ZBKX,
    }

    ISA_PREFIX_TO_CS_MODE = {
        "xthead": CS_MODE_RISCV_THEAD,
        "xcorev": CS_MODE_RISCV_COREV,
        "xcv": CS_MODE_RISCV_COREV,
        "xsfv": CS_MODE_RISCV_SIFIVE,
    }

    RISCV_MODE_ALL = (
        CS_MODE_RISCV64
        | CS_MODE_RISCV_C
        | CS_MODE_RISCV_FD
        | CS_MODE_RISCV_V
        | CS_MODE_RISCV_A
        | CS_MODE_RISCV_ZICFISS
        | CS_MODE_RISCV_ZCMP_ZCMT_ZCE
        | CS_MODE_RISCV_ZBA
        | CS_MODE_RISCV_ZBB
        | CS_MODE_RISCV_ZBC
        | CS_MODE_RISCV_ZBKB
        | CS_MODE_RISCV_ZBKC
        | CS_MODE_RISCV_ZBKX
        | CS_MODE_RISCV_ZBS
    )

    ARM64_MODE_DEFAULT = 0

    CS_MODE_FLAG_NAMES = {
        CS_MODE_RISCV_C: "C",
        CS_MODE_RISCV_FD: "FD",
        CS_MODE_RISCV_V: "V",
        CS_MODE_RISCV_A: "A",
        CS_MODE_RISCV_ZFINX: "Zfinx",
        CS_MODE_RISCV_ZICFISS: "Zicfiss",
        CS_MODE_RISCV_ZCMP_ZCMT_ZCE: "Zc*",
        CS_MODE_RISCV_ZBA: "Zba",
        CS_MODE_RISCV_ZBB: "Zbb",
        CS_MODE_RISCV_ZBC: "Zbc",
        CS_MODE_RISCV_ZBS: "Zbs",
        CS_MODE_RISCV_ZBKB: "Zbkb",
        CS_MODE_RISCV_ZBKC: "Zbkc",
        CS_MODE_RISCV_ZBKX: "Zbkx",
        CS_MODE_RISCV_THEAD: "XThead",
        CS_MODE_RISCV_COREV: "XCoreV",
        CS_MODE_RISCV_SIFIVE: "XSiFive",
    }

except ImportError:
    try:
        from capstone import Cs, CS_ARCH_RISCV, CS_MODE_RISCV64, CS_MODE_RISCVC
        try:
            from capstone import CS_ARCH_ARM64 as CS_ARCH_A64
        except ImportError:
            from capstone import CS_ARCH_AARCH64 as CS_ARCH_A64

        HAS_CAPSTONE = True
        CAPSTONE_V6 = False
        ISA_TO_CS_MODE = {}
        ISA_PREFIX_TO_CS_MODE = {}
        RISCV_MODE_ALL = CS_MODE_RISCV64 | CS_MODE_RISCVC
        CS_MODE_FLAG_NAMES = {}
        ARM64_MODE_DEFAULT = 0
    except ImportError:
        HAS_CAPSTONE = False
        CAPSTONE_V6 = False
        ISA_TO_CS_MODE = {}
        ISA_PREFIX_TO_CS_MODE = {}
        RISCV_MODE_ALL = 0
        CS_MODE_FLAG_NAMES = {}
        CS_ARCH_A64 = 0  # type: ignore[misc, assignment]
        ARM64_MODE_DEFAULT = 0

if HAS_CAPSTONE:
    try:
        from capstone import CsError
    except ImportError:
        CsError = RuntimeError  # type: ignore[misc, assignment]
else:
    CsError = RuntimeError  # type: ignore[misc, assignment]


OPCODE_MAP = {
    0b0110111: "LUI",
    0b0010111: "AUIPC",
    0b1101111: "JAL",
    0b1100111: "JALR",
    0b1100011: "BRANCH",
    0b0000011: "LOAD",
    0b0100011: "STORE",
    0b0010011: "OP-IMM",
    0b0110011: "OP",
    0b0001111: "MISC-MEM",
    0b1110011: "SYSTEM",
    0b0011011: "OP-IMM-32",
    0b0111011: "OP-32",
    0b0000111: "LOAD-FP",
    0b0100111: "STORE-FP",
    0b1000011: "MADD",
    0b1000111: "MSUB",
    0b1001011: "NMSUB",
    0b1001111: "NMADD",
    0b1010011: "OP-FP",
    0b1010111: "OP-V",
    0b0101111: "AMO",
}

C_QUADRANT_MAP = {
    0b00: "C0 (Load/Store)",
    0b01: "C1 (Arith/Jump)",
    0b10: "C2 (Stack/Jump)",
}

ARTIFACT_TYPES = ("H", "D", "X", "T")
ARCH_RISCV = "riscv"
ARCH_AARCH64 = "aarch64"


def detect_isa_extensions_from_string(isa_string: str) -> Set[str]:
    """Parse an ISA string into normalized extension tokens."""
    exts: Set[str] = set()
    isa = (isa_string or "").strip().lower()
    parts = [p for p in isa.split("_") if p]
    if not parts:
        return exts

    base = parts[0]
    if base.startswith(("rv32", "rv64", "rv128")):
        for ch in base[4:]:
            if ch.isalpha():
                exts.add(ch)

    for token in parts[1:]:
        exts.add(token)
    return exts


def build_capstone_mode(isa_exts: Iterable[str]) -> int:
    """Build Capstone cs_mode bitmask from detected ISA extension tokens."""
    if not HAS_CAPSTONE or not CAPSTONE_V6:
        return RISCV_MODE_ALL

    mode = CS_MODE_RISCV64
    for token in isa_exts:
        if token in ISA_TO_CS_MODE:
            mode |= ISA_TO_CS_MODE[token]
            continue
        for prefix, flag in ISA_PREFIX_TO_CS_MODE.items():
            if token.startswith(prefix):
                mode |= flag
                break
    return mode


def capstone_mode_for_sifter_arch(arch: str, isa_exts: Iterable[str]) -> int:
    """Capstone cs_mode for the given sifter CPU architecture."""
    if arch == "aarch64":
        return ARM64_MODE_DEFAULT
    return build_capstone_mode(isa_exts)


def describe_capstone_mode(mode: int, sifter_arch: str = "riscv") -> str:
    """Return short string listing enabled Capstone extension flags."""
    if sifter_arch == "aarch64":
        return "AArch64 default" if mode == 0 else f"AArch64 cs_mode=0x{mode:x}"
    if not mode:
        return "(none)"
    names = [name for flag, name in CS_MODE_FLAG_NAMES.items() if mode & flag]
    return " ".join(names) if names else "RV64 base only"


def is_compressed(encoding: int) -> bool:
    return (encoding & 0x3) != 0x3


def get_opcode(encoding: int) -> int:
    return encoding & 0x7F


def get_funct3(encoding: int) -> int:
    return (encoding >> 12) & 0x7


def get_funct7(encoding: int) -> int:
    return (encoding >> 25) & 0x7F


def compute_mask(encodings: Sequence[int]) -> Tuple[int, int]:
    if not encodings:
        return (0, 0xFFFFFFFF)

    all_bits = 0xFFFFFFFF
    any_bits = 0
    for enc in encodings:
        all_bits &= enc
        any_bits |= enc
    varying = all_bits ^ any_bits
    constant = ~varying & 0xFFFFFFFF
    return (all_bits & constant, varying)


def categorize_instruction(encoding: int, arch: str = ARCH_RISCV) -> str:
    if arch == ARCH_AARCH64:
        top6 = (encoding >> 26) & 0x3F
        op0 = (encoding >> 29) & 0x7
        op1 = (encoding >> 25) & 0xF
        return f"A64 op0={op0} op1=0x{op1:x} top6=0x{top6:02x}"
    if is_compressed(encoding):
        quadrant = encoding & 0x3
        funct3 = (encoding >> 13) & 0x7
        return f"Compressed {C_QUADRANT_MAP.get(quadrant, f'Q{quadrant}')}.{funct3}"
    opcode = get_opcode(encoding)
    return f"{OPCODE_MAP.get(opcode, f'Unknown(0x{opcode:02x})')}.{get_funct3(encoding)}"


def identify_probable_extension(encoding: int, arch: str = ARCH_RISCV) -> Optional[str]:
    """Best-effort extension guess reused by offline analysis."""
    if arch == ARCH_AARCH64:
        return "A64"
    if is_compressed(encoding):
        c = encoding & 0xFFFF
        q = c & 0x3
        f3 = (c >> 13) & 0x7
        bit12 = (c >> 12) & 0x1
        if q == 0x0 and f3 == 0x4:
            return "Zcb"
        if q == 0x1 and f3 in (0x4, 0x5, 0x6, 0x7):
            if bit12 == 1:
                return "Zcmp"
            return "Zc*"
        if q == 0x2 and f3 in (0x4, 0x5):
            return "Zcmp"
        return None

    opc = encoding & 0x7F
    f3 = (encoding >> 12) & 0x7
    f7 = (encoding >> 25) & 0x7F
    fmt = (encoding >> 25) & 0x3
    f5 = f7 >> 2

    if opc in (0x07, 0x27) and f3 == 1:
        return "Zfh"
    if opc in (0x07, 0x27) and f3 in (0, 7):
        return "V"
    if opc in (0x43, 0x47, 0x4B, 0x4F) and fmt == 2:
        return "Zfh"
    if opc == 0x53:
        if fmt == 2:
            return "Zfh"
        if (f7 & 0x7C) == 0x78 or f7 in (0x14, 0x15, 0x20, 0x21):
            return "Zfa"
    if opc == 0x57:
        return "V"
    if opc == 0x33:
        if f7 == 0x05:
            return "Zba"
        if f7 == 0x04 and f3 >= 4:
            return "Zbb"
        if f7 == 0x20 and f3 in (1, 4, 5, 6, 7):
            return "Zbb"
        if f7 == 0x30:
            return "Zbb"
        if f7 in (0x14, 0x24, 0x34):
            return "Zbs"
        if f7 == 0x08:
            return "Zbc"
        if f7 in (0x48, 0x18, 0x10):
            return "Zbkb"
        if f7 in (0x19, 0x1A, 0x1F, 0x7A):
            return "Zb*"
    if opc == 0x3B:
        if f7 == 0x30:
            return "Zbb"
        if f7 in (0x04, 0x05):
            return "Zba"
        if f7 == 0x10 and f3 in (2, 4, 6):
            return "Zb*"
    if opc == 0x13:
        if f7 == 0x30 and f3 in (1, 5):
            return "Zbb"
        if (f7 & 0x3E) == 0x24 or (f7 == 0x34 and f3 == 1):
            return "Zbs"
        if f7 == 0x31 and f3 == 5:
            return "Zbb"
    if opc == 0x1B:
        if f7 == 0x30:
            return "Zbb"
        if f7 == 0x04 and f3 == 0:
            return "Zba"
        if f7 == 0x05 and f3 == 1:
            return "Zba"
    if opc == 0x2F:
        if f3 in (0, 1):
            return "Zabha"
        if f5 == 5:
            return "Zacas"
    if opc == 0x73 and f3 != 0 and f7 >= 0x40:
        return "Zimop"
    if opc == 0x0F:
        if f3 == 2:
            return "Zicbom"
        if f3 == 6:
            return "Zicbop"
        if f3 in (0, 1):
            return "Zihintntl"
    return None


def extension_enabled(ext_name: Optional[str], isa_extensions: Set[str], strict: bool = False) -> bool:
    ext = (ext_name or "").lower()
    if not ext:
        return False
    if not isa_extensions:
        return not strict
    if strict:
        return ext in isa_extensions
    if ext == "v":
        return ("v" in isa_extensions) or any(x.startswith("zv") for x in isa_extensions)
    if ext == "zb*":
        return any(x.startswith("zb") for x in isa_extensions)
    if ext == "zk":
        return any(x.startswith("zk") for x in isa_extensions)
    return ext in isa_extensions


class RiscvDisassembler:
    """RISC-V disassembler wrapper that mirrors the scan-time mode choice."""

    def __init__(self, mode: Optional[int] = None):
        self.md = None
        requested = mode if mode is not None else RISCV_MODE_ALL
        self.mode = requested
        if not HAS_CAPSTONE:
            return
        candidates: List[int] = [requested, RISCV_MODE_ALL, CS_MODE_RISCV64]
        seen: Set[int] = set()
        ordered = [m for m in candidates if not (m in seen or seen.add(m))]
        last_err: Optional[BaseException] = None
        for m in ordered:
            try:
                self.md = Cs(CS_ARCH_RISCV, m)
                if m != requested:
                    warnings.warn(
                        "Capstone rejected cs_mode=0x%x (%s); using 0x%x for disassembly."
                        % (requested, last_err or "error", m),
                        UserWarning,
                        stacklevel=2,
                    )
                self.mode = m
                return
            except (CsError, Exception) as exc:
                last_err = exc
                continue
        self.md = None

    def disassemble(self, encoding: int, size: int = 4) -> Tuple[str, str]:
        if not self.md:
            return ("(no disas)", "")
        if is_compressed(encoding):
            size = 2
        try:
            insn_bytes = encoding.to_bytes(size, byteorder="little")
            for insn in self.md.disasm(insn_bytes, 0):
                return (insn.mnemonic, insn.op_str)
        except Exception:
            pass
        return ("(unk)", "")


class SifterDisassembler:
    """Architecture-aware disassembler wrapper used by offline reports."""

    def __init__(self, arch: str = ARCH_RISCV, mode: Optional[int] = None):
        self.arch = arch
        self.md = None
        self.mode = mode if mode is not None else (
            ARM64_MODE_DEFAULT if arch == ARCH_AARCH64 else RISCV_MODE_ALL
        )
        if not HAS_CAPSTONE:
            return
        try:
            if arch == ARCH_AARCH64:
                self.md = Cs(CS_ARCH_A64, self.mode)
            else:
                rv = RiscvDisassembler(mode=self.mode)
                self.md = rv.md
                self.mode = rv.mode
        except (CsError, Exception):
            self.md = None

    def disassemble(self, encoding: int, size: int = 4) -> Tuple[str, str]:
        if not self.md:
            return ("(no disas)", "")
        if self.arch == ARCH_AARCH64:
            size = 4
        elif is_compressed(encoding):
            size = 2
        try:
            insn_bytes = encoding.to_bytes(size, byteorder="little")
            for insn in self.md.disasm(insn_bytes, 0):
                return (insn.mnemonic, insn.op_str)
        except Exception:
            pass
        return ("(unk)", "")


@dataclass
class ArtifactRecord:
    encoding: int
    signal: int
    code: int
    artifact_type: str
    sifter_arch: str = ARCH_RISCV
    source_line: str = ""
    is_compressed: bool = False
    opcode_group: str = ""
    probable_extension: str = "unknown"
    decode_status: str = ""
    execution_status: str = ""
    interpretation: str = ""
    mnemonic: str = ""
    operands: str = ""
    family_key: str = ""
    mask_value: int = 0
    mask_varying: int = 0

    def __post_init__(self) -> None:
        self.is_compressed = (self.sifter_arch == ARCH_RISCV) and is_compressed(self.encoding)
        self.opcode_group = categorize_instruction(self.encoding, self.sifter_arch)
        self.probable_extension = identify_probable_extension(self.encoding, self.sifter_arch) or "unknown"
        if not self.decode_status:
            self.decode_status = decode_status_for_artifact(self.artifact_type)
        if not self.execution_status:
            self.execution_status = execution_status_for_artifact(self.artifact_type, self.signal)
        if not self.interpretation:
            self.interpretation = interpretation_for_artifact(self.artifact_type)


@dataclass
class AnalysisContext:
    logfile: str
    metadata_file: Optional[str] = None
    last_file: Optional[str] = None
    sifter_arch: str = ARCH_RISCV
    isa_string: str = "(unknown)"
    isa_extensions: Set[str] = field(default_factory=set)
    cs_mode: Optional[int] = None
    strict_filter: bool = False
    filter_ext: bool = False
    mode_name: str = "unknown"
    jobs: Optional[int] = None
    command: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def capstone_mode_description(self) -> str:
        return describe_capstone_mode(self.cs_mode or 0, self.sifter_arch)


@dataclass
class ArtifactFamily:
    key: str
    artifacts: List[ArtifactRecord]
    mask_value: int
    mask_varying: int
    opcode_group: str
    probable_extension: str
    interpretation: str


def decode_status_for_artifact(artifact_type: str) -> str:
    mapping = {
        "H": "Capstone unknown or ISA-illegal",
        "D": "Capstone legal",
        "X": "Capstone unknown or ISA-illegal",
        "T": "decode status unavailable from text log",
    }
    return mapping.get(artifact_type, "unknown")


def execution_status_for_artifact(artifact_type: str, signal_num: int) -> str:
    if artifact_type == "H":
        return "CPU accepted and reached sentinel"
    if artifact_type == "D":
        return f"CPU rejected at test PC with signal {signal_num}"
    if artifact_type == "X":
        return f"CPU accepted, then faulted with signal {signal_num}"
    if artifact_type == "T":
        return "instruction timed out"
    return f"signal {signal_num}"


def interpretation_for_artifact(artifact_type: str) -> str:
    mapping = {
        "H": "Capstone unknown, CPU accepted",
        "D": "Capstone legal, CPU rejected at test PC",
        "X": "CPU accepted, then faulted in memcage",
        "T": "instruction timed out",
    }
    return mapping.get(artifact_type, "unclassified artifact")


def parse_log_file(filepath: str, sifter_arch: str = ARCH_RISCV) -> List[ArtifactRecord]:
    records: List[ArtifactRecord] = []
    with open(filepath, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                artifact_type = parts[0] if parts[0] in ARTIFACT_TYPES else ""
                if not artifact_type:
                    continue
                encoding = int(parts[1], 16) if parts[1].startswith("0x") else int(parts[1], 0)
                signal_num = int(parts[2], 0)
                code = int(parts[3], 0) if len(parts) > 3 else 0
            except ValueError:
                continue
            records.append(
                ArtifactRecord(
                    encoding=encoding,
                    signal=signal_num,
                    code=code,
                    artifact_type=artifact_type,
                    sifter_arch=sifter_arch,
                    source_line=line,
                )
            )
    return records


def derive_default_paths(logfile: str) -> Tuple[Optional[str], Optional[str]]:
    data_dir = os.path.dirname(os.path.abspath(logfile))
    metadata_file = os.path.join(data_dir, "run.json")
    last_file = os.path.join(data_dir, "last")
    return (
        metadata_file if os.path.exists(metadata_file) else None,
        last_file if os.path.exists(last_file) else None,
    )


def parse_log_headers(logfile: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    with open(logfile, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line.startswith("#"):
                break
            if ":" not in line:
                continue
            key, value = line[1:].split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return headers


def _load_json_if_exists(path: Optional[str]) -> Dict[str, object]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def build_analysis_context(
    logfile: str,
    isa: Optional[str] = None,
    cs_mode: Optional[int] = None,
    strict_filter: bool = False,
    filter_ext: bool = False,
    metadata_file: Optional[str] = None,
    last_file: Optional[str] = None,
) -> AnalysisContext:
    auto_metadata, auto_last = derive_default_paths(logfile)
    metadata_path = metadata_file or auto_metadata
    last_path = last_file or auto_last
    metadata = _load_json_if_exists(metadata_path)
    headers = parse_log_headers(logfile)

    isa_string = (
        isa
        or str(metadata.get("isa_string", "")).strip()
        or headers.get("isa", "")
        or "(unknown)"
    )
    isa_extensions = detect_isa_extensions_from_string(isa_string)

    mode_value = cs_mode
    # run.json may store a Capstone 6 bitmask; Capstone 5 rejects unknown bits.
    if mode_value is None and CAPSTONE_V6 and "cs_mode" in metadata:
        try:
            mode_value = int(metadata["cs_mode"])
        except (TypeError, ValueError):
            mode_value = None
    if mode_value is None:
        mode_value = build_capstone_mode(isa_extensions)

    s_arch = str(
        metadata.get(
            "sifter_arch",
            headers.get("architecture", ARCH_RISCV),
        )
    ).lower()
    if s_arch not in (ARCH_RISCV, ARCH_AARCH64):
        s_arch = ARCH_RISCV

    context = AnalysisContext(
        logfile=logfile,
        metadata_file=metadata_path,
        last_file=last_path,
        sifter_arch=s_arch,
        isa_string=isa_string,
        isa_extensions=isa_extensions,
        cs_mode=mode_value,
        strict_filter=bool(metadata.get("strict_filter", strict_filter)),
        filter_ext=bool(metadata.get("filter_ext", filter_ext)),
        mode_name=str(metadata.get("mode", headers.get("mode", "unknown"))),
        jobs=_safe_int(metadata.get("jobs", headers.get("workers"))),
        command=str(metadata.get("command", headers.get("command", ""))),
        metadata=metadata,
    )
    return context


def _safe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def annotate_records(
    records: Sequence[ArtifactRecord],
    disassembler: Optional[RiscvDisassembler] = None,
) -> List[ArtifactRecord]:
    annotated = list(records)
    if disassembler is None:
        return annotated
    for record in annotated:
        record.mnemonic, record.operands = disassembler.disassemble(record.encoding)
    return annotated


def build_family_key(record: ArtifactRecord) -> str:
    if record.sifter_arch == ARCH_AARCH64:
        top6 = (record.encoding >> 26) & 0x3F
        op0 = (record.encoding >> 29) & 0x7
        return f"{record.artifact_type}:a64:op0={op0}:top6=0x{top6:02x}"
    if record.is_compressed:
        quadrant = record.encoding & 0x3
        funct3 = (record.encoding >> 13) & 0x7
        return f"{record.artifact_type}:C{quadrant}:f3={funct3}:{record.probable_extension}"
    return (
        f"{record.artifact_type}:opc=0x{get_opcode(record.encoding):02x}:"
        f"f3={get_funct3(record.encoding)}:{record.probable_extension}"
    )


def group_records_by_family(records: Sequence[ArtifactRecord]) -> List[ArtifactFamily]:
    grouped: Dict[str, List[ArtifactRecord]] = defaultdict(list)
    for record in records:
        key = build_family_key(record)
        record.family_key = key
        grouped[key].append(record)

    families: List[ArtifactFamily] = []
    for key, artifacts in grouped.items():
        value, varying = compute_mask([item.encoding for item in artifacts])
        for item in artifacts:
            item.mask_value = value
            item.mask_varying = varying
        extension_counts = Counter(item.probable_extension for item in artifacts)
        interp_counts = Counter(item.interpretation for item in artifacts)
        families.append(
            ArtifactFamily(
                key=key,
                artifacts=sorted(artifacts, key=lambda x: x.encoding),
                mask_value=value,
                mask_varying=varying,
                opcode_group=artifacts[0].opcode_group,
                probable_extension=extension_counts.most_common(1)[0][0],
                interpretation=interp_counts.most_common(1)[0][0],
            )
        )
    families.sort(key=lambda fam: (-len(fam.artifacts), fam.key))
    return families


def summarize_counts(records: Sequence[ArtifactRecord]) -> Dict[str, int]:
    counts = {kind: 0 for kind in ARTIFACT_TYPES}
    for record in records:
        counts[record.artifact_type] = counts.get(record.artifact_type, 0) + 1
    counts["total"] = len(records)
    return counts

