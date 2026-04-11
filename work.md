# RISC-V Sifter — Agent 工作文档

## 项目概述

RISC-V Sifter 是一个隐藏指令探测工具，灵感来自 x86 的 sandsifter。它在真实（或 QEMU 模拟的）RISC-V CPU 上逐条注入指令编码，通过信号捕获判断 CPU 是否接受了反汇编器不认识的编码，从而发现 undocumented 指令、反汇编器 bug、以及执行异常。

项目在 macOS 上通过 Docker + QEMU user-mode 跑 riscv64 Linux 容器完成全部扫描。

## 架构

```
sifter.py  (Python 前端)
  ├── 解析 CLI 参数
  ├── detect_isa_extensions() → 读 /proc/cpuinfo ISA 字符串
  ├── build_capstone_mode()   → ISA token → Capstone cs_mode bitmask
  ├── 计算 shard 分片 (exhaustive + -j N)
  ├── 为每个 shard 启动独立的 ./injector 进程 (传 --cs-mode N)
  ├── 每个 injector 有独立的 reader 线程读 stdout pipe
  ├── 结果汇聚到共享的 Tests 对象 (线程锁保护)
  ├── curses GUI 实时显示 (含 ISA 字符串、Capstone flag)
  └── 结果写入 data/sync, data/log

injector  (C 二进制，必须在 riscv64 上运行)
  ├── src/injector.c        — 主逻辑：内存笼、信号处理、指令迭代、输出
  ├── src/ptrace_runner.c   — ptrace 单步执行模式 (备选)
  ├── src/handler_trampoline.S — 汇编跳板，恢复 gp/tp 后 tail-call 到 C handler
  └── include/injector.h    — 类型定义和接口
```

## 关键文件速查

| 文件 | 作用 |
|------|------|
| `sifter.py` | Python 前端，整个控制面。约 1280 行。 |
| `src/injector.c` | C 注入器，约 1480 行。核心扫描循环、信号处理、raw 输出。 |
| `include/injector.h` | C 头文件，`config_t` / `state_t` / `result_t` 等结构体定义。 |
| `scripts/macos-docker-run.sh` | macOS 一键启动脚本。默认跑 `exhaustive -j 10`。 |
| `Dockerfile.riscv64` | 构建 riscv64 容器镜像。基于 `riscv64/ubuntu:22.04`，编译 Capstone 6 (next) + injector。 |
| `summarize.py` | 离线分析 `data/log` 的汇总工具。 |
| `data/sync` | 实时写入的 artifact 记录（`--sync` 启用时）。 |
| `data/log` | 退出时写入的最终结果。 |

## 二进制协议（sifter.py ↔ injector）

injector 以 `-R`（raw mode）运行时，stdout 输出固定 12 字节帧：

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0 | 1 | worker_id | 标识是哪个 worker 产出的结果 |
| 1 | 1 | disas_len | 反汇编器认为的指令长度 |
| 2 | 1 | disas_known | 反汇编器是否识别该编码 (0/1) |
| 3 | 1 | disas_illegal | Capstone 6: 能解码但 ISA 定义为非法 (0/1) |
| 4 | 4 | encoding | 指令编码 (uint32, little-endian) |
| 8 | 1 | valid | 结果是否有效 |
| 9 | 1 | length | 实际指令长度 |
| 10 | 1 | signum | 捕获到的信号号 (0=OK, 4=SIGILL, 14=SIGALRM, ...) |
| 11 | 1 | sicode | 信号 si_code |

Python 端 `RawResult.__init__` 解析这 12 字节。分类逻辑中 `disas_valid = disas_known and not disas_illegal`。

## 并行模型（当前实现）

当 `-j N > 1` 且模式为 exhaustive 时：

1. `sifter.py` 在 `main()` 里调 `compute_shards(N, begin, end)` 把 `[0, 0xFFFFFFFF]` 切成 N 段。
2. 为每段创建独立的 `Settings`（带 `-b`/`-e`）和 `Injector`（带 `--worker-id K`）。
3. 启动 N 个独立的 `./injector` 子进程，每个有自己的 stdout pipe。
4. `Poll` 类为每个 injector 启动一个 reader 线程。
5. 每个 reader 线程批量读取（64 帧/批），在线程内完成解析、分类、per-worker 计数，最后才短暂拿一次 `threading.Lock` 写全局 `Tests` 计数器。
6. 每个 reader 线程独立处理自己的 injector crash，只重启那一个进程。

