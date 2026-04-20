# Result-to-Claim Gate

**Date**: 2026-04-15  
**Scope**: `R001-R009`, `R014-R015`, current thesis draft posture  
**Method**: 根据 `skills/skills-codex/result-to-claim/SKILL.md`，整理实验结果后交由外部 reviewer 风格判断，再回填 working claims。

## Overall Verdict

当前已完成实验支持一篇以 **Layer A + Layer C** 为主体的中文系统/工程型论文初稿：可以主张 Linux 容器环境下的主循环稳定、AArch64 匹配子集上的 `memcage`/`ptrace` 吞吐差异，以及配置驱动生成链路能够产出可运行 MIPS 基线；但**尚不能**主张实机可复现性、Layer A/B 差异可归因、或 H/D/P/T/X 分类闭环已经完全建立。

## Claim Assessment

| Claim | Verdict | Current support | Current gap |
|------|---------|-----------------|-------------|
| C1 | yes | `R001-R003` 已表明当前 Linux Layer A 环境下主循环、日志与收尾是稳定的 | 仍需补真实硬件上的稳定性说明，但不影响 Layer A 结论 |
| C2 | partial | `R008/R009` 在 AArch64 匹配子集上表明 `memcage` median 吞吐高于 `ptrace` | 范围仍局限于单架构、单窗口、3 次重复，不能外推为普遍规律 |
| C3 | partial | RISC-V 运行已能稳定产出 `D/T/X` 及 raw/filter hidden；AArch64 受控样例能表达 legal/privileged | `R005/R007` 暴露 illegal-at-test 与 trap/exec-fault 未进入 artifact 日志 |
| C4 | partial | `R004` 与 `R006` 支持部分 controlled path | `R005/R007` 说明端到端“注入-观测-分类-落盘”链路仍有缺口 |
| C5 | no | 无 | 尚无任何实机结果 |
| C6 | no | 无 | 尚无 Layer B 结果，无法做 A/B 对照与归因 |
| C7 | partial | `R014` + `R015` 已证明生成链路可产出可运行 MIPS 基线 | 仍有 3 条 `MEMCAGE_TODO` 待人工确认，不能写成 fully closed |

## What Results Actually Support

- 当前工具已在 Layer A 环境下形成稳定、可审计的扫描与日志链路。
- 当前 AArch64 匹配子集上，`memcage` 相比 `ptrace` 观察到有限但稳定的吞吐优势。
- 当前受控样例足以说明分类链路**部分可用**，但不应被写成 fully closed taxonomy。
- 当前生成器贡献适合写成“降低 bring-up 成本”，而不是“自动完成新 ISA 支持”。

## What Results Do Not Support

- 任何关于真实硬件可复现性或实机统计的结论。
- 任何关于仿真/容器和实机差异可解释的结论。
- 任何“`memcage` 一般优于 `ptrace`”的泛化表述。
- 任何“分类器已经稳定覆盖 H/D/P/T/X 全部路径”的表述。

## Suggested Claim Revision

- 将 `C2` 改写为：在**当前 AArch64 对齐子集与当前环境**中，`memcage` 的 median 吞吐高于 `ptrace`。
- 将 `C3/C4` 改写为：当前系统能够稳定表达部分 controlled path，同时通过 illegal/trap 样例发现 taxonomy/logging gap。
- 将 `C7` 改写为：配置驱动生成器能够产出可运行的 ISA 基线，并将剩余人工工作显式外露。

## Minimum Next Experiments

1. 至少完成 1 台 RISC-V 实机上的 bounded campaign。
2. 针对 `R005/R007` 设计补实验，明确 illegal-at-test 与 known-disassembly exec-fault 是否进入 artifact 输出。
3. 若条件允许，补更多 matched subset 或更强统计，以增强 `C2`。
4. 对 MIPS case 增加“人工 patch 数/来源”的可追溯记录。

## Writing Route

- 当前可进入正文主结果：`C1`、收缩版 `C2`、收缩版 `C3/C4`、收缩版 `C7`
- 当前必须留空/占位：`C5`、`C6`
- 当前建议稿型：**中文学位论文初稿（Layer A + Layer C 主体，Layer B 占位）**
