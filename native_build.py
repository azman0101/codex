#!/usr/bin/env python3
"""Native macOS -> Android Codex 0.162.0. Run through uv, Python 3.11+."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import difflib
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.parse
import zipfile

import verified_tools as verified

ROOT = Path(__file__).resolve().parent
VERSION = "0.162.0"
RUST = "1.95.0"
V8 = "150.4.0"
CC_VERSION = "1.2.55"
CODEX_COMMIT = "c1382380de69521303b416720a52f42d51af6248"
V8_COMMIT = "5c15a6995c9bb4bacd3e341b59fff32c909c80bf"
BUILD_COMMIT = "8acb33ac8dceef0503443109c0a92988189563ef"
V8_ENGINE_COMMIT = "ac1e23989121713ca642f6650b34deff7b686896"
RECIPE_COMMIT = "f09736bd5da206635ad7336af79c04f512a9db7f"
TERMUX_COMMIT = "27da60be397a6e9eb898bba8d03bcd648782ccd5"
NDK_REVISION = "30.0.16248370"
NDK_SHA1 = "48591224b6657f46eebbc9d95d4af09bbed8d107"
NDK_SIZE = 1072970961
NDK_URL = "https://dl.google.com/android/repository/android-ndk-r30-darwin.dmg"
API = 24
# Pinned by V8_ENGINE_COMMIT's DEPS; CIPD instance IDs address archive contents.
GN_REVISION = "3357c4f51b1a9e676378c695dd9c7e9911c35ee6"
GN_PACKAGES = {
    "aarch64-apple-darwin": {
        "package": "gn/gn/mac-arm64",
        "instance_id": "nPM80tJCa7RVmrRhdOwQMerJkZ7SSXEXQye2q0Mx5BUC",
        "archive_sha256": "9cf33cd2d2426bb4559ab46174ec1031eac9919ed24971174327b6ab4331e415",
        "binary_sha256": "e013c1b3521a32469f12eb5887eb4a83ece624bc6e58e8da48a951d72b8d90df",
    },
    "x86_64-apple-darwin": {
        "package": "gn/gn/mac-amd64",
        "instance_id": "u03NQuU6mWX6BnEfYUpt-xgKkPqjdQgmNcU03UEuuOUC",
        "archive_sha256": "bb4dcd42e53a9965fa06711f614a6dfb180a90faa375082635c534dd412eb8e5",
        "binary_sha256": "06bb4f809167322249fba3ce20f194450fea2ce6cc6afe881b97f9ea267876f9",
    },
}
PREFIX = "/data/data/com.termux/files/usr"
REPOSITORY = "https://packages-cf.termux.dev/apt/termux-main/"
KEYS = {
    "2096779623": "95d9781b3f8d06dd71769e3cc5a3f36ea61c9efe",
    "agnostic-apollo": "2ad4eeda5d3ba72f0bed54eb11de257cf181a396",
    "grimler": "b681819339118aa488dec7a16a54e72c6b630e4e",
    "kcubeterm": "69c7cabd56650cab65abca429a8508a09418c0ad",
    "landfillbaby": "b8d149ad7de2723a85dfdff5a9c2807c710aed30",
    "mradityaalok": "a4abdbb4b87fff23969e18e3ebb15e7edc739944",
    "termux-autobuilds": "c5ed76a1b9a1f2bc2e296bdd5ca50cf1f1f12706",
    "termux-pacman": "d7e0ced082eb6e05945201c1fa511af66bb3a781",
    "thunder-coding": "2d172a0e3982ab84db8ebe1c772d3a9b17b52ed7",
}


def rustup_bin_dir() -> Path | None:
    candidates = []
    if "CODEX_NATIVE_RUSTUP" in os.environ:
        candidates.append(Path(os.environ["CODEX_NATIVE_RUSTUP"]))
    candidates.extend([
        Path("/opt/homebrew/opt/rustup/bin"),
        Path.home() / ".cargo/bin",
        Path("/usr/local/opt/rustup/bin"),
    ])
    for candidate in candidates:
        if (candidate / "cargo").exists():
            return candidate
    return None


def ensure_rustup_path(env: dict[str, str] | None = None) -> None:
    rustup_dir = rustup_bin_dir()
    if not rustup_dir:
        return
    rustup_str = str(rustup_dir)
    target = os.environ if env is None else env
    paths = target.get("PATH", "").split(os.pathsep)
    if rustup_str in paths:
        paths.remove(rustup_str)
    paths.insert(0, rustup_str)
    target["PATH"] = os.pathsep.join(paths)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run(*args, cwd=None, env=None, capture=False, output=None):
    command = [str(a) for a in args]
    print("+ " + shlex.join(command), flush=True)
    if output is not None:
        with Path(output).open("wb") as stream:
            subprocess.run(command, cwd=cwd, env=env, stdout=stream, check=True)
        return ""
    result = subprocess.run(command, cwd=cwd, env=env, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def download(url: str, path: Path, *, expected=None, algorithm="sha256", size=None):
    if not url.startswith("https://"):
        raise ValueError("HTTPS is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_name(path.name + ".part")
        run("curl", "--fail", "--location", "--retry", "3", "--proto", "=https",
            "--proto-redir", "=https", "--output", temporary, url)
        temporary.replace(path)
    actual = file_hash(path, algorithm)
    if (size is not None and path.stat().st_size != size) or (expected and actual != expected):
        path.unlink()
        raise ValueError(f"Integrity check failed for {url}; invalid cached download removed")
    return actual


def file_hash(path: Path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def clone(url: str, ref: str, expected: str, path: Path, submodules=False):
    if not path.exists():
        temporary = path.with_name(path.name + ".fetching")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        run("git", "init", temporary)
        run("git", "remote", "add", "origin", url, cwd=temporary)
        run("git", "fetch", "--depth=1", "origin", ref, cwd=temporary)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=temporary)
        if run("git", "rev-parse", "HEAD", cwd=temporary, capture=True) != expected:
            raise ValueError(f"Unexpected Git revision in {url}; expected {expected}")
        temporary.rename(path)
    if run("git", "rev-parse", "HEAD", cwd=path, capture=True) != expected:
        raise ValueError(f"Source checkout changed: {path}")
    if submodules:
        run("git", "submodule", "update", "--init", "--recursive", "--depth=1", cwd=path)
        for child, revision in {"build": BUILD_COMMIT, "v8": V8_ENGINE_COMMIT}.items():
            if run("git", "rev-parse", "HEAD", cwd=path / child, capture=True) != revision:
                raise ValueError(f"Unexpected pinned submodule: {child}")


def bundle_identity():
    return {str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".sh", ".diff", ".patch"}
            and "tests" not in p.parts}


def host_check():
    if platform.system() != "Darwin":
        raise ValueError("A native macOS host is required")
    host = platform.machine()
    if host not in {"arm64", "x86_64"}:
        raise ValueError(f"Unsupported Mac architecture: {host}")
    translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"],
                                capture_output=True, text=True)
    if translated.stdout.strip() == "1":
        raise ValueError("Rosetta is enabled for this shell; open a native Terminal")
    run("xcodebuild", "-version")
    sdk = run("xcrun", "--sdk", "macosx", "--show-sdk-version", capture=True)
    if int(sdk.split(".")[0]) < 15:
        raise ValueError(f"macOS SDK >=15 required by pinned Chromium; found {sdk}")
    for command in ("rustup", "cargo", "ninja", "cmake", "pkg-config", "gpgv", "curl", "git"):
        if not shutil.which(command):
            raise ValueError(f"Missing {command}; run setup")
    llvm = Path(os.environ["CODEX_NATIVE_LLVM"])
    for binary in ("clang", "llvm-readelf"):
        run(llvm / "bin" / binary, "--version")
    cpu = "arm64" if host == "arm64" else "x86_64"
    for path in (llvm / "bin/clang", llvm / "bin/llvm-readelf", Path(shutil.which("ninja"))):
        description = run("/usr/bin/file", "-L", path, capture=True)
        if "Mach-O" not in description or cpu not in description:
            raise ValueError(f"A native {cpu} Mac executable is required: {path}: {description}")
    if not (llvm / "lib" / "libclang.dylib").exists():
        raise ValueError("Homebrew LLVM libclang is missing")
    if not Path(os.environ["CODEX_NATIVE_PATCH"]).is_file():
        raise ValueError("GNU patch is missing; run setup")
    return "aarch64-apple-darwin" if host == "arm64" else "x86_64-apple-darwin"


def install_gn(work: Path, host: str):
    """Use V8's pinned native GN, regardless of a missing/wrong GN on PATH."""
    if host not in GN_PACKAGES:
        raise ValueError(f"No pinned GN for Mac host {host}")
    pin = GN_PACKAGES[host]
    archive = work / "downloads" / ("gn-" + host + "-" + GN_REVISION[:12] + ".zip")
    if not archive.exists():
        endpoint = "https://chrome-infra-packages.appspot.com/_ah/api/repo/v1/instance?" + urllib.parse.urlencode(
            {"package_name": pin["package"], "instance_id": pin["instance_id"]})
        response = json.loads(run("curl", "--fail", "--silent", "--show-error", "--retry", "3",
                                  "--proto", "=https", endpoint, capture=True))
        instance = response.get("instance", {})
        if (response.get("status") != "SUCCESS" or instance.get("package_name") != pin["package"]
                or instance.get("instance_id") != pin["instance_id"]):
            raise ValueError("Chromium CIPD returned an unexpected GN instance")
        fetch_url = response.get("fetch_url")
        if not isinstance(fetch_url, str) or not fetch_url.startswith("https://"):
            raise ValueError("Chromium CIPD did not return an HTTPS GN download URL")
        download(fetch_url, archive, expected=pin["archive_sha256"])
    else:
        # Recheck cached bytes before trusting them; no network is needed on resume.
        download("https://chrome-infra-packages.appspot.com/", archive, expected=pin["archive_sha256"])
    binary = work / "tools" / ("gn-" + host + "-" + GN_REVISION[:12]) / "bin" / "gn"
    if not binary.exists():
        with zipfile.ZipFile(archive) as contents:
            # Extract only the root executable; never unpack arbitrary archive paths.
            if contents.namelist().count("gn") != 1:
                raise ValueError("Pinned GN archive must contain exactly one root gn executable")
            data = contents.read("gn")
        if hashlib.sha256(data).hexdigest() != pin["binary_sha256"]:
            raise ValueError("Pinned GN executable checksum failed")
        binary.parent.mkdir(parents=True, exist_ok=True)
        temporary = binary.with_name("gn.part")
        temporary.write_bytes(data)
        temporary.chmod(0o755)
        temporary.replace(binary)
    if binary.is_symlink() or file_hash(binary) != pin["binary_sha256"]:
        raise ValueError(f"Installed GN was modified: {binary}. Rename it and run setup again.")
    binary.chmod(0o755)
    cpu = "arm64" if host.startswith("aarch64") else "x86_64"
    description = run("/usr/bin/file", "-L", binary, capture=True)
    if "Mach-O" not in description or cpu not in description:
        raise ValueError(f"Pinned GN is not a native {cpu} Mac executable: {description}")
    version = run(binary, "--version", capture=True)
    if GN_REVISION[:12] not in version:
        raise ValueError(f"Unexpected GN revision: {version}")
    os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ.get("PATH", "")
    write_json(work / "metadata/gn-bootstrap.json", dict(pin, git_revision=GN_REVISION,
               host=host, version=version, executable=str(binary)))
    print(f"Pinned native GN ready: {version} ({cpu})", flush=True)
    return binary


