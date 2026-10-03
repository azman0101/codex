# AGENTS.md — Codex 0.160.0 native macOS → Android build

Notes for coding agents (and humans) working on this bundle. Read this before changing anything.

## What this project is

A launcher + Python driver that cross-compiles **Codex CLI 0.160.0** (plus `codex-code-mode-host`) for **Android/Termux** using only **native macOS host tools**. No Docker, no Linux emulation. V8 (via `rusty_v8` 150.4.0) is built from source with Chromium GN/Ninja on the Mac.

| File | Role |
|---|---|
| `build-codex-android-native.sh` | Bash 3.2 launcher: checks Xcode/Homebrew, sets `PATH` and `CODEX_NATIVE_*` vars, runs the driver via `uv run --python 3.11` |
| `native_build.py` | Driver: `setup` / `check` / `build`. Pinned versions and hashes are at the top of the file |
| `verified_tools.py` | ELF and lockfile verification helpers |
| `patches/` | Patches for rust `cc`, `rusty_v8` and Chromium `build/`. `0004` is **generated** by `native_v8_patch()` and compared byte-for-byte with the stored file |
| `tests/test_native_macos.py` | Offline regression tests |
| `VALIDATION.json` | Recorded validation status |

Default workspace: `~/codex-termux-native-macos` (override with `CODEX_NATIVE_WORKDIR`). Build logs: `<work>/logs/`, latest log path in `<work>/latest-log`.

## Commands

```bash
bash build-codex-android-native.sh setup            # tools, GN, Rust 1.95.0, NDK r30, compiler probes
bash build-codex-android-native.sh build aarch64    # full build (V8 takes hours)
bash build-codex-android-native.sh monitor          # tail latest log
python3 -m unittest tests/test_native_macos.py      # 29 tests, 2 skipped without llvm-readelf on PATH
```

## Conventions and gotchas

- **Bundle identity check.** `main()` hashes every `*.py`, `*.sh`, `*.diff` and `*.patch` file (excluding `tests/`) into `<work>/metadata/launcher-identity.json`. Editing any of these makes the next run fail with *"The build bundle changed"*. During development, delete `<work>/metadata/launcher-identity.json` after each edit. **Do not** delete prepared sources or caches. `.md`/`.json` edits do not trigger the check.
- **Resume markers.** `metadata/codex-prepared.json` and `metadata/v8-prepared.json` record prepared state. If the driver is interrupted mid-patch it refuses to continue. Fix: `git checkout -- . && git clean -fd` in the affected `sources/*` repo, or rename it.
- **Patches apply with `--fuzz=0`** and a dry run first. If upstream drifts they must fail closed; never loosen this.
- **Toolchain resolution.** Homebrew's `rust` formula installs `/opt/homebrew/bin/cargo`, which does not understand `+1.95.0`, and `uv run` puts `/opt/homebrew/bin` first on `PATH`. `ensure_rustup_path()` moves the rustup shim directory (`CODEX_NATIVE_RUSTUP`) back to the front of `PATH`. Keep calling it.
- **Environment hygiene.** `target_environment()` strips leaked CC/CFLAGS/RUSTFLAGS/etc. All env values must be strings, never `None`.

## Caching and Incremental Builds

Do we cache compiled files? **Yes, extensively.**

