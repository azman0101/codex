"""Shared version, lockfile and Android ELF checks from the inspected port."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import tomllib


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()



def check_lock_transition(before: Path, after: Path, report: Path) -> None:
    """Consigner une préparation Cargo sans changer les versions critiques/Git."""
    old = tomllib.loads(before.read_text())["package"]
    new = tomllib.loads(after.read_text())["package"]
    errors = []
    for name in ("cc", "v8"):
        expected = {p["version"] for p in old if p["name"] == name}
        actual = {p["version"] for p in new if p["name"] == name}
        if expected and actual != expected:
            errors.append(f"Versions {name} modifiées : {sorted(expected)} -> {sorted(actual)}")
    def git_sources(packages):
        result = {}
        for package in packages:
            source = package.get("source", "")
            if source.startswith("git+"):
                repository = source.split("?", 1)[0].split("#", 1)[0]
                result.setdefault(repository, set()).add(source)
        return result
    old_git, new_git = git_sources(old), git_sources(new)
    for repository in old_git.keys() & new_git.keys():
        if not new_git[repository].issubset(old_git[repository]):
            errors.append(f"Révision Git modifiée : {repository}")
    old_sources = {}
    for package in old:
        old_sources.setdefault((package["name"], package["version"]), set()).add(package.get("source"))
    for package in new:
        key = (package["name"], package["version"])
        source = package.get("source")
        previous = old_sources.get(key, set())
        if previous and source not in previous and not (key[0] in {"cc", "v8"} and source is None):
            errors.append(f"Source modifiée : {key[0]} {key[1]}")
    def identities(packages):
        return {(p["name"], p["version"], p.get("source", "")) for p in packages}
    old_ids, new_ids = identities(old), identities(new)
    def describe(items):
        return [{"name": name, "version": version, "source": source}
                for name, version, source in sorted(items)]
    write_json(report, {
        "before_sha256": digest(before), "after_sha256": digest(after),
        "added": describe(new_ids - old_ids), "removed": describe(old_ids - new_ids),
        "errors": errors,
    })
    if errors:
        raise ValueError("Préparation du lockfile refusée : " + "; ".join(errors))



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



def write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

