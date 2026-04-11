#!/bin/bash
# macOS Docker RISC-V 运行脚本
# 使用 Docker + QEMU 在 macOS 上运行 RISC-V Sifter
#
# Usage:
#   ./scripts/macos-docker-run.sh           # 默认运行
#   ./scripts/macos-docker-run.sh shell     # 交互式 shell
#   ./scripts/macos-docker-run.sh quick     # 快速测试
#   ./scripts/macos-docker-run.sh rebuild   # 重新构建镜像
#   ./scripts/macos-docker-run.sh [args]    # 自定义参数

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="riscv-sifter"

cd "$PROJECT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# 检查 Docker 是否安装并运行
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装"
        echo ""
        echo "请安装 Docker Desktop for Mac:"
        echo "  brew install --cask docker"
        echo "  或从 https://docker.com 下载"
        exit 1
    fi
    
    if ! docker info > /dev/null 2>&1; then
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

# 设置多架构支持
setup_multiarch() {
    print_info "配置多架构支持..."
    docker run --privileged --rm tonistiigi/binfmt --install all 2>/dev/null || true
}

# 构建 Docker 镜像
build_image() {
    print_info "构建 Docker 镜像 (首次可能需要 5-10 分钟)..."
    echo ""
    
    # 确保多架构支持已启用
    setup_multiarch
    
    # 构建镜像
    if docker build --platform linux/riscv64 -f Dockerfile.riscv64 -t "$IMAGE_NAME" .; then
        print_status "镜像构建完成"
    else
        print_error "镜像构建失败"
        exit 1
    fi
}

# 检查镜像是否存在
check_image() {
    if ! docker images --format "{{.Repository}}" | grep -q "^${IMAGE_NAME}$"; then
        print_warning "Docker 镜像不存在，开始构建..."
        build_image
    else
        print_status "Docker 镜像已存在"
    fi
}

# 显示帮助
show_help() {
    echo "RISC-V Sifter - macOS Docker 运行脚本"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  (无参数)     运行默认扫描 (exhaustive 模式, 10 workers)"
    echo "  shell        进入交互式 shell"
    echo "  quick        快速测试 (约 60 秒)"
    echo "  rebuild      强制重新构建 Docker 镜像"
    echo "  injector     直接运行 injector"
    echo "  help         显示此帮助"
    echo ""
    echo "Examples:"
    echo "  $0                              # 默认运行"
    echo "  $0 shell                        # 进入容器 shell"
    echo "  $0 quick                        # 快速测试"
    echo "  $0 ./injector -r -T -x          # 自定义 injector 参数"
    echo "  $0 ./sifter.py --unk -j 10      # 自定义 sifter 参数"
    echo ""
}

# 主函数
main() {
    echo ""
    echo "========================================"
    echo "   RISC-V Sifter (macOS Docker+QEMU)"
    echo "========================================"
    echo ""
    
    # 检查 Docker
    check_docker
    
    # 创建数据目录
    mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/results"
    
    # 处理命令
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
            docker run --rm -it --platform linux/riscv64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" /bin/bash
            ;;
        quick)
            check_image
            echo ""
            print_info "运行快速测试 (60 秒超时)..."
            echo ""
            docker run --rm -it --platform linux/riscv64 \
                -v "$PROJECT_DIR/data:/app/data" \
                "$IMAGE_NAME" \
                timeout 60 ./injector -r -T -x 2>&1 | head -200
            echo ""
            print_status "快速测试完成"
            ;;
        injector)
            check_image
            shift
            echo ""
            print_info "运行 injector..."
            echo ""
            docker run --rm -it --platform linux/riscv64 \
                -v "$PROJECT_DIR/data:/app/data" \
                "$IMAGE_NAME" \
                ./injector "${@:--r -T -x}"
            ;;
        "")
            check_image
            echo ""
            print_info "运行默认扫描 (exhaustive 模式, 10 workers, 带实时界面)..."
            print_info "按 Q 退出, P 暂停"
            echo ""
            docker run --rm -it --platform linux/riscv64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" \
                ./sifter.py --unk -j 10 --dis --sync --tick --filter-ext
            ;;
        *)
            check_image
            echo ""
            print_info "运行自定义命令..."
            echo ""
            docker run --rm -it --platform linux/riscv64 \
                -v "$PROJECT_DIR/data:/app/data" \
                -v "$PROJECT_DIR/results:/app/results" \
                "$IMAGE_NAME" "$@"
            ;;
    esac
}

# 运行主函数
main "$@"