| Cache Layer | Location | Contents | Invalidation / Behavior |
|---|---|---|---|
| **V8 Ninja Objects & Library** | `<work>/sources/rusty_v8/target/<arch>-linux-android/release/gn_out/` | All 4,489 C++ object files (`.o`), intermediate host tools, and `obj/librusty_v8.a` (170 MB) | Tracked by Ninja (`.ninja_log`, `.ninja_deps`). If `args.gn` and sources are unchanged, Ninja skips compilation (`no work to do`) in <1s. |
| **Rusty_v8 Crate Artifacts** | `<work>/sources/rusty_v8/target/` | `libv8.rlib` (176 MB), `src_binding.rs` (38 KB), proc-macros, build scripts | Tracked by Cargo fingerprinting. Rebuilt only if Rust source files change. |
| **Codex Crate Artifacts** | `<work>/sources/codex/target/` | Intermediate crate objects, `codex`, and `codex-code-mode-host` binaries | Standard Cargo incremental compilation. Reuses prebuilt `librusty_v8.a` via `RUSTY_V8_ARCHIVE_<TRIPLE>`. |
| **Cargo Registry & Git** | `<work>/cargo-home/` | Crates.io index and downloaded crate tarballs | Retained indefinitely; avoids re-downloading crates. |
| **Toolchains & Downloads** | `<work>/downloads/`, `<work>/sdk/`, `<work>/tools/` | NDK r30 DMG, GN binary, Termux `.deb` packages | Verified by hash once; reused across all runs without re-downloading or re-extracting. |
| **Sysroot Staging** | `<work>/dependencies/<arch>/` | Staged Termux `libc++`, `openssl`, `zlib` headers and `.so` files | Reused directly; verified via `InRelease` GPG signature. |

**Important caching details:**
- **External compiler caches (`ccache` / `sccache`):** Intentionally stripped by `target_environment()`. They are not used to prevent host/target sysroot contamination and compiler confusion between Chromium clang, NDK clang, and Homebrew clang. Ninja's local dependency database is sufficient.
- **V8 downstream reuse:** Codex's vendored `v8` crate does *not* recompile V8 from C++. It is patched (`rusty-v8-search-files-with-target-suffix.diff`) to directly consume the prebuilt `librusty_v8.a` archive generated by the `sources/rusty_v8` build step.
- **Preserving caches across bundle edits:** If you edit driver scripts or patches, delete `<work>/metadata/launcher-identity.json` to allow the build to proceed. **Do not** delete the workspace directory or `sources/`, as that would discard the cached 170 MB `librusty_v8.a`.
- **Cache invalidation triggers:** Modifying `EXTRA_GN_ARGS` or `gn_args` (e.g. changing quotes, flags, or API levels) marks Ninja targets dirty and will trigger a full recompilation of V8.

## Progress (as of 2026-10-03)

| Stage | Status |
|---|---|
| `setup`: Homebrew tools, pinned GN (CIPD), Rust 1.95.0 + android std | ✅ |
| NDK r30 DMG download, SHA-1 check, extraction | ✅ (fixed) |
| C and Rust Android probe binaries (ELF/linker64 verified) | ✅ |
| Termux dependency staging (InRelease gpgv, libc++/openssl/zlib) | ✅ (fixed) |
| Codex source clone, `cargo metadata`, `cargo vendor`, cc/v8 patches | ✅ (fixed) |
| rusty_v8 clone, patches 0001/0003/0004 | ✅ |
| V8 `gn gen` for `target_os="android"` on a Mac host | ✅ (fixed, patch 0005) |
| V8 ninja: smoke build of `v8_libbase` (Android arm64 + Mac host objects) | ✅ |
| **Full V8 ninja build → `librusty_v8.a`** (4,489 / 4,489 targets) | ✅ |
| V8 crate compilation with rustc / bindgen (`libv8.rlib`) | ✅ (fixed, patch 0006) |
| Codex `cargo rustc` for `codex` and `codex-code-mode-host` | ✅ |
| **Packaging (`output/<arch>/codex-0.160.0-android-<arch>.tar.gz`)** | ✅ (376 MB) |
| Validation on an Android device | ⏳ **next** (deploy via `install-codex-termux.sh`) |

### Fixes applied in this session

