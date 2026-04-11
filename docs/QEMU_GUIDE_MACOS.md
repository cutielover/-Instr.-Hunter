# 在 macOS 上运行 RISC-V Sifter

本项目当前在 macOS 上的推荐运行方式是：

```text
Docker Desktop
  + linux/riscv64 容器
  + QEMU user-mode / binfmt
  + 容器内构建并运行 riscv-sifter
```

也就是说，macOS 宿主机本身不直接编译或执行 `injector`，而是通过 Docker 进入 riscv64 Linux 环境完成构建、扫描和分析。

## 推荐工作流

### 1. 安装并启动 Docker Desktop

可以从 Docker 官网安装，或通过 Homebrew：

```bash
brew install --cask docker
open -a Docker
```

等待 Docker Desktop 启动完成后，确认：

```bash
docker info
```

### 2. 在项目根目录运行一键脚本

```bash
./scripts/macos-docker-run.sh
```

脚本会自动：

- 检查 Docker 是否可用
- 在需要时安装 `binfmt`
- 构建 `riscv-sifter` 镜像
- 以 `linux/riscv64` 平台启动容器
- 默认运行一轮 exhaustive 扫描

默认命令等价于：

```bash
./sifter.py --unk --dis --sync --tick -j 10 --rwx --filter-ext
```

### 3. 常用脚本子命令

#### 进入容器 shell

```bash
./scripts/macos-docker-run.sh shell
```

#### 强制重建镜像

```bash
./scripts/macos-docker-run.sh rebuild
```

#### 快速测试

```bash
./scripts/macos-docker-run.sh quick
```

这个模式会直接运行：

```bash
timeout 60 ./injector -r -T -x
```

适合确认镜像、QEMU 和 `injector` 本身是否工作正常。

#### 运行自定义命令

```bash
./scripts/macos-docker-run.sh ./sifter.py --unk --dis --random --no-gui
./scripts/macos-docker-run.sh injector -E -T -x
```

## 容器内环境

`Dockerfile.riscv64` 会在 riscv64 Ubuntu 容器内完成以下工作：

- 安装构建依赖
- 从源码编译并安装 Capstone 6 `next` 分支
- 安装 Python 绑定
- 拷贝项目源码到 `/app`
- 执行 `make clean all`

容器默认工作目录是：

```bash
/app
```

容器默认入口命令是：

```bash
./sifter.py --unk --dis -j 10 --sync --tick
```

而 `macos-docker-run.sh` 默认又额外传入 `--rwx --filter-ext`。

## 容器内手动运行

进入 shell 后，常见用法如下：

```bash
# 查看帮助
./sifter.py --help

# 默认风格扫描
./sifter.py --unk --dis --sync --tick -j 10 --rwx --filter-ext

# 随机模式
./sifter.py --unk --dis --random --sync --no-gui

# 指定范围
./sifter.py --unk --dis -b 0x00000000 -e 0x10000000

# 跳过压缩指令
./sifter.py --unk --dis --no-compressed

# 直接运行 injector 的 text 输出
./injector -E -T -x
```

## 数据目录映射

脚本会把宿主机目录挂载进容器：

- 宿主机 `./data` -> 容器 `/app/data`
- 宿主机 `./results` -> 容器 `/app/results`

因此在容器里运行后的结果可以直接在宿主机看到：

```bash
ls data
./summarize.py data/log
```

## 当前推荐与非推荐路径

### 推荐

- 使用 `scripts/macos-docker-run.sh`
- 在容器内构建和运行
- 用 `sifter.py` 作为主入口

### 可选

- 直接 `docker build --platform linux/riscv64 -f Dockerfile.riscv64 -t riscv-sifter .`
- 直接 `docker run --platform linux/riscv64 ...`
- 进入容器后手动跑 `injector`

### 不作为默认工作流

- 在 macOS 宿主机直接 `make`
- 在 macOS 宿主机直接运行 `injector`
- 依赖本机的 Capstone 或交叉工具链完成主流程

## QEMU 与真实硬件

在 macOS + Docker 工作流中，扫描运行在 QEMU 模拟环境里。它很适合：

- 验证扫描器逻辑
- 验证 raw/text 输出和分析脚本
- 发现 QEMU 与 Capstone 之间的行为差异
- 做受控的回归测试

如果目标是发现特定芯片的 undocumented 指令或实现特有行为，仍然需要在真实 RISC-V 硬件上运行。

## 常见问题

### Docker 未启动

```bash
open -a Docker
docker info
```

### 构建镜像失败

先尝试强制重建：

```bash
./scripts/macos-docker-run.sh rebuild
```

也可以手动执行：

```bash
docker build --platform linux/riscv64 -f Dockerfile.riscv64 -t riscv-sifter .
```

### 想看实时结果

运行带 `--sync` 的扫描后，结果会持续写入：

```bash
data/sync
```

结束后最终结果在：

```bash
data/log
```

### 想离线分析

```bash
./summarize.py data/log
./summarize.py data/log --detailed
./analysis/filter.py data/log --group
```

## 相关文件

- `scripts/macos-docker-run.sh`
- `Dockerfile.riscv64`
- `README.md`
- `docs/memcage.md`