`-j 1` 或非 exhaustive 模式退化为单进程单线程读取。

## injector 端的 `-j` 旧逻辑

`src/injector.c` 里仍保留了内部 `fork` + `output_mutex` 的多进程路径（`config.jobs > 1` 时触发）。当前 Python 端不会传 `-j > 1` 给 injector，所以这段代码不会执行。保留是为了兼容直接命令行调用 `./injector -j 4` 的场景。

`--worker-id N` 是一个长选项，设置 `worker_id` 全局变量，会写入每条 raw 输出帧的第 0 字节。只有 `config.jobs <= 1` 时该值生效；若 `config.jobs > 1`，`worker_id` 会被 fork 后的 `job` 索引覆盖。

`--resume-shards` 也保留但当前不再使用（Python 端直接按 per-worker 重启单个进程）。

## 构建与运行

```bash
# macOS: 一键构建镜像 + 运行
./scripts/macos-docker-run.sh              # 默认 exhaustive -j 10
./scripts/macos-docker-run.sh rebuild      # 强制重建镜像
./scripts/macos-docker-run.sh shell        # 进容器交互

# 容器内手动运行
./sifter.py --unk --dis --sync --tick -j 10 --rwx --filter-ext

# 本机无法直接 make（缺 capstone + 非 riscv64），编译在 Docker 内完成
```

## 已知注意事项

- **本机 `make` 会失败**：macOS 上没有 `capstone/capstone.h`，且不是 riscv64 架构。所有编译必须在 Docker 容器内完成（`Dockerfile.riscv64` 的 `RUN make clean all`）。测试代码改动是否编译通过，用 `./scripts/macos-docker-run.sh rebuild`。
- **Python 语法可以本机验证**：`python3 -m py_compile sifter.py` 可以在 macOS 上跑。
- **`strncpy` 警告**：`injector.c` 编译时有两条 `-Wstringop-truncation` 警告，来自 `disassemble_instruction` 里的 `strncpy`，非阻塞，历史遗留。
- **Dockerfile 平台警告**：`FROM --platform=linux/riscv64` 被 Docker 提示不应写死常量，不影响构建。
- **GUI 只支持 `q`（退出）和 `p`（暂停）**：没有运行时切换模式的功能。旧文档里提到的"按 M 切模式"已经不存在。GUI 显示 Mode/Jobs、Caps（开启的 Capstone flag）、ISA（完整原始字符串，多行折行）、Statistics、Workers 面板、Recent Anomalies。
- **exhaustive 全量扫描空间是 4G 编码**（`0x00000000` ~ `0xFFFFFFFF`），包含 16-bit 和 32-bit。跑完需要很长时间。
- **`--filter-ext`** 会过滤 Capstone 不认识但属于已知扩展的编码，避免 false positive。Capstone 6 能原生解码绝大部分扩展指令，此过滤器主要作为 fallback。`--strict-filter` 更严格，要求 `/proc/cpuinfo` 里确实报告了该扩展。
- **Capstone 6 仍为 Alpha**：构建和使用正常，但属于 pre-release。若遇到解码异常，可通过 `--cs-mode` 手动指定 mode bitmask 绕过。

## 最近完成的改动摘要

