"""Tests locaux : .deb/.tar réels, commandes Termux et exécution Android simulées.

Ces tests ne constituent ni un build de Codex, ni une validation sur Android.
"""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_tools", ROOT / "tools.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class NativeTests(unittest.TestCase):
    def test_container_entrypoint_reaches_build_with_real_uv(self):
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp)
            support = framework / "codex-support"
            (support / "bin").mkdir(parents=True)
            uv = shutil.which("uv")
            self.assertIsNotNone(uv, "uv requis pour tester le point d’entrée")
            (support / "bin/uv").symlink_to(uv)
            for name, value in {
                "toolchain.request": "1.95.0",
                "framework.commit": "fixture-framework",
                "image.digest": "fixture-image",
                "TUR-COMMIT.txt": "fixture-origin",
            }.items():
                (support / name).write_text(value + "\n")
            build = framework / "build-package.sh"
            build.write_text('#!/bin/bash\nset -eu\nprintf "%s\\n" "$@" > "$TEST_BUILD_LOG"\n')
            build.chmod(0o755)
            # Seul le montage absolu du conteneur est adapté à la fixture.
            # Le script complet et son uv sont réellement exécutés.
            script = (ROOT / "in-container.sh").read_text().replace(
                "cd /home/builder/termux-packages", 'cd "$TEST_FRAMEWORK"', 1)
            log = framework / "build.args"
            env = os.environ | {"TEST_FRAMEWORK": str(framework), "TEST_BUILD_LOG": str(log)}
            result = subprocess.run(["bash", "-s", "--", "aarch64"], input=script,
                                    text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(log.read_text().splitlines(), [
                "-f", "-I", "-a", "aarch64", "--format", "debian",
                "-o", str(framework / "output"), "./custom-packages/codex-termux"])
            self.assertEqual((framework / "output/codex-termux-toolchain.lock").read_text(), "1.95.0\n")

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="codex-native-tests-")
        cls.shared = Path(cls.scratch.name)
        source = cls.shared / "dummy.c"
        source.write_text('int main(void) { return 0; }\n')
        cls.binary = cls.shared / "dummy-android"
        subprocess.run(["gcc", "-fPIE", "-pie", "-Wl,--dynamic-linker=/system/bin/linker64",
                        str(source), "-o", str(cls.binary)], check=True)
        cls.host_binary = cls.shared / "dummy-linux"
        subprocess.run(["gcc", "-fPIE", "-pie", str(source), "-o", str(cls.host_binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_toolchain_is_dated(self):
        self.assertEqual(tools.validate_toolchain("nightly-2026-10-01"), "nightly-2026-10-01")
        with self.assertRaises(ValueError): tools.validate_toolchain("nightly")

    def test_v8_lock_version(self):
        lock = self.shared / "Cargo.lock"
        lock.write_text('[[package]]\nname="v8"\nversion="1.2.3"\n')
        self.assertEqual(tools.v8_version(lock), "1.2.3")
        lock.write_text('[[package]]\nname="v8"\nversion="1.2.3"\n[[package]]\nname="v8"\nversion="2.0.0"\n')
        with self.assertRaises(ValueError): tools.v8_version(lock)

    def lock_fixture(self, change=None, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            original='''version = 4
[[package]]
name = "cc"
version = "1.2.55"
source = "registry+https://github.com/rust-lang/crates.io-index"
[[package]]
name = "v8"
version = "150.4.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
[[package]]
name = "git-lib"
version = "1.0.0"
source = "git+https://github.com/example/repo?rev=abc#abc"
'''
            if change=='duplicate':
                original+='''[[package]]
name = "git-lib"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
'''
            transformed=original+'[[package]]\nname="new-workspace-member"\nversion="0.160.0"\n'
            if change=='cc': transformed=transformed.replace('1.2.55','1.2.56')
            if change=='v8': transformed=transformed.replace('150.4.0','150.5.0')
            if change=='git': transformed=transformed.replace('rev=abc#abc','rev=def#def')
            if change=='local': transformed=transformed.replace('source = "registry+https://github.com/rust-lang/crates.io-index"\n','')
            if change=='wrong-source': transformed=transformed.replace('git+https://github.com/example/repo?rev=abc#abc','git+https://github.com/example/another?rev=abc#abc')
            before,after,report=tmp/'before.lock',tmp/'after.lock',tmp/'changes.json'
            before.write_text(original)
            after.write_text(transformed)
            if fail:
                with self.assertRaises(ValueError): tools.check_lock_transition(before,after,report)
                self.assertTrue(json.loads(report.read_text())['errors'])
            else:
                tools.check_lock_transition(before,after,report)
                result=json.loads(report.read_text())
                self.assertEqual(result['errors'],[])
                self.assertEqual(result['after_sha256'],tools.digest(after))
                self.assertIn('new-workspace-member',[p['name'] for p in result['added']])

    def test_lock_preparation_records_changes(self): self.lock_fixture()
    def test_lock_rejects_cc_update(self): self.lock_fixture('cc',fail=True)
    def test_lock_rejects_v8_update(self): self.lock_fixture('v8',fail=True)
    def test_lock_rejects_git_revision_update(self): self.lock_fixture('git',fail=True)
    def test_lock_allows_local_cc_and_v8(self): self.lock_fixture('local')
    def test_lock_rejects_other_source_change(self): self.lock_fixture('wrong-source',fail=True)
    def test_lock_preserves_multiple_sources(self): self.lock_fixture('duplicate')

    def test_preconfigure_prepares_lock_before_locked_vendor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rust=root/'codex-rs'
            rust.mkdir()
            (rust/'Cargo.toml').write_text('[workspace]\n[patch.crates-io]\n')
            (rust/'Cargo.lock').write_text('# fixture-stale\n[[package]]\nname="cc"\nversion="1.2.55"\n[[package]]\nname="v8"\nversion="150.4.0"\n')
            support=root/'support'
            (support/'bin').mkdir(parents=True)
            (support/'bin/uv').symlink_to(shutil.which('uv'))
            (support/'tools.py').symlink_to(ROOT/'tools.py')
            script='''set -Eeuo pipefail
source "$TEST_RECIPE"
termux_setup_rust() { :; }
rustup() { :; }
patch() { cat >/dev/null; }
cargo() {
    printf '%s\\n' "$*" >> "$TEST_CARGO_LOG"
    if [[ "$2" == metadata ]]; then
        sed -i '/fixture-stale/d' Cargo.lock
    elif [[ "$2" == vendor ]]; then
        [[ "$3" == --locked ]]
        if grep -q fixture-stale Cargo.lock; then
            echo 'cannot update the lock file because --locked was passed' >&2
            return 101
        fi
        mkdir -p vendor/cc vendor/v8
    fi
}
termux_step_pre_configure
'''
            log=root/'cargo.log'
            env=os.environ | {
                'TEST_RECIPE':str(ROOT/'recipe/codex-termux/build.sh'),
                'TEST_CARGO_LOG':str(log),'CODEX_RUST_TOOLCHAIN':'1.95.0',
                'CARGO_TARGET_NAME':'aarch64-linux-android','TERMUX_ARCH':'aarch64',
                'CODEX_SUPPORT_DIR':str(support),
                'CODEX_BUILD_METADATA':str(root/'output/build.json'),
                'TERMUX_PKG_BUILDER_DIR':str(ROOT/'recipe/codex-termux'),
            }
            result=subprocess.run(['bash','-s'],input=script,cwd=root,env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(log.read_text().splitlines(),[
                '+1.95.0 metadata --format-version 1',
                '+1.95.0 vendor --locked',
                '+1.95.0 metadata --format-version 1'])
            records=root/'output/lockfiles-aarch64'
            self.assertIn('fixture-stale',(records/'codex-upstream-before.lock').read_text())
            self.assertNotIn('fixture-stale',(records/'codex-upstream-after.lock').read_text())

    def test_dependency_constraints(self):
        self.assertEqual(tools.dependencies('libc++ (>= 1:2.3-1), openssl')[0],
                         {"name": "libc++", "operator": ">=", "version": "1:2.3-1"})
        with self.assertRaises(ValueError): tools.dependencies('openssl | bad')
        with self.assertRaises(ValueError): tools.dependencies('--allow-unauthenticated')

    def test_linux_binary_is_rejected(self):
        tools.check_android_elf(self.binary)
        with self.assertRaises(ValueError): tools.check_android_elf(self.host_binary)

    def test_recipe_installs_delivery_binary_and_licenses(self):
        # Exécuter le hook réel : l’assembleur doit trouver les licences même
        # si le framework change son propre emplacement de documentation.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source"
            rust = source / "codex-rs"
            target = rust / "target/test-target/release"
            target.mkdir(parents=True)
            (target / "codex").write_bytes(self.binary.read_bytes())
            (target / "codex-code-mode-host").write_bytes(self.binary.read_bytes())
            (source / "LICENSE").write_text("Codex license fixture\n")
            (source / "NOTICE").write_text("Codex notice fixture\n")
            (source / "docs").mkdir()
            (source / "docs/README.md").write_text("Documentation fixture\n")
            (rust / "librusty_v8/v8").mkdir(parents=True)
            (rust / "librusty_v8/LICENSE").write_text("Rusty V8 license fixture\n")
            (rust / "librusty_v8/v8/LICENSE").write_text("V8 license fixture\n")
            prefix = tmp / "prefix"
            env = os.environ | {
                "RECIPE": str(ROOT / "recipe/codex-termux/build.sh"),
                "TERMUX_PREFIX": str(prefix), "TERMUX_PKG_SRCDIR": str(rust),
                "TERMUX_PKG_NAME": "codex-termux", "TERMUX_DEBUG_BUILD": "false",
                "CARGO_TARGET_NAME": "test-target",
            }
            subprocess.run(["bash", "-ec", 'source "$RECIPE"; termux_step_make_install'],
                           cwd=rust, env=env, check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((prefix / "bin/codex").read_bytes(), self.binary.read_bytes())
            licenses = prefix / "share/codex-termux"
            self.assertEqual({p.name for p in licenses.iterdir()},
                             {"LICENSE", "NOTICE", "librusty_v8-LICENSE", "librusty_v8-v8-LICENSE"})

    def test_real_deb_packaging(self):
        self.package_fixture("0.122.0", companion=False)

    def test_current_version_packaging_with_companion(self):
        self.package_fixture("0.160.0", companion=True)

    def package_fixture(self, version, companion):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            package_root = tmp / "debroot"
            control = package_root / "DEBIAN"
            control.mkdir(parents=True)
            control.joinpath("control").write_text(
                f'Package: codex-termux\nVersion: {version}-1\nArchitecture: x86_64\n'
                'Maintainer: Local <local@example.invalid>\nDescription: fixture\n'
                'Depends: libc++ (>= 1), openssl\n')
            prefix = package_root / tools.PREFIX
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin/codex").write_bytes(self.binary.read_bytes())
            if companion:
                (prefix / "bin/codex-code-mode-host").write_bytes(self.binary.read_bytes())
            (prefix / "share/codex-termux").mkdir(parents=True)
            (prefix / "share/codex-termux/LICENSE").write_text('test fixture\n')
            output = tmp / "output"
            output.mkdir()
            deb = output / f"codex-termux_{version}-1_x86_64.deb"
            subprocess.run(['dpkg-deb','--build',str(package_root),str(deb)],check=True,stdout=subprocess.DEVNULL)
            (output / "codex-termux-build-x86_64.json").write_text(json.dumps({
                "version": version, "architecture": "x86_64", "minimum_api": 24}))
            tools.package(output, tmp / "dist", "x86_64")
            archive = tmp / "dist" / f"codex-termux-{version}-x86_64.tar.gz"
            with tarfile.open(archive) as tar:
                manifest=json.load(tar.extractfile('manifest.json'))
                self.assertEqual(manifest['dependencies'][0]['operator'], '>=')
                self.assertEqual(manifest['binary_sha256'],tools.digest(self.binary))
                self.assertEqual(manifest['version'],version)
                if companion:
                    self.assertEqual(manifest['companions']['codex-code-mode-host'],tools.digest(self.binary))
                    self.assertIn('bin/codex-code-mode-host',tar.getnames())
            self.assertEqual(archive.with_suffix('.gz.sha256').read_text().split()[0],tools.digest(archive))

    def install(self, *, change=None, rerun=False, existing=False, replace=False,
                bad_checksum=False, startup_failure=False, owned=False, bad_dependency=False):
        with tempfile.TemporaryDirectory(prefix="codex-install-test-") as tmp:
            tmp=Path(tmp)
            prefix=tmp / "com.termux/files/usr"
            bindir=prefix / "bin"
            bindir.mkdir(parents=True)
            def stub(name, code):
                path=bindir/name
                path.write_text('#!/bin/bash\n'+code+'\n')
                path.chmod(0o755)
            stub('id','echo 12345')
            stub('getprop','echo 35')
            stub('pkg','printf "%s\\n" "$*" >> "$TEST_LOG"')
            stub('dpkg','if [[ "$1" == --print-architecture ]]; then echo x86_64; else /usr/bin/dpkg "$@"; fi')
            stub('dpkg-query', 'if [[ "$1" == -S ]]; then [[ "$TEST_OWNED" == yes ]] && echo "codex: $PREFIX/bin/codex"; else echo "$TEST_DEP_VERSION"; fi')
            # L’ELF de fixture a un chargeur Android et ne s’exécute pas sous
            # Linux. Simuler uniquement ses deux probes, en conservant les
            # contrôles et les opérations de fichiers du véritable installateur.
            stub('timeout','if [[ "$TEST_FAIL" == yes ]]; then exit 42; fi; [[ "${@: -1}" == --version ]] && echo "codex-cli 0.122.0"; exit 0')
            manifest={"format":1,"version":"0.122.0","architecture":"x86_64",
                      "prefix":str(prefix),"minimum_api":24,
                      "binary_sha256":tools.digest(self.binary),
                      "dependencies":[{"name":"openssl","operator":">=","version":"1"}]}
            files={"bin/codex":self.binary.read_bytes(),"BUILD.json":b'{}',"licenses/LICENSE":b'fixture'}
            if change: change(manifest, files)
            files["manifest.json"]=json.dumps(manifest).encode()
            archive=tmp / 'input.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                for name,data in files.items():
                    entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o755 if name=='bin/codex' else 0o644
                    tar.addfile(entry,io.BytesIO(data))
            expected='0'*64 if bad_checksum else tools.digest(archive)
            if existing or owned: (bindir/'codex').write_text('existing command')
            env=dict(os.environ,PREFIX=str(prefix),TERMUX_VERSION='fixture',
                     PATH=f'{bindir}:/usr/bin:/bin',TEST_LOG=str(tmp/'pkg.log'),
                     TEST_FAIL='yes' if startup_failure else 'no',TEST_OWNED='yes' if owned else 'no',
                     TEST_DEP_VERSION='0' if bad_dependency else '100')
            args=['bash',str(ROOT/'install-codex-termux.sh'),'--archive',str(archive),'--sha256',expected,'--yes']
            if replace: args.append('--replace')
            result=subprocess.run(args,env=env,text=True,capture_output=True)
            if rerun and result.returncode==0:
                result=subprocess.run(args,env=env,text=True,capture_output=True)
            installed=(bindir/'codex').is_symlink()
            original=(bindir/'codex').read_text() if (existing or owned) and not installed else None
            backups=list((prefix/'libexec/codex-termux/backups').glob('*'))
            if installed:
                self.assertEqual(tools.digest((bindir/'codex').resolve()),tools.digest(self.binary))
            self.assertFalse((prefix/'libexec/codex-termux/.install-lock').exists())
            if installed and manifest.get('companions'):
                self.assertTrue((bindir/'codex').resolve().with_name('codex-code-mode-host').is_file())
            return result,installed,original,backups

    def test_companion_installation(self):
        def companion(manifest, files):
            manifest['companions']={'codex-code-mode-host':tools.digest(self.binary)}
            files['bin/codex-code-mode-host']=self.binary.read_bytes()
        result,installed,_,_=self.install(change=companion,rerun=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(installed)

    def test_undeclared_companion_rejected(self):
        result,installed,_,_=self.install(change=lambda m,f:f.update({'bin/codex-code-mode-host':self.binary.read_bytes()}))
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(installed)

    def test_bad_companion_hash_rejected(self):
        def companion(manifest, files):
            manifest['companions']={'codex-code-mode-host':'0'*64}
            files['bin/codex-code-mode-host']=self.binary.read_bytes()
        result,installed,_,_=self.install(change=companion)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(installed)

    def test_bad_companion_elf_rejected(self):
        def companion(manifest, files):
            manifest['companions']={'codex-code-mode-host':tools.digest(self.host_binary)}
            files['bin/codex-code-mode-host']=self.host_binary.read_bytes()
        result,installed,_,_=self.install(change=companion)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(installed)

    def source_fixture(self, *, mismatch=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            recipe=tmp/'recipe'
            recipe.mkdir()
            (recipe/'build.sh').write_text('TERMUX_PKG_VERSION="0.160.0"\nTERMUX_PKG_SHA256='+'0'*64+'\n')
            (recipe/'profile.json').write_text((ROOT/'recipe/codex-termux/profile.json').read_text())
            release=tmp/'release.json'
            release.write_text(json.dumps({'tag_name':'rust-v0.160.0','draft':False}))
            files={
                'codex-rs/Cargo.toml':'[workspace.package]\nversion="0.160.0"\n',
                'codex-rs/Cargo.lock':'[[package]]\nname="cc"\nversion="1.2.55"\n[[package]]\nname="v8"\nversion="150.4.0"\n',
                'codex-rs/rust-toolchain.toml':'[toolchain]\nchannel="1.95.0"\n',
            }
            output=tmp/'source.json'
            requested='0.160.0'
            if mismatch=='tag': release.write_text(json.dumps({'tag_name':'rust-v0.162.0'}))
            if mismatch=='version': files['codex-rs/Cargo.toml']='[workspace.package]\nversion="0.122.0"\n'
            if mismatch=='v8': files['codex-rs/Cargo.lock']=files['codex-rs/Cargo.lock'].replace('150.4.0','149.0.0')
            if mismatch=='toolchain': files['codex-rs/rust-toolchain.toml']='[toolchain]\nchannel="nightly"\n'
            if mismatch=='hash': output.write_text(json.dumps({'source_sha256':'0'*64}))
            if mismatch=='profile': requested='0.162.0'
            source=tmp/'source.tar.gz'
            with tarfile.open(source,'w:gz') as tar:
                for name,text in files.items():
                    data=text.encode()
                    member=tarfile.TarInfo('codex-rust-v0.160.0/'+name)
                    member.size=len(data)
                    tar.addfile(member,io.BytesIO(data))
            if mismatch:
                with self.assertRaises(ValueError): tools.prepare_recipe(release,source,recipe,requested,output)
            else:
                tools.prepare_recipe(release,source,recipe,requested,output)
                prepared=json.loads(output.read_text())
                self.assertEqual(prepared['version'],'0.160.0')
                self.assertEqual(prepared['rust_toolchain'],'1.95.0')
                self.assertEqual(prepared['source_sha256'],tools.digest(source))
                self.assertIn(tools.digest(source),(recipe/'build.sh').read_text())

    def test_prepare_current_sources(self): self.source_fixture()
    def test_source_tag_mismatch(self): self.source_fixture(mismatch='tag')
    def test_source_version_mismatch(self): self.source_fixture(mismatch='version')
    def test_source_v8_mismatch(self): self.source_fixture(mismatch='v8')
    def test_source_toolchain_mismatch(self): self.source_fixture(mismatch='toolchain')
    def test_source_changed_hash(self): self.source_fixture(mismatch='hash')
    def test_source_profile_mismatch(self): self.source_fixture(mismatch='profile')

    def test_install_and_rerun(self):
        r,installed,_,_=self.install(rerun=True)
        self.assertEqual(r.returncode,0,r.stderr+r.stdout); self.assertTrue(installed)

    def test_wrong_checksum(self):
        r,installed,_,_=self.install(bad_checksum=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_wrong_architecture(self):
        r,installed,_,_=self.install(change=lambda m,f:m.update(architecture='aarch64'))
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_path_traversal(self):
        r,installed,_,_=self.install(change=lambda m,f:f.update({'../outside':b'bad'}))
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_bad_binary_hash(self):
        r,installed,_,_=self.install(change=lambda m,f:m.update(binary_sha256='0'*64))
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_api_too_old(self):
        r,installed,_,_=self.install(change=lambda m,f:m.update(minimum_api=99))
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_dependency_too_old(self):
        r,installed,_,_=self.install(bad_dependency=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed)

    def test_existing_command_preserved(self):
        r,installed,original,_=self.install(existing=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed);self.assertEqual(original,'existing command')

    def test_existing_command_backed_up(self):
        r,installed,_,backups=self.install(existing=True,replace=True)
        self.assertEqual(r.returncode,0,r.stderr+r.stdout);self.assertTrue(installed);self.assertEqual(len(backups),1)

    def test_package_owned_command_preserved(self):
        r,installed,original,_=self.install(owned=True,replace=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed);self.assertEqual(original,'existing command')

    def test_startup_failure_preserves_command(self):
        r,installed,original,_=self.install(existing=True,replace=True,startup_failure=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(installed);self.assertEqual(original,'existing command')


if __name__=='__main__':
    unittest.main(verbosity=2)
