import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from venturi.artifacts import verify
from venturi.jobs import (
    RunManager,
    cancel_attempt,
    initialize_attempt,
    launch_attempt,
    read_json,
    reconcile,
    update_status,
    wait_attempt,
)
from venturi.models import RunRequest, StudySpec, write_json
from venturi.runtime import process_alive, process_identity
from venturi.server import create_app


def until(function, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for process state")


@pytest.fixture
def sleeping_runtime(tmp_path, monkeypatch):
    root = tmp_path / "foam"
    (root / "etc").mkdir(parents=True)
    (root / "etc/bashrc").touch()
    bin_dir = root / "platforms/linux64GccDPInt32Opt/bin"
    bin_dir.mkdir(parents=True)
    for name in ("foamRun", "blockMesh", "checkMesh", "foamToVTK"):
        tool = bin_dir / name
        if name == "blockMesh":
            tool.write_text(
                "#!/bin/sh\necho 'process fixture started'\nsleep 30 &\necho $! > \"$2/../descendant.pid\"\nwait\n"
            )
        else:
            tool.write_text("#!/bin/sh\necho OpenFOAM-14\n")
        tool.chmod(0o755)
    monkeypatch.setenv("VENTURI_FOAM_ROOT", str(root))
    return root


def test_concurrent_managers_admit_only_one_attempt(tmp_path, monkeypatch):
    launched = []

    def launch(folder):
        launched.append(folder)
        return update_status(folder, status="running", process=process_identity(os.getpid()))

    monkeypatch.setattr("venturi.jobs.launch_attempt", launch)
    request = RunRequest(kind="reference", request_id="concurrent-request")
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(lambda _: RunManager(tmp_path).submit(request), range(4)))
    assert len({x["id"] for x in states}) == len(launched) == 1
    with pytest.raises(ValueError, match="different inputs"):
        RunManager(tmp_path).submit(request.model_copy(update={"reason": "Changed"}))
    with pytest.raises(ValueError, match="active run"):
        RunManager(tmp_path).submit(request.model_copy(update={"request_id": "another-request"}))


def test_api_restart_reconnects_and_cancel_stops_process_group(tmp_path, sleeping_runtime):
    store = tmp_path / "store"
    headers = {"Authorization": "Bearer testing"}
    payload = {"kind": "reference", "request_id": "restart-request"}
    with TestClient(create_app(store, "testing")) as client:
        first = client.post("/v1/runs", json=payload, headers=headers).json()
        folder = store / "runs" / first["id"]
        until(lambda: (folder / "descendant.pid").exists())
        pid = int((folder / "descendant.pid").read_text())
        descendant = process_identity(pid)
    assert process_alive(first["process"])
    with TestClient(create_app(store, "testing")) as client:
        again = client.post("/v1/runs", json=payload, headers=headers).json()
        assert again["id"] == first["id"]
        assert again["status"] == "running"
        assert client.post(f"/v1/runs/{first['id']}/cancel", headers=headers).status_code == 200
        state = wait_attempt(folder)
        assert state["status"] == "cancelled"
        until(lambda: not process_alive(descendant))
        assert verify(folder)
        assert len(list((store / "runs").iterdir())) == 1


def test_killed_runner_is_interrupted_and_descendants_are_reaped(tmp_path, sleeping_runtime):
    manager = RunManager(tmp_path / "store")
    state = manager.submit(RunRequest(kind="reference", request_id="kill-runner-request"))
    folder = manager.folder(state["id"])
    until(lambda: (folder / "descendant.pid").exists())
    descendant = process_identity(int((folder / "descendant.pid").read_text()))
    os.kill(state["process"]["pid"], signal.SIGKILL)
    until(lambda: not process_alive(state["process"]))
    # Process death and release of its file lock are asynchronous. The API/CLI
    # poll reconciliation; require bounded recovery instead of an immediate read.
    recovered = until(lambda: (value := reconcile(folder))["status"] == "interrupted" and value)
    assert recovered["status"] == "interrupted"
    until(lambda: not process_alive(descendant))
    assert verify(folder)
    assert "process fixture started" in (folder / "logs/blockMesh.log").read_text()
    retry = manager.submit(
        RunRequest(
            kind="reference",
            request_id="explicit-retry-request",
            retry_of=state["id"],
            reason="Retry after runner crash",
        )
    )
    retry_folder = manager.folder(retry["id"])
    cancel_attempt(retry_folder)
    assert wait_attempt(retry_folder)["status"] == "cancelled"
    assert retry["id"] != state["id"]
    assert retry["retry_of"] == state["id"]
    assert verify(folder)


