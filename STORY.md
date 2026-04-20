# STORY.md — 论文叙事骨架（完整方案版）

> 与 `NARRATIVE_REPORT.md`、`docs/PAPER_OUTLINE_zh.md`、`docs/EVALUATION_MATRIX.md` 一致：论文按**完整目标**写——**实机硅后验证** + **通用扩展 / 配置驱动代码生成**，仿真/容器是开发与对照层，不是最终叙事终点。  
> 定稿前用实验数字替换 `[TBD]`。

---

## 一句话贡献

我们设计并实现一套**可扩展的微处理器隐藏指令分析工具**：在 **RISC-V 与 AArch64** 上以 **memcage 与 ptrace** 实现可控单条注入，用 **Capstone** 提供 ISA 视图并与信号联合分类；通过**统一编排与二进制协议**支撑多后端；并实现**面向新 ISA 的配置驱动模板生成**（`arch-specs/* -> generate_arch_backend.py -> arch_*.c`），在仿真/容器与后续实机环境中完成分层评估。

---

## 要解决什么问题

- 多 ISA、多供应商环境下，需要**同一套方法论**做隐藏指令 / 工具链-CPU 不一致性分析。  
- 全空间或大范围扫描必须**单条可控、可收回**，且尽量**可复用、可扩展**。  
- Ground truth 常依赖反汇编器；需**实机结果**支撑「不仅是模拟器 artifact」的论述，并诚实讨论局限。  
- 新架构接入成本高；需要**配置 + 生成**降低重复劳动，同时**不夸大**「零代码换 ISA」（memcage 跳板仍可能需人工）。

---

## 我们怎么做（方法要点）

1. **扫描循环**：写指 → 执行 → 信号 → 反汇编 → 分类（H/D/T/X 等）。  
2. **双执行后端**：memcage（吞吐）；ptrace（可观察性、**更利于生成框架默认路径**）。  
3. **统一核心**：`injector_core` + `arch_*` + 稳定 raw 协议；Python 编排与 Capstone 配置。  
4. **扩展框架**：配置文件描述 ISA 与信号语义、Capstone、黑名单等；生成器产出可编译桩与检查列表；memcage 高性能路径允许「生成 + 人工加固」。  
5. **评估分层**：**受控/仿真**（开发、吞吐、注入验证）+ **实机**（主结论与硅片行为）+ **扩展性实验**（新 ISA 从配置到可跑）。

---

## 当前落地进展（用于写作时区分已完成/待完成）

- 已完成：`arch-specs/mips64el.json`、`tools/generate_arch_backend.py`、生成 `src/arch_mips.c` 与 `src/arch_mips.c.MEMCAGE_TODO.md`。  
- 已完成：MIPS 容器镜像编译与 quick smoke（memcage 路径）可跑，`data/log` 可见非零 `Tested`。  
- 已知边界：`linux/mips64le` qemu-user 下 ptrace 可能不可用（`PTRACE_TRACEME` 限制），ptrace 对照优先放实机/原生 Linux。  
- 待完成：层 B 实机数据、MIPS memcage 的 ISA 特化 hardening 与信号语义细化。

---

## 与 iScanU / 既有工作的关系

- **继承**：ptrace + memcage 思想、信号 + 反汇编分析框架、用户态扫描。  
- **增量**：工具链（Capstone 6、`illegal`、ISA 驱动 mode）、工程结构、**显式的配置/生成扩展线**、**以实机为主的评估叙事**（与 iScanU 同样重视硅片，而非止步于 QEMU）。  
- **边界**：x86 sandsifter 级剪枝作相关工作；本文主体在**固定字长或可统一 cage 的 ISA** 上展开。

---

## 核心主张（claims → 证据）

| ID | Claim | 证据来源 |
|----|--------|----------|
| C1 | 在声明的 Linux 环境下，扫描主循环稳定（memcage / ptrace） | [TBD] 层 A 日志 + 命令 |
| C2 | memcage 较 ptrace 在给定范围内有显著吞吐优势 | [TBD] 表 6-1 对照行 |
| C3 | 分类语义与设计与实现一致（含典型样例与人工复核） | [TBD] 统计 + 编码样例 |
| C4 | 受控注入（如 QEMU/桩）下端到端行为符合预期 | [TBD] 层 A 记录 |
| **C5** | **在真实硬件上完成扫描并得到可报告统计** | [TBD] **层 B：板卡标识 + `data/log`** |
| **C6** | **仿真与实机在约定子集上可对照**（一致或差异可解释） | [TBD] **层 A vs 层 B 表** |
| **C7** | **新增 ISA 可通过配置 + 生成（+ 限定手工）接入并产出有效输出** | 已完成首版：`mips64el` 生成链路 + quick 运行证据（`src/arch_mips.c`、`src/arch_mips.c.MEMCAGE_TODO.md`、`data/log`） |

---

## 讨论与威胁（种子）

- 反汇编器 ≠ 官方 ISA 的完全形式化模型。  
- user-mode 仿真与硅片在 SIG 细节、特权行为上可能分歧——**实机实验用于收窄该风险**。  
- `ILL_PRVOPC` / `ILL_ILLOPC` 与「隐藏指令」叙事需分开写清。  
- 配置生成：**诚实写清**自动生成范围与仍需手写部分（尤其 memcage 跳板）。

---

## 叙事顺序建议（引言逻辑）

问题与多 ISA 需求 → 执行控制难点 → 反汇编对照与局限 → **本文架构 + 扩展框架** → **三层评估（仿真 / 实机 / 扩展性）** → 贡献小结。
