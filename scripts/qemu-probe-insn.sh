#!/bin/bash
# Build and run the minimal QEMU instruction probe in the riscv-sifter image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="riscv-sifter"
WORD="${1:-0x80007fdd}"

cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "docker daemon is not running"
    exit 1
fi

if ! docker images --format "{{.Repository}}" | grep -q "^${IMAGE_NAME}$"; then
    echo "image '${IMAGE_NAME}' not found, build it first"
    exit 1
fi

docker run --rm --platform linux/riscv64 \
    -v "$PROJECT_DIR:/app" \
    -w /app \
    "$IMAGE_NAME" \
    /bin/bash -lc "gcc -O2 -Wall -Wextra -g -o /tmp/qemu_insn_probe examples/qemu_insn_probe.c && /tmp/qemu_insn_probe ${WORD}"