def test_tampered_study_stops_before_any_command(tmp_path):
    folder = tmp_path / "tampered"
    initialize_attempt(folder, RunRequest(kind="reference", request_id="tampered-study"))
    study = read_json(folder / "study.json")
    study["pipe"]["length_m"] = 0.2
    write_json(folder / "study.json", study)
    launch_attempt(folder)
    state = wait_attempt(folder)
    assert state["status"] == "failed"
    assert "modified" in state["error"]
    assert not (folder / "commands").exists()
    assert verify(folder)


def test_total_deadline_creates_failed_report(tmp_path, sleeping_runtime):
    study = StudySpec.model_validate({"resources": {"wall_time_seconds": 2}})
    manager = RunManager(tmp_path / "store")
    state = manager.submit(RunRequest(kind="reference", request_id="deadline-request", study=study))
    folder = manager.folder(state["id"])
    result = wait_attempt(folder)
    assert result["status"] == "failed"
    assert "wall-time" in result["error"]
    assert verify(folder)


def test_finalization_recovered_after_process_dies(tmp_path):
    from venturi.jobs import finish_attempt

    folder = tmp_path / "finalized"
    initialize_attempt(folder, RunRequest(kind="reference", request_id="finalization-test"))
    finish_attempt(folder, "failed", error="Injected tool failure")
    (folder / "manifest.json").unlink()  # crash between terminal status and manifest
    assert reconcile(folder)["status"] == "failed"
    assert verify(folder)
    assert any(
        read_json(p)["event"] == "finalization_recovered"
        for p in (folder / "events").glob("*.json")
    )


def test_api_process_death_does_not_terminate_active_attempt(tmp_path, sleeping_runtime):
    import socket
    import subprocess
    import sys

    import httpx

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    store = tmp_path / "api-store"
    env = {**os.environ, "VENTURI_API_TOKEN": "api-restart-test"}
    command = [
        sys.executable,
        "-m",
        "venturi.cli",
        "serve",
        "--storage",
        str(store),
        "--port",
        str(port),
    ]
    headers = {"Authorization": "Bearer api-restart-test"}
    base = f"http://127.0.0.1:{port}"
    processes = []
    folder = None

    def start_server():
        proc = subprocess.Popen(
            command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        processes.append(proc)

        def ready():
            try:
                return httpx.get(base + "/v1/health", timeout=0.5).status_code == 200
            except httpx.HTTPError:
                return False

        until(ready)
        return proc

    try:
        first = start_server()
        payload = {"kind": "reference", "request_id": "api-process-restart"}
        response = httpx.post(base + "/v1/runs", json=payload, headers=headers)
        assert response.status_code == 202
        state = response.json()
        folder = store / "runs" / state["id"]
        until(lambda: (folder / "descendant.pid").exists())
        first.kill()
        first.wait(timeout=5)
        assert process_alive(state["process"])
        start_server()
        same = httpx.post(base + "/v1/runs", json=payload, headers=headers).json()
        assert same["id"] == state["id"]
        assert same["status"] == "running"
        assert (
            httpx.post(base + f"/v1/runs/{state['id']}/cancel", headers=headers).status_code == 200
        )
        assert wait_attempt(folder)["status"] == "cancelled"
        assert verify(folder)
    finally:
        if folder and reconcile(folder)["status"] in {"queued", "running"}:
            cancel_attempt(folder)
            wait_attempt(folder)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
