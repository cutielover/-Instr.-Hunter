# Findings

## 2026-04-15 Result-to-Claim Gate

### Overall Verdict

当前已完成的实验足以支撑一篇以 **Layer A + Layer C** 为主体的中文系统/工程型论文初稿，但**不足以**支撑“实机可复现”与“仿真/容器和实机差异可归因”这两类完整闭环结论。更稳妥的写法是：把本文当前版本定位为**方法 + 部分验证 + 扩展性案例研究**，并明确将 Layer B 结果作为后续补证部分。

### Claim Assessment

- `C1`: `yes`
  - `R001-R003` 已经表明当前 Linux 容器环境下的主循环、日志落盘与基本 worker 编排是稳定的。
- `C2`: `partial`
  - `R008/R009` 在 AArch64 匹配子集上给出了 `memcage` 相比 `ptrace` 的稳定优势，但范围仍限于单架构、单窗口和当前重复次数。
- `C3`: `partial`
  - 当前系统能够表达部分行为类别，但 `R005/R007` 已暴露 illegal-at-test 与 known-disassembly exec-fault 两类路径的 taxonomy/logging gap。
- `C4`: `partial`
  - legal 与 privileged 受控样例已经跑通，但 illegal 与 trap 路径尚未形成完整“注入-观测-分类-落盘”闭环。
- `C5`: `no`
  - 尚无真实硬件扫描结果。
- `C6`: `no`
  - 尚无 Layer B 对照数据，不能讨论容器/仿真与实机的差异归因。
- `C7`: `partial`
  - MIPS 生成链路与 quick smoke 已证明“配置 + 生成”可产出可运行基线，但 `MEMCAGE_TODO` 中仍有 3 条待人工确认的检查项。

### Allowed Main Claims

- 本文已实现一套面向 RISC-V 与 AArch64 的用户态隐藏指令分析框架，并可在声明的 Layer A 环境下稳定运行。
- 在当前 AArch64 匹配子集上，`memcage` 的 median 吞吐高于 `ptrace`。
- 当前系统已能稳定表达 legal 与 privileged-in-user 两类受控路径。
- 配置驱动的生成链路能够将 MIPS64 接入为一个可运行的基线后端。

### Claims To Soften Or Delay

- 不应把 `memcage` 写成“通常显著优于 `ptrace`”，只能写成“在当前匹配子集上观察到吞吐优势”。
- 不应把当前分类器写成“已稳定覆盖 H/D/P/T/X 全部路径”。
- 不应把受控样例实验写成“端到端完全验证”，只能写成“部分验证并暴露缺口”。
- 不应在正文主结论中写入任何实机复现或 Layer A/B 差异归因结论。
- 不应把生成器写成“零人工支持新 ISA”，只能写成“生成可运行基线并降低 bring-up 成本”。

### Minimum Next Experiments

- 至少完成 1 台 RISC-V 实机上的 bounded campaign，并保留完整环境指纹与三次复跑记录。
- 针对 `R005/R007` 补一轮分类器/日志口径实验，明确 illegal-at-test 与 exec-fault/trap 路径是否进入 artifact 输出。
- 若条件允许，再补 1 个额外 matched subset 或额外重复组，增强 `C2` 的可信度。
- 在 MIPS case 上进一步补“人工 patch 数/来源”级别的工作量记录。

## Recommended Skill Chain

对于当前项目，最合适的 skill 调用链如下：

1. `result-to-claim`
   - 先判断当前结果到底允许写什么。
2. `research-review`
   - 再从 reviewer 视角检查 narrative risk、缺实验风险与 overclaim。
3. `writing-systems-papers`
   - 作为章节职责与段落结构蓝图，约束论文像“系统论文/学位论文”而不是实验日志。
4. `paper-write`
   - 用它的 claim-evidence 写作方法继续扩正文稿，但不直接照搬英文会议模板。
5. `paper-writing`
   - 等 Layer B 补齐、图表和最终结构稳定后，再进入完整 LaTeX/编译流水线。
6. `auto-paper-improvement-loop`
   - 用于最终稿润色，不适合在证据仍缺口较大时过早启动。
