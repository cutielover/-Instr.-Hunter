#!/bin/bash
# Analyze QEMU scan results
# Usage: ./scripts/qemu-analyze.sh [logfile]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

LOGFILE="${1:-data/qemu_scan.log}"

if [ ! -f "$LOGFILE" ]; then
    echo "Log file not found: $LOGFILE"
    echo "Run a scan first: ./scripts/qemu-scan.sh"
    exit 1
fi

echo "========================================"
echo "RISC-V Sifter - QEMU Scan Analysis"
echo "========================================"
echo ""
echo "Analyzing: $LOGFILE"
echo ""

# Count total lines
TOTAL=$(wc -l < "$LOGFILE")
echo "Total entries: $TOTAL"

# Look for anomalies (non-SIGILL results)
# In text mode, look for lines that don't contain "sigill"
INTERESTING=$(grep -v "sigill" "$LOGFILE" 2>/dev/null | grep -v "^#" | wc -l)
echo "Non-SIGILL results: $INTERESTING"

# Count by signal type
echo ""
echo "Results by signal:"
grep -o 'sig[a-z]*' "$LOGFILE" 2>/dev/null | sort | uniq -c | sort -rn || echo "  (no signal data found)"

echo ""
echo "========================================"
echo ""

if [ $INTERESTING -gt 0 ]; then
    echo "Interesting results (non-SIGILL):"
    echo ""
    grep -v "sigill" "$LOGFILE" | grep -v "^#" | head -20
    
    REMAINING=$((INTERESTING - 20))
    if [ $REMAINING -gt 0 ]; then
        echo ""
        echo "... and $REMAINING more"
    fi
else
    echo "No anomalies found (all instructions triggered SIGILL)"
    echo ""
    echo "This is expected in QEMU - it correctly rejects undefined instructions."
    echo "To find real hidden instructions, run on actual RISC-V hardware."
fi

echo ""
echo "========================================"
