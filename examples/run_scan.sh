#!/bin/bash
# Example scan script for RISC-V Sifter

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "================================"
echo "RISC-V Sifter Example Scan"
echo "================================"
echo ""

# Check if injector exists
if [ ! -f "./injector" ]; then
    echo "Injector not found. Building..."
    make
fi

# Check if running as root (recommended)
if [ "$EUID" -ne 0 ]; then
    echo "Warning: Not running as root. Some features may be limited."
    echo ""
fi

# Example 1: Quick random scan (10 seconds)
echo "Example 1: Quick random scan..."
echo "Command: ./sifter.py --unk --dis --random --no-gui &"
echo ""

# Example 2: Scan specific opcode range
echo "Example 2: Scan specific opcode (0x33 = OP instructions)"
echo "Command: ./sifter.py --unk --dis -b 0x00000033 -e 0x80000033 --no-gui"
echo ""

# Example 3: Full exhaustive scan (takes a long time!)
echo "Example 3: Full exhaustive scan"
echo "Command: sudo ./sifter.py --unk --dis --sync --tick"
echo ""

# Example 4: Multi-threaded scan
echo "Example 4: Multi-threaded scan with 4 workers"
echo "Command: sudo ./sifter.py --unk --dis --sync -j 4"
echo ""

# Example 5: Analyze results
echo "Example 5: Analyze scan results"
echo "Command: ./summarize.py data/log"
echo ""

echo "================================"
echo "To run a scan, use one of the commands above."
echo "Remember: This tool needs to run on actual RISC-V hardware"
echo "to detect real hidden instructions!"
echo "================================"
