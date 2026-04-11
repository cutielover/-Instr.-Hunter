# RISC-V Sifter

RISC-V Sifter 是一个受 x86 `sandsifter` 启发的 RISC-V 指令空间探测工具。它会在真实或模拟的 RISC-V CPU 上逐条注入指令编码，通过执行结果与反汇编结果的差异，发现隐藏指令、反汇编器误判，以及异常执行行为。

当前项目主要面向 macOS 开发环境，依赖 Docker + QEMU user-mode 运行 riscv64 Linux 容器，在容器内构建并执行扫描。

## 项目目标

RISC-V Sifter 用来回答下面几类问题：

- CPU 能执行，但反汇编器不认识的编码在哪里
- 反汇编器认为合法，但 CPU 拒绝执行的编码在哪里
- 某些未识别编码是否会触发超时、访问错误或其他执行异常
- 已知 ISA 扩展、QEMU 行为与 Capstone 解码之间是否存在边界差异

## 架构概览

项目由两部分组成：

- `sifter.py`
  Python 前端，负责 CLI、ISA 检测、Capstone mode 选择、worker 调度、结果汇聚、同步写盘和 curses GUI。
- `injector` / `injector_aarch64`
  C 端执行器：共享 [src/injector_core.c](src/injector_core.c) 与架构后端（[src/arch_riscv.c](src/arch_riscv.c) 或 [src/arch_aarch64.c](src/arch_aarch64.c)），负责构造测试页、注入指令、捕获信号、调用 Capstone，并以 text 或 raw 协议输出结果。

整体流程如下：

```text
sifter.py
  -> 读取 /proc/cpuinfo 的 ISA 字符串
  -> 计算 Capstone cs_mode bitmask
  -> 启动一个或多个 injector 进程
  -> 每个 injector 扫描各自的编码空间
  -> Python reader 线程解析 raw 输出并分类
  -> 结果实时显示到 GUI，并写入 data/sync / data/log
```

## 核心组件

### `sifter.py`

主控制程序，负责：

- 解析 CLI 参数
- 检测 CPU ISA 扩展
- 基于 ISA 选择合适的 Capstone RISC-V mode flag
- 在 exhaustive 模式下按 shard 启动多个独立 `injector`
- 为每个 `injector` 建立独立 reader 线程
- 汇聚统计信息并维护最近异常记录
- 提供 curses 实时界面
- 在退出时生成结果文件

### `src/injector_core.c` 与架构后端

- **共享核心**（`injector_core.c`）：CLI 解析、多进程分片、缓冲输出、`main` 循环。
- **RISC-V**（`arch_riscv.c` + `handler_trampoline.S`）：`ebreak` 哨兵、寄存器沙箱、exhaustive/random/targeted、可选 `ptrace`（`ptrace_runner.c`）。
- **AArch64 Linux**（`arch_aarch64.c` + `handler_trampoline_aarch64.S`）：`BRK` 哨兵、全空间 **+1** exhaustive；支持 Linux `ptrace` 单步模式（共享 `ptrace_runner.c` + AArch64 backend）。

详见 [docs/AARCH64_LINUX.md](docs/AARCH64_LINUX.md)。

### `summarize.py`

离线分析工具，用于读取 `data/log` 并做汇总，包括：

- 按 opcode / 压缩象限粗分类
- 统计 `H` / `D` 类结果数量
- 展示代表性样本
- 输出详细列表或 CSV

### `analysis/`

辅助分析脚本目录：

- `analysis/filter.py`：按 opcode、压缩/非压缩、范围、mask 等方式过滤结果
- `analysis/riscv_opcodes.py`：RISC-V opcode 参考与简单编码解析

## 扫描模式

`injector` 当前支持三种搜索模式：

- `exhaustive`
  穷举遍历编码空间。默认模式。
- `random`
  随机采样编码。
- `targeted`
  按 opcode 组和 `funct3` 槽位做定向抽样。

在 `sifter.py` 中：

- `-j 1` 或非 exhaustive 模式下，使用单个 `injector`
- exhaustive 且 `-j N > 1` 时，Python 前端会启动 `N` 个独立 `injector`，每个 worker 处理一个独立 shard

## 结果分类

项目使用“执行结果 + 反汇编结果”联合判定：

- `H`：执行成功，但反汇编器不认为它是合法指令
- `D`：执行得到 `SIGILL`，但反汇编器认为它是合法指令
- `T`：执行超时
- `X`：执行触发其他异常，且反汇编器也不认为它是合法指令

Capstone 6 中，某些编码虽然能解码，但被 ISA 定义为非法。项目会使用 `disas_illegal` 标志把这类编码从“已识别合法指令”中排除，避免把“可解码但 ISA 非法”的编码误计为反汇编器 bug。

在 `--arch aarch64` 下，日志会额外区分 `Disas Mismatch Raw` 与 `Disas Mismatch Strict`。其中 `Disas Bugs` 默认对应 stricter 口径（`SIGILL + ILL_ILLOPC + disas_illegal=0`），用于减少用户态不可执行编码带来的噪声；`H` 判定不变。

## ISA 感知的 Capstone 配置

扫描前，`sifter.py` 会读取 `/proc/cpuinfo` 里的 `isa` 字符串，并把扩展 token 映射到 Capstone 6 的 RISC-V `cs_mode` bitmask。这个 bitmask 会同时用于：

