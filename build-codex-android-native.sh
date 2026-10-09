#!/bin/bash
# macOS host -> Android target. Compatible with Apple's Bash 3.2.
set -euo pipefail
script_dir="$(cd "$(dirname "$0")" && pwd -P)"
action="${1:-help}"
architecture="${2:-aarch64}"
work="${CODEX_NATIVE_WORKDIR:-$HOME/codex-termux-native-macos}"
case "$action" in
  help|--help|-h)
    cat <<'EOF'
Usage: bash build-codex-android-native.sh setup
       bash build-codex-android-native.sh check [aarch64|x86_64]
       bash build-codex-android-native.sh build [aarch64|x86_64]
       bash build-codex-android-native.sh monitor

Codex 0.162.0, Rust 1.95.0, Android NDK r30, API 24.
Native macOS host tools; Android output. No container or emulation.
Requires full Xcode with macOS SDK >= 15 and native Homebrew.
Setup installs Homebrew dependencies, pinned Chromium GN, Rust and the Mac NDK; it does not build.
CODEX_NATIVE_WORKDIR selects an isolated workspace (default ~/codex-termux-native-macos).
CODEX_BUILD_JOBS controls concurrency (default 4).
EOF
    exit 0 ;;
  setup|check|build|monitor) ;;
  *) echo "Unknown action: $action" >&2; exit 2 ;;
esac
[[ "$(uname -s)" == Darwin ]] || { echo 'Run this launcher on your Mac.' >&2; exit 1; }
if [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" == 1 ]]; then
  echo 'This Terminal runs under Rosetta. Open a native Terminal before continuing.' >&2
  exit 1
fi
case "$architecture" in aarch64|x86_64) ;; *) echo 'Only aarch64 and x86_64 are supported.' >&2; exit 2;; esac
if [[ "$action" == monitor ]]; then
  [[ -f "$work/latest-log" ]] || { echo "No native build log in $work" >&2; exit 1; }
  exec tail -n 50 -F "$(cat "$work/latest-log")"
fi
# Chromium's Mac host toolchain invokes xcodebuild, not just clang.
if ! xcodebuild -version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Full Xcode is required; Command Line Tools alone are insufficient.
Install Xcode from the App Store or developer.apple.com, launch it once, then:
  sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
  sudo xcodebuild -runFirstLaunch
Run setup again after Xcode finishes installing.
EOF
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  for brew_candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$brew_candidate" ]]; then
      export PATH="$(dirname "$brew_candidate"):$PATH"
      break
    fi
  done
fi
command -v brew >/dev/null || {
  echo 'Install native Homebrew from https://brew.sh, then run setup again.' >&2
  exit 1
}
if [[ "$action" == setup ]]; then
  brew install uv rustup ninja llvm cmake pkgconf libarchive gnupg gpatch
fi
# GN is provisioned from Chromium CIPD by the driver, not Homebrew.
# Explicit prefixes also work when an installed formula has not been linked.
for formula in uv rustup ninja cmake pkgconf gnupg; do
  export PATH="$(brew --prefix "$formula")/bin:$PATH"
done
for tool in uv ninja cmake pkg-config gpgv; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Run setup first." >&2; exit 1; }
done
export CODEX_NATIVE_RUSTUP="$(brew --prefix rustup)/bin"
export PATH="$CODEX_NATIVE_RUSTUP:$HOME/.cargo/bin:$PATH"
export CODEX_NATIVE_LLVM="$(brew --prefix llvm)"
export CODEX_NATIVE_BSDTAR="$(brew --prefix libarchive)/bin/bsdtar"
export CODEX_NATIVE_PATCH="$(brew --prefix gpatch)/bin/gpatch"
[[ -x "$CODEX_NATIVE_BSDTAR" ]] || { echo 'Run setup to install libarchive.' >&2; exit 1; }
mkdir -p "$work/logs"
log="$work/logs/$action-$(date +%Y%m%d-%H%M%S)-$$.log"
printf '%s\n' "$log" > "$work/latest-log"
echo "Native macOS -> Android $architecture; log: $log"
# caffeinate prevents sleep while commands run. PIPESTATUS preserves failures.
set +e
caffeinate -i uv run --no-project --python 3.11 \
  "$script_dir/native_build.py" "$action" "$architecture" --work "$work" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
exit "$status"
