#!/usr/bin/env python3
"""Start the authenticated local worker and browser or native workbench.

Use `uv run python scripts/dev.py --desktop` for the native Tauri shell.
On Windows/macOS, run the worker in WSL/a Linux VM separately and use npm run
tauri dev with VENTURI_API_TOKEN set; see the platform testing guide.
"""

import argparse
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_for(url: str, processes: list[subprocess.Popen], seconds: int = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("A development process exited. See its output above.")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument(
        "--built", action="store_true", help="Launch the compiled desktop without rebuilding it"
    )
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if args.built and not args.desktop:
        parser.error("--built requires --desktop")
    env = os.environ.copy()
    local_node = ROOT / ".tools/node/bin"
    if local_node.is_dir():
        env["PATH"] = str(local_node) + os.pathsep + env.get("PATH", "")
    npm = shutil.which("npm", path=env["PATH"])
    if npm is None and not args.built:
        sys.exit("Install Node.js 22.12+ before starting the desktop.")
    if not args.built and not (ROOT / "apps/desktop/node_modules").exists():
        sys.exit("Run npm ci in apps/desktop first.")
    token = secrets.token_urlsafe(32)
    env["VENTURI_API_TOKEN"] = token
    processes = []
    try:
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/v1/health", timeout=1)
        except (urllib.error.URLError, TimeoutError):
            pass
        else:
            sys.exit(
                "A worker is already listening on port 8765. Stop that worker or connect the existing desktop to it."
            )
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "venturi.cli", "serve"],
                cwd=ROOT,
                env=env,
                start_new_session=True,
            )
        )
        wait_for("http://127.0.0.1:8765/v1/health", processes)
        command = [npm, "run", "tauri", "dev"] if args.desktop else [npm, "run", "dev"]
        if args.built:
            executable = ROOT / "apps/desktop/src-tauri/target/release/venturi-desktop"
            if not executable.is_file():
                raise RuntimeError("Build the desktop first: npm run tauri build -- --no-bundle")
            command = [str(executable)]
        processes.append(
            subprocess.Popen(command, cwd=ROOT / "apps/desktop", env=env, start_new_session=True)
        )
        if not args.desktop:
            wait_for("http://127.0.0.1:1420", processes)
            url = "http://127.0.0.1:1420/#token=" + token
            if args.no_open:
                print("Open this local session:", url, flush=True)
            else:
                webbrowser.open(url)
                print("Browser workbench ready at http://127.0.0.1:1420", flush=True)
        print(
            "Press Ctrl+C to stop this development session. Run artifacts will be retained.",
            flush=True,
        )
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


if __name__ == "__main__":
    main()