def install_ndk(work: Path):
    destination = work / "sdk" / "android-ndk-r30"
    marker = destination / ".codex-native-integrity.json"
    expected = {"url": NDK_URL, "sha1": NDK_SHA1, "revision": NDK_REVISION}
    if destination.exists():
        if not marker.exists() or json.loads(marker.read_text()) != expected:
            raise ValueError(f"Unverified or interrupted NDK directory: {destination}. Rename it and run setup again.")
    else:
        image = work / "downloads" / "android-ndk-r30-darwin.dmg"
        download(NDK_URL, image, expected=NDK_SHA1, algorithm="sha1", size=NDK_SIZE)
        write_json(work / "metadata" / "ndk-download.json", dict(expected, sha256=file_hash(image), size=NDK_SIZE))
        mount = plistlib.loads(subprocess.check_output(
            ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(image)]))
        volumes = [Path(e["mount-point"]) for e in mount["system-entities"] if "mount-point" in e]
        try:
            candidates = [p.parent for volume in volumes for p in volume.rglob("source.properties")
                          if re.search(r"Pkg\.Revision\s*=\s*" + re.escape(NDK_REVISION) + r"\s*$", p.read_text(), re.M)
                          and (p.parent / "toolchains/llvm/prebuilt/darwin-x86_64/bin/clang").exists()]
            if len(candidates) != 1:
                raise ValueError("The verified Mac NDK image has an unexpected layout")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".copying")
            if temporary.exists():
                shutil.rmtree(temporary)
            shutil.copytree(candidates[0], temporary, symlinks=True)
            write_json(temporary / marker.name, expected)
            temporary.rename(destination)
        finally:
            for volume in volumes:
                run("hdiutil", "detach", volume)
    revision = (destination / "source.properties").read_text()
    if not re.search(r"Pkg\.Revision\s*=\s*" + re.escape(NDK_REVISION) + r"\s*$", revision, re.M):
        raise ValueError("Installed NDK revision changed")
    toolchain = destination / "toolchains/llvm/prebuilt/darwin-x86_64"
    if not (toolchain / "bin/clang").exists():
        raise ValueError("Mac NDK toolchain is missing")
    run(toolchain / "bin/clang", "--version")
    return destination


