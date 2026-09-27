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
