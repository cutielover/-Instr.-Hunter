#!/bin/bash
# Run the instruction probe one mode at a time so hangs are isolated.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="riscv-sifter"
WORD="${1:-0x80019f82}"

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

run_mode() {
    local label="$1"
    local arg="$2"
    echo "=== ${label} ==="
    timeout 10 docker run --rm --platform linux/riscv64 \
        -v "$PROJECT_DIR:/app" \
        -w /app \
        "$IMAGE_NAME" \
        /bin/bash -lc "gcc -O2 -Wall -Wextra -g -o /tmp/qemu_insn_probe examples/qemu_insn_probe.c && /tmp/qemu_insn_probe ${WORD} ${arg}"
    local rc=$?
    echo "exit=${rc}"
    echo ""
}

run_mode "plain" "--plain-only" || true
run_mode "sp-only" "--sp-only-only" || true
run_mode "gprs-no-sp" "--gprs-no-sp-only" || true
run_mode "full-sandbox" "--sandbox-only" || true
