# Evaluation 实验矩阵（完整方案：层 A 仿真 + 层 B 实机 + 层 C 扩展框架）

本文档与 `docs/PAPER_OUTLINE_zh.md` 第 6 章、`NARRATIVE_REPORT.md` §4、`STORY.md` 对齐。  
**学位论文 / 完整版正文**：**层 B（实机）为必要证据**；层 A 用于开发效率、吞吐与受控验证；层 C 支撑「通用生成框架」类贡献。

---

## 0. 三层结构

| 层级 | 目的 | 在论文中的角色 |
|------|------|----------------|
| **层 A** | Docker / QEMU user / 受控注入 | 吞吐对比、管线正确性、与层 B **对照**的基线 |
| **层 B** | **真实 RISC-V / AArch64 Linux 硬件** | **主结论**：硅片上的分类统计与可复现记录 |
| **层 C** | 配置 + 代码生成 → 新 ISA 可跑 | 扩展性 / 创新点验证（如 MIPS case） |

**原则**：不在正文把「仅在 QEMU 里跑过」写成最终科学结论；**实机章节与对照表**承担对外主张。

---

## 1. 环境矩阵（实验前填写）

| ID | 层级 | 宿主 / 硬件 | OS / 容器 | 二进制架构 | 备注 |
|----|------|-------------|-----------|------------|------|
| E1-RV-QEMU | A | macOS arm64 等 | `linux/riscv64` 镜像 | riscv64 | 容器内常含指令级仿真 |
| E2-A64-CTR | A | macOS arm64 等 | `linux/arm64` | aarch64 | 多为原生执行 |
| **E3-RV-HW** | **B** | **[TBD] 板卡型号** | **原生 Linux，无 Docker 或可选** | riscv64 | **填 CPU、步进、内核** |
| **E4-A64-HW** | **B** | **[TBD] 设备** | **原生 Linux** | aarch64 | 可选第二实机腿 |
| E5-MIPS-GEN | C | 视实现 | 视实现 | mips 等 | 生成框架 case study |

---

## 2. 对比方法（Comparison factors）

| 因子 | 水平 | 论文问题 |
|------|------|----------|
| **层 A vs 层 B** | 同编码子集或同 random seed | 仿真/容器 vs 硅片：分类一致性与差异来源 |
| 执行后端 | memcage vs `--ptrace` | 性能；结果分布抽样对比 |
| 扫描模式 | exhaustive 子区间 vs random（固定 seed） | 吞吐表与长时间实机实验的可行性 |
| W^X | 默认 vs `--rwx` | 主要层 A / QEMU；实机按内核策略记录 |
| 并行度 | `-j 1` vs `-j N` | 加速比（实机注意散热与 throttling） |

---

## 3. 指标（Metrics）

| 指标 | 定义 | 适用层 |
|------|------|--------|
| 吞吐 | insn/s | A（主）；B（可选子集，避免长时间跑满空间） |
| H/D/T/X 计数 | README 分类 | A、B |
| **A↔B 一致率** | 同集合上类别相同比例 / 差异清单 | **B 对照** |
| 崩溃率 | worker 重启等 | A、B |
| **接入成本（层 C）** | 从新建配置到首次成功 raw 输出的时间或新增 LOC | C |

---

## 4. 最小可跑命令（层 A — 开发与基线）

项目根目录；详见历史脚本与 `README.md`。

### 4.1 RISC-V 容器（E1）

```bash
./scripts/macos-docker-run.sh
```

短程示例：

```bash
./sifter.py --unk --dis --sync --no-gui --random --seed 1 -n 50000 --rwx --filter-ext
```

memcage vs ptrace：

```bash
./sifter.py --unk --dis --sync --no-gui --random --seed 1 -n 20000 --rwx --filter-ext
./sifter.py --unk --dis --sync --no-gui --random --seed 1 -n 20000 --rwx --filter-ext --ptrace
```

汇总：

```bash
./summarize.py data/log --csv results/eval_layerA_rv.csv
```

### 4.2 AArch64 容器 / 环境（E2）

```bash
make injector_aarch64
./sifter.py --arch aarch64 --unk --dis --sync --no-gui -j 4 --random --seed 1 -n 20000
```

---

## 5. 层 B — 实机实验（论文必填项的操作清单）

1. **记录表 6-2**（`PAPER_OUTLINE_zh.md`）：板卡、CPU stepping、内核、`git`、Capstone 构建。  
2. **与层 A 对齐**：选定固定 `-b/-e` 或固定 `--seed` + `-n`，在 E3（及可选 E4）上运行**相同**参数（若架构相同）；AArch64 仅与 E2/E4 对齐。  
3. **归档**：每次运行复制 `data/log` → `results/eval_hw_<isa>_<date>/`。  
4. **讨论**：逐项解释 A/B 差异（user-mode、特权、计时噪声等）。

实机命令形态与容器内**相同**（直接运行 `sifter.py` + 本机编译的 `injector`）；差异在**构建方式与环境变量**，需在附录记录。

---

## 6. 层 C — 扩展框架验证

- **输入**：配置文件路径与版本。  
- **输出**：生成文件列表、`make` 目标、首次成功运行的命令行。  
- **指标**： wall-clock 接入时间；自动生成 LOC vs 手工 LOC 比例（诚实写）。  
- **最低标准**：新 ISA 下至少一次与层 A 或 B 相当的短程扫描 + `summarize.py` 输出。

---

## 7. 建议论文表映射

- **表 6-1**：同时包含层 A、层 B（及层 C 若单独成实验）行；**「环境层」列必填**。  
- **表 6-2 / 6-3**：实机 vs 仿真分列。  
- **对照小节**：层 A vs 层 B 子集结果表或附录长表。

---

## 8. 检查清单

- [ ] 层 A：memcage vs ptrace；random 短跑可重复  
- [ ] **层 B：至少一种 ISA 实机完整记录（推荐 RISC-V）**  
- [ ] **层 B：与层 A 可对照子集 + 差异分析**  
- [ ] 层 C（若作创新点）：配置 + 生成 + 首跑证据  
- [ ] 所有 `data/log` 已版本化归档并对应 `git` commit
