#!/usr/bin/env python3
"""Helpers de build ; exécution via uv, bibliothèque standard uniquement."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tarfile
import tempfile
import tomllib

PREFIX = Path("data/data/com.termux/files/usr")


def validate_version(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Version Codex exacte requise : X.Y.Z")
    return value


def prepare_recipe(release_path: Path, source_archive: Path, recipe: Path,
                   requested: str, output: Path) -> None:
    """Contrôler le tag, les sources et le patch racine avant le build coûteux."""
    version = validate_version(requested)
    profile = json.loads((recipe / "profile.json").read_text())
    if profile["codex_version"] != version:
        raise ValueError(f"Le profil de patches vise Codex {profile['codex_version']} ; adapter le profil avant de construire {version}")
    release = json.loads(release_path.read_text())
    if release.get("tag_name") != f"rust-v{version}" or release.get("draft"):
        raise ValueError("La release GitHub ne correspond pas à la version Codex demandée")
    checksum = digest(source_archive)
    # Pour la recette historique, garder aussi la vérification indépendante
    # de l’empreinte fournie dans l’archive utilisateur.
    if version == "0.122.0" and checksum != "b012a31ce96076dd2a71a3b9606c8a598952140d896a7b12ec07b1471ed130da":
        raise ValueError("Les sources 0.122.0 diffèrent de l’empreinte inspectée")
    patches = sorted(recipe.glob("*.patch"))
    expected = {line[6:] for patch in patches for line in patch.read_text().splitlines() if line.startswith("--- a/")}
    expected.update({"codex-rs/Cargo.toml", "codex-rs/Cargo.lock", "codex-rs/rust-toolchain.toml"})
    with tempfile.TemporaryDirectory(prefix="codex-source-check-") as scratch:
        directory = Path(scratch)
        found = set()
        with tarfile.open(source_archive, "r:gz") as tar:
            for member in tar:
                # Copier seulement les fichiers nécessaires ; aucune extraction
                # arbitraire, aucun lien ni chemin fourni au système de fichiers.
                relative = member.name.partition("/")[2]
                if relative not in expected:
                    continue
                if not member.isfile() or member.size > 32 * 1024 * 1024 or relative in found:
                    raise ValueError("Entrée de sources invalide ou dupliquée")
                found.add(relative)
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = tar.extractfile(member)
                target.write_bytes(stream.read())
        missing = expected - found
        if missing:
            raise ValueError(f"Portage requis : fichiers du patch absents de Codex {version} : {sorted(missing)}")
        workspace = tomllib.loads((directory / "codex-rs/Cargo.toml").read_text())
        if workspace.get("workspace", {}).get("package", {}).get("version") != version:
            raise ValueError("La version dans les sources ne correspond pas au tag demandé")
        locked = tomllib.loads((directory / "codex-rs/Cargo.lock").read_text())
        for name in ("cc", "v8"):
            versions = {p["version"] for p in locked["package"] if p["name"] == name}
            if versions != {profile[f"{name}_version"]}:
                raise ValueError(f"Portage requis : versions {name} inattendues : {versions}")
        toolchain = validate_toolchain(tomllib.loads((directory / "codex-rs/rust-toolchain.toml").read_text())["toolchain"]["channel"])
        if toolchain != profile["rust_toolchain"]:
            raise ValueError("La toolchain amont diffère du profil inspecté")
        for patch in patches:
            check = subprocess.run(["patch", "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(patch.resolve())],
                                   cwd=directory, text=True, capture_output=True)
            if check.returncode:
                raise ValueError(f"Le patch {patch.name} doit être adapté à Codex {version}.\n{check.stdout}{check.stderr}")
    if output.exists() and json.loads(output.read_text()).get("source_sha256") != checksum:
        raise ValueError("Les sources ont changé depuis le premier build ; examiner ce changement dans un nouveau dossier")
    build_script = recipe / "build.sh"
    text = build_script.read_text()
    text, versions = re.subn(r'^TERMUX_PKG_VERSION="[^"]+"$', f'TERMUX_PKG_VERSION="{version}"', text, flags=re.M)
    text, hashes = re.subn(r'^TERMUX_PKG_SHA256=[a-f0-9]+$', f'TERMUX_PKG_SHA256={checksum}', text, flags=re.M)
    if versions != 1 or hashes != 1:
        raise ValueError("Recette ambiguë ; une version et une empreinte attendues")
    build_script.write_text(text)
    write_json(output, {"version": version, "source_sha256": checksum, "rust_toolchain": toolchain,
                        "source_url": f"https://github.com/openai/codex/archive/refs/tags/rust-v{version}.tar.gz",
                        "release_url": f"https://github.com/openai/codex/releases/tag/rust-v{version}"})


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def validate_toolchain(value: str) -> str:
    if not re.fullmatch(r"(?:nightly-\d{4}-\d{2}-\d{2}|\d+\.\d+\.\d+)", value):
        raise ValueError("Toolchain requise : nightly-YYYY-MM-DD ou X.Y.Z ; pas de canal flottant")
    return value


def v8_version(lock: Path) -> str:
    doc = tomllib.loads(lock.read_text())
    versions = {p["version"] for p in doc["package"] if p["name"] == "v8"}
    if len(versions) != 1:
        raise ValueError(f"Version v8 ambiguë ou absente du Cargo.lock : {versions}")
    value = versions.pop()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"Version V8 inattendue : {value}")
    return value


def elf_arch(path: Path) -> str:
    with path.open("rb") as f:
        header = f.read(20)
    if len(header) != 20 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01":
        raise ValueError("ELF 64 bits little-endian requis")
    machine = struct.unpack_from("<H", header, 18)[0]
    try:
        return {183: "aarch64", 62: "x86_64"}[machine]
    except KeyError as e:
        raise ValueError(f"Architecture ELF inconnue : {machine}") from e


def check_android_elf(path: Path) -> None:
    with path.open("rb") as f:
        header = f.read(64)
        if len(header) < 64 or struct.unpack_from("<H", header, 16)[0] != 3:
            raise ValueError("Un exécutable PIE est requis pour Android")
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        if size < 56 or count > 1024:
            raise ValueError("Table ELF invalide")
        for index in range(count):
            f.seek(offset + index * size)
            program = f.read(56)
            if len(program) < 56:
                raise ValueError("En-tête ELF tronqué")
            if struct.unpack_from("<I", program)[0] == 3:
                location = struct.unpack_from("<Q", program, 8)[0]
                length = struct.unpack_from("<Q", program, 32)[0]
                if length > 256:
                    raise ValueError("Interpréteur ELF invalide")
                f.seek(location)
                interpreter = f.read(length).rstrip(b"\0")
                if interpreter != b"/system/bin/linker64":
                    raise ValueError(f"Interpréteur ELF non Android : {interpreter!r}")


def dependencies(raw: str) -> list[dict[str, str]]:
    result = []
    for item in raw.split(","):
        if not item.strip():
            continue
        match = re.fullmatch(
            r"\s*([a-z0-9][a-z0-9+.-]*)(?:\s*\((>=|<=|=|>>|<<)\s*([A-Za-z0-9.+:~_-]+)\))?\s*",
            item,
        )
        if not match:
            raise ValueError(f"Dépendance non prise en charge : {item!r}")
        name, operator, version = match.groups()
        result.append({"name": name, "operator": operator or "", "version": version or ""})
    return result


def write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def package(output: Path, destination: Path, arch: str) -> None:
    metadata = output / f"codex-termux-build-{arch}.json"
    build = json.loads(metadata.read_text())
    version = validate_version(build["version"])
    if build["architecture"] != arch:
        raise ValueError("Les métadonnées ne correspondent pas au build demandé")
    candidates = []
    for deb in output.glob("codex-termux_*.deb"):
        fields = subprocess.check_output(
            ["dpkg-deb", "--field", str(deb), "Package", "Version", "Architecture", "Depends"],
            text=True,
        )
        info = dict(line.split(": ", 1) for line in fields.splitlines() if ": " in line)
        if (info.get("Package") == "codex-termux" and info.get("Architecture") == arch
                and info.get("Version", "").split("-")[0] == version):
            candidates.append((deb, info))
    if len(candidates) != 1:
        raise ValueError(f"Un paquet local attendu pour {arch} ; trouvé {len(candidates)}")
    deb, info = candidates[0]
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"codex-termux-{version}-{arch}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="codex-package-") as scratch:
        tmp = Path(scratch)
        extracted = tmp / "extracted"
        subprocess.run(["dpkg-deb", "--extract", str(deb), str(extracted)], check=True)
        binary = extracted / PREFIX / "bin/codex"
        if elf_arch(binary) != arch:
            raise ValueError("Architecture du binaire incorrecte")
        check_android_elf(binary)
        # Le framework effectue les nettoyages ELF ; conserver le binaire produit.
        stage = tmp / "payload"
        (stage / "bin").mkdir(parents=True)
        (stage / "bin/codex").write_bytes(binary.read_bytes())
        (stage / "bin/codex").chmod(0o755)
        companions = {}
        companion = extracted / PREFIX / "bin/codex-code-mode-host"
        if version == "0.160.0" and not companion.is_file():
            raise ValueError("Le binaire codex-code-mode-host requis pour Code Mode est absent")
        if companion.is_file():
            if elf_arch(companion) != arch:
                raise ValueError("Architecture du compagnon incorrecte")
            check_android_elf(companion)
            (stage / "bin/codex-code-mode-host").write_bytes(companion.read_bytes())
            companions["codex-code-mode-host"] = digest(companion)
        licenses = extracted / PREFIX / "share/codex-termux"
        if not licenses.is_dir():
            raise ValueError("Les licences du build sont absentes")
        (stage / "licenses").mkdir()
        for source in licenses.rglob("*"):
            if source.is_file() and not source.is_symlink():
                target = stage / "licenses" / source.relative_to(licenses)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        write_json(stage / "manifest.json", {
            "format": 1, "version": version, "architecture": arch,
            "prefix": "/" + str(PREFIX), "minimum_api": build["minimum_api"],
            "binary_sha256": digest(stage / "bin/codex"),
            "companions": companions,
            "dependencies": dependencies(info.get("Depends", "")),
        })
        write_json(stage / "BUILD.json", build)
        temporary = archive.with_suffix(archive.suffix + ".tmp")
        with tarfile.open(temporary, "w:gz") as tar:
            for source in sorted(stage.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(stage)
                if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(relative)):
                    raise ValueError(f"Nom de livraison invalide : {relative}")
                entry = tar.gettarinfo(str(source), arcname=str(relative))
                entry.uid = entry.gid = 0
                entry.uname = entry.gname = ""
                entry.mtime = 0
                entry.mode = 0o755 if relative.parts[0] == "bin" else 0o644
                with source.open("rb") as f:
                    tar.addfile(entry, f)
        temporary.replace(archive)
    checksum = digest(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n")
    print(archive)


def metadata(destination: Path, lock: Path) -> None:
    def command(*args: str) -> str:
        return subprocess.check_output(args, text=True).strip()
    source = json.loads((Path(os.environ["CODEX_SUPPORT_DIR"]) / "source.json").read_text())
    doc = {
        "version": validate_version(source["version"]),
        "architecture": os.environ["TERMUX_ARCH"],
        "target": os.environ["CARGO_TARGET_NAME"],
        "minimum_api": int(os.environ["TERMUX_PKG_API_LEVEL"]),
        "rust_toolchain": os.environ["CODEX_RUST_TOOLCHAIN"],
        "rustc": command("rustup", "run", os.environ["CODEX_RUST_TOOLCHAIN"], "rustc", "--version", "--verbose"),
        "v8_version": v8_version(lock),
        "cargo_lock_sha256": digest(lock),
        "recipe_sha256": digest(Path(os.environ["TERMUX_PKG_BUILDER_DIR"]) / "build.sh"),
        "framework_commit": os.environ["CODEX_FRAMEWORK_COMMIT"],
        "builder_image": os.environ["CODEX_BUILDER_IMAGE"],
        "patches_origin_commit": os.environ["CODEX_TUR_COMMIT"],
        "ndk_version": os.environ["TERMUX_NDK_VERSION"],
        "patches_sha256": {
            str(p.relative_to(Path(os.environ["TERMUX_PKG_BUILDER_DIR"]))).replace(os.sep, "/"): digest(p)
            for p in sorted(Path(os.environ["TERMUX_PKG_BUILDER_DIR"]).rglob("*"))
            if p.is_file() and p.suffix in {".patch", ".diff"}
        },
        "codex_source_sha256": source["source_sha256"],
        "codex_source_url": source["source_url"],
    }
    write_json(destination, doc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["nightly", "validate-toolchain", "validate-version", "source-toolchain", "prepare-recipe", "v8-version", "package", "metadata"])
    parser.add_argument("args", nargs="+")
    options = parser.parse_args()
    match options.operation:
        case "nightly":
            doc = tomllib.loads(Path(options.args[0]).read_text())
            print(validate_toolchain("nightly-" + str(doc["date"])))
        case "validate-toolchain":
            validate_toolchain(options.args[0])
        case "validate-version":
            validate_version(options.args[0])
        case "source-toolchain":
            print(validate_toolchain(json.loads(Path(options.args[0]).read_text())["rust_toolchain"]))
        case "prepare-recipe":
            prepare_recipe(Path(options.args[0]), Path(options.args[1]), Path(options.args[2]), options.args[3], Path(options.args[4]))
        case "v8-version":
            print(v8_version(Path(options.args[0])))
        case "package":
            package(Path(options.args[0]), Path(options.args[1]), options.args[2])
        case "metadata":
            metadata(Path(options.args[0]), Path(options.args[1]))


if __name__ == "__main__":
    main()
