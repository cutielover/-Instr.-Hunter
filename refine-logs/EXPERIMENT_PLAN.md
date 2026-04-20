# Experiment Plan

**Problem**: 如何在用户态、跨 ISA、可复现地发现并归因“CPU 实现行为 vs 文档/工具链认知”之间的差异，同时避免把 emulator / disassembler artifact 误写成硬件隐藏指令。  
**Method Thesis**: 一个以 `memcage` / `ptrace` 为执行控制、以信号与双解码器 triage 为核心、并带有配置驱动扩展能力的框架，能够在真实硬件与 Layer A 受控环境中生成可信的隐藏指令审计 artifact。  
**Date**: 2026-04-14

## Claim Map

| Claim | Why It Matters | Minimum Convincing Evidence | Linked Blocks |
|-------|-----------------|-----------------------------|---------------|
| C1. 指定 Linux 环境下主循环稳定且 artifact 可审计 | 没有稳定 harness，后续所有 finding 都不可信 | 安全/超时/重启/信号路径测试通过；短程扫描重复运行稳定；interesting case 可复现 | B1, B2, B4 |
| C4. 受控注入可验证端到端探测链路 | 证明分类链路不是“碰巧能跑” | 对合法/非法/特权/trap case 的分类结果稳定正确，且附带 artifact bundle | B2 |
| C2. `memcage` 在匹配子集上吞吐优于 `ptrace` | 支撑双后端设计不是无意义复杂化 | 同 ISA 同范围的 median/p95 吞吐对照，覆盖相同子集，无明显 artifact 偏差 | B3 |
| C5/C6. 实机可复现，A/B 差异可归因 | 这是完整论文的主价值来源 | 至少 1 台实机 3 次复跑稳定；A/B 子集差异可分桶解释 | B4 |
| C7. 配置 + 生成 + 有界人工加固可接入新 ISA | 支撑“通用扩展框架”创新点 | MIPS case 的 time-to-first-scan、人工 patch 数、首跑 artifact | B5 |

## Paper Storyline

- Main paper must prove:
  - 框架能稳定、可审计地运行，并在受控环境中给出正确 triage。
  - 至少一条实机证据链成立。
  - 新 ISA 接入可被量化为“生成 + 有界人工加固”的工程过程。
- Appendix can support:
  - 更长的 throughput 表
  - 更多信号样例
  - 更多 emulator / disassembler mismatch 长表
- Experiments intentionally cut:
  - 跨所有 ISA 的大而全性能比较
  - 没有实机支撑的“发现很多隐藏指令”式宣传
  - 没有 artifact bundle 的人工印象式 case analysis

## Experiment Blocks

### Block 1: Harness Safety and Stability

- Claim tested: C1
- Why this block exists: 没有 containment 和稳定性，后续实验都不可信。
- Dataset / split / task: RISC-V 与 AArch64 短程随机扫描；手工构造 `SIGILL / SIGSEGV / SIGBUS / SIGTRAP / timeout` case。
- Compared systems:
  - `memcage`
  - `ptrace`
  - watchdog on/off（仅用于验证，不写入主文主结果）
- Metrics:
  - worker 存活率
  - restart 次数
  - hang recovery 成功率
  - canary / guard page 完整性
- Setup details:
  - 每组运行 1000-10000 case 的短程测试
  - 固定 `-j 1` 起步，确认单 worker 稳定后再加并行
  - RISC-V Layer A 基线默认开启 `--filter-ext`，但必须同步保留 `raw_hidden_count / hidden_count / filtered_hidden_count`
  - 必须保存每次失败的环境指纹
- Success criterion:
  - 所有预期信号路径都能回到 harness
  - 无 cage 外状态破坏
  - 重复多次无随机性崩溃
- Failure interpretation:
  - 若 containment 不成立，则必须先修系统，不得进入主实验
- Table / figure target:
  - Table A1（附录）
  - 也可在正文 §6.1 用一段文字总结
- Priority: MUST-RUN

### Block 2: Controlled Classification Validation

- Claim tested: C4，部分支撑 C3
- Why this block exists: 要证明“受控注入 -> 分类 -> artifact”这条链路本身可靠。
- Dataset / split / task:
  - 修改 QEMU 或受控执行环境，注入 4 类已知 case：
    - legal
    - illegal
    - privileged-in-user
    - memory-trap
- Compared systems:
  - 主解码器：Capstone
  - 第二解码器：`scripts/secondary-decode.sh`（Linux target-native 优先 `objdump`，宿主机 fallback 为 `llvm-mc --hex`）
  - 分类器输出 vs 预期标签
- Metrics:
  - precision
  - recall
  - UNKNOWN 比例
  - flakiness（重复运行不一致次数）
- Setup details:
  - 每类 case 至少重复 1000 次
  - RISC-V controlled run 默认开启 `--filter-ext`，并在 `run.json` / `data/log` 中同时报告 raw H 与过滤后 H
  - 保存完整 artifact bundle
  - 先在单 worker 下完成，再做少量多 worker 回归
- Success criterion:
  - 四类 case 全部被稳定归到正确桶或 UNKNOWN
  - 无无解释漂移
- Failure interpretation:
  - 若 controlled case 都不稳定，则 C3/C4 不能写
- Table / figure target:
  - Table 6-1 或 Fig. “classification decision matrix”
- Priority: MUST-RUN

### Block 3: Matched Backend Throughput

- Claim tested: C2
- Why this block exists: 证明双后端设计不是徒增复杂度。
- Dataset / split / task:
  - 只选择一条两种后端都可用的 ISA 路径
  - 固定 random seed 或固定 `-b/-e`
- Compared systems:
  - `memcage`
  - `ptrace`