- Python 侧本地反汇编
- C 侧 `injector --cs-mode N`

这样可以让扫描尽量只启用目标 CPU 已报告支持的扩展，降低“Capstone 解码范围明显大于执行环境”的误报概率。

## 输出协议

### text 输出

text 模式下，每行一条异常记录：

```text
H 0x12345678 0 0
D 0x87654321 4 1
T 0x0000006f 14 0
X 0xfeedbeef 11 2
```

格式为：

```text
<type> <encoding> <signum> <sicode>
```

### raw 输出

`sifter.py` 与 `injector` 之间默认使用 raw 二进制协议。每条结果固定为 12 字节：

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 1 | `worker_id` | 结果来源 worker |
| 1 | 1 | `disas_len` | Capstone 认为的指令长度 |
| 2 | 1 | `disas_known` | 是否识别该编码 |
| 3 | 1 | `disas_illegal` | 是否属于“能解码但 ISA 非法” |
| 4 | 4 | `encoding` | 32-bit little-endian 编码 |
| 8 | 1 | `valid` | 执行结果是否有效 |
| 9 | 1 | `length` | 实际指令长度 |
| 10 | 1 | `signum` | 捕获到的信号号 |
| 11 | 1 | `sicode` | `si_code` |

Python 侧由 `RawResult` 解析这些帧，再完成分类、统计和落盘。

## 实时界面

默认情况下，`sifter.py` 会启动 curses GUI，展示：

- 当前模式与 jobs 数
- 当前启用的 Capstone flag 简称
- 原始 ISA 字符串
- 累计测试数、隐藏指令数、反汇编器问题数、超时数、执行异常数
- 每个 worker 的当前编码和统计信息
- 最近发现的异常记录

运行期间支持：

- `q`：退出
- `p`：暂停 / 继续

## 构建与运行

### 推荐方式：macOS + Docker

项目默认工作流是在 macOS 上通过 Docker 启动 riscv64 容器。

#### 一键运行

```bash
./scripts/macos-docker-run.sh
```

默认会在容器中执行一轮 exhaustive 扫描。

#### 强制重建镜像

```bash
./scripts/macos-docker-run.sh rebuild
```

#### 进入容器

```bash
./scripts/macos-docker-run.sh shell
```

### 容器内手动运行

进入容器后，可以直接运行：

```bash
./sifter.py --unk --dis --sync --tick -j 10 --rwx --filter-ext
```

常见示例：

```bash
# 仅扫描未知/隐藏指令
./sifter.py --unk --sync

# 同时记录隐藏指令和反汇编器问题
./sifter.py --unk --dis --sync

# 指定扫描范围
./sifter.py --unk --dis -b 0x00000000 -e 0x10000000

# 跳过压缩指令
./sifter.py --unk --dis --no-compressed

# 使用多个 worker 做 exhaustive 扫描
./sifter.py --unk --dis --sync -j 10

# 使用 ptrace 执行模式
./sifter.py --unk --dis --sync --ptrace
```

### Linux AArch64 扫描

在 **Linux AArch64** 主机或交叉编译环境下构建 `injector_aarch64`（`make injector_aarch64`），然后：

```bash
./sifter.py --arch aarch64 --unk --dis --sync --no-gui -j 8
```

完整空间约 2³² 条编码，耗时极长；可用 `-b`/`-e` 分片。说明见 [docs/AARCH64_LINUX.md](docs/AARCH64_LINUX.md)。

## 结果文件

扫描结果默认写到 `data/` 目录：

- `data/sync`
  运行中的实时结果文件
- `data/log`
  退出时生成的最终结果文件
- `data/tick`
  进度信息文件
- `data/last`
  最近一次命令/状态记录

## 离线分析

### 汇总结果

```bash
./summarize.py data/log
```

### 详细输出

```bash
./summarize.py data/log --detailed
```

### 导出 CSV

```bash
./summarize.py data/log --csv out.csv
```

### 用辅助脚本过滤

```bash
./analysis/filter.py data/log --group
./analysis/filter.py data/log --compressed
./analysis/filter.py data/log --opcode 0x33
```

## 项目结构

```text
riscv-sifter/
├── README.md
├── work.md
├── Makefile
├── sifter.py
├── summarize.py
├── include/
│   ├── injector.h
│   └── arch.h
├── src/
│   ├── injector_core.c
│   ├── arch_riscv.c
│   ├── arch_aarch64.c
│   ├── ptrace_runner.c
│   ├── ptrace_stub.c
│   ├── handler_trampoline.S
│   └── handler_trampoline_aarch64.S
├── scripts/
│   ├── macos-docker-run.sh
│   ├── qemu-build.sh
│   ├── qemu-scan.sh
│   ├── qemu-analyze.sh
│   └── test-hidden-insn.sh
├── analysis/
│   ├── filter.py
│   └── riscv_opcodes.py
├── docs/
│   ├── QEMU_GUIDE_MACOS.md
│   ├── AARCH64_LINUX.md
│   └── memcage.md
└── data/
    ├── log
    ├── sync
    └── tick
```

## 参考

- [RISC-V ISA Specification](https://riscv.org/specifications/)
- [Sandsifter](https://github.com/xoreaxeaxeax/sandsifter)
- [Capstone Disassembly Framework](https://www.capstone-engine.org/)
