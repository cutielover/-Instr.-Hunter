#!/bin/bash
# Test hidden instruction detection using custom QEMU
#
# The custom QEMU has a hidden instruction at encoding 0x0000006B
# that executes as NOP. Standard QEMU should reject this encoding.
# Sifter should detect it as a "hidden instruction" with the custom QEMU.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
QEMU_CUSTOM="/Users/Orpheus/qemu-build/output/qemu-riscv64-custom"
ENCODING="6B"
DOCKER_PROXY_HOST="${DOCKER_PROXY_HOST:-host.docker.internal}"
DOCKER_PROXY_PORT="${DOCKER_PROXY_PORT:-7890}"
USE_CAPSTONE="${USE_CAPSTONE:-1}"
CAPSTONE_VERSION="${CAPSTONE_VERSION:-5.0.7}"

if [ ! -f "$QEMU_CUSTOM" ]; then
    echo "ERROR: Custom QEMU not found at $QEMU_CUSTOM"
    echo "Build it first with: cd /Users/Orpheus/qemu-build && bash build-custom-qemu.sh"
    exit 1
fi

echo "========================================"
echo "  Hidden Instruction Detection Test"
echo "========================================"
echo ""
echo "Custom QEMU: $QEMU_CUSTOM"
echo "Target encoding: 0x0000006B"
echo "Docker proxy: ${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}"
echo "Capstone mode: USE_CAPSTONE=${USE_CAPSTONE}"
echo ""

if [ "$USE_CAPSTONE" = "1" ]; then
    echo "=== Stage 1: Building injector in linux/riscv64 container ==="
    docker run --rm -it \
        -v "$PROJECT_DIR:/app" \
        -e http_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
        -e https_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
        --platform linux/riscv64 \
        riscv64/ubuntu:22.04 \
        bash -c '
set -euo pipefail

CAPSTONE_VERSION='"$CAPSTONE_VERSION"'

echo "=== Installing riscv64 build dependencies ==="
apt-get update -qq
apt-get install -y -qq build-essential cmake git python3 make file 2>&1 | tail -5

echo ""
echo "=== Building static Capstone ${CAPSTONE_VERSION} for riscv64 ==="
git clone --depth 1 --branch "$CAPSTONE_VERSION" https://github.com/capstone-engine/capstone.git /tmp/capstone 2>&1 | tail -5
cd /tmp/capstone
cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DCAPSTONE_ARCHITECTURE_DEFAULT=ON 2>&1 | tail -5
cmake --build build -j"$(nproc)" 2>&1 | tail -5
cmake --install build 2>&1 | tail -5
rm -rf /tmp/capstone

echo ""
echo "=== Building riscv64 injector with Capstone ==="
cd /app
make USE_CAPSTONE=1 clean all 2>&1 | tail -5
file ./injector
'
else
    echo "=== Stage 1: Cross-compiling injector in linux/arm64 container ==="
    docker run --rm -it \
        -v "$PROJECT_DIR:/app" \
        -e http_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
        -e https_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
        --platform linux/arm64 \
        ubuntu:22.04 \
        bash -c '
set -euo pipefail

echo "=== Installing cross-compilation dependencies ==="
apt-get update -qq
apt-get install -y -qq gcc-riscv64-linux-gnu g++-riscv64-linux-gnu \
    qemu-user python3 make file 2>&1 | tail -5

echo ""
echo "=== Cross-compiling sifter for RISC-V ==="
cd /app
make CROSS_COMPILE=riscv64-linux-gnu- USE_CAPSTONE=0 clean all 2>&1 | tail -5
file ./injector
'
fi

echo ""
echo "=== Stage 2: Running hidden-instruction tests in linux/arm64 container ==="
docker run --rm -it \
    -v "$PROJECT_DIR:/app" \
    -v "$QEMU_CUSTOM:/usr/local/bin/qemu-riscv64-custom:ro" \
    -e http_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
    -e https_proxy="http://${DOCKER_PROXY_HOST}:${DOCKER_PROXY_PORT}" \
    --platform linux/arm64 \
    ubuntu:22.04 \
    bash -c '
set -euo pipefail

echo "=== Installing runtime dependencies ==="
apt-get update -qq
apt-get install -y -qq gcc-riscv64-linux-gnu g++-riscv64-linux-gnu \
    qemu-user python3 make file 2>&1 | tail -5

echo ""
echo "=== Verifying binary ==="
cd /app
file ./injector

echo ""
echo "=== Test 1: STANDARD QEMU (expect: 0 hidden, encoding rejected as SIGILL) ==="
echo "Command: qemu-riscv64 ./injector -T -x -b 6B -e 6B --rwx"
qemu-riscv64 -L /usr/riscv64-linux-gnu ./injector -T -x -b 6B -e 6B --rwx 2>&1 || true

echo ""
echo "=== Test 2: CUSTOM QEMU (expect: 1 hidden, encoding accepted as NOP) ==="
echo "Command: qemu-riscv64-custom ./injector -T -x -b 6B -e 6B --rwx"
/usr/local/bin/qemu-riscv64-custom -L /usr/riscv64-linux-gnu ./injector -T -x -b 6B -e 6B --rwx 2>&1 || true

echo ""
echo "========================================"
echo "  Expected results:"
echo "    Test 1: 0 hidden (standard QEMU rejects 0x0000006B)"
echo "    Test 2: 1 hidden (custom QEMU accepts 0x0000006B)"
echo "========================================"
'
