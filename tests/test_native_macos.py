"""Offline regression checks. Run: uv run --no-project --python 3.11 -m unittest discover -s tests -v"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_build as n

FIXTURES = Path(__file__).parent / "fixtures"
READELF = shutil.which("llvm-readelf") or shutil.which("readelf")
GPATCH = shutil.which("gpatch") or shutil.which("patch")


def android_elf(path, machine=183, interpreter=b"/system/bin/linker64\0"):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", header, 16, 3, machine, 1)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HHH", header, 52, 64, 56, 1)
    program = bytearray(56)
    struct.pack_into("<I", program, 0, 3)
    struct.pack_into("<Q", program, 8, 120)
    struct.pack_into("<Q", program, 32, len(interpreter))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + program + interpreter)


class NativeBuildTests(unittest.TestCase):
    def test_setup_launcher_reaches_driver_without_homebrew_gn(self):
        # Exercise the real shell entry point, rather than only checking its text.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bins = directory / "bin"
            bins.mkdir()
            scripts = {
                "uname": "echo Darwin",
                "sysctl": "echo 0",
                "xcodebuild": "exit 0",
                "brew": 'if [ "$1" = --prefix ]; then echo "$FAKE_BREW_PREFIX"; else printf "%s\\n" "$*" >> "$BREW_CALLS"; fi',
                "caffeinate": 'shift; exec "$@"',
                "uv": 'echo "Native driver reached: $*"',
            }
            for name in ("rustup", "ninja", "cmake", "pkg-config", "gpgv", "bsdtar"):
                scripts[name] = "exit 0"
            for name, body in scripts.items():
                executable = bins / name
                executable.write_text("#!/bin/bash\n" + body + "\n")
                executable.chmod(0o755)
            calls = directory / "brew-calls"
            env = dict(os.environ, PATH=str(bins) + ":/usr/bin:/bin",
                       FAKE_BREW_PREFIX=str(directory), BREW_CALLS=str(calls),
                       CODEX_NATIVE_WORKDIR=str(directory / "work"))
            result = subprocess.run(["bash", n.ROOT / "build-codex-android-native.sh", "setup"],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Native driver reached:", result.stdout)
            self.assertIn("native_build.py setup aarch64", result.stdout)
            self.assertNotIn("gn", calls.read_text().split())

    def gn_fixture(self, work, host, entries=None):
        content = b"GN fixture " + host.encode()
        pin = dict(n.GN_PACKAGES[host])
        pin["binary_sha256"] = hashlib.sha256(content).hexdigest()
        archive = work / "downloads" / ("gn-" + host + "-" + n.GN_REVISION[:12] + ".zip")
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as output:
            for name, data in (entries or {"gn": content}).items():
                output.writestr(name, data)
        pin["archive_sha256"] = n.file_hash(archive)
        return pin, archive

    def gn_run(self, host):
        cpu = "arm64" if host.startswith("aarch64") else "x86_64"
        def fake(*args, **kwargs):
            if str(args[0]) == "/usr/bin/file":
                return "Mach-O 64-bit " + cpu + " executable"
            if args[-1] == "--version":
                return "2407 (" + n.GN_REVISION[:12] + ")"
            raise AssertionError("Unexpected command: " + str(args))
        return fake

    def test_gn_cached_install_uses_host_cpu_and_overrides_path(self):
        for host in n.GN_PACKAGES:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                pin, _ = self.gn_fixture(work, host)
                with patch.dict(n.GN_PACKAGES, {host: pin}), patch.dict(os.environ, PATH="/wrong/gn:/usr/bin", CODEX_NATIVE_LLVM="/mac/llvm"), \
                     patch.object(n, "run", side_effect=self.gn_run(host)):
                    binary = n.install_gn(work, host)
                    self.assertEqual(shutil.which("gn"), str(binary))
                    self.assertEqual(n.file_hash(binary), pin["binary_sha256"])
                    self.assertEqual(n.install_gn(work, host), binary)
                    env = n.target_environment(Path("/mac/ndk"), "aarch64", host, work)
                    self.assertEqual(env["GN"], str(binary))
                metadata = json.loads((work / "metadata/gn-bootstrap.json").read_text())
                self.assertEqual(metadata["package"], pin["package"])
                self.assertEqual(metadata["git_revision"], n.GN_REVISION)

    def test_gn_fetches_exact_cipd_instance_and_verifies_archive(self):
        host = "aarch64-apple-darwin"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ):
            work = Path(temporary)
            pin, archive = self.gn_fixture(work, host)
            payload = archive.read_bytes()
            archive.unlink()
            commands = []
            def fake_run(*args, **kwargs):
                commands.append(args)
                if args[0] == "curl":
                    return json.dumps({"status": "SUCCESS", "instance": {
                        "package_name": pin["package"], "instance_id": pin["instance_id"]},
                        "fetch_url": "https://example.com/pinned-gn.zip"})
                return self.gn_run(host)(*args, **kwargs)
            def fake_download(url, path, **kwargs):
                self.assertEqual(kwargs["expected"], pin["archive_sha256"])
                self.assertEqual(url, "https://example.com/pinned-gn.zip")
                path.write_bytes(payload)
            with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run", side_effect=fake_run), \
                 patch.object(n, "download", side_effect=fake_download):
                n.install_gn(work, host)
            self.assertIn(pin["instance_id"], commands[0][-1])
            self.assertIn("gn%2Fgn%2Fmac-arm64", commands[0][-1])

    def test_gn_wrong_instance_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(n, "run", return_value=json.dumps(
                {"status": "SUCCESS", "instance": {"instance_id": "wrong"}})), patch.object(n, "download") as download:
            with self.assertRaisesRegex(ValueError, "unexpected GN instance"):
                n.install_gn(Path(temporary), "x86_64-apple-darwin")
            download.assert_not_called()

    def test_gn_corrupted_archive_is_removed_before_execution(self):
        host = "aarch64-apple-darwin"
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            pin, archive = self.gn_fixture(work, host)
            archive.write_bytes(b"corrupted")
            with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run") as run:
                with self.assertRaisesRegex(ValueError, "Integrity"):
                    n.install_gn(work, host)
                run.assert_not_called()
            self.assertFalse(archive.exists())

    def test_gn_modified_binary_is_rejected_on_resume(self):
        host = "aarch64-apple-darwin"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ):
            work = Path(temporary)
            pin, _ = self.gn_fixture(work, host)
            with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run", side_effect=self.gn_run(host)):
                binary = n.install_gn(work, host)
                binary.write_bytes(b"modified")
                with self.assertRaisesRegex(ValueError, "GN was modified"):
                    n.install_gn(work, host)

    def test_gn_wrong_architecture_or_version_is_rejected(self):
        host = "aarch64-apple-darwin"
        for outputs in (("Mach-O 64-bit x86_64 executable",), ("Mach-O 64-bit arm64 executable", "wrong revision")):
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                pin, _ = self.gn_fixture(work, host)
                with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run", side_effect=outputs):
                    with self.assertRaises(ValueError):
                        n.install_gn(work, host)

    def test_gn_never_extracts_other_zip_paths(self):
        host = "aarch64-apple-darwin"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ):
            work = Path(temporary)
            pin, _ = self.gn_fixture(work, host, {"gn": b"GN fixture " + host.encode(), "../../escaped": b"bad"})
            with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run", side_effect=self.gn_run(host)):
                n.install_gn(work, host)
            self.assertFalse((work / "escaped").exists())
            self.assertFalse((work.parent / "escaped").exists())

    def test_gn_archive_requires_exact_root_binary_and_binary_checksum(self):
        host = "aarch64-apple-darwin"
        for entries in ({"nested/gn": b"wrong"}, {"gn": b"wrong"}):
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                pin, _ = self.gn_fixture(work, host, entries)
                with patch.dict(n.GN_PACKAGES, {host: pin}), patch.object(n, "run") as run:
                    with self.assertRaises(ValueError):
                        n.install_gn(work, host)
                    run.assert_not_called()

    def test_setup_bootstraps_gn_before_ndk_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            events = []
            with patch.object(sys, "argv", ["native_build.py", "setup", "aarch64", "--work", temporary]), \
                 patch.object(n, "host_check", return_value="aarch64-apple-darwin"), \
                 patch.object(n, "install_gn", side_effect=lambda *args: events.append("gn")) as gn, \
                 patch.object(n, "install_ndk", side_effect=lambda *args: events.append("ndk") or work / "ndk"), \
                 patch.object(n, "run"), patch.object(n, "target_environment", return_value={}), \
                 patch.object(n, "preflight", side_effect=lambda *args: events.append("preflight")):
                n.main()
            gn.assert_called_once_with(work.resolve(), "aarch64-apple-darwin")
            self.assertEqual(events, ["gn", "ndk", "preflight"])

    def test_bash_syntax_and_help(self):
        launcher = n.ROOT / "build-codex-android-native.sh"
        subprocess.run(["bash", "-n", launcher], check=True)
        output = subprocess.check_output(["bash", launcher, "help"], text=True)
        self.assertIn("Native macOS host tools", output)
        self.assertIn("0.160.0", output)

    def test_ndk_dmg_with_root_and_app_properties_selects_toolchain(self):
        import plistlib
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            image = work / "downloads/android-ndk-r30-darwin.dmg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"dmg")
            mount_point = work / "mount"
            mount_point.mkdir()
            (mount_point / "source.properties").write_text(f"Pkg.Revision = {n.NDK_REVISION}\n")
            nested_ndk = mount_point / "AndroidNDK16248370.app/Contents/NDK"
            (nested_ndk / "toolchains/llvm/prebuilt/darwin-x86_64/bin").mkdir(parents=True)
            (nested_ndk / "toolchains/llvm/prebuilt/darwin-x86_64/bin/clang").write_text("clang")
            (nested_ndk / "source.properties").write_text(f"Pkg.Revision = {n.NDK_REVISION}\n")
            plist_data = plistlib.dumps({"system-entities": [{"mount-point": str(mount_point)}]})

            with patch.object(n, "download"), \
                 patch.object(n, "file_hash", return_value="dummy_hash"), \
                 patch.object(n.subprocess, "check_output", return_value=plist_data), \
                 patch.object(n, "run"):
                dest = n.install_ndk(work)
                self.assertTrue((dest / "source.properties").exists())
                self.assertTrue((dest / "toolchains/llvm/prebuilt/darwin-x86_64/bin/clang").exists())
                self.assertTrue((dest / ".codex-native-integrity.json").exists())

    def test_linux_host_rejected_before_download(self):
        with patch.object(n.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "native macOS"):
                n.host_check()

    def test_real_cc_patches_apply_without_fuzz(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, CODEX_NATIVE_PATCH=GPATCH):
            directory = Path(temporary)
            (directory / "src").mkdir()
            shutil.copy2(FIXTURES / "cc-lib.rs", directory / "src/lib.rs")
            n.apply_patch(directory, n.ROOT / "patches/rust-cc-do-not-concatenate-all-the-CFLAGS.diff")
            n.apply_patch(directory, n.ROOT / "patches/rust-cc-allow-warnings.diff")
            output = (directory / "src/lib.rs").read_text()
            self.assertNotIn("#![deny(warnings)]", output)
            self.assertIn("if any_set {", output)

    def v8_fixture(self, directory):
        shutil.copy2(FIXTURES / "v8-build.rs", directory / "build.rs")
        (directory / "build/config/android").mkdir(parents=True)
        (directory / "build/rust/gni_impl").mkdir(parents=True)
        shutil.copy2(FIXTURES / "android-config.gni", directory / "build/config/android/config.gni")
        shutil.copy2(FIXTURES / "run_bindgen.py", directory / "build/rust/gni_impl/run_bindgen.py")
        for name in ("0001-unset-BINDGEN_EXTRA_CLANG_ARGS-in-v8_s-bindgen.patch", "0003-declare-android-ndk-args.patch"):
            n.apply_patch(directory, n.ROOT / "patches" / name)

    def test_real_native_v8_patch_replaces_linux_ndk_and_android_sysroots(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, CODEX_NATIVE_PATCH=GPATCH):
            directory = Path(temporary)
            self.v8_fixture(directory)
            native = directory / "native.patch"
            native.write_text(n.native_v8_patch(directory))
            self.assertEqual(native.read_text(), (n.ROOT / "patches/0004-native-macos-android.patch").read_text())
            n.apply_patch(directory, native)
            source = (directory / "build.rs").read_text()
            android = source.split('if target_triple != env::var("HOST").unwrap() && target_os == "android" {')[1].split("// iOS / iOS-simulator.")[0]
            self.assertNotIn("maybe_install_sysroot", android)
            self.assertNotIn("linux-x86_64", android)
            self.assertNotIn("android-ndk-r26c", source)
            self.assertIn("darwin-x86_64", android)
            self.assertIn('if target_os == "linux" {', source)
            self.assertIn('println!("GN args: {args}")', source)
            self.assertIn('android_ndk_version = ""', (directory / "build/config/android/config.gni").read_text())
            self.assertIn('key.startswith("BINDGEN_EXTRA_CLANG_ARGS_")', (directory / "build/rust/gni_impl/run_bindgen.py").read_text())
            self.assertIn('os.environ.pop("TARGET", None)', (directory / "build/rust/gni_impl/run_bindgen.py").read_text())

    def test_v8_patch_fails_closed_after_upstream_drift(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, CODEX_NATIVE_PATCH=GPATCH):
            directory = Path(temporary)
            self.v8_fixture(directory)
            path = directory / "build.rs"
            path.write_text(path.read_text().replace("android-ndk-r26c-linux.zip", "changed.zip"))
            with self.assertRaisesRegex(ValueError, "differs"):
                n.native_v8_patch(directory)

    def test_target_suffix_patch_on_real_v8_build_script(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, CODEX_NATIVE_PATCH=GPATCH):
            directory = Path(temporary)
            shutil.copy2(FIXTURES / "v8-build.rs", directory / "build.rs")
            n.apply_patch(directory, n.ROOT / "patches/rusty-v8-search-files-with-target-suffix.diff")
            self.assertIn('format!("RUSTY_V8_ARCHIVE_{target_u}")', (directory / "build.rs").read_text())

    def test_host_and_target_environments_are_separate(self):
        dirty = {"CC": "/linux/gcc", "CFLAGS": "--target=android", "HOST_CFLAGS": "bad", "GN_ARGS": "bad",
                 "BINDGEN_EXTRA_CLANG_ARGS": "bad", "CLANG_BASE_PATH": "/linux/clang", "CODEX_NATIVE_LLVM": "/mac/llvm"}
        for arch, host in (("aarch64", "aarch64-apple-darwin"), ("x86_64", "x86_64-apple-darwin")):
            with patch.dict(os.environ, dirty):
                env = n.target_environment(Path("/mac/ndk"), arch, host, Path("/mac/work"))
            triple = arch + "_linux_android"
            self.assertNotIn("CC", env)
            self.assertNotIn("HOST_CFLAGS", env)
            self.assertNotIn("GN_ARGS", env)
            self.assertNotIn("CLANG_BASE_PATH", env)
            self.assertIn("darwin-x86_64", env["CC_" + triple])
            self.assertTrue(env["CC_" + triple].endswith("android24-clang"))
            self.assertEqual(env["CC_" + host.replace("-", "_")], "/usr/bin/clang")
            self.assertIn("--target=" + arch + "-linux-android24", env["BINDGEN_EXTRA_CLANG_ARGS_" + triple])
            self.assertNotIn("/mac/ndk", env["PATH"])

    def test_checksum_mismatch_stops_and_removes_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "download"
            path.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "Integrity"):
                n.download("https://example.com/file", path, expected="0" * 64)
            self.assertFalse(path.exists())

    def test_verified_cached_download_needs_no_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "download"
            path.write_bytes(b"correct")
            with patch.object(n, "run", side_effect=AssertionError("Network called")):
                n.download("https://example.com/file", path, expected=n.file_hash(path), size=7)

    def test_signed_repository_parsing_and_all_arch_packages(self):
        release = "Origin: termux\nSHA256:\n " + "a" * 64 + " 42 main/binary-aarch64/Packages.gz\nSHA512:\n other\n"
        self.assertEqual(n.release_entries(release), {"main/binary-aarch64/Packages.gz": ("a" * 64, 42)})
        packages = "Package: libc++\nArchitecture: aarch64\nVersion: 30\n\nPackage: ca-certificates\nArchitecture: all\nVersion: 1\n"
        self.assertEqual(n.package_entries(packages)["ca-certificates"]["Architecture"], "all")
        with self.assertRaises(ValueError):
            n.release_entries("Origin: bad")

    @unittest.skipUnless(READELF, "Add Homebrew LLVM bin directory to PATH")
    def test_real_elf_parser_rejects_linux_and_macho(self):
        readelf = Path(READELF)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "binary"
            android_elf(path)
            self.assertEqual(n.verify_executable(path, "aarch64", readelf), set())
            with self.assertRaisesRegex(ValueError, "architecture"):
                n.verify_executable(path, "x86_64", readelf)
            android_elf(path, interpreter=b"/lib64/ld-linux-x86-64.so.2\0")
            with self.assertRaisesRegex(ValueError, "non Android"):
                n.verify_executable(path, "aarch64", readelf)
            path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 60)
            with self.assertRaisesRegex(ValueError, "ELF"):
                n.verify_executable(path, "aarch64", readelf)

    @unittest.skipUnless(READELF, "Add Homebrew LLVM bin directory to PATH")
    def test_no_loader_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "binary"
            android_elf(path)
            data = bytearray(path.read_bytes())
            struct.pack_into("<I", data, 64, 1)
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "linker64"):
                n.verify_executable(path, "aarch64", Path(READELF))

    def test_runtime_dependencies_and_rpath_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "binary"
            android_elf(path)
            programs = "[Requesting program interpreter: /system/bin/linker64]"
            for dynamic in ("(NEEDED) [libSystem.B.dylib]", "(RUNPATH) [/Users/test/lib]"):
                with patch.object(n, "run", side_effect=[programs, dynamic]):
                    with self.assertRaises(ValueError):
                        n.verify_executable(path, "aarch64", Path("readelf"))

    def test_lock_preparation_records_and_rejects_cc_bump(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / "Cargo.lock"
            old = 'version = 4\n[[package]]\nname = "cc"\nversion = "1.2.55"\n[[package]]\nname = "v8"\nversion = "150.4.0"\n'
            lock.write_text(old)
            with patch.object(n, "run", return_value=""):
                n.normalize_lock(directory, directory / "records", "normal", {})
            self.assertTrue((directory / "records/normal.diff").exists())
            def bump(*args, **kwargs):
                lock.write_text(old.replace("1.2.55", "1.2.56"))
            with patch.object(n, "run", side_effect=bump):
                with self.assertRaisesRegex(ValueError, "cc"):
                    n.normalize_lock(directory, directory / "records", "bump", {})
            self.assertTrue(json.loads((directory / "records/bump-changes.json").read_text())["errors"])

    def test_concurrent_build_lock_is_refused_and_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with n.workspace_lock(directory):
                with self.assertRaisesRegex(ValueError, "Another native"):
                    with n.workspace_lock(directory):
                        pass
            self.assertFalse((directory / ".native-build-lock").exists())

    def build_fixture(self, directory):
        codex, v8 = directory / "codex/codex-rs", directory / "v8"
        codex.mkdir(parents=True)
        v8.mkdir()
        return codex, v8

    def test_build_sequence_uses_locked_cargo_and_both_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            codex, v8 = self.build_fixture(work)
            env = {"CC_aarch64_linux_android": "/mac/ndk/clang", "BINDGEN_EXTRA_CLANG_ARGS_aarch64_linux_android": "--target=aarch64-linux-android24"}
            builtins = work / "builtins.a"
            builtins.write_bytes(b"archive")
            calls = []
            def fake_run(*args, **kwargs):
                calls.append((args, kwargs))
                if args[0] == "cargo" and args[2] == "build":
                    out = v8 / "target/aarch64-linux-android/release/gn_out"
                    (out / "obj").mkdir(parents=True)
                    (out / "args.gn").write_text(kwargs["env"]["EXTRA_GN_ARGS"])
                    (out / "obj/librusty_v8.a").write_bytes(b"static")
                    (out / "src_binding.rs").write_text("bindings")
                return str(builtins) if "-print-libgcc-file-name" in args else ""
            with patch.object(n, "stage_dependencies", return_value=(work / "deps/usr", {"packages": []})), \
                 patch.object(n, "prepare_codex", return_value=codex), patch.object(n, "prepare_v8", return_value=v8), \
                 patch.object(n, "run", side_effect=fake_run), patch.object(n, "package_outputs") as package:
                n.build(work, "aarch64", "aarch64-apple-darwin", work / "ndk", env)
            cargo = [a for a, _ in calls if a[0] == "cargo"]
            self.assertEqual(len(cargo), 3)
            self.assertTrue(all("--locked" in a for a in cargo))
            self.assertEqual([a[a.index("--bin")+1] for a in cargo[1:]], ["codex", "codex-code-mode-host"])
            v8_env = calls[0][1]["env"]
            self.assertEqual(v8_env["V8_FROM_SOURCE"], "1")
            self.assertIn('host_cpu="arm64"', v8_env["EXTRA_GN_ARGS"])
            self.assertIn('android_ndk_version="30"', v8_env["EXTRA_GN_ARGS"])
            self.assertTrue((work / "metadata/gn-aarch64/args.gn").exists())
            self.assertIn("RUSTY_V8_ARCHIVE_AARCH64_LINUX_ANDROID", env)
            package.assert_called_once()

    def test_failure_keeps_gn_args_and_stops_before_codex(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            codex, v8 = self.build_fixture(work)
            env = {"BINDGEN_EXTRA_CLANG_ARGS_aarch64_linux_android": "--target=android"}
            def fail(*args, **kwargs):
                out = v8 / "target/aarch64-linux-android/release/gn_out"
                out.mkdir(parents=True)
                (out / "args.gn").write_text("generated before failure")
                raise subprocess.CalledProcessError(101, args)
            with patch.object(n, "stage_dependencies", return_value=(work / "deps/usr", {})), \
                 patch.object(n, "prepare_codex", return_value=codex), patch.object(n, "prepare_v8", return_value=v8), \
                 patch.object(n, "run", side_effect=fail), patch.object(n, "package_outputs") as package:
                with self.assertRaises(subprocess.CalledProcessError):
                    n.build(work, "aarch64", "aarch64-apple-darwin", work / "ndk", env)
            self.assertEqual((work / "metadata/gn-aarch64/args.gn").read_text(), "generated before failure")
            package.assert_not_called()

    def test_archive_contains_both_binaries_licenses_and_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            codex, v8 = self.build_fixture(work)
            for name in ("codex", "codex-code-mode-host"):
                android_elf(codex / "target/aarch64-linux-android/release" / name)
            (codex / "Cargo.lock").write_text("lock")
            (codex.parent / "LICENSE").write_text("Codex license fixture")
            (v8 / "LICENSE").write_text("Rusty V8 license fixture")
            (v8 / "v8").mkdir()
            (v8 / "v8/LICENSE").write_text("V8 license fixture")
            gn_out = v8 / "target/aarch64-linux-android/release/gn_out"
            (gn_out / "obj").mkdir(parents=True)
            (gn_out / "obj/librusty_v8.a").write_bytes(b"fixture archive")
            (gn_out / "src_binding.rs").write_text("fixture bindings")
            n.write_json(work / "metadata/ndk-download.json", {"sha256": "a" * 64})
            n.write_json(work / "metadata/gn-bootstrap.json", {"git_revision": n.GN_REVISION})
            deps = {"packages": [{"name": "openssl", "version": "1:3.6.5"}, {"name": "libc++", "version": "30"}]}
            with patch.object(n, "verify_executable", return_value={"libc++_shared.so", "libssl.so.3"}), patch.object(n, "run", return_value="fixture compiler version"):
                n.package_outputs(work, "aarch64", codex, v8, work / "ndk", deps, "aarch64-apple-darwin")
            archive = work / "output/aarch64/codex-0.160.0-android-aarch64.tar.gz"
            self.assertEqual(archive.with_name(archive.name + ".sha256").read_text().split()[0], n.file_hash(archive))
            with tarfile.open(archive) as content:
                self.assertIn("bin/codex-code-mode-host", content.getnames())
                manifest = json.load(content.extractfile("manifest.json"))
                self.assertEqual(manifest["version"], "0.160.0")
                self.assertEqual(len(manifest["dependencies"]), 2)
                for name in ("codex", "codex-code-mode-host"):
                    digest = hashlib.sha256(content.extractfile("bin/" + name).read()).hexdigest()
                    expected = manifest["binary_sha256"] if name == "codex" else manifest["companions"][name]
                    self.assertEqual(digest, expected)
                metadata = json.load(content.extractfile("BUILD.json"))
                self.assertEqual(metadata["gn_bootstrap"]["git_revision"], n.GN_REVISION)
                self.assertFalse(metadata["validated_on_android_device"])


if __name__ == "__main__":
    unittest.main()
