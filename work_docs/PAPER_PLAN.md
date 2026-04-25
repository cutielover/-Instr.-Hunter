# Paper Plan

**Title**: 一种微处理器隐藏指令分析工具的设计与实现  
**One-sentence contribution**: 本文设计并实现一套面向 RISC 风格处理器的可扩展隐藏指令分析框架，将 `memcage` / `ptrace` 执行控制、信号与反汇编联合分类、以及配置驱动的新架构后端生成结合起来，在容器/QEMU 到真实硬件的分层验证路径上降低新 ISA 接入成本。  
**Venue**: 学位论文（按系统论文叙事组织，可后续压缩为 IEEE/DSN 风格）  
**Type**: empirical + method + systems  
**Date**: 2026-04-14  
**Page budget**: 学位论文不设硬限制；若压缩为会议稿，建议主文 10-12 页  
**Section count**: 7 章正文 + 摘要 + 附录

## Claims-Evidence Matrix

| Claim | Evidence | Status | Section |
|-------|----------|--------|---------|
| C1. 在声明的 Linux 环境下，RISC-V / AArch64 扫描主循环稳定运行 | R001-R003 短程扫描日志、worker 崩溃计数、收尾落盘记录 | Supported in Layer A | §4, §5, §6.1 |
| C2. 在匹配子集上，`memcage` 吞吐通常高于 `ptrace` | R008/R009 同 ISA、同范围、同 worker 数、3 次重复的吞吐对照 | Supported on matched Layer A subset | §6.2 |
| C3. 信号 + 反汇编联合分类可以稳定区分 H/D/P/T/X 等行为 | 分类决策表、代表性样例、人工复核说明；同时报告当前未覆盖/未出日志路径 | Partially supported | §3, §4, §6.1 |
| C4. 受控注入环境可验证探测链路端到端有效 | R004-R007 legal/privileged/control 样例；illegal/trap 路径暴露当前分类缺口 | Partially supported | §6.1 |
| C5. 至少一种真实硬件平台上的扫描结果可复现且可报告 | 板卡信息、`uname -a`、`git` 版本、`data/log` 归档 | Required | §6.3 |
| C6. 容器/仿真与实机在对齐子集上可比较，差异可归因 | A/B 对照表，差异分桶：模拟器 bug / 反汇编 bug / 特权 / 状态依赖 | Required | §6.4 |
| C7. 新 ISA 可通过“配置 + 生成 + 限定人工加固”接入 | `mips64el.json -> generate_arch_backend.py -> arch_mips.c`，quick smoke 记录，人工加固清单 | Strongly supported in Layer C | §5, §6.5 |

## Structure

### §0 Abstract

- **What we achieve**: 构建一套用户态隐藏指令分析工具，支持多 ISA、双后端执行控制、配置驱动扩展。
- **Why it matters / is hard**: 处理器不能被视为完全可信黑盒，但第三方缺少跨 ISA、可复现、可扩展的指令空间审计工具。
- **How we do it**: 通过写指-执行-信号-反汇编-分类主循环，将 `memcage` / `ptrace`、Capstone、统一协议与生成器整合到同一框架。
- **Evidence**: 层 A 正确性与吞吐、层 B 实机扫描、层 C MIPS 接入 case study。
- **Most remarkable result**: 至少填入 1 个数字化结论，例如吞吐倍数、对齐子集一致率、首个新 ISA 接入耗时。
- **Estimated length**: 150-250 词。
- **Self-contained check**: 摘要必须直接写出 ISA、平台层级、最强数字结果，不依赖正文解释。

### §1 Introduction