def target_environment(ndk: Path, arch: str, host: str, work: Path):
    env = dict(os.environ)
    # Do not leak old Linux/Docker target settings into macOS host build scripts.
    for key in list(env):
        if key in {"CC", "CXX", "AR", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "HOST_CFLAGS", "HOST_CXXFLAGS", "TARGET_CFLAGS", "TARGET_CXXFLAGS", "CXXSTDLIB", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "GN_ARGS", "EXTRA_GN_ARGS", "SDKROOT", "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "RUSTUP_TOOLCHAIN", "CARGO_BUILD_TARGET", "CLANG_BASE_PATH", "V8_FROM_SOURCE"} or key.startswith(("CC_", "CXX_", "AR_", "CFLAGS_", "CXXFLAGS_", "CARGO_TARGET_", "BINDGEN_EXTRA_CLANG_ARGS", "RUSTY_V8_", "OPENSSL_")):
            env.pop(key)
    triple = arch + "-linux-android"
    tag = triple.replace("-", "_")
    toolchain = ndk / "toolchains/llvm/prebuilt/darwin-x86_64"
    clang_target = triple + str(API)
    env.update({
        "RUSTUP_TOOLCHAIN": RUST,
        "CARGO_HOME": str(work / "cargo-home"),
        "LIBCLANG_PATH": str(Path(env["CODEX_NATIVE_LLVM"]) / "lib"),
        "GN": shutil.which("gn") or "", "NINJA": shutil.which("ninja") or "",
        "PYTHON": sys.executable,
        # Native host tools stay first. All Android compiler choices are explicit.
        "PATH": env["PATH"],
        "CARGO_TARGET_" + tag.upper() + "_LINKER": str(toolchain / "bin" / (clang_target + "-clang")),
        "CC_" + tag: str(toolchain / "bin" / (clang_target + "-clang")),
        "CXX_" + tag: str(toolchain / "bin" / (clang_target + "-clang++")),
        "AR_" + tag: str(toolchain / "bin/llvm-ar"),
        "CFLAGS_" + tag: "-fPIC -O2",
        "CXXFLAGS_" + tag: "-fPIC -O2",
        "CC_" + host.replace("-", "_"): "/usr/bin/clang",
        "CXX_" + host.replace("-", "_"): "/usr/bin/clang++",
        "BINDGEN_EXTRA_CLANG_ARGS_" + tag: shlex.join([
            "--target=" + clang_target, "--sysroot=" + str(toolchain / "sysroot")]),
    })
    ensure_rustup_path(env)
    return env


def ensure_std_android_flock(env: dict):
    """Upstream Rust 1.95.0 std omits target_os = 'android' in unix flock stubs.
    Patch the local toolchain std source so -Z build-std provides functional flock on Android Bionic."""
    try:
        rustc = run("rustup", "which", "rustc", env=env, capture=True).strip()
        if not rustc:
            return
        p = Path(rustc)
        if len(p.parents) < 2:
            return
        toolchain_dir = p.parents[1]
        unix_rs = toolchain_dir / "lib/rustlib/src/rust/library/std/src/sys/fs/unix.rs"
        if not unix_rs.exists():
            run("rustup", "component", "add", "--toolchain", RUST, "rust-src", env=env)
        if unix_rs.exists():
            content = unix_rs.read_text()
            flock_pos = content.find("pub fn lock(")
            if flock_pos != -1 and 'target_os = "android"' not in content[flock_pos:]:
                before = content[:flock_pos]
                after = content[flock_pos:].replace('target_os = "linux",', 'target_os = "android",\n        target_os = "linux",')
                unix_rs.write_text(before + after)
                print("Patched Rust standard library: enabled flock on Android Bionic.", flush=True)
    except Exception:
        pass


def verify_executable(path: Path, arch: str, readelf: Path):
    if verified.elf_arch(path) != arch:
        raise ValueError(f"Output architecture mismatch: {path}")
    verified.check_android_elf(path)
    programs = run(readelf, "-l", path, capture=True)
    if "[Requesting program interpreter: /system/bin/linker64]" not in programs:
        raise ValueError(f"An Android linker64 executable is required: {path}")
    dynamic = run(readelf, "-d", path, capture=True)
    system = {"libc.so", "libdl.so", "libm.so", "liblog.so", "libandroid.so", "libz.so"}
    supplied = {"libc++_shared.so", "libssl.so.3", "libcrypto.so.3"}
    needed = set(re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic))
    if needed - system - supplied:
        raise ValueError(f"Unexpected runtime libraries in {path}: {sorted(needed-system-supplied)}")
    for line in dynamic.splitlines():
        if "(RPATH)" in line or "(RUNPATH)" in line:
            paths = re.search(r"\[([^]]*)\]", line)
            if not paths or any(p not in {PREFIX + "/lib", "$ORIGIN", "$ORIGIN/../lib"} for p in paths[1].split(":")):
                raise ValueError(f"Non-Termux runtime path: {line}")
    return needed