- Metrics:
  - median insn/s
  - p95 insn/s
  - worker restart 数
  - interesting case 数量是否一致
- Setup details:
  - 固定 CPU affinity
  - 固定 worker 数
  - 至少 3 次重复
  - 不做跨 ISA 横向大表
- Success criterion:
  - 在 matched subset 上，`memcage` 明显更快且输出分布没有不可解释偏移
- Failure interpretation:
  - 若两者差异不稳定，则只保留“双后端互补”，不写吞吐优越 claim
- Table / figure target:
  - 主文 Table 3
- Priority: MUST-RUN

### Block 4: Minimal Real-Hardware Reproducibility

- Claim tested: C5, C6
- Why this block exists: 这是完整方案中最关键的证据锁。
- Dataset / split / task:
  - 选定 1 台可稳定控制的 RISC-V 板卡
  - 固定一个 bounded campaign（例如固定 seed + 条数，或固定 `-b/-e` 小区间）
- Compared systems:
  - Layer B 实机
  - 对齐的 Layer A 容器/QEMU 运行
- Metrics:
  - 三次复跑的一致性
  - top interesting cases 稳定度
  - A/B 差异分桶统计
  - 日志完整性
- Setup details:
  - 记录板卡、SoC、内核、Capstone 版本、commit hash
  - 每次运行归档 `data/log`
  - 若有串口/控制台，保存额外崩溃信息
- Success criterion:
  - 至少 1 组实机 campaign 可复现
  - A/B 差异可以归入已定义桶中
- Failure interpretation:
  - 若实机不可稳定复现，则必须先缩 claim，再考虑扩展实验
- Table / figure target:
  - 主文 Table 2, Table 3, Layer A/B 对照图
- Priority: MUST-RUN

### Block 5: Generator Case Study

- Claim tested: C7
- Why this block exists: 生成器如果不量化成本，只会像工程附带功能。
- Dataset / split / task:
  - `arch-specs/mips64el.json`
  - `tools/generate_arch_backend.py`
  - `scripts/macos-docker-run-mips64.sh quick`
- Compared systems:
  - 生成前的人工 bring-up 基线（回溯估计）
  - 当前生成路径
- Metrics:
  - time-to-first-scan
  - 新增/修改 LOC
  - 人工 patch 数
  - 首次 smoke 成功前失败次数
- Setup details:
  - 记录生成命令、构建命令、quick run 命令
  - 记录 qemu-user ptrace 限制
- Success criterion:
  - 可以清楚展示“自动生成了什么”和“仍需人工做什么”
- Failure interpretation:
  - 若无法量化工作量，则 C7 只能降格为实现细节
- Table / figure target:
  - 主文 Table 4
- Priority: MUST-RUN

## Run Order and Milestones

| Milestone | Goal | Runs | Decision Gate | Cost | Risk |
|-----------|------|------|---------------|------|------|
| M0 | harness 不崩且 signal 路径闭环 | Block 1 短程 runs | 若 containment 不过，停止后续实验 | 低，数小时 CPU | 未预料信号/越界写 |
| M1 | controlled cases 可正确分类 | Block 2 全部 case | 若四类 case 不稳定，暂停论文写作主线 | 低，数小时 CPU | QEMU 修改 / case 设计不充分 |
| M2 | 确定 `memcage` / `ptrace` 是否值得写性能 claim | Block 3 3 次重复 | 若无稳定优势，C2 降级 | 中，半天到一天 CPU | 输出分布受后端偏差影响 |
| M3 | 解锁 Layer B 主证据 | Block 4 三次实机复跑 | 若无法稳定复现，收缩 C5/C6 | 中到高，取决于板卡可用性 | 板卡不稳定 / 环境记录不全 |
| M4 | 锁定生成器贡献表达 | Block 5 case study | 若量化不清，C7 收缩为 supporting claim | 低 | 生成器边界写不清 |

## Compute and Data Budget

- Total estimated CPU-hours:
  - M0-M2: 10-20 CPU-hours
  - M3: 依赖实机，另计 1-3 天墙钟时间
  - M4: 2-4 CPU-hours
- Data preparation needs:
  - 不需要大型外部数据集
  - 需要 artifact 归档结构和双解码器输出
- Human evaluation needs:
  - 对 interesting cases 做人工 triage
  - 审核 UNKNOWN -> hidden/disassembler/emulator 的升级规则
- Biggest bottleneck:
  - 实机可用性与 triage 时间，而不是算力

## Risks and Mitigations

- Risk: 把 decoder/emulator artifact 当成硬件 finding  
- Mitigation: 双解码器 + `--filter-ext`(仅 RISC-V Layer A 基线) + raw/filtered H 同时记账 + UNKNOWN + minimal repro + A/B 重放

- Risk: 实机 runs 不稳定，C5/C6 无法成立  
- Mitigation: 尽早做 bounded campaign，不等到后期再碰实机

- Risk: `memcage` 容易被 reviewer 质疑 containment  
- Mitigation: 把 safety invariant 和 canary/guard 检查前置成独立 block

- Risk: MIPS case 看起来只是“生成了代码”，没有实证价值  
- Mitigation: 量化 time-to-first-scan、LOC、人工 patch 数

## Final Checklist

- [ ] Main paper tables are covered
- [ ] Novelty is isolated
- [ ] Simplicity is defended
- [ ] Frontier contribution is justified or explicitly not claimed
- [ ] Nice-to-have runs are separated from must-run runs
- [ ] Layer B bounded campaign 已定义
- [x] artifact bundle schema 已冻结（`docs/ARTIFACT_SCHEMA.md`）
- [x] 第二解码器已选定（`scripts/secondary-decode.sh`）
- [x] `R001-R007` runbook 已写定（`refine-logs/R001_R007_RUNBOOK.md`）