- **Opening hook**: 处理器实现缺陷、隐藏/异常指令、以及工具链与 CPU 认知不一致，会直接影响系统可靠性与安全性。
- **Gap / challenge**: 既有工作要么集中在 x86，要么缺少“多 ISA + 真实硬件 + 可扩展接入框架”的统一叙事。
- **One-sentence contribution**: 本文给出一套可扩展、可复现、可落地到实机的隐藏指令分析框架，并证明其不仅能扫描，还能降低新架构接入门槛。
- **Approach overview**: 用双后端保证单条可控执行，用信号与反汇编联合分类，用配置生成器把新架构接入标准化。
- **Key questions**:
  - Q1: 如何在用户态对任意指令字实现单条、可回收、可分类的执行？
  - Q2: 仿真/容器结果与真实硬件之间能否建立可信对照？
  - Q3: 新 ISA 的接入能否从“手工重写后端”收敛为“配置 + 生成 + 限定加固”？
- **Contributions**:
  - 我们实现了一个面向 RISC-V 与 AArch64 的用户态隐藏指令分析框架，统一了扫描循环、raw 协议与多 worker 编排。
  - 我们将 `memcage` 与 `ptrace` 组织为互补后端，并给出面向 H/D/P/T/X 的联合分类口径。
  - 我们实现了配置驱动的后端生成链路，并以 MIPS 首版接入证明“生成可用基线 + 人工 memcage 加固”的可行性。
  - 我们采用层 A/B/C 的评估框架，把容器/QEMU、真实硬件与扩展性验证纳入同一证据链。
- **Results preview**:
  - 层 A：已拿到主循环稳定性、受控 case 与 AArch64 matched subset 吞吐对照。
  - 层 B：待补至少一条真实硬件证据链。
  - 层 C：已拿到 MIPS 接入成本与生成边界的首批证据。
- **Hero figure**: 整体框架图，左侧是写指-执行-分类主循环，中间是 `memcage` / `ptrace` 双后端，右侧是 `arch-spec -> generator -> arch backend` 生成链，下方是 A/B/C 三层评估闭环。
- **Estimated length**: 学位论文中约 4-6 页；若压缩为会议稿约 1.5 页。
- **Key citations**:
  - `iScanU: A Portable Scanner for Undocumented Instructions on RISC Processors` (DSN 2020)
  - `Breaking the x86 ISA` (Christopher Domas, Black Hat 2017)
  - `Uncovering Hidden Instructions in Armv8-A Implementations` (HASP 2020)
- **Front-loading check**: 引言结尾前必须让读者看清“做了什么、为什么可信、为什么值得写”。

### §2 Related Work

- **Subtopics**:
  - x86 隐藏指令发现与搜索空间裁剪
  - RISC 用户态隐藏指令扫描
  - Arm 隐藏指令/软件诱发异常行为分析
  - 形式化 ISA 语义、合规测试与 authoritative ground truth
  - 反汇编器/模拟器与真实硬件不一致
- **Positioning**:
  - 相比 `sandsifter`，本文不追求 x86 变长 ISA 深度剪枝，而强调固定字长/混合字长 RISC 上的统一执行控制与可扩展接入。
  - 相比 `iScanU`，本文突出配置驱动生成链路、当前工具链更新、以及分层评估叙事。
  - 相比 `armshaker`，本文不是单 ISA 研究，而是通用框架与多 ISA 接入路线。
  - 相比 `Islaris`、Sail、`riscv-arch-test` 等形式化/合规工具，本文定位在第三方黑盒审计，而非 authoritative proof。
- **Minimum length**: 至少 4 段综合性综述，不按论文逐篇流水账写。
- **Organization rule**: 按方法家族和“ground truth 来源”分组，不按年份堆积。
- **Must-not-miss comparison axes**:
  - 可移植性与用户态安全模型
  - ground truth 依赖与局限
  - 是否包含实机证据
  - 新架构 bring-up 成本

### §3 Problem Definition and Design Goals

- **Notation / terms**:
  - `H`: CPU 接受但文档/反汇编不接受的隐藏或未文档化行为
  - `D`: 反汇编器认为合法，但 CPU 触发非法/不支持
  - `P`: 特权/权限相关行为
  - `T`: 预期 trap / illegal / timeout / guard 命中
  - `X`: 状态依赖或暂不能稳定归类的异常
- **Problem formulation**:
  - 给定目标 ISA、执行后端、扫描范围与运行环境，判定指令字在 CPU 与 ground truth 之间是否存在行为分歧，并输出可复现日志。