1. **修复模式参数链路**：`sifter.py` 之前在 `Injector._build_cmd()` 里会额外 prepend 一次 `-E`，导致实际命令出现 `-E ... -r` 两个模式参数冲突。已移除重复拼接。
2. **默认模式改为 exhaustive**：`scripts/macos-docker-run.sh` 和 `Dockerfile.riscv64` 的默认命令从 `--random` 改为 exhaustive + `-j 10`。
3. **raw 协议扩展 worker_id**：帧从 10 字节扩展到 11 字节，首字节为 `worker_id`。C 端新增 `--worker-id` CLI 参数。
4. **并行模型重构**：从"injector 内部 fork + 共享 stdout + output_mutex"改为"sifter.py 管理 N 个独立 injector 进程 + 独立 pipe + 独立 reader 线程"。
5. **性能优化**：reader 线程批量读取 64 帧，per-worker 计数无锁，全局计数器每批提交一次，锁持有时间降低约两个数量级。
6. **GUI worker 面板**：`-j N > 1` 时界面会列出每个 worker 的当前编码、测试数、各类 artifact 计数。
7. **per-worker crash recovery**：某个 worker 的 injector 挂了只重启那一个，不影响其他 worker。
8. **升级 Capstone 5.0.7 → 6.0 (next)**：Dockerfile 改用 `--branch next`，Python 绑定从源码安装。C 和 Python 两侧均启用全部 RISC-V 扩展 mode flag。
9. **raw 协议扩展 disas_illegal**：帧从 11 字节扩展到 12 字节，新增 `disas_illegal` 字段。Capstone 6 的 `cs_insn.illegal` 可以标识能解码但 ISA 定义为非法的指令。分类逻辑改为 `disas_valid = known && !illegal`，避免把"Capstone 认识但 ISA 非法"的指令误判为 disas bug。
10. **ISA 驱动的 Capstone 扩展选择**：不再硬编码开启所有扩展，而是从 `/proc/cpuinfo` 读 ISA 字符串，只开启 CPU 实际支持的扩展。通过 `build_capstone_mode(isa_exts)` 函数和 `ISA_TO_CS_MODE` 映射表，将 ISA token 转换为 Capstone mode flag。C 端通过新增 `--cs-mode N` CLI 参数接收 Python 计算的 bitmask，保证两侧一致。GUI 显示两段信息：`Caps:` 行列出实际开启的 Capstone flag 简称，`ISA:` 下方多行完整显示原始 ISA 字符串（按屏幕宽度自动折行）。`describe_capstone_mode()` 和 `CS_MODE_FLAG_NAMES` 提供 flag→简称的反查。

---

## 待优化：分片策略

### 当前问题

当前 `compute_shards()` 按编码数值连续切分 `[0, 0xFFFFFFFF]`。实际运行中观测到严重的负载不均：

- W05（覆盖 `0x80000000` ~ `0x99999999`）独占了绝大部分 hidden 指令和 exec fault，因为该范围的 opcode 位大量落在有合法扩展指令的编码空间。
- W(K) 和 W(K+5) 的计数几乎镜像（例如 W01≈W06, W04≈W09），因为它们仅差 bit 31（funct7 最高位），而大部分指令的合法性不取决于这一位。
- 负载不均意味着某些 worker 很快跑完空转，另一些 worker 独自扛着 hidden 热点区域。

### 推荐方案：交错分片（interleave）

不按连续范围切，而是按 `encoding % N == worker_id` 分配。每个 worker 均匀覆盖所有 opcode 空间。

**优点：**
- 每个 worker 看到的 opcode 分布完全一致，负载天然均衡。
- 消除镜像重复（W(K) 和 W(K+5) 不再做几乎相同的事）。
- 实现简单：injector 只需把 `encoding += 1` 改成 `encoding += N`，每个 worker 从不同起点出发。

**缺点：**
- 不能按范围做断点续跑（无法简单用 `-b` 恢复）。需要改用"已完成编码数"或"最后编码值"来恢复。
- 压缩指令（16-bit）的步进逻辑需要适配。

**实现思路：**
- 给 injector 加 `--stride N --offset K` 参数，使迭代变成 `next = current + stride`，起始为 `begin + offset`。
- Python 端为 worker K 传 `--stride N --offset K`。
- 恢复时传 `--begin <last_encoding>`，injector 从该编码继续按 stride 步进。

### 备选方案：按 opcode 分组

按 `bits[6:0]`（128 种 opcode）分组，将 opcode 组均匀分配给 N 个 worker。每个 worker 扫描若干 opcode 组的完整编码空间。

优点是每个 worker 的 opcode 种类数量相当，且保持范围连续性（方便续跑）。缺点是实现复杂度高，且 opcode 组内的 hidden 分布仍可能不均。

---

## Capstone 6.0 升级（已完成）

已从 Capstone 5.0.7 升级到 6.0 (next branch, 基于 LLVM-18)。

### 关键改进

