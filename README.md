# Codex 0.160.0: native Mac host, Android output

This bundle implements a separate macOS → Android build. Rust, GN, Ninja, Clang and V8's build-time programs run on macOS. The output is Codex CLI **0.160.0** for Android/Termux, including `codex-code-mode-host`. It does not produce a Codex executable for macOS.

It uses neither Docker nor Linux emulation, FUSE, the Termux Linux package-builder, or TUR. It does not edit repositories on GitHub, dispatch workflows, or reuse your Docker build directory.

## Start on your Mac

Extract the ZIP and enter its `codex-native-macos` directory. Keep all bundle files together.

```bash
cd ~/Downloads/codex-native-macos
bash build-codex-android-native.sh setup
bash build-codex-android-native.sh build aarch64
```

Requirements: a native Terminal, native [Homebrew](https://brew.sh), full Xcode installed and initialized, and a macOS SDK **15 or newer**. The driver checks these before compiling. Command Line Tools alone cannot satisfy Chromium's `xcodebuild` calls. If Xcode is installed but not selected:

```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -runFirstLaunch
```

`setup` installs Homebrew build tools, V8's pinned native **Chromium GN**, Rust **1.95.0** with the Android standard library, and the verified Mac Android NDK **r30 / 30.0.16248370**. GN is downloaded from official Chromium CIPD for your **Mac host CPU**, with archive and executable SHA-256 verification. It does not require a Homebrew `gn` formula or a pre-existing GN on PATH. It runs small C and Rust cross-compilation checks, but does **not** compile Codex. The NDK download is approximately 1 GB. All Python execution uses `uv` and Python 3.11.

If the previous bundle stopped at `Missing gn. Run setup first.`, replace the entire extracted bundle with this updated version and run `setup` again. That early failure did not prepare a native workspace, so you can keep the default workspace. The Homebrew "already installed and up-to-date" warnings are harmless; no reinstall is needed.

`build` uses `~/codex-termux-native-macos` by default. Your `~/codex-termux-macos` Docker workspace is separate. Allow **50 GB or more free space**; the driver refuses to start a build with less than 25 GiB free. Default concurrency is four jobs. To reduce memory use:

```bash
CODEX_BUILD_JOBS=2 bash build-codex-android-native.sh build aarch64
```

To choose another workspace, use the same variable for every action; avoid spaces in the path:

```bash
export CODEX_NATIVE_WORKDIR="$HOME/codex-android-native-test"
bash build-codex-android-native.sh setup
bash build-codex-android-native.sh build aarch64
```

Android `x86_64` is an additional target:

```bash
bash build-codex-android-native.sh setup x86_64
bash build-codex-android-native.sh build x86_64
```

## Progress and resume

In another Terminal in this bundle directory:

```bash
bash build-codex-android-native.sh monitor
```

Logs are saved in `~/codex-termux-native-macos/logs/`. `caffeinate` prevents idle sleep while the operation runs. Verbose Cargo and Ninja output show actual build activity. Native V8 compilation can still take a long time; no build duration has been measured on your Mac.

After an ordinary compiler failure, rerun `build` to reuse Cargo and V8 outputs. A build does not delete the previous build cache. After an interrupted **source patch/preparation** phase, it refuses to reuse partially patched sources. Its error names the source directory to rename before retrying. After a forcibly killed launcher, inspect running processes before removing the private `.native-build-lock` directory. Do not start two operations in the same workspace simultaneously.

Changing bundle code after preparing sources requires a new `CODEX_NATIVE_WORKDIR`, because the driver records the launcher identity and patched source hashes. This prevents mixing different porting revisions unnoticed.

## Output and Termux installation

After a successful build, `~/codex-termux-native-macos/output/aarch64/` contains:

- `codex-0.160.0-android-aarch64.tar.gz` with both Android binaries, licenses, manifest and build metadata;
- `codex-0.160.0-android-aarch64.tar.gz.sha256`;
- `install-codex-termux.sh`;
- `BUILD.json`.

Copy the archive, its `.sha256` sidecar and the installer together to the Android device. Then, inside Termux in that directory:

```bash
bash install-codex-termux.sh \
  --archive codex-0.160.0-android-aarch64.tar.gz --yes
codex --version
```

The installer uses already configured official Termux packages and never activates TUR. It validates archive and binary hashes, Android architecture and API, dependency versions, and `codex --version`, `codex --help` and the companion's startup before installing. An APT-owned existing Codex binary is protected. `--replace` explicitly allows replacement of a previous manual installation after backing it up.

## Porting changes and integrity

- Codex tag `rust-v0.160.0` is checked against commit `a956835d020762cb2b570053af06f643a11c0ecc`.
- Rust `1.95.0`, crate `cc 1.2.55`, crate `v8 150.4.0`, rusty_v8 commit `5c15a6995c9bb4bacd3e341b59fff32c909c80bf`, V8 commit `ac1e23989121713ca642f6650b34deff7b686896`, and Chromium build commit `8acb33ac8dceef0503443109c0a92988189563ef` remain pinned.
- GN revision `3357c4f51b1a9e676378c695dd9c7e9911c35ee6` comes from that V8 commit's `DEPS`. Both Mac CPU packages use fixed CIPD instance IDs and SHA-256 values. The driver verifies GN's native Mach-O architecture and reported revision, adds its private executable directory to PATH, and supplies its absolute path to V8. Downloads are reused with checksum checks. `metadata/gn-bootstrap.json` records the source and hashes and is included in `BUILD.json`.
- Mac NDK r30's official DMG size and published SHA-1 are checked before mounting and copying; SHA-256 is additionally recorded. The NDK's `source.properties` must match the exact revision.
- Official Termux `libc++`, OpenSSL and zlib are staged into this private workspace. `InRelease` is verified with signing keys authenticated against the pinned Termux keyring Git objects. Signed index hashes and individual package SHA-256 values are checked. No repository is enabled on your Mac or Android device by this staging operation.
- The existing `cc` host/target flags patches, V8 target archive/binding selection patch and GN NDK argument declarations are retained.
- The generated `0004-native-macos-android.patch` replaces V8's automatic **Linux NDK r26c** download with the explicitly verified **Mac NDK r30**. It removes the Android x86_64 Debian sysroot download. The Linux-only `0002-install-sysroot.patch` is not applied. Android compilation uses the NDK sysroot; Mac host tools use the Xcode SDK.
- Rust's target-specific bindgen variables point to the Android sysroot. GN's separate bindgen processes discard Cargo's extra flags and `TARGET`, keeping Mac host actions separate from Android target actions.
- `cargo metadata` prepares each lockfile; before/after copies, diffs and version/Git revision checks are recorded. `cargo vendor` and every compilation keep `--locked`. V8 keeps `v8_enable_sandbox` so the Code Mode ABI matches.
- Extra GN arguments and final `args.gn`, when GN creates it, are preserved under `metadata/gn-<architecture>/` on both success and failure. Auxiliary Android platform and Catapult checkout revisions are recorded there.
- Packaging checks both outputs as 64-bit Android ELF PIE files with `/system/bin/linker64`. It rejects Mach-O, Linux loaders, unexpected libraries and runtime paths pointing into the build workspace.

The metadata directory and logs remain alongside the workspace output. They include lockfile evidence and the generated Mac porting patch; they are useful if a later compiler or GN error needs inspection.

Codex and the standalone Rust V8 crate use Rust 1.95.0. V8's GN build also downloads the Chromium Clang and auxiliary Rust toolchain selected by its pinned upstream scripts. These are native Mac tools; their versions and executable SHA-256 hashes are recorded after a successful build.

## Validation scope

The bundle includes 29 offline regression tests using inspected upstream `cc`, V8 and Chromium files. They cover the real Bash setup entry point without Homebrew GN, pinned GN provisioning for both Mac host CPUs, checksum/architecture/revision failures, safe ZIP extraction, setup sequencing, patch application without fuzz, Mac/Android compiler separation, locked command sequencing, preservation of GN arguments on failure, both binary outputs, Android ELF rejection rules, packaging and SHA-256.

Run them with:

```bash
python3 -m unittest tests/test_native_macos.py
```

The tests use a host `readelf` command where available; macOS Homebrew LLVM supplies `llvm-readelf`.

**Status**: The end-to-end build has **successfully completed natively on macOS** (Apple Silicon `aarch64-apple-darwin` host, M2 Max, Xcode 27, NDK r30). Both binaries (`bin/codex` and `bin/codex-code-mode-host`) have been compiled, linked, verified against ELF64 / `/system/bin/linker64` / `/data/data/com.termux/files/usr/lib` RUNPATH requirements, and packaged into `output/aarch64/codex-0.160.0-android-aarch64.tar.gz` with verified SHA-256 checksums and `BUILD.json` provenance metadata. Validation on a physical Android/Termux device using `install-codex-termux.sh` is the remaining verification step.

Primary references: [Android NDK downloads](https://developer.android.com/ndk/downloads), [NDK cross-compilation host tools](https://developer.android.com/ndk/guides/other_build_systems), [V8's pinned GN revision](https://github.com/denoland/v8/blob/ac1e23989121713ca642f6650b34deff7b686896/DEPS), [rusty_v8's GN downloader](https://github.com/denoland/rusty_v8/blob/v150.4.0/tools/ninja_gn_binaries.py), and the pinned source commits above.

