#!/bin/bash
# Cross-compile riscv-sifter for RISC-V using QEMU user mode
# Usage: ./scripts/qemu-build.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "========================================"
echo "RISC-V Sifter - Cross Compilation Setup"
echo "========================================"
echo ""

# Check for required tools
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo "❌ $1 not found"
        return 1
    else
        echo "✅ $1 found"
        return 0
    fi
}

echo "Checking required tools..."
MISSING=0

check_tool "qemu-riscv64" || MISSING=1
check_tool "riscv64-linux-gnu-gcc" || MISSING=1

if [ $MISSING -eq 1 ]; then
    echo ""
    echo "Installing missing dependencies..."
    echo ""
    
    if [ -f /etc/debian_version ]; then
        sudo apt-get update
        sudo apt-get install -y \
            qemu-user \
            qemu-user-static \
            gcc-riscv64-linux-gnu \
            g++-riscv64-linux-gnu \
            libc6-riscv64-cross
    elif [ -f /etc/fedora-release ]; then
        sudo dnf install -y \
            qemu-user \
            qemu-user-static \
            gcc-riscv64-linux-gnu
    else
        echo "Please install QEMU and RISC-V cross compiler manually."
        exit 1
    fi
fi

echo ""
echo "Cross-compiling for RISC-V..."
echo ""

# Clean and build
make CROSS_COMPILE=riscv64-linux-gnu- USE_CAPSTONE=0 clean

# Build without capstone (simpler cross-compile)
make CROSS_COMPILE=riscv64-linux-gnu- USE_CAPSTONE=0

# Verify the binary
echo ""
echo "Verifying binary..."
file ./injector

if file ./injector | grep -q "RISC-V"; then
    echo ""
    echo "✅ Successfully built RISC-V binary!"
    echo ""
    echo "To run in QEMU:"
    echo "  qemu-riscv64 -L /usr/riscv64-linux-gnu ./injector -E -T -x"
    echo ""
else
    echo ""
    echo "❌ Build failed - binary is not RISC-V"
    exit 1
fi