def preflight(ndk: Path, arch: str, env: dict, work: Path):
    triple = arch + "-linux-android"
    tag = triple.replace("-", "_")
    directory = work / "probe" / arch
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test.c").write_text("int main(void) { return 0; }\n")
    run(env["CC_" + tag], "-fPIE", "-pie", directory / "test.c", "-o", directory / "android-probe")
    verify_executable(directory / "android-probe", arch, ndk / "toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-readelf")
    rustc = run("rustup", "run", RUST, "rustc", "-vV", capture=True)
    expected_host = "aarch64-apple-darwin" if platform.machine() == "arm64" else "x86_64-apple-darwin"
    if not re.search(r"^host: " + re.escape(expected_host) + "$", rustc, re.M):
        raise ValueError("Rust host is not native to this Mac")
    if not re.search(r"^release: " + re.escape(RUST) + "$", rustc, re.M):
        raise ValueError("Rust version changed")
    (directory / "test.rs").write_text("fn main() {}\n")
    run("cargo", "--version", env=env)
    run("rustup", "run", RUST, "rustc", "--target", triple, "-C", "linker=" + env["CC_" + tag],
        directory / "test.rs", "-o", directory / "rust-android-probe", env=env)
    verify_executable(directory / "rust-android-probe", arch, ndk / "toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-readelf")
    print("Native compiler and Rust Android probes passed; neither target executable was run on the Mac.", flush=True)


def release_entries(text: str):
    section = text.split("SHA256:\n", 1)
    if len(section) != 2:
        raise ValueError("Signed Release lacks SHA256 hashes")
    entries = {}
    for line in section[1].splitlines():
        if not line.startswith(" "):
            break
        match = re.fullmatch(r"\s+([a-f0-9]{64})\s+(\d+)\s+(\S+)", line)
        if match:
            entries[match[3]] = (match[1], int(match[2]))
    return entries


def package_entries(text: str):
    result = {}
    for paragraph in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines() if ": " in line and not line.startswith(" "))
        if "Package" in fields:
            result[fields["Package"]] = fields
    return result


def stage_dependencies(work: Path, arch: str):
    directory = work / "dependencies" / arch
    marker = directory / "verified.json"
    if marker.exists():
        info = json.loads(marker.read_text())
        for item in info["packages"]:
            download(item["url"], work / "downloads" / item["archive"], expected=item["sha256"], size=item["size"])
        if info["staging_state"] != staging_state(directory / "root"):
            raise ValueError("Verified Android dependency staging changed; use a new workspace")
        return directory / "root" / PREFIX.lstrip("/"), info
    directory.mkdir(parents=True, exist_ok=True)
    keyring = bytearray()
    for name, expected in KEYS.items():
        key = work / "keys" / (name + ".gpg")
        download(f"https://raw.githubusercontent.com/termux/termux-packages/{TERMUX_COMMIT}/packages/termux-keyring/{name}.gpg", key)
        content = key.read_bytes()
        if hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() != expected:
            raise ValueError("Termux signing key differs from its pinned Git object")
        keyring.extend(content)
    (directory / "keys.gpg").write_bytes(keyring)
    # InRelease must be current on first staging; future retries use pinned package hashes.
    signed = directory / "InRelease"
    if signed.exists():
        signed.unlink()
    download(REPOSITORY + "dists/stable/InRelease", signed)
    gnupg = directory / "gnupg"
    release = directory / "Release"
    release.unlink(missing_ok=True)
    (directory / "Release.part").unlink(missing_ok=True)
    run("gpgv", "--homedir", gnupg, "--keyring", directory / "keys.gpg", "--output", "-", signed, output=release)
    if not release.exists() and (directory / "Release.part").exists():
        (directory / "Release.part").rename(release)
    entries = release_entries(release.read_text())
    indexes = {}
    for machine in (arch, "all"):
        choices = [f"main/binary-{machine}/Packages.xz", f"main/binary-{machine}/Packages.gz", f"main/binary-{machine}/Packages"]
        name = next((p for p in choices if p in entries), None)
        if not name:
            if machine == "all":
                continue  # Some repositories include Architecture: all in each arch index.
            raise ValueError(f"Signed package index missing for {machine}")
        sha, size = entries[name]
        path = directory / (sha[:12] + "-" + Path(name).name.replace("Packages", "Packages-" + machine))
        download(REPOSITORY + "dists/stable/" + name, path, expected=sha, size=size)
        content = lzma.decompress(path.read_bytes()) if name.endswith(".xz") else gzip.decompress(path.read_bytes()) if name.endswith(".gz") else path.read_bytes()
        indexes.update(package_entries(content.decode()))
    staging = directory / "root"
    if staging.exists():
        shutil.rmtree(staging)  # Only our private, incomplete extraction directory.
    staging.mkdir()
    packages = []
    for name in ("libc++", "openssl", "zlib"):
        fields = indexes.get(name)
        if not fields or fields.get("Architecture") not in {arch, "all"}:
            raise ValueError(f"Official Termux package missing: {name}")
        filename = fields["Filename"].removeprefix("./")
        if filename.startswith("/") or ".." in Path(filename).parts:
            raise ValueError("Invalid signed package path")
        if name == "libc++" and fields["Version"].split("-")[0] != "30":
            raise ValueError("Official libc++ version differs from the pinned NDK r30; review before updating")
        archive = name.replace("+", "p") + "-" + file_hash(signed)[:12] + "-" + arch + ".deb"
        path = work / "downloads" / archive
        url = REPOSITORY + urllib.parse.quote(filename, safe="/+:_")
        download(url, path, expected=fields["SHA256"], size=int(fields["Size"]))
        ar_tool = str(Path(os.environ["CODEX_NATIVE_LLVM"]) / "bin/llvm-ar") if "CODEX_NATIVE_LLVM" in os.environ else (shutil.which("llvm-ar") or "/usr/bin/ar")
        listing = [p.rstrip("/") for p in run(ar_tool, "-t", path, capture=True).splitlines()]
        data = [p for p in listing if re.fullmatch(r"data\.tar(?:\.(?:xz|gz|zst|bz2))?", p)]
        if len(data) != 1:
            raise ValueError("Unexpected Debian archive layout")
        payload = directory / data[0]
        run(ar_tool, "-p", path, data[0], output=payload)
        run(os.environ["CODEX_NATIVE_BSDTAR"], "-xf", payload, "-C", staging, "--no-same-owner")
        packages.append({"name": name, "version": fields["Version"], "url": url, "sha256": fields["SHA256"], "size": int(fields["Size"]), "archive": archive})
    info = {"repository": REPOSITORY, "release_sha256": file_hash(signed), "packages": packages, "staging_state": staging_state(staging)}
    write_json(marker, info)
    return staging / PREFIX.lstrip("/"), info


