#!/bin/bash
# Run RISC-V instruction scan using QEMU user mode
# Usage: ./scripts/qemu-scan.sh [options]
#
# Options:
#   -r, --random     Use random sampling mode
#   -n, --count NUM  Number of instructions to test (random mode)
#   -b, --begin HEX  Start instruction
#   -e, --end HEX    End instruction
#   -q, --quiet      Minimal output
#   -h, --help       Show help

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Default options
MODE="E"  # Exhaustive
BEGIN=""
END=""
QUIET=0
COUNT=0

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--random)
            MODE="r"
            shift
            ;;
        -n|--count)
            COUNT="$2"
            MODE="r"
            shift 2
            ;;
        -b|--begin)
            BEGIN="$2"
            shift 2
            ;;
        -e|--end)
            END="$2"
            shift 2
            ;;
        -q|--quiet)
            QUIET=1
            shift
            ;;
        -h|--help)
            echo "RISC-V Sifter QEMU Scanner"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -r, --random     Use random sampling mode"
            echo "  -n, --count NUM  Number of instructions (implies -r)"
            echo "  -b, --begin HEX  Start instruction (hex)"
            echo "  -e, --end HEX    End instruction (hex)"
            echo "  -q, --quiet      Minimal output"
            echo "  -h, --help       Show this help"
            echo ""
            echo "Examples:"
            echo "  $0 -r -n 10000              # Random scan 10000 instructions"
            echo "  $0 -b 0x00000033 -e 0x00001033  # Scan OP instructions"
            echo ""
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check for RISC-V binary
if [ ! -f "./injector" ]; then
    echo "Injector not found. Building..."
    ./scripts/qemu-build.sh
fi

if ! file ./injector | grep -q "RISC-V"; then
    echo "Injector is not a RISC-V binary. Rebuilding..."
    ./scripts/qemu-build.sh
fi

# Build command
CMD="qemu-riscv64 -L /usr/riscv64-linux-gnu ./injector"
CMD="$CMD -$MODE"
CMD="$CMD -T"  # Text output
CMD="$CMD -x"  # Show progress

if [ -n "$BEGIN" ]; then
    CMD="$CMD -b $BEGIN"
fi

if [ -n "$END" ]; then
    CMD="$CMD -e $END"
fi

# Create output directory
mkdir -p data

if [ $QUIET -eq 0 ]; then
    echo "========================================"
    echo "RISC-V Sifter - QEMU Scan"
    echo "========================================"
    echo ""
    echo "Mode: $([ "$MODE" = "r" ] && echo "Random" || echo "Exhaustive")"
    [ -n "$BEGIN" ] && echo "Start: $BEGIN"
    [ -n "$END" ] && echo "End: $END"
    echo ""
    echo "Command: $CMD"
    echo ""
    echo "Starting scan... (Ctrl+C to stop)"
    echo "========================================"
    echo ""
fi

# Run scan
if [ $COUNT -gt 0 ]; then
    # Limited count mode
    $CMD 2>&1 | head -n $COUNT | tee data/qemu_scan.log
else
    # Continuous mode
    $CMD 2>&1 | tee data/qemu_scan.log
fi

echo ""
echo "Results saved to data/qemu_scan.log"
