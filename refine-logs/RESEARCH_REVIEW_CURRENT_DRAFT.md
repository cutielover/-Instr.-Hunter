# Current Draft Review

**Date**: 2026-04-15  
**Scope**: `PAPER_DRAFT_zh.md`, `PAPER_PLAN.md`, current Layer A + Layer C evidence  
**Method**: 按 `skills/skills-codex/research-review/SKILL.md` 的 reviewer 视角，对当前中文稿做风险与写作顺序复查。

## Top Risks

1. 叙事仍可能在“系统实现”“扩展框架”“完整硅后验证”之间漂移，导致读者误读为闭环已经完成。
2. 题目强调“微处理器隐藏指令分析工具”，但当前主证据仍集中在 Layer A 与生成器案例，需要持续强调适用边界。
3. `memcage` 与 `ptrace` 吞吐对比若写成“性能优势”而不是“当前匹配子集上的观测差异”，会被视为外推过度。
4. 若正文仍按 H/D/P/T/X 像闭环分类器那样叙述，将与 `R005/R007` 暴露出的缺口相矛盾。
5. Layer C 若铺陈过多，容易喧宾夺主，让读者误以为“生成器”是另一篇独立工作。

## Defensible Strengths

1. Layer A / B / C 分层评估框架清晰，并明确拒绝将任何单层偷换为“唯一真理”。
2. Layer A 稳定性与可审计日志证据已经足够扎实，可作为工程型论文底座。
3. 对反汇编器不是 authoritative semantics 的保守表述有助于增强论文可信度。
4. `memcage` / `ptrace` 双路径定位清晰，且已有可复核的 matched-subset 吞吐数据。
5. 对 illegal/trap 分类缺口的诚实报告，本身能增强论文可信度与系统局限讨论质量。

## Best Next Writing Order

1. 先改摘要与绪论，把“当前稿边界”写清。
2. 再改第 3 章，把“当前日志能自动输出什么、不能输出什么”钉死。
3. 再写第 4 章，把系统设计与 alternatives 组织得更像正式系统论文。
4. 再写第 5 章，把已运行路径与生成器边界写清。
5. 最后再扩第 6 章，保持 `6.1/6.2/6.5` 为当前主结果，`6.3/6.4` 为显式占位。

## Explicit Placeholders To Keep

- `§6.3` 实机复现
- `§6.4` Layer A / Layer B 对照
- 所有与板卡型号、CPU stepping、内核、对照子集定义有关的结果表格空位
- 任何跨环境差异归因的因果句

## Immediate Editing Guidance

1. 摘要与贡献条目要反复提醒“当前稿完成的是 Layer A + Layer C”。
2. 第 3 章要增加一张“标签 vs 当前日志自动输出能力”表。
3. 吞吐表格要附带重复次数、匹配子集与环境边界。
4. 生成器部分要始终保留“基线可运行，不等于 fully hardened”。
5. 结论中不得提前写入任何实机可复现性表述。
