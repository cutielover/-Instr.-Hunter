# Review Summary

**Date**: 2026-04-14  
**Scope**: `PAPER_PLAN.md`, `STORY.md`, `NARRATIVE_REPORT.md`, and pre-experiment paper defensibility  
**Review mode**: external reviewer style, conservative systems/security thesis standard

## Core Verdict

当前计划的故事线已经成形，但在真正开始写章节之前，仍有一个关键问题没有闭环：**你还没有拿到足够强的 Layer B 证据来支撑“完整方案”中的主结论**。因此，下一阶段的目标不应再是扩写大纲，而应是尽快跑出一组最小但可信的实验包，优先验证：

1. 这套框架在指定 Linux 环境中是否安全、稳定、可重复。  
2. 受控注入能否证明分类链路本身是可信的。  
3. 至少一台真实硬件是否能稳定复现结果并生成可审计 artifact。

## Top Findings

1. **C5 / C6 风险最高**  
   当前最脆弱的地方不是设计，也不是实现，而是“实机可复现统计”和“A/B 差异可解释”还没拿到数据。没有这两项，论文会退化成工具说明，而不是完整研究闭环。

2. **Capstone 不能被写成真正的 ground truth**  
   既然论文本身就在讨论 decoder / ISA / CPU 之间可能不一致，那么单独依赖 Capstone 做真值判定会被质疑为循环论证。必须引入第二解码器，或至少显式加入 `UNKNOWN` 分类与人工升级规则。

3. **安全隔离与 containment 还需要更强表述**  
   `memcage` / `ptrace` 用户态执行任意指令天然会触发 reviewer 对 host corruption、越界写、死循环和资源泄漏的担心。需要把 safety invariant 作为实验前置条件。

4. **生成器 claim 必须缩到“有界自动化”**  
   目前 C7 最可信的写法不是“配置就能自动支持新 ISA”，而是“配置驱动生成可运行基线，并显著减少 bring-up 工作量；memcage 仍需 ISA 特化 hardening”。

5. **QEMU 是 Layer A 比较工具，不是验证 oracle**  
   特别是在 MIPS qemu-user + ptrace 已知不稳定的条件下，任何 Layer A 发现都必须默认先怀疑是 emulator / decoder artifact，而不是 CPU truth。

## Claim Prioritization

### Primary

- **C1**：在声明的 Linux 环境下，扫描主循环稳定且可复现。  
- **C4**：受控注入环境能验证端到端探测与分类链路。  
- **C7（收缩后）**：新 ISA 可通过“配置 + 生成 + 有界人工加固”接入。

### Supporting

- **C2（严格收缩）**：在匹配子集上，`memcage` 吞吐通常高于 `ptrace`。  
- **C6**：Layer A 与 Layer B 在对齐子集上可比较，且差异可分桶解释。

### Conditional / Appendix-only Unless Data Is Clean

- **C3**：分类方法帮助 triage，但不应写成单独的 truth oracle。  
- **C5**：只有在完成至少一组干净的实机复现实验后，才能进入正文主 claim。

## Minimum Pre-Experiment Package

### D0. Lock taxonomy and scope

- 明确定义 `H / D / P / T / X / UNKNOWN`。  
- 写清哪些状态空间不在当前论文范围内：secure world、firmware、MMU-off、microcode-only 路径等。

### D1. Safety invariants and harness checks

- 每条测试都有超时与 watchdog。  
- 验证 `SIGILL / SIGSEGV / SIGBUS / SIGTRAP` 路径不会把 harness 打坏。  
- 用 canary / guard page 检查“tested instruction 不会改坏 cage 外状态”。

### D2. Auditable artifact bundle

对每个 interesting case 自动保存：

- 原始字节
- 主解码器结果
- 第二解码器结果
- signal / `si_code`
- fault address / PC
- register snapshot
- 最小复现实验命令
- 环境指纹（kernel / QEMU / commit / CPU model）

### D3. Minimal Layer A correctness

至少覆盖以下 4 类受控 case：

- 明确合法指令
- 明确非法编码
- 用户态执行特权指令
- 明确触发 trap 的访存行为

要求：多次重复下分类稳定，无明显 flakiness。

### D4. Minimal matched throughput test

- 只选一条既支持 `memcage` 又支持 `ptrace` 的 ISA 路径。  
- 固定范围、固定 CPU affinity、重复多次。  
- 输出 median / p95，而不是只给单次最好成绩。

### D5. Minimal Layer B hardware run

- 先只拿 **1 台你能稳定控制和重启的 RISC-V 板卡**。  
- 不追求全空间，先跑一组可复现的小规模 campaign。  
- 三次复跑结果中，top interesting cases 要稳定出现。

## Results-to-Claims Gate

### Scenario A: 实机没有发现新的隐藏指令

可保留：

- C1
- C2（收缩）
- C4
- C7

需要改写：

- C5 改成“在给定预算内未观察到新的硬件隐藏行为”
- C6 仅在 A/B 对照确实有价值时保留

论文成立方式：

- 强调 negative result 的覆盖度、artifact、和差异分析价值。

### Scenario B: 实机只发现 emulator / disassembler mismatch

可保留：

- C1
- C2（收缩）
- C4
- C6
- C7

需要改写：

- C3 改成“多工具差异检测与 triage”
- C5 改成“可复现 mismatch 统计”，而不是“发现隐藏指令”

论文成立方式：

- 把贡献重心转向“黑盒审计框架 + 差异归因 + 工具链偏差数据集”。

### Scenario C: 实机发现可信的 undocumented behavior

可强化：

- C5
- C6

保留但谨慎：

- C3 仍不能写成 truth oracle

论文成立方式：

- 需要高标准 triage：跨重启复现、排除 privilege / undefined / tool bug、保留完整 artifact bundle。

## Writing Fixes Needed Before Results Arrive

- 主文 Related Work 必须加入 differential disassembly / decoder disagreement 叙事。  
- `PAPER_PLAN.md` 中 C3 的语言应继续收缩，避免“分类器即真值”口吻。  
- 主实验章节必须提前设计一张 artifact schema 表，而不是等结果出来再整理。  
- 若短期内拿不到 AArch64 实机，应尽早把它从主 claim 收缩为增强项。

## Immediate Next Action

直接进入实验计划拆解，不再继续扩展论文框架。实验计划的目标应是：

- 先 unlock `C1 + C4 + D5`
- 再决定 `C2` 是否值得写成主文结果
- 最后用 Layer C 证明 `C7`
