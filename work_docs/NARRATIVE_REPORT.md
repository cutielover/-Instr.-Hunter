# NARRATIVE_REPORT.md — 研究叙事与证据索引（完整方案版）

> 论文叙事对齐**完整目标**：**实机验证** + **通用生成/配置框架**；本文件用于 `paper-plan` 抽取大纲与 Claims–Evidence 表。  
> `[TBD]` 由实验完成后填写；**层 B（实机）为学位论文/完整版正文的关键证据，不可缺省**（除非培养单位明确允许仅仿真，需单独说明）。

---

## 元数据

| 字段 | 内容 |
|------|------|
| 工作标题 | 一种微处理器隐藏指令分析工具的设计与实现 |
| 代码仓库 | riscv-sifter（本目录） |
| 参考论文 | iScanU: A Portable Scanner for Undocumented Instructions on RISC Processors |
| 文档版本 | 0.3 完整方案 + 生成器落地 |
| 最后更新 | 由作者在定稿前填写 |

---

## 1. 问题陈述（Problem）

- CPU 与工具链/规范对指令字的认知可能不一致；需要可自动化、可审计、**可扩展到多 ISA** 的手段。  
- **论文目标范围**：不仅证明「代码能跑」，而且包括**真实处理器上的测量**与**新架构接入成本可量化**的扩展框架。

---

## 2. 方法摘要（Method）

- **分析准则**：信号（含 `si_code`）+ Capstone（含 `illegal`）联合分类；定义见 `README.md`、`docs/memcage.md`。  
- **执行后端**：memcage（默认高性能）；ptrace（通用性、生成友好）。  
- **扩展框架**（设计与实现进度见仓库）：配置 schema → 生成 C/Python 片段或完整桩 → 与 `injector_core` 链接；memcage 路径文档化「需手工」部分。

---

## 3. 实现与计划清单（工程状态）

> 写作时区分「已实现」「部分实现」「计划」；答辩材料与论文目录仍按完整方案组织。

- [x] 共享 `injector_core.c` + `arch.h`  
- [x] RISC-V：memcage + 跳板 + ptrace  
- [x] AArch64 Linux：memcage + 跳板 + ptrace ops  
- [x] Python：`sifter.py`、raw 协议、多 worker、`summarize.py`  
- [ ] **实机实验**：RISC-V 板卡 / AArch64 设备 [TBD 型号]  
- [x] **配置 + 代码生成工具**（模板化）`tools/generate_arch_backend.py`  
- [x] **MIPS**（第三 ISA 首版）`arch-specs/mips64el.json` → `src/arch_mips.c`  
- [ ] MIPS memcage ISA 特化 hardening（见 `src/arch_mips.c.MEMCAGE_TODO.md`）  
- [ ] x86：相关工作讨论，**不强制实现**

**关键文件索引**

| 主题 | 路径 |
|------|------|
| 分类与使用 | `README.md` |
| memcage | `docs/memcage.md` |
| AArch64 | `docs/AARCH64_LINUX.md` |
| 生成器 | `tools/generate_arch_backend.py` |
| MIPS 规格 | `arch-specs/mips64el.json` |
| MIPS 后端与 TODO | `src/arch_mips.c` / `src/arch_mips.c.MEMCAGE_TODO.md` |
| 评估矩阵 | `docs/EVALUATION_MATRIX.md` |
| 论文目录 | `docs/PAPER_OUTLINE_zh.md` |

---

## 4. 实验与证据（分层）

### 4.1 层 A：仿真 / 容器 / 受控注入

| 项目 | 值 |
|------|-----|
| 目的 | 吞吐、memcage vs ptrace、管线正确性、可复现开发基线 |
| 复现 | `docs/EVALUATION_MATRIX.md` §4 |
| 当前证据 | 已有 MIPS quick 记录（`data/log`：`Tested` 非零，示例一次约 `22000`） |

### 4.2 层 B：实机 RISC-V（论文主证据）

| 项目 | 值 |
|------|-----|
| 硬件 | [TBD] 型号 / stepping / SoC |
| 系统 | [TBD] Linux 版本、`uname -a` |
| 命令 | [TBD] 与仓库 CLI 一致 |
| `data/log` 归档 | [TBD] `results/eval_hw_riscv_*/` |

### 4.3 层 B：实机 AArch64（若纳入正文）

| 项目 | 值 |
|------|-----|
| 硬件 | [TBD] |
| 结果归档 | [TBD] |

### 4.4 层 C：扩展框架 / 新 ISA case study

| 项目 | 值 |
|------|-----|
| 配置输入 | `arch-specs/mips64el.json` |
| 生成物 | `src/arch_mips.c` + `src/arch_mips.c.MEMCAGE_TODO.md` |
| 首跑成功记录 | `scripts/macos-docker-run-mips64.sh quick`，产出 `data/log` |
| 边界 | qemu-user 下 MIPS ptrace 可能不可用；容器优先 memcage，ptrace 对照放实机 |

### 4.5 层 A vs 层 B 对照子集

| 子集定义 | 一致率 / 差异说明 |
|----------|-------------------|
| [TBD] 相同 `-b/-e` 或相同 random seed + 范围 | [TBD] |

---

## 5. Claims–Evidence 矩阵

| Claim ID | 陈述 | 证据 § | 状态 |
|----------|------|--------|------|
| C1 | 层 A 下主循环稳定 | 4.1 | in_progress |
| C2 | memcage 吞吐高于 ptrace | 4.1 | pending |
| C3 | 分类符合设计 | 4.1 + 样例 | in_progress |
| C4 | 受控注入有效 | 4.1 | pending |
| **C5** | **实机扫描完成且可复现** | **4.2 / 4.3** | **required** |
| **C6** | **实机与仿真可对照** | **4.5** | **required** |
| **C7** | **配置+生成接入新 ISA** | **4.4** | partially_supported（mips64el 首版） |

---

## 6. 伦理与表述注意

- 硅片级结论需绑定**具体步进与实验设置**；避免过度推断。  
- 区分工具链 bug、模拟器 artifact 与 **CPU 实现行为**。  
- 负责任披露与学校对敏感编码展示的要求。