1. **NDK layout** (`install_ndk`): the r30 DMG has two `source.properties` files with the matching revision, one at the volume root and one under `AndroidNDK16248370.app/Contents/NDK`. The fix only accepts candidates that also contain `toolchains/llvm/prebuilt/darwin-x86_64/bin/clang`. Test added.
2. **Launcher `brew` PATH**: the script only prepends the standard Homebrew paths when `brew` is not already on `PATH`, so the test harness mocks keep working.
3. **gpgv 2.5 output**: `gpgv --output FILE` leaves `FILE.part` behind and never renames it. The driver now uses `--output -` and redirects stdout, with a fallback rename of `Release.part`.
4. **`.deb` extraction**: Apple's BSD `/usr/bin/ar` lists GNU members as `data.tar.xz/` and cannot extract them. The driver now uses Homebrew `llvm-ar` and strips trailing `/` from member names.
5. **`cargo +toolchain`**: the driver now uses the rustup shim (see Conventions). Added `CODEX_NATIVE_RUSTUP`, `rustup_bin_dir()` and `ensure_rustup_path()`.
6. **`GN`/`NINJA` env**: these default to `""` instead of `None`, because `None` crashed `subprocess`.
7. **Chromium Linux-host assertion**: new `patches/0005-allow-android-on-macos-host.patch`, applied in `prepare_v8()`:
   - `build/config/BUILDCONFIG.gn` now allows `host_os == "mac"` for Android targets.
   - `build/config/clang/BUILD.gn`: Chromium's Mac clang package has no Android `clang_rt` runtimes, so on a Mac host they are taken from `$android_ndk_root/toolchains/llvm/prebuilt/darwin-x86_64/lib/clang/21/lib`.
8. **V8 `prepared_state` ordering**: `prepared_state()` tracks `build_diff_sha256` for the `build/` submodule. Patch 0005 was initially applied after `v8-prepared.json` had been written, causing `prepared_state` checksum mismatches on subsequent runs. Patch 0005 is now applied before recording `prepared_state`, and existing pre-0005 markers are automatically migrated.
9. **Clang 23 bindgen enum mangling in rusty_v8**: Homebrew LLVM 23's libclang omits the outer `v8_String_` namespace prefix when mangling anonymous enum constants inside `v8::String::WriteFlags`, emitting `WriteFlags_kNullTerminate` and `WriteFlags_kReplaceInvalidUtf8` into `src_binding.rs`. Upstream `src/string.rs` expects `v8_String_WriteFlags_*`. Added `patches/0006-bindgen-clang23-write-flags.patch` providing compatibility aliases in `src/binding.rs`. (Note: when generating the patch, the hunk line count must be `@@ -4,3 +4,10 @@` to prevent `gpatch` from truncating `v8_String_WriteFlags_kReplaceInvalidUtf8`). The patch is applied to both `sources/rusty_v8` and `codex/vendor/v8`, and `prepare_codex()` validates that `kReplaceInvalidUtf8` is present.

## Open issues and risks

- **Physical device validation.** Binaries have been verified with `llvm-readelf` (ELF64 little-endian ARM aarch64, PIE, dynamic linking, correct RUNPATH `/data/data/com.termux/files/usr/lib`), but have not yet been booted and tested on a physical Android/Termux device.
- **Mixed clang versions.** Objects were compiled with Chromium clang 23 and linked with NDK clang 21 builtins. Both binaries linked without symbols errors.
- **Harmless GN warning.** GN reports `use_system_xcode=true` as "Build argument has no effect"; it is set in `build()` GN args.
- **Reproducibility.** Before publishing final releases, a clean run in a fresh `CODEX_NATIVE_WORKDIR` can verify clean bootstrap end-to-end.
- **Docs not yet updated.** `README.md` can be refreshed with the native build results and instructions.

## Debugging tips

- Reproduce V8 `gn gen` quickly without cargo: copy the `GN args:` line from the build log and run `<work>/tools/gn-*/bin/gn --root=<work>/sources/rusty_v8 gen /tmp/gn_probe --args='…'`. Then use `ninja -C /tmp/gn_probe <target>` for targeted smoke builds. Delete `/tmp/gn_probe` afterwards.
- The effective GN args are copied to `<work>/metadata/gn-<arch>/args.gn`, even after a failure.
