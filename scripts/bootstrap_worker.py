#!/usr/bin/env python3
"""Install the pinned Ubuntu/WSL x86_64 worker into .tools without root.

Downloads official binary packages, verifies committed SHA-256 digests, and
extracts only into the project. Does not change apt sources or shell profiles.
"""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        sys.exit(
            "This bootstrap is qualified only for Ubuntu 22.04 x86_64, including WSL2. See docs/testing.md for macOS/ARM qualification."
        )
    os_release = Path("/etc/os-release").read_text()
    if "ID=ubuntu" not in os_release or 'VERSION_ID="22.04"' not in os_release:
        sys.exit(
            "The pinned packages target Ubuntu 22.04. Use a matching WSL distribution/VM; do not install these into another OS baseline."
        )
    if not shutil.which("dpkg-deb"):
        sys.exit("dpkg-deb is required to unpack the worker.")
    downloads = ROOT / ".tools/downloads"
    runtime = ROOT / ".tools/runtime"
    downloads.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    packages = [
        json.loads((ROOT / "worker/runtime.lock.json").read_text()),
        *json.loads((ROOT / "worker/system-dependencies.lock.json").read_text()),
    ]
    for package in packages:
        path = downloads / (package["sha256"] + ".deb")
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != package["sha256"]:
            print("Downloading", package.get("package", "OpenFOAM 14"), flush=True)
            partial = path.with_suffix(".partial")
            with (
                urllib.request.urlopen(package["url"], timeout=120) as response,
                partial.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != package["sha256"]:
                partial.unlink()
                sys.exit("Package checksum failed. Refusing to extract the download.")
            partial.replace(path)
        subprocess.run(["dpkg-deb", "-x", str(path), str(runtime)], check=True)
    print("Worker installed at", runtime / "opt/openfoam14")
    print("Next: uv sync --locked --extra cad --extra dev; uv run venturi doctor")


if __name__ == "__main__":
    main()
