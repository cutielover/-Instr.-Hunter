# AArch64 injector (Linux)

## Purpose

`injector_aarch64` performs a **full 32-bit exhaustive** scan (step **+1** over every `uint32_t` encoding), executing each word at a 4-aligned address inside a memory cage. Results are classified the same way as the RISC-V build (H/D/X/T) by comparing execution signals with Capstone (`CS_ARCH_ARM64`).

## Build

On Debian/Ubuntu (cross-compile for Linux AArch64 ELF):

```bash
sudo apt-get install gcc-aarch64-linux-gnu libcapstone-dev
make USE_CAPSTONE=1 injector_aarch64
```

On a **native Linux AArch64** machine you can use the host compiler:

```bash
make CC_AARCH64=gcc injector_aarch64
```

The default `CC_AARCH64=aarch64-linux-gnu-gcc` produces binaries for **Linux AArch64**, not macOS.

## Run

```bash
./sifter.py --arch aarch64 --unk --dis --sync --tick --no-gui -j 8
```

Options **not** supported for `--arch aarch64`: `--random` and extension filtering flags (they are rejected or ignored). `--ptrace` is Linux-only.

## Signal semantics (Linux)

- **Success (hidden/disas comparison path)**: `SIGTRAP` from the **BRK** sentinel at `PC == test_addr + 4`.
- **Rejected encoding**: `SIGILL` with fault PC at the **test** word (`test_addr`).
- **`SIGTRAP` at `test_addr`**: treated as a trap on the first word (e.g. a BRK-class encoding that was not skipped); recorded similarly to an illegal-at-test case.
- **`SIGILL` not at the test address**: treated as **success past the test instruction** (parity with QEMU user-mode quirks on RISC-V).

Re-check `si_addr` on your exact kernel if you port to another OS.

## AArch64 ptrace mode (Linux-only)

`--ptrace` now works for `--arch aarch64` on Linux builds. It uses a shared ptrace scan loop with an AArch64 backend (`NT_PRSTATUS` register set, single-step, `BRK` sentinel encoding).

Practical caveats:

- ptrace mode is substantially slower than cage mode.
- stop classification around trap-like opcodes may differ from cage semantics on some kernels.
- non-Linux builds still fall back to the ptrace stub behavior.

## D mismatch semantics (AArch64)

For AArch64 runs, reporting now keeps two mismatch counters:

- `Disas Mismatch Raw`: all `SIGILL` + disassembler-known samples (`D_raw`) for traceability.
- `Disas Mismatch Strict`: stricter subset (`D_strict`) requiring Linux `ILL_ILLOPC` (`si_code == 1`) and `disas_illegal == 0`.

`Disas Bugs` in AArch64 summaries maps to `D_strict` by default. Hidden-instruction detection (`H`) is unchanged.

## Scope and runtime

The space is **2³²** encodings (~4.29×10⁹). Expect **very long** runs unless you shard with `-b`/`-e` and many workers (`-j`).

## Files

| Component | Path |
|-----------|------|
| Shared core | [src/injector_core.c](../src/injector_core.c) |
| AArch64 backend | [src/arch_aarch64.c](../src/arch_aarch64.c) |
| Signal trampoline | [src/handler_trampoline_aarch64.S](../src/handler_trampoline_aarch64.S) |
| Ptrace stub | [src/ptrace_stub.c](../src/ptrace_stub.c) |