def staging_state(directory: Path):
    state = {}
    for path in sorted(directory.rglob("*")):
        name = str(path.relative_to(directory))
        if path.is_symlink():
            state[name] = {"symlink": os.readlink(path)}
        elif path.is_file():
            state[name] = {"sha256": file_hash(path)}
    return state


def apply_patch(directory: Path, patch: Path):
    executable = os.environ["CODEX_NATIVE_PATCH"]
    run(executable, "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", patch, cwd=directory)
    run(executable, "--batch", "--fuzz=0", "-p1", "-i", patch, cwd=directory)


def prepared_state(directory: Path, extra=()):
    """Keep locally patched sources immutable across resumed builds."""
    state = {"root_diff_sha256": hashlib.sha256(run("git", "diff", "--binary", "--no-ext-diff", cwd=directory, capture=True).encode()).hexdigest()}
    for child in ("build", "v8"):
        if (directory / child / ".git").exists():
            state[child + "_diff_sha256"] = hashlib.sha256(run("git", "diff", "--binary", "--no-ext-diff", cwd=directory / child, capture=True).encode()).hexdigest()
    for name in extra:
        state[name] = file_hash(directory / name)
    return state


def native_v8_patch(directory: Path):
    """Fail closed if the inspected Linux Android block changes."""
    path = directory / "build.rs"
    text = path.read_text()
    start = text.index('    // NDK 23 and above removes libgcc entirely.')
    end = text.index('    static CHROMIUM_URI:', start)
    old = text[start:end]
    if "android-ndk-r26c-linux.zip" not in old or "prebuilt/linux-x86_64" not in old:
        raise ValueError("V8 Android NDK block differs from inspected 150.4.0")
    replacement = '''    // Native macOS Android build: use the verified caller-supplied Mac NDK.
    let ndk = env::var("ANDROID_NDK_HOME").expect("Verified Android NDK is required");
    assert!(cfg!(target_os = "macos"), "Native launcher requires a macOS host");
    assert!(Path::new(&ndk).join("toolchains/llvm/prebuilt/darwin-x86_64/bin/clang").exists());
'''
    text = text[:start] + replacement + text[end:]
    old_sysroot = '''    if target_arch == "x86_64" {
      maybe_install_sysroot("amd64");
    }
'''
    if text.count(old_sysroot) != 1:
        raise ValueError("Unexpected V8 Android x86_64 sysroot block")
    text = text.replace(old_sysroot, "    // Android uses its NDK sysroot; Mac host tools use the Xcode SDK.\n")
    patch = "".join(difflib.unified_diff(path.read_text().splitlines(True), text.splitlines(True), fromfile="a/build.rs", tofile="b/build.rs"))
    wrapper = directory / "build/rust/gni_impl/run_bindgen.py"
    original = wrapper.read_text()
    anchor = 'os.environ.pop("BINDGEN_EXTRA_CLANG_ARGS", None)\n'
    if original.count(anchor) != 1:
        raise ValueError("Expected bindgen environment isolation patch is missing")
    updated = original.replace(anchor, anchor + '''# GN provides explicit host/target clang flags. Cargo's target-specific
# bindgen variables must not force Android headers onto Mac host actions.
os.environ.pop("TARGET", None)
for key in list(os.environ):
  if key.startswith("BINDGEN_EXTRA_CLANG_ARGS_"):
    os.environ.pop(key)
''')
    patch += "".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                     fromfile="a/build/rust/gni_impl/run_bindgen.py", tofile="b/build/rust/gni_impl/run_bindgen.py"))
    return patch


def normalize_lock(source: Path, records: Path, phase: str, env: dict):
    records.mkdir(parents=True, exist_ok=True)
    before = records / (phase + "-before.lock")
    after = records / (phase + "-after.lock")
    shutil.copy2(source / "Cargo.lock", before)
    run("cargo", "+" + RUST, "metadata", "--format-version", "1", cwd=source, env=env, output=records / (phase + "-metadata.json"))
    shutil.copy2(source / "Cargo.lock", after)
    verified.check_lock_transition(before, after, records / (phase + "-changes.json"))
    diff = difflib.unified_diff(before.read_text().splitlines(True), after.read_text().splitlines(True), fromfile=before.name, tofile=after.name)
    (records / (phase + ".diff")).write_text("".join(diff))


