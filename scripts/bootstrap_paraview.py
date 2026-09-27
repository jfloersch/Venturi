#!/usr/bin/env python3
"""Optional official ParaView binary for the M0 batch-rendering smoke test."""

import hashlib
import json
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This optional binary is pinned for Linux x86_64 only.")
    lock = json.loads((ROOT / "worker/paraview.lock.json").read_text())
    target = ROOT / ".tools/downloads/paraview-6.0.1.tar.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        print("Downloading ParaView 6.0.1 (approximately 788 MiB).", flush=True)
        partial = target.with_suffix(".partial")
        with (
            urllib.request.urlopen(lock["url"], timeout=120) as response,
            partial.open("wb") as stream,
        ):
            shutil.copyfileobj(response, stream)
        partial.replace(target)
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != lock["sha256"]:
        raise SystemExit("ParaView archive checksum mismatch. Refusing extraction.")
    subprocess.run(["tar", "-xf", str(target), "-C", str(ROOT / ".tools")], check=True)
    print("ParaView ready:", ROOT / ".tools" / lock["directory"] / "bin/pvpython")


if __name__ == "__main__":
    main()
