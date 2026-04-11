#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Filter and inspect RISC-V Sifter artifacts using scan-aware semantics."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.common import (
    build_analysis_context,
    build_family_key,
    compute_mask,
    get_opcode,
    group_records_by_family,
    is_compressed,
    parse_log_file,
)


def find_ranges(encodings):
    if not encodings:
        return []
    sorted_enc = sorted(encodings)
    ranges = []
    start = prev = sorted_enc[0]
    for enc in sorted_enc[1:]:
        if enc != prev + 1:
            ranges.append((start, prev))
            start = enc
        prev = enc
    ranges.append((start, prev))
    return ranges


def apply_filters(records, args):
    filtered = list(records)
    if args.compressed:
        filtered = [r for r in filtered if r.is_compressed]
    if args.standard:
        filtered = [r for r in filtered if not r.is_compressed]
    if args.opcode is not None:
        opcode = int(args.opcode, 16)
        filtered = [r for r in filtered if not r.is_compressed and get_opcode(r.encoding) == opcode]
    if args.artifact:
        wanted = {item.strip().upper() for item in args.artifact.split(",") if item.strip()}
        filtered = [r for r in filtered if r.artifact_type in wanted]
    if args.extension:
        filtered = [r for r in filtered if r.probable_extension.lower() == args.extension.lower()]
    if args.signal is not None:
        filtered = [r for r in filtered if r.signal == args.signal]
    if args.mode_wide_d:
        filtered = [
            r for r in filtered
            if r.artifact_type == "D" and r.probable_extension in ("FD", "V", "Zfh", "Zfa")
        ]
    return filtered


def print_groups(records):
    groups = {}
    for family in group_records_by_family(records):
        groups[family.key] = family.artifacts
    for key, artifacts in sorted(groups.items(), key=lambda item: -len(item[1])):
        print(f"\n{key}: {len(artifacts)} instructions")
        for record in artifacts[:5]:
            print(
                f"  [{record.artifact_type}] 0x{record.encoding:08x} "
                f"sig={record.signal}/{record.code} ext={record.probable_extension}"
            )
        if len(artifacts) > 5:
            print(f"  ... and {len(artifacts) - 5} more")


def main():
    parser = argparse.ArgumentParser(description="Filter RISC-V sifter results")
    parser.add_argument("logfile", help="Log file to process")
    parser.add_argument("-o", "--opcode", type=str, help="Filter by opcode (hex)")
    parser.add_argument("-c", "--compressed", action="store_true",
                        help="Show only compressed instructions")
    parser.add_argument("-s", "--standard", action="store_true",
                        help="Show only standard (32-bit) instructions")
    parser.add_argument("-g", "--group", action="store_true",
                        help="Group by artifact family")
    parser.add_argument("-r", "--ranges", action="store_true",
                        help="Show contiguous ranges")
    parser.add_argument("-m", "--mask", action="store_true",
                        help="Compute bit mask")
    parser.add_argument("-a", "--artifact", type=str, default=None,
                        help="Filter by artifact type, e.g. H,D,X,T")
    parser.add_argument("-e", "--extension", type=str, default=None,
                        help="Filter by probable extension label")
    parser.add_argument("--signal", type=int, default=None,
                        help="Filter by signal number")
    parser.add_argument("--family", type=str, default=None,
                        help="Filter by family key substring")
    parser.add_argument("--mode-wide-d", action="store_true",
                        help="Only show D artifacts likely caused by broad decode modes")
    args = parser.parse_args()

    context = build_analysis_context(args.logfile)
    records = parse_log_file(args.logfile)
    for record in records:
        record.family_key = build_family_key(record)
    records = apply_filters(records, args)
    if args.family:
        records = [r for r in records if args.family in r.family_key or args.family in r.opcode_group]

    print(f"Loaded {len(records)} artifacts")
    print(f"ISA: {context.isa_string}")
    print(f"Capstone flags: {context.capstone_mode_description}")

    encodings = [record.encoding for record in records]
    if args.group:
        print_groups(records)
    elif args.ranges:
        ranges = find_ranges(encodings)
        print(f"\n{len(ranges)} contiguous ranges:")
        for start, end in ranges:
            count = end - start + 1
            print(f"  0x{start:08x} - 0x{end:08x} ({count} instructions)")
    elif args.mask:
        const, varying = compute_mask(encodings)
        print(f"\nConstant bits: 0x{const:08x}")
        print(f"Varying bits:  0x{varying:08x}")
        print(f"Pattern:       0x{const:08x} & 0x{~varying & 0xFFFFFFFF:08x}")
    else:
        for record in sorted(records, key=lambda item: (item.artifact_type, item.encoding)):
            print(
                f"[{record.artifact_type}] 0x{record.encoding:08x} "
                f"sig={record.signal}/{record.code} ext={record.probable_extension} "
                f"{record.opcode_group}"
            )


if __name__ == "__main__":
    main()
