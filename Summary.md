# RISC-V Sifter

## 1. 项目概述

RISC-V Sifter 是一个 **RISC-V 架构隐藏指令分析器**，通过系统性地生成、注入并执行所有可能的指令编码，对比 CPU 实际行为与反汇编器（Capstone）的预期，发现四类异常：

| 类型 | 标记 | 含义 |
|------|------|------|
| Hidden Instruction | `H` | CPU 成功执行到哨兵，且 Capstone 无法识别 |
| Disassembler Bug | `D` | Capstone 认为合法，但 CPU 拒绝执行（SIGILL） |
| Exec Fault | `X` | CPU 已接受并开始执行该编码，但执行时产生非 SIGILL 信号（SIGSEGV/SIGFPE 等），且 Capstone 不识别 |
| Timeout | `T` | 指令执行超时 |

项目灵感来自 x86 领域的 [sandsifter](https://github.com/xoreaxeaxeax/sandsifter)

### 运行环境

当前项目主要在 **macOS + Docker + QEMU** 环境下运行。Docker 容器基于 `riscv64/ubuntu:22.04`，内部通过 QEMU 用户模式透明地模拟 RISC-V 指令执行。

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         riscv-sifter                                 │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐  stdin/stdout┌──────────────────┐                   │
│  │  sifter.py  │──────────────│  injector (C)    │                   │
│  │  Python 前端 │  (raw 二进制) │  指令注入+信号捕获  │                  │
│  └──────┬──────┘              └────────┬─────────┘                   │
│         │                              │                             │
│         │ 结果写入                      │ 内联反汇编                    │
│         ▼                              ▼                             │
│  ┌─────────────┐               ┌──────────────────┐                  │
│  │  data/sync  │               │  Capstone 5.x    │                  │
│  │  data/log   │               │  (libcapstone)   │                  │
│  └──────┬──────┘               └──────────────────┘                  │
│         │                                                            │
│         ▼                                                            │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐           │
│  │ summarize.py│    │ filter.py    │    │riscv_opcodes.py│           │
│  │ 结果汇总报告  │    │ 编码过滤工具   │    │ 指令集参考       │           │
│  └─────────────┘    └──────────────┘    └────────────────┘           │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  Docker 容器 (riscv64/ubuntu:22.04 + QEMU user-mode)     │         │
│  │  Dockerfile.riscv64 → 构建 Capstone 5.x from source      │         │
│  │  scripts/macos-docker-run.sh → macOS 一键启动            │          │
│  └─────────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件

### 3.1 injector（C 核心引擎）

**文件**: `src/injector.c`, `include/injector.h`, `src/handler_trampoline.S`

负责在 RISC-V 环境中实际执行指令探测。

#### 内存布局

```
[GUARD 256页 不可执行] [1页 RWX 测试页] [1页 R-X 陷阱页] [GUARD 256页 不可执行]
         ↑                    ↑                 ↑                    ↑
    PC 相对跳转落地         写入测试指令      全部填充 ebreak       PC 相对跳转落地
    → SIGSEGV              后跟 ebreak 哨兵   寄存器跳转 → SIGTRAP   → SIGSEGV
                                              寄存器存储 → SIGSEGV
```

- **测试页 (RWX)**：每次将一条待测指令写入页首，其后紧跟 `ebreak` 哨兵指令
- **陷阱页 (R-X, 只读+可执行)**：全部填充 `ebreak`；所有通用寄存器（除 gp/tp）在执行前被设为指向此页中央，使得：
  - 通过寄存器的跳转 → 落入 ebreak → SIGTRAP
  - 通过寄存器的存储 → 写只读页 → SIGSEGV
- **Guard 区域 (不可执行)**：共 2MB，覆盖 ±1MB 的 PC 相对跳转范围，落入此区域 → SIGSEGV

#### 寄存器沙箱

执行测试指令前，injector 用内联汇编将除 gp(x3)、tp(x4) 以外的所有 31 个通用寄存器设为指向陷阱页的安全地址，最后通过 `jalr zero, t1, 0` 跳转到测试指令。这样即使测试指令尝试使用任意寄存器做内存操作或跳转，都会被安全捕获。

#### 信号处理

- 使用 `sigaltstack` 在独立栈上运行信号处理器（因为 sp 也被设为陷阱页地址）
- `handler_trampoline.S` 是一段汇编跳板：当测试指令破坏了 gp/tp 时，通过 PC 相对寻址（`auipc`）恢复 gp 和 tp，然后 tail-call 到 C 信号处理器
- 通过 `si_addr`（信号发生地址）区分信号来源：
  - `si_addr == 测试指令地址` → CPU 拒绝该编码
  - `SIGILL` 且 `si_addr != 测试指令地址` → 测试指令已成功执行，信号来自后续的 ebreak 哨兵
  - `SIGSEGV` / `SIGBUS` / `SIGFPE` → CPU 已接受该编码并进入执行路径，只是在 memcage 或运行时异常中 fault

#### 信号分类逻辑

| 收到的信号 | si_addr 位置 | 判定 |
|-----------|-------------|------|
| SIGTRAP | — | 执行成功（命中 ebreak 哨兵） |
| SIGILL | == 测试指令地址 | CPU 拒绝该编码 |
| SIGILL | != 测试指令地址 | 执行成功（QEMU 用户模式下 ebreak 产生 SIGILL） |
| SIGALRM | — | 超时（`alarm(1)` 触发） |
| 其他 (SIGSEGV/SIGFPE/SIGBUS…) | — | 指令已被 CPU 接受并开始执行，但在 memcage 或运行时产生 fault |

这里需要区分“广义 hidden”和输出分类里的 `H`：

- **广义 hidden**：Capstone 不识别，但 CPU 没有在测试指令地址上报 SIGILL；也就是说，CPU 至少接受了这个编码并开始执行。
- **狭义 `H`**：广义 hidden 中成功落到 ebreak 哨兵的子集。
- **`X`**：同样属于 Capstone 未识别、但 CPU 已接受的编码；区别只是执行过程中触发了 SIGSEGV / SIGBUS / SIGFPE 等 fault，因此单独记为 `X`。

#### 搜索模式

| 模式 | 标志 | 策略 |
|------|------|------|
| Exhaustive (`-E`) | 默认 | 从 `start` 到 `end` 逐一递增遍历 |
| Random (`-r`) | `-r` | 随机生成编码 |
| Targeted (`-t`) | `-t` | 按 (opcode, funct3) 分组，每组随机采样 64 条，覆盖所有已知 opcode 槽位 |

#### 输出模式

- **Raw (`-R`)**：10 字节二进制结构体，供 `sifter.py` 通过 stdout 管道读取
- **Text (`-T`)**：人类可读的文本行 `H/D/X/T 0xXXXXXXXX signal code`

### 3.2 sifter.py（Python 前端控制器）

**文件**: `sifter.py`

作为用户交互的主入口，职责包括：

1. **解析命令行参数**并构造 injector 子进程的启动命令
2. **启动 injector 子进程**，通过 stdout 管道接收 10 字节 raw 结果
3. **Poll 线程**：持续读取 injector 输出，对每条结果进行分类判定
4. **GUI 线程**：基于 curses 的实时终端界面，显示当前指令、统计信息、最近异常
5. **崩溃自动恢复**：当 injector 进程异常退出时，自动从最后一条指令的下一条恢复扫描（最多 500 次重启）
6. **结果持久化**：实时写入 `data/sync`，退出时汇总写入 `data/log`

#### 关键参数

| 参数 | 作用 |
|------|------|
| `--unk` | 搜索隐藏指令 (H) |
| `--dis` | 搜索反汇编器 bug (D) |
| `--sync` | 实时写入结果文件 |
| `--tick` | 显示进度 |
| `--filter-ext` | **启用扩展指令白名单过滤**（见第 5 节） |
| `--random` | 随机采样模式 |
| `--targeted` | 定向 opcode 组搜索 |
| `--no-compressed` | 跳过 16 位压缩指令 |
| `--low-mem` | 低内存模式，不在内存中保存所有结果 |
| `-b` / `-e` | 指定扫描范围（十六进制） |
| `-j N` | 多进程并行 |
| `--no-gui` | 无界面模式 |

### 3.3 summarize.py（结果分析器）

**文件**: `summarize.py`

读取 `data/log` 或 `data/sync` 文件，提供：

- 按 opcode 类别分组统计（H/D 数量）
- 位模式分析（找出每组指令的固定位和变化位）
- 详细指令列表（`-d`）
- CSV 导出（`-c`）
- 报告输出到文件（`-o`）

### 3.4 分析工具集

| 文件 | 功能 |
|------|------|
| `analysis/filter.py` | 按 opcode/压缩/标准过滤结果，分组统计，计算位掩码，查找连续范围 |
| `analysis/riscv_opcodes.py` | RISC-V 指令集参考库，提供 opcode 解码、字段提取、格式化输出 |

---

## 4. 黑名单与 HINT 过滤

### 黑名单

以下指令在扫描时被跳过，因为它们会导致系统不稳定或干扰扫描流程：

| 指令 | 原因 |
|------|------|
| `ecall` | 触发系统调用 |
| `ebreak` / `c.ebreak` | 触发断点（与哨兵指令冲突） |
| `wfi` | 等待中断，可能挂起 |
| `mret` / `sret` / `uret` | 特权级返回 |
| `sfence.vma` | TLB 刷新，影响内存映射 |

### HINT 指令过滤

RISC-V 规范定义了一系列 HINT 指令——它们是架构上合法的 NOP，CPU 必须执行但可以忽略其效果。Capstone 通常不识别这些 HINT 变体，导致大量误报。injector 和 sifter.py 都实现了 HINT 识别逻辑：

**32 位 HINT**：
- `LUI`/`AUIPC`/`OP-IMM`/`OP-IMM-32` 当 `rd == x0` 时
- `FENCE` 当 `rd == x0 && rs1 == x0` 且 `pred == 0 || succ == 0` 时

**16 位压缩 HINT**：
- `C.NOP` 当 `nzimm != 0`
- `C.ADDI rd, 0` 当 `rd != x0 && imm == 0`
- `C.LI x0, imm` / `C.LUI x0, imm`
- `C.SLLI x0, shamt`
- `C.MV x0, rs2` / `C.ADD x0, rs2`

---

## 5. 扩展指令白名单过滤（`--filter-ext`）

### 问题背景

QEMU 实现了大量 RISC-V 扩展（尤其是以 `Z` 开头的众多扩展），但 **Capstone 5.x 尚未支持其中许多扩展的反汇编**。这导致在 QEMU 环境中扫描时，大量属于合法扩展的指令被误判为 "隐藏指令"（H），严重干扰对真正异常的分析。

从实际扫描日志可以看到，QEMU 报告的 ISA 字符串极为丰富：

```
rv64imafdcbvh_zic64b_zicbom_zicbop_zicboz_ziccamoa_ziccif_zicclsm_ziccrse
_zicfilp_zicfiss_zicond_zicntr_zicsr_zifencei_zihintntl_zihintpause_zihpm
_zimop_zmmul_za64rs_zaamo_zabha_zacas_zama16b_zalrsc_zawrs_zfa_zfbfmin_zfh
_zfhmin_zca_zcb_zcd_zcmop_zba_zbb_zbc_zbkb_zbkc_zbkx_zbs_zk_zkn_zknd_zkne
_zknh_zkr_zks_zksed_zksh_zkt_ztso_zvbb_zvbc_zve32f_zve32x_zve64f_zve64d
_zve64x_zvfbfmin_zvfbfwma_zvfh_zvfhmin_zvkb_zvkg_zvkn_zvknc_zvkned_zvkng
_zvknha_zvknhb_zvks_zvksc_zvksed_zvksg_zvksh_zvkt ...
```

### 解决方案

`--filter-ext` 参数启用两级过滤：

#### 第一级：扩展指令识别（`_identify_known_extension`）

在 injector.c 和 sifter.py 中都实现了同一套基于编码位模式的扩展识别函数，覆盖以下 Capstone 不支持的扩展：

| 扩展 | 覆盖指令类型 |
|------|-------------|
| **Zfh** / **Zfa** | 半精度浮点 / 附加浮点操作 |
| **V** (含 Zv*) | 向量指令 |
| **Zba** / **Zbb** / **Zbc** / **Zbs** | 位操作扩展族 |
| **Zbkb** / **Zb*** | 位操作密码学扩展 |
| **Zicond** | 条件操作 |
| **Zabha** / **Zacas** | 原子操作扩展 |
| **Zimop** | May-be-operations |
| **Zicbom** / **Zicbop** | Cache block 操作 |
| **Zihintntl** | Non-temporal locality hints |
| **Zcb** / **Zcmop** | 压缩指令扩展 |

识别逻辑通过解析 opcode、funct3、funct7 等字段进行精确的位模式匹配。

#### 第二级：ISA 感知过滤（`_extension_enabled`）

sifter.py 中的 `detect_isa_extensions()` 函数解析 `/proc/cpuinfo` 的 `isa` 字段，提取当前环境实际支持的扩展列表。只有当识别出的扩展确实在 ISA 字符串中存在时，才将对应的 H 条目过滤掉。

特殊匹配规则：
- `V` 扩展：匹配 `v` 或任何 `zv*` 前缀
- `Zb*` 通配：匹配任何 `zb*` 前缀
- `Zk` 通配：匹配任何 `zk*` 前缀
- ISA 检测不可用时（如非 Linux 环境），回退到全部放行

### 效果

启用 `--filter-ext` 后，在 QEMU 环境中 262,001 条随机采样的扫描结果：
- Hidden (H): **52** 条（成功执行到哨兵的未识别编码；过滤前可能数千条）
- Disas Bug (D): **84** 条
- Exec Fault (X): **2,067** 条（CPU 已接受但执行时 fault 的未识别编码）
- Timeout (T): **43** 条

---

## 6. 构建与运行

### 项目文件结构

```
riscv-sifter/
├── Makefile                    # 构建系统（支持交叉编译、Capstone 可选）
├── Dockerfile.riscv64          # Docker 镜像定义（基于 riscv64/ubuntu:22.04）
├── requirements.txt            # Python 依赖（capstone>=4.0.0）
├── sifter.py                   # 主控前端（Python3, curses GUI）
├── summarize.py                # 结果汇总分析
├── src/
│   ├── injector.c              # 核心注入引擎（~1200 行 C）
│   └── handler_trampoline.S    # RISC-V 信号处理器汇编跳板
├── include/
│   └── injector.h              # 类型定义与接口声明
├── scripts/
│   ├── macos-docker-run.sh     # macOS Docker 一键运行脚本
│   ├── qemu-build.sh           # 交叉编译脚本
│   ├── qemu-scan.sh            # QEMU 用户模式扫描脚本
│   └── qemu-analyze.sh         # 扫描结果快速分析
├── analysis/
│   ├── filter.py               # 结果过滤工具
│   └── riscv_opcodes.py        # RISC-V 指令集参考
├── examples/
│   └── run_scan.sh             # 示例扫描命令
├── docs/
│   ├── QEMU_GUIDE.md           # QEMU 使用指南（Linux）
│   └── QEMU_GUIDE_MACOS.md    # QEMU 使用指南（macOS）
├── data/                       # 扫描结果存储
│   ├── log                     # 最终汇总日志
│   └── sync                    # 实时同步日志
└── results/                    # 分析报告输出
```

### macOS 快速开始

```bash
# 1. 确保 Docker Desktop 已安装并运行

# 2. 构建 Docker 镜像（首次约 5-10 分钟）
./scripts/macos-docker-run.sh rebuild

# 3. 运行默认扫描（随机模式 + 扩展过滤 + 实时界面）
./scripts/macos-docker-run.sh

# 4. 快速测试（60 秒超时）
./scripts/macos-docker-run.sh quick

# 5. 进入容器 shell 手动操作
./scripts/macos-docker-run.sh shell
```

### Docker 内部构建流程

`Dockerfile.riscv64` 的构建过程：

1. 基于 `riscv64/ubuntu:22.04` 镜像
2. 安装 build-essential、cmake、git、python3
3. **从源码编译 Capstone 5.0.7**（Ubuntu apt 仓库只有 4.0.2，不支持 RISC-V 架构）
4. `pip install capstone`（Python 绑定）
5. `make clean all`（编译 injector，链接 libcapstone）

### Makefile 关键目标

| 目标 | 作用 |
|------|------|
| `make` | 默认构建（编译 injector，链接 Capstone） |
| `make USE_CAPSTONE=0` | 不链接 Capstone 构建 |
| `make CROSS_COMPILE=riscv64-linux-gnu-` | 交叉编译 |
| `make docker-build` | 构建 Docker 镜像 |
| `make macos-run` | macOS Docker 运行 |
| `make analyze` | 分析 data/log |
| `make report` | 生成详细报告 + CSV |

---

## 7. 数据流与日志格式

### 实时数据流

```
injector (C)  ──[10字节 raw struct]──>  sifter.py (Poll线程)
                                            │
                                            ├──> curses GUI（实时显示）
                                            ├──> data/sync（实时追加）
                                            └──> data/log（退出时写入）
```

### Raw 二进制结构（10 字节）

```
offset  size  field
  0      1    disas_len      反汇编长度
  1      1    disas_known    Capstone 是否识别 (0/1)
  2      4    encoding       指令编码 (little-endian uint32)
  6      1    valid          结果是否有效
  7      1    length         指令长度 (2 或 4)
  8      1    signum         信号编号
  9      1    sicode         信号代码
```

### 文本日志格式

```
# 元数据行以 # 开头
# RISC-V Sifter Results
# Command: ./sifter.py --unk --dis -j 10 --sync --tick --filter-ext
# Tested: 262001
# Hidden: 52
# ...

# 数据行格式: 类型 编码 信号 代码
H 0x12345678 4 1      # 隐藏指令
D 0x87654321 4 1      # 反汇编器 bug
X 0xABCDEF00 11 2     # 执行异常
T 0x11223344 14 0     # 超时
```

---

## 8. 技术要点与设计决策

### 为什么需要汇编跳板（handler_trampoline.S）

RISC-V 的 `gp`（全局指针）和 `tp`（线程指针）寄存器被 C 运行时用于访问全局变量和 TLS。如果测试指令破坏了这两个寄存器，C 信号处理器将无法正常工作，`siglongjmp` 也会崩溃。汇编跳板使用 `auipc`（PC 相对寻址，不依赖 gp）从内存中恢复 gp 和 tp，然后 tail-call 到 C 处理器。

### 为什么使用 alarm(1) 超时

某些指令编码可能导致 CPU 进入无限循环（如某些分支指令的特殊编码）。`alarm(1)` 设置 1 秒超时，SIGALRM 会中断执行并被信号处理器捕获，标记为 Timeout。

### QEMU 用户模式的特殊行为

QEMU 用户模式下，`ebreak` 指令产生的是 SIGILL 而非 SIGTRAP（与真实硬件不同）。因此判定逻辑不能仅依赖信号类型，而是通过 `si_addr` 比较来区分信号来源：如果 `SIGILL` 发生在测试指令地址，说明 CPU 拒绝该编码；如果 `SIGILL` 发生在其他地址，则说明测试指令已经执行成功，信号来自后续的 `ebreak` 哨兵。对于 `SIGSEGV` / `SIGBUS` / `SIGFPE` 等信号，则应理解为 CPU 已接受该编码并进入执行路径，只是执行过程中触发了 memcage 或运行时 fault。

### Capstone 5.x 的局限性

Capstone 5.0.7 对 RISC-V 的支持仅覆盖基础指令集（RV32/64IMAFDC）和少量扩展。QEMU 实现的大量 Z 系列扩展（Zba/Zbb/Zbs/Zfh/Zfa/V/Zcb 等）在 Capstone 中没有对应的反汇编支持，这是 `--filter-ext` 功能存在的根本原因。

---

## 9. 典型工作流

```
1. 构建镜像
   ./scripts/macos-docker-run.sh rebuild

2. 运行扫描（容器内自动执行）
   ./scripts/macos-docker-run.sh
   # 或进入 shell 手动运行:
   ./sifter.py --unk --dis -j 10 --sync --tick --filter-ext

3. 查看实时结果
   # GUI 界面显示: 当前指令、统计、最近异常
   # 按 Q 退出, P 暂停

4. 分析结果
   ./summarize.py data/log           # 汇总报告
   ./summarize.py data/log -d        # 详细列表
   ./summarize.py data/log -c out.csv  # CSV 导出

5. 进一步过滤
   python3 analysis/filter.py data/log -g    # 按 opcode 分组
   python3 analysis/filter.py data/log -m    # 计算位掩码
   python3 analysis/filter.py data/log -c    # 仅压缩指令
```

---

## 10. 性能参考

| 模式 | 原生 RISC-V | Linux QEMU | macOS Docker+QEMU |
|------|------------|------------|-------------------|
| 穷举 32 位全空间 | ~1-2 天 | ~1-2 周 | ~2-4 周 |
| 随机采样 100 万条 | ~10 秒 | ~10 分钟 | ~15-30 分钟 |
| 随机采样 26 万条 | — | — | ~46 秒（实测） |

---

## 11. 参考资料

- [RISC-V ISA Specification](https://riscv.org/specifications/)
- [Sandsifter (x86)](https://github.com/xoreaxeaxeax/sandsifter)
- [Capstone Disassembly Framework](http://www.capstone-engine.org/)
- [QEMU RISC-V Documentation](https://www.qemu.org/docs/master/system/target-riscv.html)
