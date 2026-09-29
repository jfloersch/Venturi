"""Installer fault injection with deterministic dependency/runtime substitutes."""

import fcntl
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from venturi import installation

ROOT = Path(__file__).resolve().parents[1]


def seal(payload):
    (payload / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
            for p in sorted(payload.iterdir())
            if p.is_file() and p.name != "SHA256SUMS"
        )
    )


@pytest.fixture
def installer(tmp_path):
    payload = tmp_path / "payload space ' ü"
    payload.mkdir()
    (payload / "install-worker.sh").write_bytes((ROOT / "scripts/install-worker.sh").read_bytes())
    (payload / "VERSION").write_text("0.5.0\n")
    (payload / "requirements.txt").write_text("test-only\n")
    (payload / "venturi_workbench-0.5.0-py3-none-any.whl").write_text("test-only\n")
    stub = f"""#!{sys.executable}
import os,sys,time
from pathlib import Path
args=sys.argv[1:]
if args[:2] == ['python','install']: pass
elif args[:2] == ['python','find']: print({sys.executable!r})
elif args[0] == 'venv':
    root=Path(args[-1]); (root/'bin').mkdir(parents=True,exist_ok=True)
    script="#!/usr/bin/env bash\\nif [[ $1 == -m && $2 == venturi.installation ]]; then [[ $VENTURI_FAULT != solver_failure ]]; else exec "+{sys.executable!r}+" \\\"$@\\\"; fi\\n"
    (root/'bin/python').write_text(script); (root/'bin/python').chmod(0o700)
elif args[:2] == ['pip','sync']:
    root=Path(args[args.index('--python')+1]).parent.parent
    (root/'application').write_text('incomplete')
    mode=os.environ.get('VENTURI_FAULT')
    if mode == 'sync_failure': sys.exit(28)
    if mode == 'interrupt':
        Path(os.environ['VENTURI_FAULT_READY']).touch()
        time.sleep(120)
elif args[:2] == ['pip','install']:
    root=Path(args[args.index('--python')+1]).parent.parent
    (root/'application').write_text('new-working-application')
else: sys.exit(99)
"""
    (payload / "uv").write_text(stub)
    (payload / "uv").chmod(0o700)
    seal(payload)
    root = tmp_path / "install-safe"
    previous = root / "releases/0.5.0"
    previous.mkdir(parents=True)
    (previous / "application").write_text("original-working-application")
    (root / "current").symlink_to(previous)
    (root / "projects").mkdir()
    (root / "projects/user-content").write_text("preserve-project")
    return payload, root, previous


def run_installer(installer, mode="", **kwargs):
    payload, root, _ = installer
    return subprocess.run(
        ["bash", str(payload / "install-worker.sh")],
        env={**os.environ, "VENTURI_INSTALL_DIR": str(root), "VENTURI_FAULT": mode},
        text=True,
        capture_output=True,
        timeout=30,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["sync_failure", "solver_failure"])
def test_failed_repair_preserves_previous_working_version(installer, mode):
    _, root, previous = installer
    result = run_installer(installer, mode)
    assert result.returncode != 0
    assert (root / "current").resolve() == previous
    assert (previous / "application").read_text() == "original-working-application"
    assert (root / "projects/user-content").read_text() == "preserve-project"


