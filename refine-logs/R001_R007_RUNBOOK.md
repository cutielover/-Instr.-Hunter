# R001-R007 Runbook

本文件把 `refine-logs/EXPERIMENT_TRACKER.md` 中 `R001-R007` 这组最小 sanity / controlled runs 具体化为：

- 输出目录约定
- 命令模板
- run 后需要保留的文件

目标：先锁定 `C1` 与 `C4` 的前置条件，再决定是否继续跑主实验。

## 1. Output Directory Convention

每次 run 统一使用：

```text
results/preflight/<RUN_ID>_<label>_<timestamp>/
```

例如：

```text
results/preflight/R001_riscv-memcage-signal-20260414-153000/
```

目录内统一放：

```text
results/preflight/<RUN_ID>_<label>_<timestamp>/
  cmd.txt
  stdout.txt
  stderr.txt
  data/
    log
    run.json
    sync
    tick
    last
  artifacts/
```

## 2. Common Procedure

所有 `R001-R007` 都按这个流程执行：

1. 创建 run 目录  
2. 把命令原样写入 `cmd.txt`  
3. 执行命令，stdout/stderr 分流  
4. 将 `data/` 下产生的文件复制到 run 目录  
5. 对 interesting case 建立 `artifacts/case_xxxxxx/`，并按 `docs/ARTIFACT_SCHEMA.md` 归档

推荐模板：

```bash
RUN_ID="R001"
LABEL="riscv-memcage-signal"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="results/preflight/${RUN_ID}_${LABEL}_${STAMP}"

mkdir -p "$RUN_DIR/data" "$RUN_DIR/artifacts"
printf '%s\n' '<COMMAND>' > "$RUN_DIR/cmd.txt"

<COMMAND> > "$RUN_DIR/stdout.txt" 2> "$RUN_DIR/stderr.txt"

cp data/log data/run.json data/last "$RUN_DIR/data/" 2>/dev/null || true
cp data/sync data/tick "$RUN_DIR/data/" 2>/dev/null || true
```

## 3. Secondary Decoder Convention

对每个 interesting case，第二解码器统一使用：

```bash
./scripts/secondary-decode.sh <arch> <hex-bytes>
```

示例：

```bash
./scripts/secondary-decode.sh riscv 13050500 > secondary_decode.txt
```

## 4. R001-R003: Sanity / Safety Runs

### R001 — RISC-V memcage signal-path sanity

目的：

- 验证最基本的 memcage 路径可以稳定返回
- 看 `data/run.json`、`data/log` 是否正常生成

命令模板：

```bash
./sifter.py --arch riscv --unk --dis --sync --no-gui --exhaustive -b 0x00000000 -e 0x00000fff --rwx --filter-ext
```

说明：

- 这里使用一个很小的 bounded exhaustive 区间，而不是 random + 样本上限，因为当前顶层 CLI 没有直接暴露随机样本数参数。
- 在 Layer A / QEMU user 环境下保留 `--rwx`，避免 W^X 导致的额外噪声。
- `--filter-ext` 作为 RISC-V Layer A 基线默认开启，但同一 run 必须检查 `raw_hidden_count`、`hidden_count`、`filtered_hidden_count` 是否一并写入 `data/run.json`。

最少保留：

- `data/run.json`
- `data/log`
- `stdout.txt`
- `stderr.txt`

重点检查：

- `data/log` 中 `# Raw Hidden`、`# Hidden`、`# Hidden Filtered Out`
- `data/run.json` 中 `raw_hidden_count`、`hidden_count`、`filtered_hidden_count`、`filtered_hidden_ext_counts`

### R002 — RISC-V memcage containment sanity

目的：

- 检查短程随机扫描下是否出现明显 worker restart / hang / 非预期崩溃
- 作为 canary / guard 检查前的基础回归 run

命令模板：

```bash
./sifter.py --arch riscv --unk --dis --sync --no-gui --exhaustive --no-compressed -b 0x00010000 -e 0x00010fff --rwx --filter-ext --stall-timeout 30
```

重点观察：

- `data/run.json` 中 worker crash / restart 信息
- `data/run.json` 中 raw/filtered hidden 统计是否自洽
- `stderr.txt` 中是否出现异常终止

