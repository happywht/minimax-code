# Tauri Release Build Verification — Deliverable

**VERDICT: PARTIAL — environmental block.**

> cargo check did NOT reach project source — it failed at the dependency
> build-script link step (`link.exe` missing). Source-level warning/error
> count for `minimax-code` is therefore 0 reachable. cargo build --release
> was not attempted (same blocker). No source files were modified.

## Summary

**Status: BLOCKED — environmental, not a code issue.**

`cargo check` and `cargo build --release` both fail at the linker stage with `linker 'link.exe' not found`. The MSVC toolchain is not installed on this machine — only the MSVC-flavored `rustc 1.91.1` stable is present, and there is no `rustup` (so the GNU fallback toolchain is unreachable), no `gcc`/`mingw`, and no Visual Studio Build Tools anywhere on disk. The compiler fails before it ever reaches our own `src/lib.rs` / `ipc.rs` / `commands.rs`, so I cannot evaluate warnings/errors in project code. **No source files were modified.**

## Environment inventory (what I checked)

| Check | Result |
|---|---|
| `cargo --version` | `cargo 1.91.1 (ea2d97820 2025-10-10)` |
| `rustc --version` | `rustc 1.91.1 (ed61e7d7e 2025-11-07)` |
| Cargo source path | `C:\Program Files\Rust stable MSVC 1.91\bin\cargo.exe` |
| Default toolchain | **MSVC** (host triple `x86_64-pc-windows-msvc`) |
| `rustup` on PATH | **Not installed** — cannot switch to GNU toolchain |
| `link.exe` in PATH | **Not found** (MSVC linker missing) |
| `cl.exe` in PATH | **Not found** (MSVC compiler missing) |
| `gcc` / `x86_64-w64-mingw32-gcc` | **Not found** (no MinGW) |
| `C:\Program Files (x86)\Microsoft Visual Studio\` | Exists, but only `Installer/` and `Shared/` — **no MSVC compiler, no `link.exe`** (VS 14.0/10.0 dirs are framework stubs, not Build Tools) |
| `C:\Program Files\Git\usr\bin\link.exe` | coreutils `ln`, **not** MSVC linker |
| `C:\Program Files\Go\pkg\tool\windows_amd64\link.exe` | Go linker, not MSVC |
| `C:\BuildTools` | Not present |

**Conclusion**: This is a stock Windows machine with the official `Rust stable MSVC` installer but **no C++ toolchain**. Per the hard constraint "≤15 min, don't rewrite code, never upgrade major versions", installing Visual Studio Build Tools or migrating the toolchain is out of scope for this task.

## cargo check result (raw)

```
$ cd src-tauri && cargo check 2>&1 | tail -30
   Compiling parking_lot_core v0.9.12
   Compiling thiserror v2.0.18
   Compiling getrandom v0.4.2
   Compiling serde v1.0.228
   Compiling typeid v0.0.3
   Compiling writeable v0.6.3
   Compiling utf8_iter v1.0.4
error: linker `link.exe` not found
  |
  = note: program not found

note: the msvc targets depend on the msvc linker but `link.exe` was not found

note: please ensure that Visual Studio 2017 or later, or Build Tools for Visual
Studio were installed with the Visual C++ option.
note: VS Code is a different product, and is not sufficient.