def test_sigkill_during_dependency_update_and_subsequent_repair(installer, tmp_path):
    payload, root, previous = installer
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        ["bash", str(payload / "install-worker.sh")],
        env={
            **os.environ,
            "VENTURI_INSTALL_DIR": str(root),
            "VENTURI_FAULT": "interrupt",
            "VENTURI_FAULT_READY": str(ready),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert ready.exists()
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    assert (root / "current").resolve() == previous
    assert (previous / "application").read_text() == "original-working-application"
    repaired = run_installer(installer)
    assert repaired.returncode == 0, repaired.stderr
    assert (root / "current/application").read_text() == "new-working-application"
    assert (previous / "application").read_text() == "original-working-application"


def test_upgrade_unusual_paths_and_rollback(installer):
    payload, root, previous = installer
    (payload / "VERSION").write_text("0.5.1\n")
    (payload / "venturi_workbench-0.5.1-py3-none-any.whl").write_text("test upgrade")
    seal(payload)
    result = run_installer(installer)
    assert result.returncode == 0, result.stderr
    assert (root / "current").resolve() != previous
    assert (root / "current/application").read_text() == "new-working-application"
    assert (previous / "application").read_text() == "original-working-application"
    rollback = root / "rollback.next"
    rollback.symlink_to(previous)
    rollback.replace(root / "current")
    assert (root / "current/application").read_text() == "original-working-application"
    assert not (payload / "SHOULD_NOT_EXIST").exists()


def test_corrupted_payload_stops_before_installation(installer):
    payload, root, previous = installer
    (payload / "requirements.txt").write_text("corrupted")
    result = run_installer(installer)
    assert result.returncode != 0 and "FAILED" in result.stdout
    assert (root / "current").resolve() == previous
    assert not (root / "setup.lock").exists()


@pytest.mark.parametrize("name", ["space here", "unicode-ü", "quote'", "$(touch SHOULD_NOT_EXIST)"])
def test_unsupported_install_paths_fail_before_changes(installer, name):
    payload, root, _ = installer
    unsupported = root.parent / name
    result = subprocess.run(
        ["bash", str(payload / "install-worker.sh")],
        env={**os.environ, "VENTURI_INSTALL_DIR": str(unsupported)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "OpenFOAM cannot use spaces or other characters" in result.stderr
    assert not unsupported.exists()
    assert not (payload / "SHOULD_NOT_EXIST").exists()


@pytest.mark.parametrize("lock", ["setup.lock", ".host.lock", "projects/.admission.lock"])
def test_setup_startup_and_admission_locks_fail_closed(installer, lock):
    _, root, previous = installer
    with (root / lock).open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = run_installer(installer)
    assert result.returncode != 0
    assert (previous / "application").read_text() == "original-working-application"


@pytest.mark.parametrize(
    "folder,state",
    [
        ("runs", "queued"),
        ("runs", "running"),
        ("campaigns", "starting"),
        ("campaigns", "running"),
        ("campaigns", "stopping"),
    ],
)
def test_active_work_blocks_repair(installer, folder, state):
    _, root, previous = installer
    status = root / "projects" / folder / "test/status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": state}))
    result = run_installer(installer)
    assert result.returncode != 0
    assert (previous / "application").read_text() == "original-working-application"


@pytest.mark.parametrize("mode", ["corruption", "interrupted", "disk_full"])
def test_solver_download_faults_never_publish_bad_package(tmp_path, monkeypatch, mode):
    content = b"a known archive"
    digest = hashlib.sha256(content).hexdigest()
    data = tmp_path / "data/worker"
    data.mkdir(parents=True)
    (data / "runtime.lock.json").write_text(
        json.dumps({"sha256": digest, "url": "https://offline.invalid/solver"})
    )
    (data / "system-dependencies.lock.json").write_text("[]")
    monkeypatch.setattr(installation, "data_root", lambda: data.parent)
    destination = tmp_path / "solver"

    class Broken(io.BytesIO):
        def read(self, *args):
            raise OSError(28 if mode == "disk_full" else 104, mode)

    monkeypatch.setattr(
        installation.urllib.request,
        "urlopen",
        lambda *a, **kw: io.BytesIO(b"bad") if mode == "corruption" else Broken(content),
    )
    with pytest.raises((ValueError, OSError)):
        installation.install_solver(destination)
    assert not (destination / "downloads" / (digest + ".deb")).exists()
    assert not (destination / "runtime").exists()
    assert not (destination / "solver-install.json").exists()
    # Retry replaces any partial download and publishes only the verified bytes.
    monkeypatch.setattr(
        installation.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(content)
    )
    monkeypatch.setattr(installation.subprocess, "run", lambda *a, **kw: None)
    installation.install_solver(destination)
    assert (destination / "downloads" / (digest + ".deb")).read_bytes() == content
