# Writing Systems Blueprint (Zh)

**Date**: 2026-04-15  
**Source skill**: `skills/writing-systems-papers/SKILL.md`  
**Adaptation target**: 中文学位论文初稿（非 OSDI/SOSP 短文），但保留系统论文的段落职责与证据组织方式。

## Chosen Pattern

当前项目最适合采用：

- **Gap Analysis**
  - G1：缺少多 ISA 统一、用户态可控的隐藏指令审计框架
  - G2：现有方案难以把 `memcage` / `ptrace` 组织成统一工程结构
  - G3：新增 ISA bring-up 成本高，缺少“配置 + 生成 + 有界人工加固”的落地路线
  - G4：Layer A 结果与 Layer B 实机结果之间缺少一条清晰证据链

## Draft Structure

### 摘要

- 5 句结构：
  1. 问题背景
  2. 现有不足
  3. 本文方法
  4. 当前最强结果
  5. 适用边界 / 后续工作

### 第 1 章 绪论

- 1. 问题与意义
- 2. 现有方法不足（按 G1-G4 枚举）
- 3. 核心 insight：用统一编排整合 `memcage` / `ptrace` 与生成链路
- 4. 贡献列表（3-5 条）
- 5. 论文组织

### 第 2 章 相关工作

- 按方法家族组织，而不是逐篇流水账：
  - x86 隐藏指令搜索
  - RISC 用户态扫描
  - Arm/AArch64 差异行为分析
  - 形式化语义 / 合规测试 / authoritative ground truth
  - decoder / emulator disagreement

### 第 3 章 问题定义与分类口径

- 术语冻结
- 标签定义
- 当前自动输出能力与缺口
- 适用范围与未覆盖空间

### 第 4 章 系统设计

- 总体架构图
- `memcage`
- `ptrace`
- 统一协议与 worker 编排
- 生成器设计与 alternatives

### 第 5 章 实现

- RISC-V
- AArch64
- 日志与离线分析
- MIPS 生成器案例

### 第 6 章 实验与评估

- `6.1` Layer A 稳定性与受控样例
- `6.2` Layer A 吞吐对比
- `6.3` Layer B 占位
- `6.4` Layer A/B 对照占位
- `6.5` Layer C 生成器案例

### 第 7 章 讨论与局限

- 分类缺口
- ground truth 局限
- 用户态边界
- 生成器边界

### 第 8 章 总结与展望

- 问题
- 方法
- 当前结果
- 后续实机补证与多 ISA 扩展

## Paragraph Rules

1. 每段只做一件事。
2. 首句必须是 topic sentence。
3. 实验段落优先写“假设/问题 -> 设计 -> 结果 -> 解释”。
4. 结论段不得引入新结果。

## Current Writing Priority

1. 强化绪论中的 gap 分析
2. 扩充相关工作到更完整的“按方法分组”
3. 把系统设计写成更强的 systems-style 结构
4. 让实验章节中的每个小节直接对应一个 claim
