"""Desktop persistence and read-only inspection are worker contracts, not browser storage."""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from venturi.geometry import import_geometry, inspect_step
from venturi.jobs import RunManager, read_json, update_status
from venturi.models import RunRequest, write_json
from venturi.server import create_app

HEADERS = {"Authorization": "Bearer test-worker-token"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prepared(tmp_path):
    pytest.importorskip("OCP")
    g = import_geometry(ROOT / "fixtures/internal/manifold.step", tmp_path)
    study = json.loads((ROOT / "fixtures/internal/manifold.study.json").read_text())
    return (
        tmp_path,
        g,
        {
            "study": study,
            "question": "How evenly does the manifold divide flow?",
            "material_source": "Water at declared conditions; supplied density and viscosity",
            "profile": "guided",
        },
    )


def client(storage):
    return TestClient(create_app(storage, "test-worker-token"), headers=HEADERS)


def test_saved_study_survives_api_restart_and_reopens_geometry(prepared):
    storage, geometry, payload = prepared
    with client(storage) as api:
        response = api.post("/v1/studies", json=payload)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["revision"] == 1
        assert saved["study"]["resources"]["memory_mb"] == 4096
        api.post("/v1/geometry/fixture")
    with client(storage) as api:
        workspace = api.get("/v1/workspace").json()
        assert workspace["active_id"] == saved["id"]
        assert workspace["studies"] == [saved]
        opened = api.post(f"/v1/studies/{saved['id']}/open").json()
        assert opened["geometry"]["geometry_hash"] == geometry["geometry_hash"]
        assert opened["geometry"]["selection"] == payload["study"]["selection"]
        assert opened["record"] == saved
        assert api.get("/v1/geometry").json()["imported"] is True


def test_conflicting_save_keeps_immutable_history(prepared):
    storage, _, payload = prepared
    with client(storage) as api:
        first = api.post("/v1/studies", json=payload).json()
        update = {**payload, "id": first["id"], "expected_revision": 1}
        update["study"]["flow_rate_m3_s"] *= 0.5
        second = api.post("/v1/studies", json=update).json()
        assert second["revision"] == 2
        assert second["study_hash"] != first["study_hash"]
        assert api.post("/v1/studies", json=update).status_code == 409
        assert read_json(storage / "studies" / first["id"] / "revisions/1.json") == first
        assert read_json(storage / "studies" / first["id"] / "current.json") == second


@pytest.mark.parametrize(
    "mutation", ["regime", "resolution", "unknown_face", "geometry", "resource", "blank_source"]
)
def test_plan_and_save_block_invalid_studies(prepared, mutation):
    storage, _, payload = prepared
    study = payload["study"]
    if mutation == "regime":
        study["flow_rate_m3_s"] = 0.1
    elif mutation == "resolution":
        study["mesh"]["cell_size_m"] = 1
    elif mutation == "unknown_face":
        role = study["selection"]["assignments"].pop(next(iter(study["selection"]["assignments"])))
        study["selection"]["assignments"]["face_0000000000000000"] = role
    elif mutation == "geometry":
        study["selection"]["geometry_hash"] = "0" * 64
    elif mutation == "resource":
        study["resources"] = {"memory_mb": 255}
    elif mutation == "blank_source":
        payload["material_source"] = " "
    with client(storage) as api:
        assert api.post("/v1/studies", json=payload).status_code in {409, 422}
        assert api.get("/v1/workspace").json()["studies"] == []
        assert api.get("/v1/runs").json() == []
        if mutation != "blank_source":
            assert api.post("/v1/studies/plan", json=study).status_code in {409, 422}


def test_plan_has_normalized_limits_without_launching_work(prepared):
    storage, _, payload = prepared
    with client(storage) as api:
        plan = api.post("/v1/studies/plan", json=payload["study"]).json()
        assert all(0 < r <= 200 for r in plan["port_reynolds"].values())
        assert plan["estimated_cells"][0] < plan["estimated_cells"][1]
        assert "not calibrated" in plan["estimate_note"]
        assert plan["resources"]["wall_time_seconds"] == 2400
        assert api.get("/v1/runs").json() == []


def test_notes_bind_edges_faces_camera_and_revision(prepared):
    storage, g, _ = prepared
    edge = g["edges"][0]
    camera = {"position": [1, 1, 1], "focal_point": [0, 0, 0], "view_up": [0, 0, 1]}
    note = {
        "id": "pump-supply",
        "entity_id": edge["id"],
        "label": "Supply edge",
        "text": "Check this rim",
        "position_m": edge["points"][:3],
        "camera": camera,
    }
    payload = {"geometry_hash": g["geometry_hash"], "annotations": [note]}
    with client(storage) as api:
        saved = api.post("/v1/annotations", json=payload)
        assert saved.status_code == 200, saved.text
        record = saved.json()
        assert record["annotations"][0]["reference"]["adjacent_faces"] == edge["adjacent_faces"]
        assert record["annotations"][0]["camera"] == camera
        assert api.post("/v1/annotations", json=payload).status_code == 409
    with client(storage) as api:
        assert api.get(f"/v1/geometry/{g['geometry_hash']}/annotations").json() == record
        payload["expected_revision"] = 1
        for change in (
            {"entity_id": "edge_0000000000000000"},
            {"position_m": [100, 100, 100]},
            {"position_m": [1, 2]},
            {"label": " "},
        ):
            invalid = {**payload, "annotations": [{**note, **change}]}
            assert api.post("/v1/annotations", json=invalid).status_code in {409, 422}
        other = import_geometry(ROOT / "fixtures/internal/bend.step", storage)
        invalid = {**payload, "geometry_hash": other["geometry_hash"], "expected_revision": 0}
        assert api.post("/v1/annotations", json=invalid).status_code == 409
        assert (
            api.get(f"/v1/geometry/{other['geometry_hash']}/annotations").json()["annotations"]
            == []
        )
        payload["annotations"] = []
        assert api.post("/v1/annotations", json=payload).json()["annotations"] == []


def test_changed_source_cannot_restore_saved_study(prepared):
    storage, g, payload = prepared
    with client(storage) as api:
        saved = api.post("/v1/studies", json=payload).json()
        source = storage / "geometry" / g["geometry_hash"] / "source.step"
        source.write_bytes(source.read_bytes() + b"\n")
        assert api.post(f"/v1/studies/{saved['id']}/open").status_code == 409


def test_geometry_reference_ids_are_repeatable_and_face_ids_stay_compatible(prepared):
    _, g, payload = prepared
    again = inspect_step(ROOT / "fixtures/internal/manifold.step")
    assert [e["id"] for e in again["edges"]] == [e["id"] for e in g["edges"]]
    assert set(payload["study"]["selection"]["assignments"]) == {f["id"] for f in g["faces"]}
    assert all(e["adjacent_faces"] and e["length_m"] > 0 for e in g["edges"])


def test_artifact_inventory_text_diff_and_path_guards(tmp_path):
    for key, text in [("a", "endTime 100;\n"), ("b", "endTime 200;\n")]:
        write_json(
            tmp_path / "runs" / key / "status.json",
            {"id": key, "kind": "reference", "status": "failed", "stage": "Stopped"},
        )
        path = tmp_path / "runs" / key / "case/system/controlDict"
        path.parent.mkdir(parents=True)
        path.write_text(text)
    folder = tmp_path / "runs/a"
    (folder / "large.log").write_bytes(b"a" * (512 * 1024 + 1))
    (folder / "binary").write_bytes(b"\x00\xff")
    secret = tmp_path / "secret"
    secret.write_text("not a run artifact")
    (folder / "symlink").symlink_to(secret)
    with client(tmp_path) as api:
        files = api.get("/v1/runs/a/artifacts").json()
        assert "case/system/controlDict" in {f["path"] for f in files}
        assert "symlink" not in {f["path"] for f in files}
        text = api.get("/v1/runs/a/text", params={"path": "case/system/controlDict"}).json()
        assert text["text"] == "endTime 100;\n"
        assert len(text["sha256"]) == 64
        diff = api.get(
            "/v1/runs/a/diff", params={"other": "b", "path": "case/system/controlDict"}
        ).json()
        assert "-endTime 200;" in diff["text"] and "+endTime 100;" in diff["text"]
        for path in (
            "../../secret",
            "symlink",
            "large.log",
            "binary",
            str(secret),
            "case\\system\\controlDict",
        ):
            assert api.get("/v1/runs/a/text", params={"path": path}).status_code == 409
        assert api.get("/v1/runs/a/artifacts", headers={"Authorization": ""}).status_code == 401


def test_attempt_freezes_desktop_context_and_refuses_mismatched_revision(prepared, monkeypatch):
    storage, _, payload = prepared
    monkeypatch.setattr("venturi.jobs.launch_attempt", lambda p: update_status(p, status="queued"))
    with client(storage) as api:
        saved = api.post("/v1/studies", json=payload).json()
    manager = RunManager(storage)
    request = {
        "kind": "internal_mesh",
        "request_id": "desktop-revision-test",
        "study": saved["study"],
        "desktop_study": {"id": saved["id"], "revision": 1},
    }
    bad = copy.deepcopy(request)
    bad["study"]["flow_rate_m3_s"] *= 0.5
    with pytest.raises(ValueError, match="differs"):
        manager.submit(RunRequest.model_validate(bad))
    assert manager.list() == []
    run = manager.submit(RunRequest.model_validate(request))
    context = read_json(manager.folder(run["id"]) / "desktop-context.json")
    assert context["study"] == saved
