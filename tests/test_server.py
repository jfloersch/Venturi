import threading

import pytest
from fastapi.testclient import TestClient

from venturi.models import write_json
from venturi.server import create_app

HEADERS = {"Authorization": "Bearer test-worker-token"}


def test_auth_and_cross_origin_mutations(tmp_path):
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        assert client.get("/v1/health").status_code == 200
        assert client.get("/v1/runs").status_code == 401
        assert client.get("/v1/runs", headers=HEADERS).status_code == 200
        assert client.post("/v1/runs", json={}).status_code == 401
        preflight = client.options(
            "/v1/runs",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 400


def test_restart_marks_unfinished_work_interrupted_and_retains_results(tmp_path):
    request = {"kind": "reference", "request_id": "existing-request", "geometry_hash": None}
    write_json(
        tmp_path / "runs/abc/status.json",
        {
            "id": "abc",
            "kind": "reference",
            "status": "running",
            "stage": "Solving",
            "request": request,
        },
    )
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        states = client.get("/v1/runs", headers=HEADERS).json()
        assert states[0]["status"] == "interrupted"
        assert "restarted" in states[0]["stage"]
        again = client.post("/v1/runs", headers=HEADERS, json=request)
        assert again.json()["id"] == "abc"
        assert again.json()["status"] == "interrupted"


def test_duplicate_request_never_launches_another_solver(tmp_path, monkeypatch):
    calls = []
    gate = threading.Event()

    def fake_reference(folder, cancel, progress):
        calls.append(folder)
        gate.wait(2)
        return {"status": "passed", "checks": []}

    monkeypatch.setattr("venturi.server.reference", fake_reference)
    monkeypatch.setattr("venturi.server.diagnostics", lambda: {"solver_ready": True})
    payload = {"kind": "reference", "request_id": "same-request-123"}
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        first = client.post("/v1/runs", json=payload, headers=HEADERS)
        second = client.post("/v1/runs", json=payload, headers=HEADERS)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        different = client.post("/v1/runs", json={**payload, "kind": "cad_mesh"}, headers=HEADERS)
        assert different.status_code == 409
        gate.set()
    assert len(calls) == 1


def test_artifact_paths_cannot_escape_run_directory(tmp_path):
    write_json(
        tmp_path / "runs/abc/status.json",
        {"id": "abc", "kind": "reference", "status": "failed", "stage": "Stopped"},
    )
    secret = tmp_path / "private.txt"
    secret.write_text("private")
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        assert (
            client.get(
                "/v1/runs/abc/files/%2e%2e%2f%2e%2e%2fprivate.txt", headers=HEADERS
            ).status_code
            == 404
        )
        assert client.get("/v1/runs/abc/files/status.json", headers=HEADERS).status_code == 200


@pytest.mark.cad
def test_selection_save_reopen_and_stale_geometry(tmp_path):
    pytest.importorskip("OCP")
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        geometry = client.get("/v1/geometry", headers=HEADERS).json()
        selection = geometry["selection"]
        assert client.post("/v1/selection", headers=HEADERS, json=selection).status_code == 200
        stale = {**selection, "geometry_hash": "0" * 64}
        assert client.post("/v1/selection", headers=HEADERS, json=stale).status_code == 422
    with TestClient(create_app(tmp_path, "test-worker-token")) as client:
        assert client.get("/v1/geometry", headers=HEADERS).json()["selection"] == selection