- **Operational definition**:
  - 必须在本章给出“信号、`si_code`、PC 变化、fault address、反汇编结果”到类别标签的决策表。
- **Coverage model**:
  - 报告扫描子空间、启用的 ISA 扩展、用户态约束、黑名单、未覆盖区域。
- **Design goals**:
  - G1: 单条可控执行
  - G2: 程序状态不被破坏
  - G3: 用户态可部署
  - G4: 多 ISA 统一编排
  - G5: 新 ISA 接入成本可量化
  - G6: 容器/仿真与实机证据可对照
- **Estimated length**: 1-2 章节点的过渡核心章节，承担术语冻结功能。

### §4 System Design

- **Architecture overview**:
  - Python 编排层
  - `injector_core`
  - `arch_*` 后端
  - raw 协议
  - `summarize.py` 与离线分析
- **Method description**:
  - 扫描循环：写指 -> 执行 -> 捕获信号 -> 反汇编 -> 分类 -> 输出
  - `memcage`：指令页、guard、altstack、寄存器沙箱、hang handler
  - `ptrace`：tracer/tracee、single-step、信号回收
  - 生成器：schema、模板、黑名单、Capstone mode、TODO 输出
- **Trade-off framing**:
  - `memcage` 偏吞吐与普适 MMU 环境
  - `ptrace` 偏可观察性、可移植 bring-up 与调试
  - 生成器可自动产出 baseline，但不能消灭 ISA 特化 hardening
- **Figures planned**:
  - Fig 1: 全框架英雄图
  - Fig 2: `memcage` 内存布局与信号路径
  - Fig 3: `ptrace` 工作流
  - Fig 4: 软件结构与 raw 协议/worker 编排
- **Estimated length**: 学位论文主体核心章节。

### §5 Implementation

- **What to include**:
  - RISC-V 后端实现与压缩/扩展支持边界
  - AArch64 后端实现与分类口径
  - MIPS 首版 case：`mips64el.json -> arch_mips.c`
  - Capstone 接入与模式配置
  - Docker / 交叉编译 / 脚本
- **Implementation evidence**:
  - 已存在的关键文件路径
  - 构建目标与 quick smoke 命令
  - `arch_mips.c.MEMCAGE_TODO.md` 作为“自动生成边界”的直接证据
- **Key point**:
  - 本章必须诚实写明“自动生成的是可编译基线，不是零人工 ISA 适配”。
- **Estimated length**: 适中，避免把实现细节淹没设计主线。

### §6 Evaluation

- **Research questions**:
  - RQ1: 分类链路是否正确？
  - RQ2: `memcage` 与 `ptrace` 的性能差异有多大？
  - RQ3: 实机结果是否可复现？
  - RQ4: 仿真/容器与实机差异能否归因？
  - RQ5: 生成器是否真的降低了新 ISA 接入成本？

- **Block A: Correctness / controlled verification**
  - 在修改 QEMU 或受控仿真环境中人为插入已知行为。
  - 至少覆盖 H/D/P/T 这四类。
  - 指标：precision、recall、误分案例，以及“当前分类器未显式暴露的路径”。
  - 产物：分类决策表 + 代表性日志。
  - 当前状态：legal/privileged case 已跑通；illegal/trap 样例暴露 taxonomy/logging gap，可作为正文局限性结果写入。

- **Block B: Throughput / matched backend comparison**
  - 相同 ISA、相同范围、固定 CPU 亲和性、重复多次。
  - 指标：insn/s、方差、hang/worker restart 次数。
  - 增加一个“why”分解：信号交付、上下文切换、`ptrace` stop 开销。
  - 当前状态：AArch64 `0x00000000-0x0003ffff`、`-j 10` 的 3 次重复已完成；当前 matched subset 上 `memcage` median 为 `9362.29 insn/s`，`ptrace` median 为 `8192.00 insn/s`。

