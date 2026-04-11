# Memory Cage

## 核心问题

要测试一条未知的 RISC-V 指令编码 `0x12345678`，会面临以下挑战：

1. **非法** — CPU 不认识这个编码，触发 SIGILL
2. **跳转** — `jalr`/`jal` 类指令会把 PC 跳到寄存器或偏移指定的地址
3. **写内存** — `sw`/`sd` 类指令会向寄存器指向的地址写入数据
4. **读内存** — `lw`/`ld` 类指令会从寄存器指向的地址读取数据
5. **修改关键寄存器** — 包括 `sp`（栈指针）、`gp`（全局指针）、`tp`（线程指针）
6. **死循环** — 某些指令会让 CPU 停下来等待

为了解决这些问题，项目默认使用 memory cage 作为高速扫描执行路径。`ptrace` 仍然保留为备选模式，但更适合慢速、可观察性更强的分析场景。

## 内存布局

```
低地址                                                                    高地址
┌──────────────────┬──────────┬──────────┬──────────┬──────────────────┐
│   Guard Pages    │  Test A  │  Test B  │   Trap   │   Guard Pages    │
│   256 × 4KB      │   4KB    │   4KB    │   4KB    │   256 × 4KB      │
│                  │  RW / RX │  RW / RX │   R-X    │                  │
└──────────────────┴──────────┴──────────┴──────────┴──────────────────┘
                    ↑                      ↑
                    测试指令                寄存器
```

### 各区域的作用

**Guard Pages（守卫页，2MB）**

两侧各 256 页，权限为 `PROT_NONE`。PC 相对跳转指令如果跳出测试页，必然落入守卫区，触发 SIGSEGV，被信号处理器捕获。

**Test Pages（测试页，2 页）**

这是被测指令实际执行的地方。整页预填充 `ebreak`（`0x00100073`），然后在页首写入待测指令。指令执行后，PC 自然前进到下一条ebreak，触发 SIGTRAP，控制权回到信号处理器。

两个测试页用于 W^X 双缓冲。在默认模式下，页权限会在 `RW` 与 `RX` 之间切换；若显式启用 `--rwx`，则使用兼容 QEMU 的 RWX 方式运行。

**Trap Page（陷阱页，1 页）**

权限为 R-X（可读、可执行、**不可写**），整页填满 ebreak。所有通用寄存器在执行前都被设置为指向这个页的中间位置。这意味着：

- **写内存**（`sw rs2, 0(rs1)`）→ rs1 指向 Trap Page → 不可写 → SIGSEGV
- **跳转到寄存器**（`jalr rd, rs1, 0`）→ rs1 指向 Trap Page → 执行 ebreak → SIGTRAP
- **读内存**（`lw rd, 0(rs1)`）→ rs1 指向 Trap Page → 可读 → 读到 ebreak 编码（无害）

## 寄存器沙箱

在跳转到测试指令之前，injector 用一段内联汇编把几乎所有通用寄存器都设置为 `trap_page + 2048`（陷阱页中间）：

```asm
mv ra,  t0      ; ra → trap page
mv t2,  t0      ; t2 → trap page
mv s0,  t0      ; s0 → trap page
...              ; (所有 28 个寄存器)
mv t6,  t0      ; t6 → trap page
mv sp,  t0      ; sp → trap page（最后设置）
jalr zero, t1, 0 ; 跳转到测试指令
```

## 汇编跳板（Assembly Trampoline）

如果测试指令修改了 `gp` 或 `tp`，C 语言信号处理函数就可能无法可靠访问全局变量和 TLS，`siglongjmp` 也可能失效。

解决方案：信号处理器的入口不是 C 函数，而是一段汇编跳板 `asm_fault_handler`：

```asm
asm_fault_handler:
    auipc   gp, %pcrel_hi(saved_gp_value)
    ld      gp, %pcrel_lo(1b)(gp)

    auipc   tp, %pcrel_hi(saved_tp_value)
    ld      tp, %pcrel_lo(2b)(tp)

    tail    fault_handler
```

`auipc` + `ld` 的组合使用 PC-relative 寻址，不需要 gp 就能找到保存的值。这样无论测试指令把 gp/tp 改成什么，信号处理器都能正确恢复。

## 信号处理与结果判定

信号处理函数 `fault_handler` 记录信号类型和故障地址，然后通过 `siglongjmp` 跳回主循环：

```
测试指令执行后，可能出现以下情况：

┌─────────────────────┬──────────┬────────────────────────────────────┐
│ 信号                 │ 故障地址  │ 含义                                │
├─────────────────────┼──────────┼────────────────────────────────────┤
│ SIGTRAP             │ 任意     │ 执行了 ebreak 哨兵 → 指令成功执行      │
│ SIGILL @ test_addr  │ 测试页首  │ CPU 拒绝该编码 → 非法指令             │
│ SIGILL @ 其他地址    │ 非测试页首│ ebreak 哨兵触发（QEMU user-mode 常见） │
│                     │          │ → 指令实际成功执行                   │
│ SIGSEGV             │ 任意     │ 指令已被 CPU 接受并开始执行，但在       │
│                     │          │ memcage 中因访存/跳转保护而 fault      │
│ SIGFPE              │ 任意     │ 指令已被 CPU 接受并开始执行，但在运行时  │
│                     │          │ 触发算术异常                          │
│ SIGBUS              │ 任意     │ 指令已被 CPU 接受并开始执行，但在运行时  │
│                     │          │ 触发总线错误                          │
│ SIGALRM             │ N/A      │ 1 秒超时 → 指令导致 CPU 挂起          │
└─────────────────────┴──────────┴────────────────────────────────────┘
```

