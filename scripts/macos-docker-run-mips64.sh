#!/bin/bash
# macOS Docker MIPS64 运行脚本
# 在 macOS 上通过 linux/mips64le 容器运行 MIPS Sifter

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="mips64-sifter"
DOCKER_PTRACE_ARGS=(--cap-add=SYS_PTRACE --security-opt seccomp=unconfined)

cd "$PROJECT_DIR"

check_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker 未安装"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "Docker 未运行"
        exit 1
    fi
}

setup_multiarch() {
    docker run --privileged --rm tonistiigi/binfmt --install all >/dev/null 2>&1 || true
}

build_image() {
    setup_multiarch
    docker build --platform linux/mips64le -f Dockerfile.mips64 -t "$IMAGE_NAME" .
}

check_image() {
    if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        build_image
    fi
}

main() {
    check_docker
    mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/results"

    case "${1:-}" in
        rebuild)
            docker rmi "$IMAGE_NAME" 2>/dev/null || true
            build_image
            ;;
        shell)
            check_image
            docker run --rm -i --platform linux/mips64le "${DOCKER_PTRACE_ARGS[@]}" \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" /bin/bash
            ;;
        quick)
            check_image
            set +e
            docker run --rm -i --platform linux/mips64le \
                -v "$PROJECT_DIR/data:/app/data" \
                "$IMAGE_NAME" \
                timeout 15 ./sifter.py --arch mips --unk --dis --no-gui -j 1 --random
            STATUS=$?
            set -e
            # GNU timeout returns 124 on timeout; treat quick-run timeout as success.
            if [ "$STATUS" -ne 0 ] && [ "$STATUS" -ne 124 ]; then
                exit "$STATUS"
            fi
            ;;
        "")
            check_image
            docker run --rm -i --platform linux/mips64le "${DOCKER_PTRACE_ARGS[@]}" \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME"
            ;;
        *)
            check_image
            docker run --rm -i --platform linux/mips64le "${DOCKER_PTRACE_ARGS[@]}" \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" "$@"
            ;;
    esac
}

main "$@"