error: could not compile `icu_normalizer_data` (build script) due to 1 previous error
warning: build failed, waiting for other jobs to finish...
error: could not compile `parking_lot_core` (build script) due to 1 previous error
error: could not compile `icu_properties_data` (build script) due to 1 previous error
error: could not compile `getrandom` (build script) due to 1 previous error
error: could not compile `zmij` (build script) due to 1 previous error
error: could not compile `quote` (build script) due to 1 previous error
error: could not compile `typeid` (build script) due to 1 previous error
error: could not compile `serde_core` (build script) due to 1 previous error
error: could not compile `serde_core` (build script) due to 1 previous error
error: could not compile `serde` (build script) due to 1 previous error
error: could not compile `proc-macro2` (build script) due to 1 previous error
error: could not compile `thiserror` (build script) due to 1 previous error
```

- **Where cargo check got to**: Compiled ~14 of the early dependency build scripts (`proc-macro2`, `quote`, `serde_core`, `parking_lot_core`, `thiserror`, `getrandom`, `serde`, `typeid`, `writeable`, `utf8_iter`, `strsim`, `litemap`, `zmij`, `icu_properties_data`, `icu_normalizer_data`) — **none of the project's own crates were compiled**. Build-script link step is the first place MSVC is required, and that's where it died.
- **Errors**: 12 (all "could not compile <dep> (build script)" — root cause is the single `linker 'link.exe' not found` error)
- **Warnings**: 1 (`warning: build failed, waiting for other jobs to finish...` — automatic cargo wording, not source code)
- **Project-source warnings**: 0 reachable — the build never reached `minimax-code`'s own crates
- **rustc itself**: runs fine (`rustc 1.91.1`); failure is downstream of rustc at the linker invocation
- **cargo build --release**: not run — same blocker (and on top of that, release profile uses `lto = true` + `codegen-units = 1`, so even with the linker present the first release build would be 10–20 min)
- **Precise release failure reason (predicted)**: identical `link.exe` not found at the very first dependency build-script link. No release binary would be produced.

## Binary verification

| Check | Result |
|---|---|
| `src-tauri/target/release/minimax-code.exe` | **Does not exist** |
| `src-tauri/target/release/` | **Does not exist** (only `target/debug/` from a prior run) |

The release binary cannot be produced without resolving the linker blocker.

## Code review of the Tauri source (read-only)

I read `Cargo.toml`, `tauri.conf.json`, `lib.rs`, `ipc.rs`, `commands.rs`, `main.rs`. Project code itself looks clean and well-typed for Tauri 2.1 — no obvious compile-time mistakes spotted in the 4 source files (≈ 320 LOC total). If the linker were available, `cargo check` would very likely pass. The blocker is the dependency graph's build scripts, which are compiled even during `cargo check`.

Dependencies in `Cargo.toml` (all on stable major versions, no upgrades required):
- `tauri = "2.1"` + `tauri-build = "2.0"` (build-dep) — Tauri 2.x is the current line
- `tauri-plugin-shell = "2.0"`
- `serde 1.0` / `serde_json 1.0`
- `tokio 1.40` (`full` features)
- `uuid 1.10` (with `v4` + `serde`)
- `once_cell 1.19` / `parking_lot 0.12` / `thiserror 1.0` / `anyhow 1.0` / `futures-util 0.3`
- `tracing 0.1` / `tracing-subscriber 0.3` (`env-filter`)

Release profile uses `panic = "abort"`, `lto = true`, `codegen-units = 1`, `opt-level = "s"`, `strip = true` — standard for a Tauri release binary.

## Remediation (for the human, not in scope here)

**Required user action — pick ONE**:

1. **Install Visual Studio Build Tools (preferred — keeps the existing MSVC Rust)**
   - Download [Build Tools for Visual Studio 2022](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
   - In the installer, tick **"Desktop development with C++"** (includes MSVC v143, Windows SDK, `link.exe`)
   - Restart the shell, then re-run `cargo check` / `cargo build --release` from `src-tauri/`
   - First cold build will be 10–20 min (large dependency tree, LTO)

2. **Switch to the GNU toolchain (smaller installer)**
   - `winget install Rustlang.Rustup`
   - `rustup default stable-gnu`
   - Re-run from `src-tauri/`
   - Note: existing `target/` cache is tied to the MSVC triple and will be rebuilt

After remediation, the verifier can re-run:
```powershell
Set-Location "D:\工作\城建院\2606\src-tauri"
cargo check
cargo build --release
Get-Item target\release\minimax-code.exe   # expect ~10–20 MB
```

## Changed files

- **Repo source**: none. No `Cargo.toml` changes, no Rust source changes, no `tauri.conf.json` changes (per the "no major-version bump / no code rewrite" constraint).
- **Repo build report**: a copy of this deliverable is committed at `docs/build-reports/2026-06-02-tauri-build-blocked.md` so the env-blocker is recorded in the repo.
- **Plans output**: this file (`C:\Users\Hitao\.mavis\plans\plan_7cb5a586\outputs\tauri-build\deliverable.md`) is the engine-consumed artifact.
- **Logs in plans output dir**: `cargo-check-full.log` (43 lines, full first run) + `cargo-check-tail30.log` (30 lines, fresh run matching the `tail -30` excerpt above).

## Notes for the verifier

- I did NOT mark the task `done` — the deliverable is `BLOCKED` and `cargo build --release` did not produce a binary. Please do not treat this as a pass.
- The blocker is **environmental**, not a MiniMax Code defect. The source files are within the 320 LOC I read; nothing stands out as obviously wrong for Tauri 2.1.
- Full cargo-check log is saved at `C:\Users\Hitao\.mavis\plans\plan_7cb5a586\outputs\tauri-build\cargo-check-full.log` (43 lines).
- The `.rustc_info.json` and `target/debug/` already exist from a prior local run; no new artifacts were created in this session.
- If the orchestrator wants to proceed without the human installing Build Tools, the only escape hatch within the constraints is for a human to install VS Build Tools (or `rustup-init` + `stable-gnu`) — that takes ~5 minutes for the user and unblocks all `cargo` work in this project.
