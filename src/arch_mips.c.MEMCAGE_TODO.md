# Memcage Manual TODOs for mips

This checklist is generated from `arch-specs/mips64el.json`.

## Why manual work is still needed

The generated backend provides a compilable memcage baseline, but high-quality memcage
usually needs ISA-specific tuning that cannot be inferred safely from config.

## Required manual checks

- [ ] Implement MIPS register sandboxing (seed GPRs to trap-safe pointer) if memcage is used for large scans.
- [ ] Verify BREAK sentinel semantics and si_addr behavior on native MIPS64 Linux.
- [ ] Tune blacklist for privileged/cache/TLB-sensitive instructions on target silicon.
