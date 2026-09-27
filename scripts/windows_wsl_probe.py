#!/usr/bin/env python3
"""Read-only Windows HTTP probe and an isolated test-file round trip from WSL."""

import hashlib
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ps_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    if "microsoft" not in platform.release().lower():
        raise SystemExit("Run this probe from WSL with a worker listening on local port 8765.")
    folder = ROOT / "artifacts/windows-wsl-probe"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "fluid volume input.txt"
    target = folder / "fluid volume returned.txt"
    source.write_text("Venturi WSL round trip\nUnits: m, Pa; μ = 0.001 Pa·s\n", encoding="utf-8")
    windows_paths = [
        subprocess.check_output(["wslpath", "-w", str(p)], text=True).strip()
        for p in (source, target)
    ]
    script = f"""
    $ErrorActionPreference = 'Stop'
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/v1/health'
    $data = [System.IO.File]::ReadAllText({ps_string(windows_paths[0])})
    [System.IO.File]::WriteAllText({ps_string(windows_paths[1])}, $data, [System.Text.UTF8Encoding]::new($false))
    @{{ protocol = $health.protocol_version; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath {ps_string(windows_paths[1])}).Hash }} | ConvertTo-Json -Compress
    """
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    value = json.loads(process.stdout.strip())
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    passed = (
        value["protocol"] == "venturi.worker.v1"
        and value["sha256"].lower() == expected
        and target.read_bytes() == source.read_bytes()
    )
    result = {
        "status": "passed" if passed else "failed",
        "protocol": value["protocol"],
        "file_round_trip": "UTF-8 with Unicode and spaces in filename",
        "sha256": expected,
        "limitation": "Transport/filesystem test only; native Windows UI remains unqualified.",
    }
    (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
