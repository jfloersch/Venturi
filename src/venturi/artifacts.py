"""Portable, hash-checked bundles. Verification never executes imported content."""

import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .models import file_hash, write_json


def artifact_files(folder: Path):
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symbolic links are not supported in portable runs.")
        if (
            path.is_file()
            and path.name not in {".run.lock", ".cancel"}
            and path != folder / "manifest.json"
        ):
            yield path


def seal(folder: Path) -> dict:
    manifest = {
        "schema_version": "venturi.artifacts.v1",
        "files": {p.relative_to(folder).as_posix(): file_hash(p) for p in artifact_files(folder)},
    }
    write_json(folder / "manifest.json", manifest)
    return manifest


def verify(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "venturi.artifacts.v1":
        raise ValueError("Unsupported artifact manifest version.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not {"result.json", "report.html"} <= files.keys():
        raise ValueError("Artifact manifest is incomplete.")
    actual = {p.relative_to(folder).as_posix(): p for p in artifact_files(folder)}
    if actual.keys() != files.keys():
        raise ValueError("Artifact inventory differs: files were added or removed.")
    for name, digest in files.items():
        if file_hash(actual[name]) != digest:
            raise ValueError(f"Artifact hash mismatch: {name}")
    return manifest


def export_bundle(folder: Path, destination: Path) -> Path:
    verify(folder)
    destination = destination.resolve()
    if destination.is_relative_to(folder.resolve()):
        raise ValueError("Export destination must be outside the run directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive final creation prevents overwriting a user's existing export.
    fd, name = tempfile.mkstemp(suffix=".zip", dir=destination.parent)
    os.close(fd)
    temp = Path(name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in [*artifact_files(folder), folder / "manifest.json"]:
                archive.write(path, f"{folder.name}/{path.relative_to(folder).as_posix()}")
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def unpack_bundle(archive: Path, destination: Path) -> Path:
    """Extract only bounded regular files under one root; reject path traversal."""
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 20000 or sum(x.file_size for x in entries) > 8 * 1024**3:
            raise ValueError("Bundle exceeds the portable-run limits.")
        seen = set()
        roots = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or len(path.parts) < 2
                or path.as_posix() in seen
                or path.as_posix() != entry.filename.rstrip("/")
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe or duplicate archive entry.")
            seen.add(path.as_posix())
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise ValueError("Bundle must contain exactly one run.")
        source.extractall(destination)
    folder = destination / roots.pop()
    verify(folder)
    return folder
