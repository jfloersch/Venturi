"""Real native attempts; synthetic provider responses are tested in test_assistant."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from venturi.artifacts import verify
from venturi.cli import reproduce
from venturi.geometry import import_geometry
from venturi.jobs import ACTIVE, RunManager, read_json, wait_attempt
from venturi.models import RunRequest, parse_study
from venturi.recovery import (
    RecoveryPolicy,
    RecoveryStart,
    list_campaigns,
    physical_hash,
    plan,
    start,
)
from venturi.visual_review import create_packet

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("VENTURI_INTEGRATION") != "1",
        reason="Set VENTURI_INTEGRATION=1 for real native acceptance",
    ),
]


def mesh(manager, study):
    state = manager.submit(
        RunRequest(kind="internal_mesh", request_id="m3-mesh-acceptance", study=study)
    )
    state = wait_attempt(manager.folder(state["id"]))
    assert state["status"] == "passed", state
    return state


def await_campaign(manager, key, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        campaign = next(s for s in list_campaigns(manager.storage) if s["id"] == key)
        if campaign["status"] not in {"queued", "running", "stopping"}:
            assert not any(s["status"] in ACTIVE for s in manager.list())
            return campaign
        time.sleep(0.5)
    raise AssertionError("Recovery failed to stop within its test budget")


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    storage = tmp_path_factory.mktemp("m3-native-recovery")
    import_geometry(ROOT / "fixtures/internal/manifold.step", storage)
    values = read_json(ROOT / "fixtures/internal/manifold.study.json")
    values["max_iterations"] = 100
    study = parse_study(values)
    manager = RunManager(storage)
    original = mesh(manager, study)
    policy = RecoveryPolicy(
        mesh_run_id=original["id"],
        study_hash=original["study_hash"],
        mesh_hash=original["result"]["mesh_hash"],
    )
    return manager, study, policy


def test_real_recovery_after_api_restart_preserves_inputs_and_records_attempts(prepared):
    manager, study, policy = prepared
    request = RecoveryStart(
        request_id="m3-native-recovery",
        policy=policy,
        approved_plan_hash=plan(manager, policy)["plan_hash"],
    )
    state = start(manager, request)
    # A fresh manager represents API restart. Submission with the same request is idempotent.
    assert start(RunManager(manager.storage), request)["id"] == state["id"]
    completed = await_campaign(manager, state["id"])
    assert completed["status"] == "provisional", completed
    assert completed["solver_attempts"] == 2 and completed["remesh_attempts"] == 1
    assert completed["changes"][0]["old"] == 100 and completed["changes"][0]["new"] == 200
    assert completed["sensitivity"]["status"] == "stable"
    for attempt in completed["attempts"]:
        folder = manager.folder(attempt["run_id"])
        verify(folder)
        assert physical_hash(read_json(folder / "study.json")) == physical_hash(study.model_dump())
        assert (
            read_json(folder / "campaign-context.json")["plan"]["plan_hash"]
            == request.approved_plan_hash
        )


@pytest.mark.parametrize("limit", ["time", "disk", "cancel", "attempts"])
def test_real_recovery_stops_within_budget_or_user_cancellation(prepared, limit):
    manager, _, policy = prepared
    changes = {
        "time": {"wall_time_seconds": 1},
        "disk": {"disk_mb": 1},
        "cancel": {},
        "attempts": {"maximum_solver_attempts": 1},
    }[limit]
    policy = policy.model_copy(update=changes)
    request = RecoveryStart(
        request_id=f"m3-native-stop-{limit}",
        policy=policy,
        approved_plan_hash=plan(manager, policy)["plan_hash"],
    )
    state = start(manager, request)
    if limit == "cancel":
        (manager.storage / "campaigns" / state["id"] / ".cancel").touch()
    completed = await_campaign(manager, state["id"])
    assert completed["status"] == ("cancelled" if limit == "cancel" else "budget_exhausted"), (
        completed
    )
    if limit == "attempts":
        assert completed["remesh_attempts"] == 0


@pytest.mark.parametrize("name", ["held-out-rotated-duct", "held-out-rotated-duct-2"])
def test_held_out_recovery_preserves_all_quantitative_gates(tmp_path, name):
    fixtures = tmp_path / "fixtures"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/make_flow_fixtures.py"),
            "--output",
            str(fixtures),
            "--held-out",
        ],
        check=True,
        capture_output=True,
    )
    storage = tmp_path / "store"
    import_geometry(fixtures / f"{name}.step", storage)
    values = read_json(fixtures / f"{name}.study.json")
    values["max_iterations"] = 100
    # Higher, still supported laminar flow makes the initial iteration budget a
    # real convergence challenge. Physics is frozen before the first attempt.
    values["flow_rate_m3_s"] = 1.6e-6
    study = parse_study(values)
    manager = RunManager(storage)
    original = mesh(manager, study)
    policy = RecoveryPolicy(
        mesh_run_id=original["id"],
        study_hash=original["study_hash"],
        mesh_hash=original["result"]["mesh_hash"],
    )
    state = start(
        manager,
        RecoveryStart(
            request_id="held-out-recovery",
            policy=policy,
            approved_plan_hash=plan(manager, policy)["plan_hash"],
        ),
    )
    completed = await_campaign(manager, state["id"])
    assert completed["status"] == "provisional", completed
    result = read_json(manager.folder(completed["result_run_id"]) / "result.json")
    assert all(c["status"] == "pass" for c in result["checks"])
    assert 2 <= completed["solver_attempts"] <= 3 and completed["remesh_attempts"] <= 2
    for attempt in completed["attempts"]:
        assert physical_hash(
            read_json(manager.folder(attempt["run_id"]) / "study.json")
        ) == physical_hash(study.model_dump())


@pytest.mark.parametrize("reynolds", [20000, 60000])
def test_real_sst_wall_resolution_gate_and_native_replay(tmp_path, reynolds):
    storage = tmp_path / "store"
    import_geometry(ROOT / "fixtures/internal/pipe.step", storage)
    values = read_json(ROOT / "fixtures/internal/pipe.study.json")
    values.pop("physics")
    values.pop("recipe")
    values.update(
        schema_version="venturi.rans-study.v1",
        flow_rate_m3_s=7.853981633974483e-9 * reynolds,
        turbulence_intensity=0.05,
        turbulence_length_scale_m=0.0007,
    )
    study = parse_study(values)
    manager = RunManager(storage)
    original = mesh(manager, study)
    state = manager.submit(
        RunRequest(
            kind="internal_flow",
            request_id="sst-flow-acceptance",
            study=study,
            mesh_run_id=original["id"],
            approved_mesh_hash=original["result"]["mesh_hash"],
        )
    )
    state = wait_attempt(manager.folder(state["id"]))
    expected = "passed" if reynolds == 60000 else "failed"
    assert state["status"] == expected, state
    failures = [c["name"] for c in state["result"]["checks"] if c["status"] != "pass"]
    assert failures == ([] if reynolds == 60000 else ["Wall resolution"])
    verify(manager.folder(state["id"]))
    packet = create_packet(manager.folder(state["id"]), tmp_path / "visual")
    assert packet["quantitative_status"] == expected
    assert packet["artifacts"][0]["units"] == "Pa"
    assert all(len(a["slices"]) == 3 for a in packet["artifacts"])
    if reynolds == 60000:
        replay = reproduce(manager.folder(state["id"]), tmp_path / "replay")
        assert replay["status"] == "passed", replay
        assert replay["result"]["reproduction_relative_difference"] <= 1e-6