- **Block C: Real hardware**
  - 至少 1 台 RISC-V 板卡作为必做项。
  - AArch64 实机作为增强项，若做不到则在导言中收缩 claim，不要拖到结论才承认。
  - 必填：板卡型号、stepping、内核、Capstone 版本、提交哈希、原始日志目录。

- **Block D: Layer A vs Layer B comparison**
  - 对齐子集必须预先定义，例如固定 `-b/-e`、固定 `--seed`、限定用户态扩展集合。
  - 差异分桶：
    - 模拟器 bug
    - 反汇编 bug
    - 特权/模式差异
    - 状态依赖 / 非确定性
  - 核心目标不是“完全一致”，而是“差异可解释”。

- **Block E: Generator case study**
  - 指标：
    - time-to-first-scan
    - 新增/修改 LOC
    - 必要人工 patch 数
    - 首次 smoke 成功前失败次数
  - Table 4 必须展示“工作量”，而不仅是“产出了几个文件”。
  - 当前状态：MIPS quick smoke 已完成；`arch-specs/mips64el.json` 为 36 行，生成的 `src/arch_mips.c` 为 497 行，`MEMCAGE_TODO` 中仍有 3 条人工检查项。

- **Negative-result framing**
  - 即使没有发现新的真实硬件隐藏指令，论文仍可成立，只要能报告：
    - 可靠的分类与对照方法
    - 仿真/反汇编差异数据集
    - 多 ISA bring-up 流程与成本量化

- **Appendix candidates**:
  - 长表、命令清单、完整环境记录、额外样例日志、未纳入主文的差异分析。

### §7 Discussion and Limitations

- **Limitations**:
  - 反汇编器不是 authoritative ISA 语义
  - 用户态看不到全部处理器状态空间
  - 特权指令和状态依赖隐藏行为可能漏检
  - qemu-user 下某些 ISA 的 `ptrace` 不可靠
  - `memcage` 的高质量实现仍依赖 ISA 特化 hardening
- **Discussion topics**:
  - “未发现新隐藏指令”不等于“CPU 完全正确”
  - 黑盒审计与形式化验证的互补关系
  - 生成器的价值是降低接入门槛，而不是替代架构工程

### §8 Conclusion

- **Restatement**: 总结工具、分层评估、生成器三条主线。
- **Limitations**: 重申用户态、ground truth、状态空间边界。
- **Future work**:
  - 更多 ISA
  - 更强的 authoritative ground truth 接入
  - 状态依赖扫描与语义推断
- **Estimated length**: 简洁收束，不新增未证明主张。

## Figure Plan

| ID | Type | Description | Data Source | Priority |
|----|------|-------------|-------------|----------|
| Fig 1 | Hero / architecture | 扫描循环 + 双后端 + 生成器 + A/B/C 评估的总览图；突出“比 iScanU 多了生成器与分层验证闭环” | manual | HIGH |
| Fig 2 | Diagram | `memcage` 指令页、guard、altstack、signal return path | `docs/memcage.md` + code | HIGH |
| Fig 3 | Diagram | `ptrace` tracer/tracee 执行与回收流程 | code/manual | HIGH |
| Fig 4 | Architecture | Python 编排、raw 协议、worker/manager、后端分层 | repo structure | MEDIUM |
| Fig 5 | Matrix / flow | 层 A/B/C 的实验与证据映射 | `docs/EVALUATION_MATRIX.md` | HIGH |
| Table 1 | Comparison table | `sandsifter` / `iScanU` / `armshaker` / 本文 在可移植性、ground truth、实机、扩展性上的差异 | manual | HIGH |
| Table 2 | Reproducibility table | 实机与容器环境字段、版本、命令、日志目录 | experiment metadata | HIGH |
| Table 3 | Main results | 吞吐、H/D/P/T/X 计数、A/B 子集一致率 | `data/log`, `summarize.py` | HIGH |
| Table 4 | Case study | MIPS 接入的输入、输出、人工工作量、首次可跑时间 | generator outputs | HIGH |

**Hero figure details**:

