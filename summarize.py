#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Summarize ISA Sifter scan results with scan-aware semantics."""

import argparse
import os
import sys
from typing import Iterable, List, Optional, Sequence

from analysis.common import (
    ARCH_AARCH64,
    ARCH_RISCV,
    HAS_CAPSTONE,
    annotate_records,
    build_analysis_context,
    group_records_by_family,
    parse_log_file,
    SifterDisassembler,
    summarize_counts,
)


def _filter_by_artifact(records, artifact_filter: Optional[str]):
    if not artifact_filter:
        return list(records)
    wanted = {item.strip().upper() for item in artifact_filter.split(",") if item.strip()}
    return [record for record in records if record.artifact_type in wanted]


def print_overview(records, families, context) -> None:
    counts = summarize_counts(records)
    title = "AARCH64" if context.sifter_arch == ARCH_AARCH64 else "RISC-V"
    print("\n" + "=" * 72)
    print(f"{title} SIFTER RESULTS SUMMARY")
    print("=" * 72)
    print(f"\nLog file:             {context.logfile}")
    print(f"Entries:              {counts['total']}")
    print(f"Artifacts:            H={counts['H']} D={counts['D']} X={counts['X']} T={counts['T']}")
    print(f"Families:             {len(families)}")
    print(f"ISA:                  {context.isa_string}")
    print(f"Capstone flags:       {context.capstone_mode_description}")
    print(f"Mode:                 {context.mode_name}")
    print(f"Jobs:                 {context.jobs if context.jobs is not None else '(unknown)'}")
    if context.command:
        print(f"Command:              {context.command}")

    print("\nTop families:")
    for family in families[:10]:
        print(
            f"  {len(family.artifacts):4d}  {family.key}  "
            f"mask=0x{family.mask_value:08x}/~0x{family.mask_varying:08x}  "
            f"ext={family.probable_extension}"
        )


def print_family_view(families, family_limit: int, show_examples: int) -> None:
    print("\n" + "=" * 72)
    print("FAMILY VIEW")
    print("=" * 72)
    for family in families[:family_limit]:
        print(f"\n[{family.key}]")
        print(f"  Count:          {len(family.artifacts)}")
        print(f"  Opcode Group:   {family.opcode_group}")
        print(f"  Extension:      {family.probable_extension}")
        print(f"  Interpretation: {family.interpretation}")
        print(f"  Mask Value:     0x{family.mask_value:08x}")
        print(f"  Varying Bits:   0x{family.mask_varying:08x}")
        print("  Examples:")
        for record in family.artifacts[:show_examples]:
            asm = f"{record.mnemonic} {record.operands}".strip()
            print(
                f"    [{record.artifact_type}] 0x{record.encoding:08x} "
                f"sig={record.signal}/{record.code} ext={record.probable_extension} {asm}"
            )


def print_examples(records, limit: int) -> None:
    print("\n" + "=" * 72)
    print("EXAMPLES")
    print("=" * 72)
    for record in sorted(records, key=lambda item: (item.artifact_type, item.encoding))[:limit]:
        asm = f"{record.mnemonic} {record.operands}".strip()
        print(
            f"[{record.artifact_type}] 0x{record.encoding:08x}  "
            f"{record.opcode_group:24s}  ext={record.probable_extension:8s}  "
            f"sig={record.signal}/{record.code}  {asm}"
        )
        print(f"  {record.interpretation}")


def export_csv(records, filepath: str) -> None:
    with open(filepath, "w") as f:
        f.write(
            "Encoding,ArtifactType,OpcodeGroup,Signal,Code,Extension,"
            "DecodeStatus,ExecutionStatus,Interpretation,Mnemonic,Operands,FamilyKey\n"
        )
        for record in sorted(records, key=lambda item: (item.artifact_type, item.encoding)):
            f.write(
                f"0x{record.encoding:08x},{record.artifact_type},{record.opcode_group},"
                f"{record.signal},{record.code},{record.probable_extension},"
                f"{record.decode_status},{record.execution_status},{record.interpretation},"
                f"{record.mnemonic},\"{record.operands}\",{record.family_key}\n"
            )
    print(f"Exported to {filepath}")


def emit_report(args) -> int:
    if not os.path.exists(args.logfile):
        print(f"Error: Log file not found: {args.logfile}")
        return 1

    context = build_analysis_context(
        args.logfile,
        isa=args.isa,
        cs_mode=args.cs_mode,
        strict_filter=args.strict_filter,
        filter_ext=args.filter_ext,
        metadata_file=args.metadata,
    )
    records = parse_log_file(args.logfile, sifter_arch=context.sifter_arch)
    records = _filter_by_artifact(records, args.artifact)
    if not records:
        print("No instructions found in log file.")
        return 0

    disas = SifterDisassembler(arch=context.sifter_arch, mode=context.cs_mode)
    annotate_records(records, disas if HAS_CAPSTONE else None)
    families = group_records_by_family(records)

    if not HAS_CAPSTONE:
        print("Warning: Capstone not available, disassembly disabled")

    if args.view in ("overview", "all"):
        print_overview(records, families, context)
    if args.view in ("families", "all"):
        print_family_view(families, args.family_limit, args.examples)
    if args.view in ("examples", "all", "raw"):
        print_examples(records, args.examples if args.view != "raw" else len(records))

    if args.csv:
        export_csv(records, args.csv)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Summarize ISA Sifter scan results (RISC-V / AArch64)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("logfile", nargs="?", default="data/log",
                        help="Log file to analyze (default: data/log)")
    parser.add_argument("--view", choices=("overview", "families", "examples", "raw", "all"),
                        default="all", help="Which report view to print")
    parser.add_argument("--artifact", default=None,
                        help="Filter artifact types, e.g. H,D,X,T")
    parser.add_argument("--family-limit", type=int, default=20,
                        help="Maximum number of families to show")
    parser.add_argument("--examples", type=int, default=5,
                        help="Examples per view/family")
    parser.add_argument("--isa", type=str, default=None,
                        help="Override ISA string used for Capstone mode selection")
    parser.add_argument("--cs-mode", type=int, default=None,
                        help="Override Capstone cs_mode bitmask")
    parser.add_argument("--metadata", type=str, default=None,
                        help="Metadata file (default: infer data/run.json)")
    parser.add_argument("--strict-filter", action="store_true",
                        help="Mark analysis context as strict-filter")
    parser.add_argument("--filter-ext", action="store_true",
                        help="Mark analysis context as filter-ext")
    parser.add_argument("-d", "--detailed", action="store_true",
                        help="Alias for --view raw")
    parser.add_argument("-c", "--csv", type=str, default=None,
                        help="Export results to CSV file")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output file for report")
    args = parser.parse_args()

    if args.detailed and args.view == "all":
        args.view = "raw"

    output_file = None
    old_stdout = None
    if args.output:
        output_file = open(args.output, "w")
        old_stdout = sys.stdout
        sys.stdout = output_file

    try:
        rc = emit_report(args)
    finally:
        if output_file:
            sys.stdout = old_stdout
            output_file.close()
            print(f"Report saved to {args.output}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