### R003 — ptrace sanity on known-good ISA path

目的：

- 确认至少一条 `ptrace` 路径可用
- 为后续 `R008/R009` throughput matched subset 做前置检查

优先选择：

- **AArch64 Linux 原生环境**
- 若没有 AArch64 实机/环境，则选 **RISC-V 已知支持 ptrace 的环境**

命令模板（AArch64）：

```bash
./sifter.py --arch aarch64 --unk --dis --sync --no-gui --exhaustive -b 0x00000000 -e 0x0000ffff --ptrace
```

若使用 RISC-V：

```bash
./sifter.py --arch riscv --unk --dis --sync --no-gui --exhaustive -b 0x00020000 -e 0x00020fff --ptrace --rwx
```

判定标准：

- 命令能稳定结束或稳定产生日志
- 不出现 “ptrace unavailable / TRACEME not implemented” 一类环境错误

## 5. R004-R007: Controlled Classification Runs

这四个 run 的核心目标不是“大规模扫描”，而是让分类链路在**已知预期 case**上稳定输出。

统一约定：

- 每个 run 使用独立输出目录
- 每个 run 至少保存 1 个 case 的完整 artifact bundle
- 所有 case 都必须保存第二解码器输出

### R004 — Controlled legal case

目的：

- 验证“明确合法编码”不会被误分到 `D/H/X`

建议 case：

- RISC-V `addi a0, a0, 0` (`13050500`)
- AArch64 `mov x0, #1` (`200080d2`)

命令模板：

```bash
./sifter.py --arch riscv --unk --dis --sync --no-gui --exhaustive -b 0x00050513 -e 0x00050513 --rwx --filter-ext
```

artifact 额外操作：

```bash
./scripts/secondary-decode.sh riscv 13050500 > "$RUN_DIR/artifacts/case_000001/secondary_decode.txt"
```

### R005 — Controlled illegal case

目的：

- 验证“明确非法编码”会稳定进入非法/未知路径，而不是被接受

建议策略：

- 选一个在 Capstone 与第二解码器都不认可、且你已知在当前 Layer A 环境下应触发 `SIGILL` 的编码
- 先小范围试跑，不要一次假定某个编码一定非法

命令模板：

```bash
./sifter.py --arch riscv --unk --dis --sync --no-gui --exhaustive -b <ILLEGAL_HEX> -e <ILLEGAL_HEX> --rwx
```

备注：

- 这里的 `<ILLEGAL_HEX>` 需要先由你选一条确定样本；本 runbook 先把流程冻结，不擅自编造最终编码。

### R006 — Controlled privileged-in-user case

目的：

- 验证特权指令在用户态下的分类、信号、`si_code` 和 `UNKNOWN/P` 路径

命令模板：

```bash
./sifter.py --arch <ARCH> --unk --dis --sync --no-gui --exhaustive -b <PRIV_HEX> -e <PRIV_HEX>
```

记录要求：

- `meta.json` 中必须填 `working_classification`
- 若当前 `sifter.py` 还没有单独输出 `P`，先标为 `UNKNOWN`，在 `notes.md` 写明“privileged candidate”

### R007 — Controlled memory-trap case

目的：

- 验证“CPU 接受编码，但随后触发访存 trap”的路径能稳定产出 artifact

命令模板：

```bash
./sifter.py --arch <ARCH> --unk --dis --sync --no-gui --exhaustive -b <TRAP_HEX> -e <TRAP_HEX> --rwx
```

成功标准：

- 不只是 `data/log` 出现一条记录
- 还必须保存最小复现脚本、第二解码器输出、以及人工说明这更像 `X` 还是 `UNKNOWN`

## 6. Exit Criteria For R001-R007

只有满足以下条件，才进入后续 `R008+`：

- `R001-R003` 至少确认 1 条 memcage 路径和 1 条 ptrace 路径可稳定使用
- `R004-R007` 至少覆盖 legal / illegal / privileged / trap 四类 controlled case
- 每类至少有 1 个 case 形成完整 artifact bundle
- 第二解码器流程已打通

若不满足，上层实验计划不应继续推进。
