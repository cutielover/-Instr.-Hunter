# Literature Review

**Topic**: hidden-instruction analysis, third-party processor auditing, ISA-level differential behavior, and practical ground truth for RISC-style processors  
**Date**: 2026-04-14  
**Scope**: support `PAPER_PLAN.md` §2 Related Work and §6 Evaluation framing

## Paper Table

| Paper | Venue | Method | Key Result | Relevance to Us | Source |
|-------|-------|--------|------------|-----------------|--------|
| Fabrice Bellard. `QEMU, a Fast and Portable Dynamic Translator` | USENIX ATC / FREENIX 2005 | Dynamic binary translation and multi-target emulation | Establishes QEMU as a practical user/full-system emulation substrate across multiple ISAs | Important as Layer A execution substrate, but must be framed as comparison target rather than oracle | Web / USENIX |
| Christopher Domas. `Breaking the x86 ISA` | Black Hat USA 2017 | Page-fault-guided x86 instruction fuzzing with `sandsifter` | Shows hidden instructions, disassembler bugs, hypervisor flaws, and hardware errata can be found by systematic instruction-space auditing | Core x86 prior work and search-space-pruning contrast; motivates why your thesis does **not** claim x86-style variable-length depth | Web |
| Rens Dofferhoff et al. `iScanU: A Portable Scanner for Undocumented Instructions on RISC Processors` | DSN 2020 | RISC instruction scanning with `ptrace` and `memcage`, signal + disassembler analysis | Finds QEMU / Capstone inconsistencies and one undocumented RISC-V behavior on hardware | Closest prior system; your work extends it with updated engineering, layered evaluation, and config-driven backend generation | Web |
| Fredrik Strupe, Rakesh Kumar. `Uncovering Hidden Instructions in Armv8-A Implementations` | HASP 2020 | `armshaker` differential discovery of hidden Armv8-A instructions on hardware and emulators | Finds software-induced hidden instructions and decoder bugs; no hardware hidden instructions confirmed on tested platforms | Strong comparator for Arm-specific hidden-instruction analysis and for negative-result framing | Web / Semantic Scholar |
| Alasdair Armstrong et al. `ISA semantics for ARMv8-A, RISC-V, and CHERI-MIPS` | PACMPL 2019 | Sail-based executable semantics for major ISAs | Provides rigorous executable semantics complete enough to boot operating systems | Key reference for authoritative/formal ground truth; useful to explain why disassemblers are practical but weaker oracles | Semantic Scholar |
| Michael Sammler et al. `Islaris: verification of machine code against authoritative ISA semantics` | PLDI 2022 | Verification above full-scale authoritative ISA semantics | Demonstrates how complete ISA models can support machine-code reasoning for Armv8-A and RISC-V | Important non-competing baseline: your work is black-box empirical auditing, not formal proof over authoritative semantics | Web / Semantic Scholar |

## Landscape Summary

这条研究线可以分成四类。

第一类是**隐藏指令发现 / 指令空间审计**。`sandsifter` 是最早的标志性工作之一，它把“处理器不是可信黑盒”这个问题公开化，并在 x86 上通过页错误分析和搜索空间裁剪找到了隐藏指令、硬件异常和工具链 bug。它的重要性不在于可直接复用到你的系统里，而在于提供了研究动机和“系统化指令审计是可行的”这一先例。与此同时，它也天然构成你的边界条件：你的论文不该声称复现 x86 变长 ISA 的搜索策略，而应明确聚焦固定字长或可统一 cage 的 RISC 风格 ISA。

第二类是**RISC / Arm 上的用户态扫描工具**。`iScanU` 是与你最接近的工作，它把 `ptrace` 与 `memcage` 这两条控制路径组织成可移植 RISC 扫描方案，并用信号和反汇编联合分类，已经覆盖了 ARMv8 与 RISC-V；`armshaker` 则更聚焦 Armv8-A，把“隐藏指令”与软件诱发异常行为联系起来，强调 Linux/QEMU/decoder 缺陷也会制造出从用户视角看似“隐藏指令”的现象。这两篇工作共同说明：第三方黑盒处理器审计是成立的，但“发现了异常”不等价于“发现了硬件隐藏指令”，中间必须经过严肃 triage。

第三类是**authoritative ISA semantics / compliance / formal verification**。Sail 系列工作和 `Islaris` 代表的是另一条路线：直接在 Armv8-A、RISC-V、MIPS 等 ISA 的形式化或权威语义之上做验证与推理。这些工作与本文不竞争同一问题。它们回答的是“给定 authoritative semantics，如何证明程序正确”；而你的系统回答的是“当只有成品 CPU、现成工具链和用户态环境时，如何做第三方黑盒审计”。这类工作对你最重要的价值，是帮助你把论文中的 “ground truth” 表述得更诚实：Capstone 可以是实践上可行的判别器，但不该写成 authoritative truth。

第四类是**执行环境和工具链基础设施**。QEMU 之类的模拟器和 Capstone 之类的反汇编器，使大规模可复现实验成为可能，但它们本身也可能制造差异甚至假阳性。因此在你的论文里，QEMU 最好被定位为 Layer A 的“受控开发与对照环境”，而不是最终证据；Capstone 应被定位为“实用 ground truth 代理”，并辅以第二解码器或 UNKNOWN 分类，降低 circularity 风险。

## What This Means for Our Positioning

- **vs `sandsifter`**: 你的贡献不是更强的搜索空间剪枝，而是面向 RISC 风格 ISA 的统一执行控制、分层评估与可扩展接入框架。
- **vs `iScanU`**: 你的增量不该只写成“重写一个实现”，而应写成三点：配置驱动生成链路、A/B/C 分层证据结构、以及当前代码库中的多 ISA 工程化落地。
- **vs `armshaker`**: 你的论文不能把“异常行为”直接宣传成“硬件隐藏指令”；应主动承接它的经验，把 emulator / disassembler / system software artifact 视为 first-class result。
- **vs Sail / `Islaris` / compliance**: 你的系统不是权威语义替代品，而是现实世界中的黑盒审计工具，适合用来发现 formal workflow 之外的实现偏差和环境差异。

## Severe Gaps To Fix In Writing

- 必须补上**双解码器 / differential disassembly** 叙事，否则 “Capstone 是 ground truth” 很容易被 reviewer 质疑为循环论证。
- 必须单独写清**Undefined / Reserved / Unpredictable / privilege-gated** 行为，否则很多“异常”会被审稿人直接判为 spec-allowed。
- 必须把 **QEMU** 明确降格为 Layer A 比较工具，而不是验证 oracle。
- 若引用 **Capstone**，建议以项目官网 / GitHub / 官方技术 slides 为准，并在正文中说明它是 practical decoder dependency，而非正式文献核心贡献点。

## Recommended Citation Spine

- Problem motivation: `Breaking the x86 ISA`, `iScanU`
- RISC scanning design baseline: `iScanU`
- Arm-specific hidden-behavior triage: `Uncovering Hidden Instructions in Armv8-A Implementations`
- Authoritative semantics contrast: `ISA semantics for ARMv8-A, RISC-V, and CHERI-MIPS`, `Islaris`
- Emulation substrate: `QEMU, a Fast and Portable Dynamic Translator`

## Keep / Cut Guidance

- **Keep in main text**: `sandsifter`, `iScanU`, `armshaker`, Sail semantics, `Islaris`, QEMU
- **Keep as tooling citation**: Capstone official project reference
- **Appendix or footnote only**: broader compliance ecosystem details unless you actually integrate them into experiments
