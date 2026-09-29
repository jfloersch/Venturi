"""Installed resource discovery and pinned solver setup without a source checkout."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .jobs import file_lock


def data_root():
    bundled = Path(__file__).parent / "data"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2]


def install_solver(destination: Path):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError(
            "The supported worker requires Ubuntu 22.04 x86_64, directly or through WSL2."
        )
    release = Path("/etc/os-release").read_text()
    if "ID=ubuntu" not in release or 'VERSION_ID="22.04"' not in release:
        raise ValueError(
            "Install in Ubuntu 22.04. Other Linux releases and ARM remain unqualified."
        )
    if not shutil.which("dpkg-deb"):
        raise ValueError("Ubuntu's dpkg-deb is required to unpack the solver.")
    destination.mkdir(parents=True, exist_ok=True)
    packages = [
        json.loads((data_root() / "worker/runtime.lock.json").read_text()),
        *json.loads((data_root() / "worker/system-dependencies.lock.json").read_text()),
    ]
    with file_lock(destination / ".install.lock"):
        downloads = destination / "downloads"
        downloads.mkdir(exist_ok=True)
        for package in packages:
            path = downloads / (package["sha256"] + ".deb")
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != package["sha256"]
            ):
                print("Downloading", package.get("package", "OpenFOAM 14"), flush=True)
                partial = path.with_suffix(".partial")
                with (
                    urllib.request.urlopen(package["url"], timeout=120) as response,
                    partial.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                if hashlib.sha256(partial.read_bytes()).hexdigest() != package["sha256"]:
                    partial.unlink()
                    raise ValueError("Downloaded package checksum failed. Installation stopped.")
                partial.replace(path)
            subprocess.run(["dpkg-deb", "-x", str(path), str(destination / "runtime")], check=True)
        (destination / "solver-install.json").write_text(
            json.dumps({"packages": packages}, indent=2)
        )


def main():
    parser = argparse.ArgumentParser(description="Install or repair the pinned Venturi solver")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        install_solver(args.destination)
        os.environ["VENTURI_FOAM_ROOT"] = str(args.destination / "runtime/opt/openfoam14")
        from .runtime import diagnostics

        report = diagnostics()
        print(json.dumps(report, indent=2))
        if any(c["status"] == "fail" for c in report["checks"]):
            sys.exit(1)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
