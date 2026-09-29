#!/usr/bin/env python3
"""Build a verifiable Linux/WSL worker payload for the native desktop installer."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release")
    parser.add_argument("--managed-url", default="")
    args = parser.parse_args()
    if args.managed_url and (
        not args.managed_url.startswith("https://") or "\n" in args.managed_url
    ):
        parser.error("Managed release URL must use HTTPS.")
    uv = shutil.which("uv")
    if not uv or not subprocess.check_output([uv, "--version"], text=True).startswith("uv 0.12.7 "):
        parser.error("Build with the pinned uv 0.12.7 Linux x86_64 binary.")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(args.output / "wheel")], cwd=ROOT, check=True
    )
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    payload = args.output / "worker-payload"
    payload.mkdir(parents=True, exist_ok=True)
    for old in payload.glob("venturi_workbench-*.whl"):
        old.unlink()
    shutil.copy2(args.output / "wheel" / f"venturi_workbench-{version}-py3-none-any.whl", payload)
    shutil.copy2(uv, payload / "uv")
    shutil.copy2(ROOT / "scripts/install-worker.sh", payload)
    shutil.copy2(ROOT / "LICENSE", payload)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", payload)
    for source, target in [
        ("uv.lock", "python-dependencies.lock"),
        ("apps/desktop/package-lock.json", "desktop-dependencies.lock.json"),
        ("apps/desktop/src-tauri/Cargo.lock", "rust-dependencies.lock"),
    ]:
        shutil.copy2(ROOT / source, payload / target)
    for name in ("LICENSE-MIT", "LICENSE-APACHE"):
        with urllib.request.urlopen(
            "https://raw.githubusercontent.com/astral-sh/uv/0.12.7/" + name, timeout=30
        ) as response:
            (payload / ("uv-" + name)).write_bytes(response.read())

    requirements = subprocess.check_output(
        [
            uv,
            "export",
            "--locked",
            "--no-dev",
            "--extra",
            "cad",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        text=True,
    )
    (payload / "requirements.txt").write_text(requirements)
    (payload / "VERSION").write_text(version + "\n")
    (payload / "managed-url").write_text(args.managed_url + "\n")
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(payload.iterdir())
        if p.is_file() and p.name != "SHA256SUMS"
    }
    (payload / "SHA256SUMS").write_text(
        "".join(f"{value}  {name}\n" for name, value in files.items())
    )
    archive = args.output / f"venturi-worker-{version}-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname="worker-payload")
    subprocess.run(
        [uv, "build", "--sdist", "--out-dir", str(args.output / "source")], cwd=ROOT, check=True
    )
    manifest = {
        "version": version,
        "worker_platform": "ubuntu-22.04-x86_64",
        "files": files,
        "archive": archive.name,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_archive": f"source/venturi_workbench-{version}.tar.gz",
        "source_archive_sha256": hashlib.sha256(
            (args.output / "source" / f"venturi_workbench-{version}.tar.gz").read_bytes()
        ).hexdigest(),
        "managed_url": args.managed_url,
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "signing": "not signed by this builder",
    }
    (args.output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    resources = ROOT / "apps/desktop/src-tauri/worker-payload"
    shutil.copytree(payload, resources, dirs_exist_ok=True)
    (args.output / "tauri.release.json").write_text(
        json.dumps({"bundle": {"resources": {str(resources) + "/": "worker-payload/"}}}, indent=2)
    )
    os.chmod(payload / "install-worker.sh", 0o755)
    print(archive)
    print("Build the native bundle with --config", args.output / "tauri.release.json")


if __name__ == "__main__":
    main()
