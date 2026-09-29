import copy
from pathlib import Path

import pytest

from venturi.artifacts import seal
from venturi.geometry import import_geometry
from venturi.jobs import RunManager, initialize_attempt, read_json, update_status
from venturi.models import RunRequest, canonical_hash, parse_study, write_json
from venturi.recovery import (
    RecoveryPolicy,
    RecoveryStart,
    campaign_status,
    clean_environment,
    next_action,
    physical_hash,
    plan,
    sensitivity,
    start,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mesh(tmp_path):
    geometry = import_geometry(ROOT / "fixtures/internal/pipe.step", tmp_path)
    s = parse_study(read_json(ROOT / "fixtures/internal/pipe.study.json"))
    manager = RunManager(tmp_path)
    folder = manager.runs / "test-mesh"
    state = initialize_attempt(
        folder,
        RunRequest(kind="internal_mesh", request_id="test-mesh-001", study=s),
        {
            "source": tmp_path / "geometry" / geometry["geometry_hash"] / "source.step",
            "geometry": geometry,
            "selection": s.selection.model_dump(),
        },
    )
    result = {
        "status": "passed",
        "workflow": "internal_mesh",
        "mesh_hash": "a" * 64,
        "study_hash": state["study_hash"],
        "recipe_hash": state["recipe_hash"],
        "checks": [{"name": "Mesh quality", "status": "pass", "detail": "Test fixture"}],
    }
    write_json(folder / "result.json", result)
    (folder / "report.html").write_text("Test fixture; no actual solver run")
    update_status(folder, status="passed", result=result)
    seal(folder)
    return (
        manager,
        s,
        RecoveryPolicy(
            mesh_run_id=folder.name, study_hash=state["study_hash"], mesh_hash=result["mesh_hash"]
        ),
    )


def test_plan_hash_binds_policy_physics_mesh_and_recipe(mesh):
    manager, study, policy = mesh
    value = plan(manager, policy)
    assert value["physical_hash"] == physical_hash(study.model_dump())
    assert value["plan_hash"] == canonical_hash(
        {k: v for k, v in value.items() if k != "plan_hash"}
    )
    assert (
        plan(manager, policy.model_copy(update={"maximum_solver_attempts": 1}))["plan_hash"]
        != value["plan_hash"]
    )
    with pytest.raises(ValueError, match="does not match"):
        plan(manager, policy.model_copy(update={"mesh_hash": "0" * 64}))


def test_solver_recovery_changes_only_iterations(mesh):
    _, study, policy = mesh
    original = study.model_dump()
    result = {
        "checks": [
            {"name": "Equation residuals", "status": "fail"},
            {"name": "Pressure-drop stability", "status": "fail"},
        ]
    }
    changed, event = next_action("internal_flow", result, original, policy)
    assert changed["max_iterations"] == 1200 and event["old"] == 600
    assert physical_hash(changed) == physical_hash(original)
    expected = copy.deepcopy(original)
    expected["max_iterations"] = 1200
    assert changed == expected
    changed, _ = next_action("internal_flow", result, changed, policy)
    assert changed["max_iterations"] == 2000
    assert next_action("internal_flow", result, changed, policy) is None


@pytest.mark.parametrize(
    "failure",
    [
        "Mass conservation",
        "Impermeable walls",
        "Frozen native inputs",
        "Outlet direction and backflow",
        "Wall resolution",
        "Solver completed",
        "malicious new check",
    ],
)
def test_unapproved_failure_cannot_trigger_recovery(mesh, failure):
    _, study, policy = mesh
    result = {
        "checks": [
            {"name": "Equation residuals", "status": "fail"},
            {"name": failure, "status": "fail"},
        ]
    }
    assert next_action("internal_flow", result, study.model_dump(), policy) is None


def test_remesh_policy_preserves_physics_and_budget(mesh):
    _, study, policy = mesh
    result = {"checks": [{"name": "Enclosed volume", "status": "fail"}]}
    changed, _ = next_action("internal_mesh", result, study.model_dump(), policy)
    assert changed["mesh"]["cell_size_m"] == study.mesh.cell_size_m * 0.8
    assert changed["mesh"]["maximum_cells"] == study.mesh.maximum_cells
    assert physical_hash(changed) == physical_hash(study.model_dump())
    for key, value in [
        ("maximum_solver_attempts", 4),
        ("maximum_remesh_attempts", 3),
        ("maximum_iterations", 2001),
        ("refinement_factor", 1.0),
        ("wall_time_seconds", 14401),
    ]:
        with pytest.raises(ValueError):
            RecoveryPolicy.model_validate({**policy.model_dump(), key: value})


def test_sensitivity_never_calls_material_answer_shift_stable():
    old = {"pressure_drop_pa": 1.0, "outlets": {"outlet_1": {"volume_flow_m3_s": 1.0}}}
    assert sensitivity(None, old)["status"] == "not_assessed"
    assert sensitivity(old, old)["status"] == "stable"
    assert sensitivity(old, {**old, "pressure_drop_pa": 1.5})["status"] == "material_change"
    assert (
        sensitivity(old, {**old, "outlets": {"outlet_1": {"volume_flow_m3_s": 1.3}}})["status"]
        == "material_change"
    )


def test_campaign_launch_is_idempotent_and_blocks_unowned_jobs(mesh, monkeypatch):
    manager, study, policy = mesh
    import os

    import venturi.recovery as module

    calls = []

    class Process:
        pid = os.getpid()

        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def poll(self):
            return None

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    request = RecoveryStart(
        request_id="test-campaign-001",
        policy=policy,
        approved_plan_hash=plan(manager, policy)["plan_hash"],
    )
    state = start(manager, request)
    assert start(manager, request) == state and len(calls) == 1
    with pytest.raises(ValueError, match="owns this worker"):
        manager.submit(RunRequest(kind="internal_mesh", study=study, request_id="unowned-mesh-001"))
    with pytest.raises(ValueError, match="reserved intent"):
        manager.submit(
            RunRequest(kind="internal_mesh", study=study, request_id="unowned-mesh-002"),
            campaign_id=state["id"],
        )
    with pytest.raises(ValueError, match="different policy"):
        start(manager, request.model_copy(update={"approved_plan_hash": "0" * 64}))


def test_dead_supervisor_is_reconciled_without_restart(mesh):
    manager, _, _ = mesh
    folder = manager.storage / "campaigns" / ("b" * 32)
    write_json(
        folder / "status.json",
        {
            "id": folder.name,
            "status": "running",
            "process": {"pid": 99999999, "start_ticks": "0", "boot_id": "test"},
        },
    )
    assert campaign_status(folder)["status"] == "interrupted"
    assert not (folder / "launch.json").exists()


def test_provider_credentials_are_not_in_solver_environment(monkeypatch):
    for name in ("OPENAI_API_KEY", "VENTURI_API_TOKEN", "ANTHROPIC_API_KEY", "OTHER_API_KEY"):
        monkeypatch.setenv(name, "test-secret")
    assert not any("test-secret" == value for value in clean_environment().values())