- 左半部分展示写指 -> 执行 -> 信号 -> 反汇编 -> 分类闭环。
- 中间展示 `memcage` 与 `ptrace` 两条执行路径，标注“吞吐”和“可观察性”差异。
- 右半部分展示 `arch-spec -> generator -> arch backend -> make target`。
- 底部增加 Layer A / B / C 三层，表明仿真不是终点，实机是主证据，扩展性是创新点闭环。
- Caption 必须明确比较对象：传统单架构/单后端工具 vs 本文的统一框架。

## Citation Plan

- Supporting notes:
  - See `LITERATURE_REVIEW.md` for grouped related-work synthesis.
  - Decoder-related claims should use **at least two** decoders in experiments; Capstone alone is not enough for strong truth claims.

- **§1 Intro**
  - `iScanU: A Portable Scanner for Undocumented Instructions on RISC Processors` — R. Dofferhoff et al., DSN 2020
  - `Breaking the x86 ISA` — Christopher Domas, Black Hat USA 2017
  - `Uncovering Hidden Instructions in Armv8-A Implementations` — Fredrik Strupe, Rakesh Kumar, HASP 2020

- **§2 Related Work: hidden-instruction discovery**
  - `Breaking the x86 ISA` — Christopher Domas, Black Hat USA 2017
  - `iScanU: A Portable Scanner for Undocumented Instructions on RISC Processors` — DSN 2020
  - `Uncovering Hidden Instructions in Armv8-A Implementations` — HASP 2020

- **§2 Related Work: formal / authoritative semantics**
  - `Islaris: Verification of Machine Code Against Authoritative ISA Semantics` — Michael Sammler et al., PLDI 2022
  - Sail / RISC-V compliance ecosystem papers or official docs `[VERIFY]`
  - `riscv-arch-test` / ACT framework docs `[VERIFY]`

- **§2 Related Work: infrastructure / ground truth**
  - QEMU original paper `[VERIFY venue/year before final write]`
  - Capstone paper or official project citation `[VERIFY]`

- **§4-§6 Method / Evaluation**
  - ISA manuals for RISC-V / ARM / MIPS
  - QEMU / Capstone bug reports or issue references when discussing concrete mismatches `[VERIFY]`

## Reviewer Feedback

来自外部 reviewer 视角的初轮结论：

- **Logical flow**: 8/10
- **Claim-evidence alignment**: 6/10
- **Missing experiments / analysis**: 5/10
- **Positioning vs prior work**: 6/10
- **Feasibility**: 7/10
- **Front-matter strength**: 6/10

最小修复项已并入本计划：

- 在 §3 提前冻结 H/D/P/T/X 的操作性定义与决策表。
- 在 §6 增加“受控注入 correctness 实验”，而不是只做吞吐。
- 在 §6 明确 coverage 模型与未覆盖空间。
- 对 C2 采用严格匹配协议，避免 `memcage > ptrace` 被质疑。
- 对 C5/C6 绑定具体板卡、具体子集和具体差异分桶。
- 对 C7 增加量化 bring-up 成本，而不是仅展示生成物。
- 明确“即使无新隐藏指令发现，论文仍可依靠差异数据集与扩展框架成立”。

进一步收敛后的执行文档：

- `LITERATURE_REVIEW.md`
- `refine-logs/REVIEW_SUMMARY.md`
- `refine-logs/EXPERIMENT_PLAN.md`
- `refine-logs/EXPERIMENT_TRACKER.md`

## Next Steps

- [x] 用 `research-lit` 扩展 Related Work，建议参数：`— sources: web, semantic-scholar`
- [x] 用 `experiment-plan` 把 §6 细化为 must-run / nice-to-have 实验清单
- [x] 用 `research-review` 再做一次 claims 与 chapter flow 的外部批评
- [ ] 在拿到首批结果后，用 `result-to-claim` 缩紧可写的主张
- [x] 在拿到首批结果后，已先手工缩紧当前可写主张（待 Layer B 后再收紧一次）
- [ ] 开始生成中文初稿骨架（实机章节保留占位）