1. **扩展覆盖大幅提升**：v5 只支持基础指令集（RV32/64IMAFDC），v6 支持所有 LLVM-18 已知的扩展。可用的 mode flag：
   - `CS_MODE_RISCV_V` — Vector
   - `CS_MODE_RISCV_A` — Atomic（含 Zabha/Zacas）
   - `CS_MODE_RISCV_ZBA` / `ZBB` / `ZBC` / `ZBS` — Bitmanip
   - `CS_MODE_RISCV_ZBKB` / `ZBKC` / `ZBKX` — Bitmanip Crypto
   - `CS_MODE_RISCV_FD` — 浮点 F+D（含 Zfh）
   - `CS_MODE_RISCV_ZFINX` — 整数寄存器浮点
   - `CS_MODE_RISCV_ZCMP_ZCMT_ZCE` — 压缩扩展（Zcb/Zcmp/Zcmt）
   - `CS_MODE_RISCV_ZICFISS` — CFI Shadow Stack
   - `CS_MODE_RISCV_SIFIVE` / `THEAD` / `COREV` — 厂商扩展
   - 注意：`CS_MODE_RISCV_BITMANIP`（1 << 13）虽然在 Python 绑定中定义，但**不在 `cs_open` 的允许掩码中**，使用会导致 `CS_ERR_MODE`。各 Zb* flag 已单独覆盖。

2. **非法指令检测**：`cs_insn.illegal` 标志——指令能解码但 ISA 定义为非法。已集成到 raw 协议（`disas_illegal` 字段）和分类逻辑。

3. **指令别名分离**：`is_alias` + `alias_id`（未使用，备用）。

### ISA 驱动的扩展选择

全开所有扩展会导致 Capstone 的解码范围超过 QEMU 的实现范围，产生大量假 disas bug。解决方案：

```
/proc/cpuinfo ISA 字符串
  → detect_isa_extensions()  →  {'i','m','a','f','d','c','zba','zbb',...}
  → build_capstone_mode()    →  CS_MODE bitmask (只包含 CPU 支持的扩展)
  → Python Disassembler(mode) + injector --cs-mode N
```

映射表 `ISA_TO_CS_MODE`（精确匹配）+ `ISA_PREFIX_TO_CS_MODE`（前缀匹配，用于 xthead*/xcorev*/xsfv* 厂商扩展）定义在 `sifter.py` 中。

ISA 字符串的可靠性：
- `/proc/cpuinfo` 的 `isa` 行是**下界近似**——报告的一定支持，没报告的不代表不支持。
- 在 QEMU user-mode Docker 中，ISA 字符串来自宿主 kernel 模拟层。
- 遗漏的扩展会以 hidden 的形式出现（正好是我们想发现的），比全开导致假 disas bug 好得多。

已知局限——Capstone mode flag 粒度比 ISA 扩展粗：
- `CS_MODE_RISCV_FD` 不仅覆盖标准 F/D，还会让 Capstone 解码 Zfh（半精度）、Zfa（额外 FP）等。QEMU 可能只实现了 F/D 但 ISA 字符串报告了 `f`+`d` → 我们开了 `CS_MODE_RISCV_FD` → Capstone 把 Zfh 指令视为合法 → CPU 拒绝 → 报 disas bug。
- 同理 `CS_MODE_RISCV_V` 覆盖所有向量指令，QEMU 可能声明了 `v` 但不完整实现所有变体。
- 这是 Capstone 6 flag 设计的固有限制，无法在 `cs_open` 层面解决。可选的后续优化：在 Python 侧对 disas bug 做二次过滤，用 `_identify_known_extension()` 检查是否属于 ISA 字符串未单独声明的子扩展。

### 构建

Dockerfile 从 `--branch 5.0.7` 改为 `--branch next`。Python 绑定从源码树 `bindings/python` 安装（PyPI 只有 v5）：

```dockerfile
RUN git clone --depth 1 --branch next https://github.com/capstone-engine/capstone.git /tmp/capstone && \
    cd /tmp/capstone && \
    cmake -B build -DCMAKE_INSTALL_PREFIX=/usr -DCAPSTONE_ARCHITECTURE_DEFAULT=ON && \
    cmake --build build -j$(nproc) && cmake --install build && ldconfig && \
    cd bindings/python && pip3 install --no-cache-dir . && \
    rm -rf /tmp/capstone
```

### 现有过滤器

`_identify_known_extension()` 和 `identify_known_extension()`（C 端）仍保留作为安全网。Capstone 6 能原生解码的编码不再需要这些过滤器，但如果某些编码仍漏过解码器，过滤器仍能兜底。docstring 已更新说明这是 fallback。