关键判定逻辑之一是：**故障地址（`si_addr`）是否等于测试指令的地址**。

- 如果 SIGILL 发生在测试指令地址 → CPU 不认识这条指令
- 如果 SIGILL 发生在其他地址 → 测试指令执行成功了，是后面的 ebreak 哨兵触发的（在 QEMU user-mode 下这很常见）
- 如果是 SIGSEGV / SIGBUS / SIGFPE → 说明 CPU 已经接受了这个编码，并进入了执行路径；只是执行过程中触发了 memcage 的保护或其他运行时异常

这个区分让 memory cage 能同时工作在真实硬件和 QEMU user-mode 环境中。

从项目输出语义上说，这里还要区分“CPU 接受了编码”和“最终被记成哪一类 artifact”：

- Capstone 不识别，且最终成功落到哨兵：记为 `H`
- Capstone 不识别，但执行过程中触发 `SIGSEGV` / `SIGBUS` / `SIGFPE` 等 fault：记为 `X`
- Capstone 认为合法，但 CPU 在测试指令地址上报 `SIGILL`：记为 `D`
- 指令超时：记为 `T`

## W^X 双缓冲

现代操作系统和硬件通常强制执行 W^X 策略：一个内存页不能同时可写和可执行。默认情况下 memory cage 使用双缓冲方案来满足这个约束：

```
第 N 次迭代：
  Test A = RW（写入第 N 条指令）
  Test B = RX（正在执行第 N-1 条指令）

写入完成后：
  Test A → 切换为 RX，跳转执行
  Test B → 切换为 RW（准备下次写入）

第 N+1 次迭代：
  Test B = RW（写入第 N+1 条指令）
  Test A = RX（刚执行完第 N 条）
  ...交替进行
```

默认路径下，每次迭代通过双缓冲减少权限切换成本。若启用 `--rwx`，则兼容性优先，不再依赖这一套 RW/RX 翻转。

## 超时保护

每条指令执行前设置 `alarm(1)`（1 秒超时）。如果指令导致 CPU 进入等待状态（如 `wfi`）或死循环，SIGALRM 会打断执行，`siglongjmp` 回到主循环。

## 黑名单

某些指令即使能被安全捕获，也不应该执行：

| 指令  | 原因 |
|------|------|
| `ecall` | 触发系统调用，可能产生不可预期的副作用 |
| `ebreak` | 与哨兵机制冲突，无法区分是测试指令还是哨兵 |
| `wfi` | 等待中断，虽然有超时保护但浪费时间 |
| `mret`/`sret` | 特权级返回，用户态下行为未定义 |
| `sfence.vma` | 刷新 TLB，可能影响地址翻译 |

## 大概的执行流程

```
     将指令写入测试页（RW 状态）
     在指令后面放 ebreak 哨兵
     sigsetjmp 保存恢复点
     保存 gp/tp 到全局变量
     将所有寄存器设为 trap_page 地址
     jalr 跳转到测试指令
       ├─ 非法指令 -> SIGILL -> asm_fault_handler -> 恢复 gp/tp -> siglongjmp
       ├─ 成功执行 -> 命中 ebreak 哨兵 -> SIGTRAP -> siglongjmp
       ├─ 跳转到寄存器 -> 跳到 trap page -> ebreak -> SIGTRAP -> siglongjmp
       ├─ 写内存 -> 写到 trap page（不可写）-> SIGSEGV -> siglongjmp
       ├─ PC 相对跳转 -> 跳到 guard page（无权限）-> SIGSEGV -> siglongjmp
       └─ 循环 -> 1 秒后 SIGALRM -> siglongjmp
     分析信号类型和故障地址，判定结果
       ├─ 成功落到哨兵且 Capstone 不识别 -> `H`
       └─ 执行中 fault 且 Capstone 不识别 -> `X`
     输出结果，继续下一条
```

## 与 ptrace 对比

| 方面 | Memory Cage | ptrace |
|------|-------------|--------|
| 速度 | 快（同进程内，无系统调用开销） | 慢 10-100x（每条指令多次 ptrace 系统调用） |
| 寄存器观察 | 不输出执行后寄存器 diff | 可以记录完整的寄存器 diff |
| W^X 处理 | 默认双缓冲，也可用 `--rwx` | 父进程通过 `PTRACE_POKETEXT` 写入，子进程页面保持 RX |
| 信号处理复杂度 | 高（`sigaltstack` + trampoline + `siglongjmp`） | 较低（异常由父进程等待并判定） |
| QEMU user-mode | 适合主流程 | 可作为备选，但更偏实验/分析 |
| 真实硬件 | 可用 | 可用 |
| 适用场景 | 大规模快速扫描 | 慢速复核、寄存器观察 |

