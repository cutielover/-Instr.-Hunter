#!/bin/bash
# macOS Docker AArch64 运行脚本
# 在 macOS 上通过 Linux/arm64 容器运行 AArch64 Sifter
#
# Usage:
#   ./scripts/macos-docker-run-aarch64.sh           # 默认运行
#   ./scripts/macos-docker-run-aarch64.sh shell     # 交互式 shell
#   ./scripts/macos-docker-run-aarch64.sh quick     # 快速测试
#   ./scripts/macos-docker-run-aarch64.sh rebuild   # 重新构建镜像
#   ./scripts/macos-docker-run-aarch64.sh [args]    # 自定义参数

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="aarch64-sifter"

cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}✅${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

print_error() {
    echo -e "${RED}❌${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ️${NC} $1"
}

check_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        print_error "Docker 未安装"
        echo ""
        echo "请安装 Docker Desktop for Mac:"
        echo "  brew install --cask docker"
        echo "  或从 https://docker.com 下载"
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        print_error "Docker 未运行"
        echo ""
        echo "请启动 Docker Desktop:"
        echo "  open -a Docker"
        echo ""
        echo "等待 Docker 启动后重试..."
        exit 1
    fi

    print_status "Docker 已就绪"
}

build_image() {
    print_info "构建 AArch64 Docker 镜像 (首次可能需要 5-10 分钟)..."
    echo ""

    if docker build --platform linux/arm64 -f Dockerfile.aarch64 -t "$IMAGE_NAME" .; then
        print_status "镜像构建完成"
    else
        print_error "镜像构建失败"
        exit 1
    fi
}

check_image() {
    if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        print_warning "Docker 镜像不存在，开始构建..."
        build_image
    else
        print_status "Docker 镜像已存在"
    fi
}

show_help() {
    echo "AArch64 Sifter - macOS Docker 运行脚本"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (无参数)     运行默认扫描 (aarch64 exhaustive, no-gui)"
    echo "  shell        进入交互式 shell"
    echo "  quick        快速测试 (约 60 秒)"
    echo "  rebuild      强制重新构建 Docker 镜像"
    echo "  injector     直接运行 injector_aarch64"
    echo "  help         显示此帮助"
    echo ""
    echo "Examples:"
    echo "  $0"
    echo "  $0 shell"
    echo "  $0 quick"
    echo "  $0 ./sifter.py --arch aarch64 --unk --dis --no-gui -j 8"
    echo ""
}

main() {
    echo ""
    echo "========================================"
    echo "  AArch64 Sifter (macOS Docker linux/arm64)"
    echo "========================================"
    echo ""

    check_docker
    mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/results"

    case "${1:-}" in
        help|--help|-h)
            show_help
            exit 0
            ;;
        rebuild)
            print_info "强制重新构建镜像..."
            docker rmi "$IMAGE_NAME" 2>/dev/null || true
            build_image
            exit 0
            ;;
        shell)
            check_image
            echo ""
            print_info "进入交互式 shell..."
            print_info "提示: 输入 'exit' 退出"
            echo ""
            docker run --rm -it --platform linux/arm64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" /bin/bash
            ;;
        quick)
            check_image
            echo ""
            print_info "运行快速测试 (60 秒超时)..."
            echo ""
            docker run --rm -it --platform linux/arm64 \
                -v "$PROJECT_DIR/data:/app/data" \
                "$IMAGE_NAME" \
                timeout 60 ./sifter.py --arch aarch64 --unk --dis --no-gui -j 1 -b 0x00000000 -e 0x0000ffff
            echo ""
            print_status "快速测试完成"
            ;;
        injector)
            check_image
            shift
            echo ""
            print_info "运行 injector_aarch64..."
            echo ""
            docker run --rm -it --platform linux/arm64 \
                -v "$PROJECT_DIR/data:/app/data" \
                "$IMAGE_NAME" \
                ./injector_aarch64 "${@:--E -T -x}"
            ;;
        "")
            check_image
            echo ""
            print_info "运行默认 AArch64 扫描 (exhaustive, 8 workers, no-gui)..."
            echo ""
            docker run --rm -it --platform linux/arm64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" \
                ./sifter.py --arch aarch64 --unk --ptrace --dis --sync --tick -j 8
            ;;
        *)
            check_image
            echo ""
            print_info "运行自定义命令..."
            echo ""
            docker run --rm -it --platform linux/arm64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" "$@"
            ;;
    esac
}

main "$@"
