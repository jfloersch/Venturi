import os
import threading

import pytest

from venturi.runtime import RunCancelled, run_tool


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    root = tmp_path / "foam"
    (root / "etc").mkdir(parents=True)
    (root / "etc/bashrc").touch()
    bin = root / "platforms/linux64GccDPInt32Opt/bin"
    bin.mkdir(parents=True)
    monkeypatch.setenv("VENTURI_FOAM_ROOT", str(root))
    return bin


@pytest.mark.skipif(os.name != "posix", reason="The worker executes on Linux")
def test_solver_failure_is_not_completion(fake_runtime, tmp_path):
    executable = fake_runtime / "foamRun"
    executable.write_text("#!/bin/sh\necho 'failure fixture'\nexit 7\n")
    executable.chmod(0o755)
    with pytest.raises(RuntimeError, match="status 7"):
        run_tool("foamRun", tmp_path / "case", 1)


@pytest.mark.skipif(os.name != "posix", reason="The worker executes on Linux")
def test_time_limit_stops_process_and_preserves_log(fake_runtime, tmp_path):
    executable = fake_runtime / "foamRun"
    executable.write_text("#!/bin/sh\necho 'started'\nsleep 30\n")
    executable.chmod(0o755)
    with pytest.raises(TimeoutError):
        run_tool("foamRun", tmp_path / "case", 0.2)
    assert "started" in (tmp_path / "logs/foamRun.log").read_text()


@pytest.mark.skipif(os.name != "posix", reason="The worker executes on Linux")
def test_user_cancellation_stops_process(fake_runtime, tmp_path):
    executable = fake_runtime / "foamRun"
    executable.write_text("#!/bin/sh\nsleep 30\n")
    executable.chmod(0o755)
    event = threading.Event()
    event.set()
    with pytest.raises(RunCancelled):
        run_tool("foamRun", tmp_path / "case", 5, event)


def test_unapproved_executable_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="allowlist"):
        run_tool("sh", tmp_path, 1)


def test_disk_limit_stops_writer_and_retains_evidence(fake_runtime, tmp_path):
    import sys

    from venturi.models import ResourcePolicy
    from venturi.runtime import ResourceExceeded, execution_context

    executable = fake_runtime / "foamRun"
    executable.write_text(
        f"#!{sys.executable}\nimport time\nfrom pathlib import Path\nimport sys\n"
        "p = Path(sys.argv[2]).parent / 'large.bin'\n"
        "with p.open('wb') as out:\n"
        " for i in range(100):\n  out.write(b'x' * 1024 * 1024)\n  out.flush()\n  time.sleep(.05)\n"
    )
    executable.chmod(0o755)
    with execution_context(tmp_path, ResourcePolicy(disk_mb=2)):
        with pytest.raises(ResourceExceeded, match="disk limit"):
            run_tool("foamRun", tmp_path / "case", 10)
    assert (tmp_path / "large.bin").stat().st_size < 20 * 1024 * 1024


def test_memory_limit_is_inherited_by_tool(fake_runtime, tmp_path):
    import sys

    from venturi.models import ResourcePolicy
    from venturi.runtime import execution_context

    executable = fake_runtime / "foamRun"
    executable.write_text(f"#!{sys.executable}\nx = bytearray(512 * 1024 * 1024)\n")
    executable.chmod(0o755)
    with execution_context(tmp_path, ResourcePolicy(memory_mb=256)):
        with pytest.raises(RuntimeError, match="status"):
            run_tool("foamRun", tmp_path / "case", 10)
    assert "MemoryError" in (tmp_path / "logs/foamRun.log").read_text()


@pytest.mark.parametrize("installed", [False, True])
def test_cad_diagnostic_distinguishes_package_from_system_library(monkeypatch, installed):
    import builtins
    import importlib.metadata

    from venturi.runtime import diagnostics

    real_import = builtins.__import__

    def version(name):
        if not installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return "7.8.1.1.post1"

    def import_without_gl(name, *args, **kwargs):
        if name == "OCP.STEPControl":
            raise ImportError("libGL.so.1: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setattr(builtins, "__import__", import_without_gl)
    monkeypatch.setattr("venturi.runtime.find_foam_root", lambda: None)
    check = diagnostics()["checks"][0]
    assert check["status"] == "fail"
    if installed:
        assert "libgl1 and libxrender1" in check["detail"]
        assert "libGL.so.1" in check["detail"]
    else:
        assert "Install the cad extra" in check["detail"]