def prepare_codex(work: Path, env: dict):
    source = work / "sources" / "codex"
    clone("https://github.com/openai/codex.git", "rust-v" + VERSION, CODEX_COMMIT, source)
    directory = source / "codex-rs"
    marker = work / "metadata" / "codex-prepared.json"
    tracked = ("Cargo.lock", "Cargo.toml", "vendor/cc/src/lib.rs", "vendor/cc/Cargo.toml", "vendor/v8/build.rs", "vendor/v8/Cargo.toml", "tui/src/startup_orchestration.rs")
    if marker.exists():
        if '!cfg!(target_os = "android")' not in (directory / "tui/src/startup_orchestration.rs").read_text():
            apply_patch(source, ROOT / "patches/0007-codex-android-embedded-server.patch")
            write_json(marker, dict(json.loads(marker.read_text()),
                                    prepared_state=prepared_state(source, tuple("codex-rs/" + p for p in tracked))))
        elif json.loads(marker.read_text())["prepared_state"] != prepared_state(source, tuple("codex-rs/" + p for p in tracked)):
            data = json.loads(marker.read_text())
            if data.get("commit") == CODEX_COMMIT:
                write_json(marker, dict(data, prepared_state=prepared_state(source, tuple("codex-rs/" + p for p in tracked))))
            else:
                raise ValueError("Prepared Codex sources changed; use a new workspace")
        if 'v8_String_WriteFlags_kReplaceInvalidUtf8' not in (directory / "vendor/v8/src/binding.rs").read_text():
            apply_patch(directory / "vendor/v8", ROOT / "patches/0006-bindgen-clang23-write-flags.patch")
        return directory
    if run("git", "status", "--porcelain", cwd=source, capture=True):
        raise ValueError("Interrupted source preparation. Rename sources/codex and rerun; Cargo caches are retained.")
    workspace = tomllib.loads((directory / "Cargo.toml").read_text())
    channel = tomllib.loads((directory / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
    if workspace["workspace"]["package"]["version"] != VERSION or channel != RUST:
        raise ValueError("Codex version or Rust toolchain changed")
    packages = tomllib.loads((directory / "Cargo.lock").read_text())["package"]
    for name, version in {"cc": CC_VERSION, "v8": V8}.items():
        if {p["version"] for p in packages if p["name"] == name} != {version}:
            raise ValueError(f"Unexpected {name} version")
    records = work / "metadata" / "lockfiles-codex"
    normalize_lock(directory, records, "upstream", env)
    # --locked authenticates vendored crate content against registry checksums.
    run("cargo", "+" + RUST, "vendor", "--locked", cwd=directory, env=env)
    for path in (directory / "vendor").iterdir():
        if path.name not in {"cc", "v8"}:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    for name in ("rust-cc-do-not-concatenate-all-the-CFLAGS.diff", "rust-cc-allow-warnings.diff"):
        apply_patch(directory / "vendor/cc", ROOT / "patches" / name)
    apply_patch(directory / "vendor/v8", ROOT / "patches/rusty-v8-search-files-with-target-suffix.diff")
    apply_patch(directory / "vendor/v8", ROOT / "patches/0006-bindgen-clang23-write-flags.patch")
    apply_patch(source, ROOT / "patches/0007-codex-android-embedded-server.patch")
    manifest = directory / "Cargo.toml"
    text = manifest.read_text()
    if text.count("[patch.crates-io]") != 1:
        raise ValueError("Unexpected workspace patch table")
    manifest.write_text(text.replace("[patch.crates-io]", '[patch.crates-io]\ncc = { path = "./vendor/cc" }\nv8 = { path = "./vendor/v8" }'))
    normalize_lock(directory, records, "local-patches", env)
    write_json(marker, {"commit": CODEX_COMMIT, "version": VERSION, "cargo_lock_sha256": file_hash(directory / "Cargo.lock"),
                        "prepared_state": prepared_state(source, tuple("codex-rs/" + p for p in tracked))})
    return directory


def prepare_v8(work: Path, ndk: Path, env: dict):
    directory = work / "sources" / "rusty_v8"
    clone("https://github.com/denoland/rusty_v8.git", "v" + V8, V8_COMMIT, directory, submodules=True)
    marker = work / "metadata/v8-prepared.json"
    if not marker.exists():
        if run("git", "diff", "--name-only", cwd=directory, capture=True):
            raise ValueError("Interrupted V8 patching. Rename sources/rusty_v8 and rerun; Cargo caches are retained.")
        apply_patch(directory, ROOT / "patches/0001-unset-BINDGEN_EXTRA_CLANG_ARGS-in-v8_s-bindgen.patch")
        apply_patch(directory, ROOT / "patches/0003-declare-android-ndk-args.patch")
        apply_patch(directory, ROOT / "patches/0005-allow-android-on-macos-host.patch")
        apply_patch(directory, ROOT / "patches/0006-bindgen-clang23-write-flags.patch")
        patch = work / "metadata/0004-native-macos-android.patch"
        generated = native_v8_patch(directory)
        if generated != (ROOT / "patches/0004-native-macos-android.patch").read_text():
            raise ValueError("Native V8 port patch differs from the reviewed source context")
        patch.write_text(generated)
        apply_patch(directory, patch)
        # Linux-only 0002-install-sysroot.patch is intentionally never applied.
        normalize_lock(directory, work / "metadata/lockfiles-v8", "standalone", env)
        write_json(marker, {"commit": V8_COMMIT, "native_patch_sha256": file_hash(patch), "prepared_state": prepared_state(directory, ("Cargo.lock",))})
    else:
        marker_data = json.loads(marker.read_text())
        if 'host_os == "linux" || host_os == "mac"' not in (directory / "build/config/BUILDCONFIG.gn").read_text():
            apply_patch(directory, ROOT / "patches/0005-allow-android-on-macos-host.patch")
        if 'v8_String_WriteFlags_kReplaceInvalidUtf8' not in (directory / "src/binding.rs").read_text():
            apply_patch(directory, ROOT / "patches/0006-bindgen-clang23-write-flags.patch")
        known_valid_roots = {
            "74afe26e4581ba7820defc0d50fb0045a3187deefaddbf54595bdb07b24119a0",  # pre-0006
            "3736b2185ff4ebf1da342e9ff8782a926e257a15dfcbc994a8c7ee1fe11ab09a",  # with 0006
        }
        known_valid_builds = {
            "ccfb7b600340f891a9ed3e09148d4591bd4583bd362dd2a5935fac8ff29a343a",  # pre-0005
            "ea57ca600ee3dcce1f364d585e2640029925de30a8469cd9ebd6289d81d0479a",  # with 0005
        }
        marker_state = marker_data.get("prepared_state", {})
        if (marker_state.get("root_diff_sha256") in known_valid_roots and
                marker_state.get("build_diff_sha256") in known_valid_builds and
                marker_state.get("v8_diff_sha256") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" and
                marker_state.get("Cargo.lock") == "4cdeb9b5c6b2a964f6429b32e73218eb91bba1b0a34e8a28ddc456299dc2070b"):
            write_json(marker, dict(marker_data, prepared_state=prepared_state(directory, ("Cargo.lock",))))
        elif marker_state != prepared_state(directory, ("Cargo.lock",)):
            raise ValueError("Prepared V8 sources changed; use a new workspace")
    link = directory / "third_party/android_ndk"
    if link.is_symlink():
        if link.resolve() != ndk.resolve():
            raise ValueError("V8's NDK symlink changed")
    elif link.exists():
        raise ValueError("V8 NDK location is not our verified symlink")
    else:
        link.symlink_to(ndk, target_is_directory=True)
    return directory


def package_outputs(work: Path, arch: str, codex: Path, v8: Path, ndk: Path, deps: dict, host: str):
    dist = work / "output" / arch
    dist.mkdir(parents=True, exist_ok=True)
    readelf = ndk / "toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-readelf"
    needed = set()
    with tempfile.TemporaryDirectory(prefix="package-", dir=dist) as temporary:
        payload = Path(temporary)
        (payload / "bin").mkdir()
        hashes = {}
        for name in ("codex", "codex-code-mode-host"):
            binary = codex / "target" / (arch + "-linux-android") / "release" / name
            needed |= verify_executable(binary, arch, readelf)
            shutil.copy2(binary, payload / "bin" / name)
            (payload / "bin" / name).chmod(0o755)
            hashes[name] = file_hash(binary)
        licenses = payload / "licenses"
        licenses.mkdir()
        for folder, prefix in ((codex.parent, "codex"), (v8, "rusty-v8"), (v8 / "v8", "v8")):
            for path in sorted(folder.glob("LICENSE*")) + sorted(folder.glob("NOTICE*")):
                if path.is_file():
                    shutil.copy2(path, licenses / (prefix + "-" + path.name))
        if not list(licenses.glob("codex-LICENSE*")) or not list(licenses.glob("v8-LICENSE*")):
            raise ValueError("Required source licenses missing")
        runtime = {"libc++"} if "libc++_shared.so" in needed else set()
        if needed & {"libssl.so.3", "libcrypto.so.3"}:
            runtime.add("openssl")
        dependencies = [{"name": p["name"], "operator": ">=", "version": p["version"]} for p in deps["packages"] if p["name"] in runtime]
        write_json(payload / "manifest.json", {"format": 1, "version": VERSION, "architecture": arch,
                   "prefix": PREFIX, "minimum_api": API, "binary_sha256": hashes["codex"],
                   "companions": {"codex-code-mode-host": hashes["codex-code-mode-host"]}, "dependencies": dependencies})
        metadata = {"format": 1, "version": VERSION, "architecture": arch, "host": host,
                    "build_method": "native-macos-cross-compile", "minimum_api": API, "rust_toolchain": RUST,
                    "codex_commit": CODEX_COMMIT, "rusty_v8_commit": V8_COMMIT,
                    "v8_commit": V8_ENGINE_COMMIT, "chromium_build_commit": BUILD_COMMIT,
                    "recipe_reference_commit": RECIPE_COMMIT, "termux_keys_commit": TERMUX_COMMIT,
                    "cc_version": CC_VERSION, "v8_version": V8, "v8_enable_sandbox": True,
                    "ndk_revision": NDK_REVISION, "ndk_download": json.loads((work / "metadata/ndk-download.json").read_text()),
                    "gn_bootstrap": json.loads((work / "metadata/gn-bootstrap.json").read_text()),
                    "cargo_lock_sha256": file_hash(codex / "Cargo.lock"), "needed_libraries": sorted(needed),
                    "dependencies": deps, "bundle_sha256": bundle_identity(),
                    "built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "validated_on_android_device": False}
        gn_out = v8 / "target" / (arch + "-linux-android") / "release/gn_out"
        metadata["v8_archive_sha256"] = file_hash(gn_out / "obj/librusty_v8.a")
        metadata["v8_bindings_sha256"] = file_hash(gn_out / "src_binding.rs")
        metadata["rustc"] = run("rustup", "run", RUST, "rustc", "-vV", capture=True)
        metadata["host_tools"] = {"gn": run("gn", "--version", capture=True), "ninja": run("ninja", "--version", capture=True)}
        for name, binary in {"chromium_rustc": v8 / "third_party/rust-toolchain/bin/rustc",
                             "chromium_clang": gn_out.parent / "clang/bin/clang"}.items():
            if binary.is_file():
                metadata[name] = {"version": run(binary, "--version", capture=True), "sha256": file_hash(binary)}
        write_json(payload / "BUILD.json", metadata)
        archive = dist / f"codex-{VERSION}-android-{arch}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name in ("bin", "licenses", "manifest.json", "BUILD.json"):
                output.add(payload / name, arcname=name)
        archive.with_name(archive.name + ".sha256").write_text(file_hash(archive) + "  " + archive.name + "\n")
        shutil.copy2(ROOT / "install-codex-termux.sh", dist)
        write_json(dist / "BUILD.json", metadata)
    print(f"Android archive and SHA-256: {dist}\nCompilation completed; Android device validation remains required.", flush=True)


def build(work: Path, arch: str, host: str, ndk: Path, env: dict):
    try:
        jobs = int(os.environ.get("CODEX_BUILD_JOBS", "4"))
    except ValueError as error:
        raise ValueError("CODEX_BUILD_JOBS must be an integer") from error
    if jobs < 1 or jobs > 128:
        raise ValueError("CODEX_BUILD_JOBS must be between 1 and 128")
    if shutil.disk_usage(work).free < 25 * 1024**3:
        raise ValueError("At least 25 GiB of free space is required before building; 50+ GiB recommended")
    print(f"Building Codex {VERSION} with {jobs} jobs; native host {host}. V8 may take substantial time.", flush=True)
    prefix, dependencies = stage_dependencies(work, arch)
    triple = arch + "-linux-android"
    upper = triple.replace("-", "_").upper()
    # openssl-sys sees only the Android staging directory, never Homebrew OpenSSL.
    env[upper + "_OPENSSL_DIR"] = str(prefix)
    env[upper + "_OPENSSL_LIB_DIR"] = str(prefix / "lib")
    env[upper + "_OPENSSL_INCLUDE_DIR"] = str(prefix / "include")
    env["PKG_CONFIG_ALLOW_CROSS"] = "1"
    env["PKG_CONFIG_LIBDIR"] = str(prefix / "lib/pkgconfig")
    env["PKG_CONFIG_SYSROOT_DIR"] = str(work / "dependencies" / arch / "root")
    codex = prepare_codex(work, env)
    v8 = prepare_v8(work, ndk, env)
    cpu = "arm64" if arch == "aarch64" else "x64"
    host_cpu = "arm64" if host.startswith("aarch64") else "x64"
    gn_args = f'''target_os="android"
target_cpu="{cpu}"
v8_target_cpu="{cpu}"
host_cpu="{host_cpu}"
android_ndk_api_level={API}
android_ndk_root={json.dumps(str(ndk))}
android_ndk_version="30"
use_system_xcode=true
'''
    records = work / "metadata" / ("gn-" + arch)
    records.mkdir(parents=True, exist_ok=True)
    (records / "extra-args.gn").write_text(gn_args)
    v8_env = dict(env, EXTRA_GN_ARGS=gn_args, V8_FROM_SOURCE="1", ANDROID_NDK_HOME=str(ndk))
    v8_env["BINDGEN_EXTRA_CLANG_ARGS"] = env["BINDGEN_EXTRA_CLANG_ARGS_" + triple.replace("-", "_")]
    try:
        run("cargo", "+" + RUST, "build", "-vv", "--locked", "--features", "v8_enable_sandbox", "--jobs", jobs,
            "--target", triple, "--release", cwd=v8, env=v8_env)
    finally:
        generated = v8 / "target" / triple / "release/gn_out/args.gn"
        if generated.exists():
            shutil.copy2(generated, records / "args.gn")
        # Record dynamically obtained upstream Android auxiliary checkouts too.
        auxiliaries = {}
        for name in ("android_platform", "catapult"):
            path = v8 / "third_party" / name
            if (path / ".git").exists():
                auxiliaries[name] = run("git", "rev-parse", "HEAD", cwd=path, capture=True)
        write_json(records / "auxiliary-revisions.json", auxiliaries)
    gn_out = v8 / "target" / triple / "release/gn_out"
    for variable, path in {"RUSTY_V8_ARCHIVE_" + upper: gn_out / "obj/librusty_v8.a",
                           "RUSTY_V8_SRC_BINDING_PATH_" + upper: gn_out / "src_binding.rs"}.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"V8 output missing: {path}")
        env[variable] = str(path)
    tag = triple.replace("-", "_")
    flags = ["-C", "link-arg=-Wl,-rpath," + PREFIX + "/lib", "-C", "link-arg=-L" + str(prefix / "lib")]
    if arch == "aarch64":
        builtins = run(env["CC_" + tag], "-print-libgcc-file-name", capture=True)
        if not Path(builtins).is_file():
            raise ValueError("NDK compiler builtins archive missing")
        flags += ["-C", "link-arg=" + builtins]
    env["RUSTC_BOOTSTRAP"] = "1"
    ensure_std_android_flock(env)
    for package, binary in (("codex-cli", "codex"), ("codex-code-mode-host", "codex-code-mode-host")):
        run("cargo", "+" + RUST, "rustc", "-vv", "--locked", "-p", package, "--bin", binary,
            "--release", "--jobs", jobs, "--target", triple, "-Z", "build-std=std,panic_abort", "--", *flags, cwd=codex, env=env)
    package_outputs(work, arch, codex, v8, ndk, dependencies, host)


@contextlib.contextmanager
def workspace_lock(work: Path):
    lock = work / ".native-build-lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError(f"Another native operation may be running. Check processes before removing {lock}") from error
    (lock / "pid").write_text(str(os.getpid()) + "\n")
    try:
        yield
    finally:
        shutil.rmtree(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "check", "build"))
    parser.add_argument("architecture", choices=("aarch64", "x86_64"))
    parser.add_argument("--work", required=True, type=Path)
    options = parser.parse_args()
    work = options.work.expanduser().resolve()
    if any(c in str(work) for c in ('"', "'", "\n", "\r", " ", "\\")):
        raise ValueError("Use a native workspace path without spaces, quotes, backslashes or newlines; some upstream build tools cannot handle them")
    ensure_rustup_path()
    work.mkdir(parents=True, exist_ok=True)
    host = host_check()
    with workspace_lock(work):
        identity = work / "metadata/launcher-identity.json"
        current = bundle_identity()
        if identity.exists() and json.loads(identity.read_text()) != current:
            raise ValueError("The build bundle changed. Use a new CODEX_NATIVE_WORKDIR to preserve reproducibility.")
        write_json(identity, current)
        install_gn(work, host)
        if options.action == "setup":
            run("rustup", "toolchain", "install", RUST, "--profile", "minimal", "--component", "rust-src", "--target", options.architecture + "-linux-android")
        else:
            run("rustup", "target", "add", "--toolchain", RUST, options.architecture + "-linux-android")
            run("rustup", "component", "add", "--toolchain", RUST, "rust-src")
        ndk = install_ndk(work)
        env = target_environment(ndk, options.architecture, host, work)
        preflight(ndk, options.architecture, env, work)
        if options.action == "build":
            build(work, options.architecture, host, ndk, env)
        else:
            print("Dependencies and native cross-compiler ready. Start compilation with: bash build-codex-android-native.sh build " + options.architecture)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}\nFull command output is in the launcher's log. No workflow was triggered.", file=sys.stderr)
        sys.exit(1)
