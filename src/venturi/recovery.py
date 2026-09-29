"""Detached, deterministic recovery supervisor. No provider access in this process."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import Field

from .artifacts import verify
from .jobs import ACTIVE, RunManager, cancel_attempt, file_lock, now, read_json, reconcile
from .models import RunRequest, StrictModel, canonical_hash, parse_study, write_json
from .recipes import freeze_study
from .runtime import process_alive, process_identity
from .workspace import plan_study


class RecoveryPolicy(StrictModel):
    mesh_run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    study_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mesh_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximum_solver_attempts: int = Field(default=3, ge=1, le=3)
    maximum_remesh_attempts: int = Field(default=2, ge=0, le=2)
    maximum_iterations: int = Field(default=2000, ge=100, le=2000)
    refinement_factor: float = Field(default=0.8, ge=0.5, le=0.95)
    wall_time_seconds: int = Field(default=7200, ge=1, le=14400)
    disk_mb: int = Field(default=4096, ge=1, le=16384)


class RecoveryStart(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    policy: RecoveryPolicy
    approved_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


SOLVER_RECOVERABLE = {
    "Pressure-drop stability",
    "Outlet flow stability",
    "Equation residuals",
    "Turbulence equation residuals",
}
MESH_RECOVERABLE = {
    "Mesh quality",
    "Enclosed volume",
    "inlet mapping",
    "wall mapping",
    "outlet_1 mapping",
    "outlet_2 mapping",
    "outlet_3 mapping",
    "outlet_4 mapping",
}
CAMPAIGN_ACTIVE = {"queued", "running", "stopping"}
_children = []


def physical_hash(study: dict):
    return canonical_hash(
        {
            k: v
            for k, v in study.items()
            if k not in {"mesh", "max_iterations", "resources", "timeout_seconds"}
        }
    )


def clean_environment():
    # Credentials supplied through the UI are never environment variables; also
    # exclude common inherited provider secrets from detached simulation processes.
    return {
        k: v
        for k, v in os.environ.items()
        if not any(
            s in k.upper()
            for s in (
                "KEY",
                "TOKEN",
                "PASSWORD",
                "SECRET",
                "OPENAI",
                "ANTHROPIC",
                "STRIPE",
                "GATEWAY",
            )
        )
    }


def plan(manager: RunManager, policy: RecoveryPolicy):
    folder = manager.folder(policy.mesh_run_id)
    state = reconcile(folder)
    if state["kind"] != "internal_mesh" or state["status"] not in {"passed", "failed"}:
        raise ValueError("Recovery starts from a completed internal mesh review.")
    with file_lock(folder / ".run.lock"):
        verify(folder)
    result = read_json(folder / "result.json")
    study = parse_study(read_json(folder / "study.json"))
    frozen = freeze_study(study)
    if result.get("mesh_hash") != policy.mesh_hash or frozen["study_hash"] != policy.study_hash:
        raise ValueError("Recovery approval does not match the mesh and study revision.")
    if result.get("recipe_hash") != frozen["recipe_hash"]:
        raise ValueError("Recipe changed; build and review a fresh mesh.")
    if study.max_iterations > policy.maximum_iterations:
        raise ValueError("The policy iteration ceiling is below the starting study.")
    value = {
        "schema_version": "venturi.recovery-plan.v1",
        "policy": policy.model_dump(),
        "study": study.model_dump(),
        "recipe": frozen["recipe"],
        "recipe_hash": frozen["recipe_hash"],
        "physical_hash": physical_hash(study.model_dump()),
        "controls": {
            "solver": "On stability/residual failure only: double iterations up to the ceiling; regenerate and audit the mesh for the changed study hash.",
            "mesh": "On supported mesh-quality/mapping failure only: reduce cell size by the approved factor; repeat every mesh gate.",
            "sensitivity": "Pressure or branch-flow change above 2% is provisional; no automatic physical changes or threshold relaxation.",
        },
        "cost": "One CPU; aggregate wall time and retained disk monitored across all attempts. No AI calls or AI spend during recovery.",
        "starting_mesh_status": state["status"],
    }
    return {**value, "plan_hash": canonical_hash(value)}


def campaign_folder(storage: Path, key: str):
    if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid recovery identifier.")
    path = storage / "campaigns" / key
    if not (path / "status.json").exists():
        raise ValueError("Recovery not found.")
    return path


def campaign_status(folder: Path):
    with file_lock(folder / ".state.lock"):
        state = read_json(folder / "status.json")
        if state["status"] in CAMPAIGN_ACTIVE and not process_alive(state.get("process")):
            # Starting campaigns are protected by the manager admission lock.
            owned = [
                s
                for s in RunManager(folder.parent.parent).list()
                if s["request"]["request_id"].startswith("recovery-" + folder.name + "-")
                and s["status"] in ACTIVE
            ]
            for run in owned:
                cancel_attempt(folder.parent.parent / "runs" / run["id"])
            state.update(
                status="stopping" if owned else "interrupted",
                reason="Recovery supervisor stopped. Owned runs are cancelled; no automatic relaunch or duplicate attempts.",
            )
            write_json(folder / "status.json", state)
        return state


def list_campaigns(storage: Path):
    # Same lock as admission avoids interpreting a not-yet-launched supervisor as dead.
    with file_lock(storage / ".admission.lock"):
        return sorted(
            [campaign_status(p.parent) for p in (storage / "campaigns").glob("*/status.json")],
            key=lambda s: s["created_at"],
            reverse=True,
        )


def start(manager: RunManager, request: RecoveryStart):
    global _children
    with file_lock(manager.storage / ".admission.lock"):
        for p in (manager.storage / "campaigns").glob("*/status.json"):
            state = campaign_status(p.parent)
            if state["request"]["request_id"] == request.request_id:
                if state["request"] != request.model_dump():
                    raise ValueError("Recovery request ID already belongs to a different policy.")
                return state
            if state["status"] in CAMPAIGN_ACTIVE:
                raise ValueError("A recovery campaign is already active.")
        if any(s["status"] in ACTIVE for s in manager.list()):
            raise ValueError("Wait for the current run before starting recovery.")
        approved = plan(manager, request.policy)
        if approved["plan_hash"] != request.approved_plan_hash:
            raise ValueError("Review and approve the exact recovery plan before execution.")
        folder = manager.storage / "campaigns" / uuid.uuid4().hex
        state = {
            "id": folder.name,
            "schema_version": "venturi.recovery.v1",
            "request": request.model_dump(),
            "created_at": now(),
            "status": "queued",
            "plan_hash": approved["plan_hash"],
            "attempts": [],
            "changes": [],
            "solver_attempts": 0,
            "remesh_attempts": 0,
            "process": None,
            "reason": "Approved by user",
            "ai_spend_usd": 0,
        }
        write_json(folder / "plan.json", approved)
        write_json(folder / "status.json", state)
        try:
            with (folder / "supervisor.log").open("a") as output:
                process = subprocess.Popen(
                    [sys.executable, "-m", "venturi.recovery", str(folder)],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=clean_environment(),
                )
            _children = [p for p in _children if p.poll() is None] + [process]
            state["process"] = process_identity(process.pid)
            write_json(folder / "status.json", state)
            write_json(folder / "launch.json", {"process": state["process"]})
        except Exception:
            state.update(status="failed", reason="Could not launch recovery supervisor.")
            write_json(folder / "status.json", state)
            raise
        return state


def failed_checks(result: dict):
    return {c["name"] for c in result.get("checks", []) if c["status"] != "pass"}


def next_action(kind: str, result: dict, study: dict, policy: RecoveryPolicy):
    failures = failed_checks(result)
    if not failures:
        return None
    changed = json.loads(json.dumps(study))
    if kind == "internal_flow" and failures <= SOLVER_RECOVERABLE:
        old = study["max_iterations"]
        new = min(old * 2, policy.maximum_iterations)
        if new <= old:
            return None
        changed["max_iterations"] = new
        field = "max_iterations"
    elif kind == "internal_mesh" and failures <= MESH_RECOVERABLE:
        old = study["mesh"]["cell_size_m"]
        new = old * policy.refinement_factor
        changed["mesh"]["cell_size_m"] = new
        field = "mesh.cell_size_m"
    else:
        return None
    assert physical_hash(changed) == physical_hash(study)
    return changed, {
        "field": field,
        "old": old,
        "new": new,
        "failing_checks": sorted(failures),
        "rationale": "Apply the next pre-authorized recipe control; all earlier attempts retained for rollback and comparison.",
        "affected_quantities": ["pressure_drop_pa", "outlet_volume_flows_m3_s"],
    }


def sensitivity(previous: dict | None, current: dict):
    if not previous or "pressure_drop_pa" not in previous:
        return {
            "status": "not_assessed",
            "reason": "No previous complete flow evidence; mesh independence is not established.",
        }
    changes = {
        "pressure_drop_pa": abs(current["pressure_drop_pa"] - previous["pressure_drop_pa"])
        / max(abs(current["pressure_drop_pa"]), 1e-30)
    }
    for name, value in current.get("outlets", {}).items():
        if name in previous.get("outlets", {}):
            changes[name] = abs(
                value["volume_flow_m3_s"] - previous["outlets"][name]["volume_flow_m3_s"]
            ) / max(abs(value["volume_flow_m3_s"]), 1e-30)
    return {
        "status": "material_change" if max(changes.values()) > 0.02 else "stable",
        "relative_changes": changes,
        "threshold": 0.02,
    }


def execute(folder: Path):
    with file_lock(folder / ".supervisor.lock"):
        state = read_json(folder / "status.json")
        if state["status"] != "queued":
            return
        manager = RunManager(folder.parent.parent)
        policy = RecoveryPolicy.model_validate(state["request"]["policy"])
        approved = read_json(folder / "plan.json")
        stopped = False

        def stop(*_):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def persist():
            with file_lock(folder / ".state.lock"):
                write_json(folder / "status.json", state)

        def budget():
            elapsed = time.time() - datetime.fromisoformat(state["created_at"]).timestamp()
            size = 0
            for attempt in state["attempts"]:
                for path in manager.folder(attempt["run_id"]).rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        try:
                            size += path.stat().st_size
                        except FileNotFoundError:
                            pass
            state.update(elapsed_seconds=elapsed, retained_disk_mb=size / 1024**2)
            return elapsed >= policy.wall_time_seconds or size >= policy.disk_mb * 1024**2

        def run(kind, study, mesh=None):
            if stopped or (folder / ".cancel").exists():
                raise InterruptedError("User cancelled recovery.")
            if budget():
                raise TimeoutError("Aggregate recovery compute or disk budget exhausted.")
            geometry_folder = manager.storage / "geometry" / study["selection"]["geometry_hash"]
            copy_bytes = sum(
                (geometry_folder / name).stat().st_size for name in ("source.step", "geometry.json")
            )
            if mesh:
                copy_bytes += sum(
                    p.stat().st_size for p in manager.folder(mesh["id"]).rglob("*") if p.is_file()
                )
            if copy_bytes + 65536 > (policy.disk_mb - state["retained_disk_mb"]) * 1024**2:
                raise TimeoutError(
                    "Remaining recovery disk budget cannot hold the required frozen inputs and approved mesh copy."
                )
            if (
                physical_hash(study) != approved["physical_hash"]
                or freeze_study(parse_study(study))["recipe_hash"] != approved["recipe_hash"]
            ):
                raise ValueError("Recovery attempted to change physics or recipe.")
            if kind == "internal_mesh":
                if state["remesh_attempts"] >= policy.maximum_remesh_attempts:
                    raise TimeoutError("Remesh attempt budget exhausted.")
                state["remesh_attempts"] += 1
            else:
                if state["solver_attempts"] >= policy.maximum_solver_attempts:
                    raise TimeoutError("Solver attempt budget exhausted.")
                state["solver_attempts"] += 1
            request = RunRequest(
                kind=kind,
                study=parse_study(study),
                request_id=f"recovery-{folder.name}-{len(state['attempts'])}",
                mesh_run_id=mesh["id"] if mesh else None,
                approved_mesh_hash=mesh["result"]["mesh_hash"] if mesh else None,
                reason=f"Approved recovery {folder.name}; plan {approved['plan_hash']}",
            )
            state["expected_request_hash"] = canonical_hash(request.model_dump())
            persist()  # Intent and count reservation precede job launch.
            gfolder = manager.storage / "geometry" / study["selection"]["geometry_hash"]
            cad = {
                "source": gfolder / "source.step",
                "geometry": read_json(gfolder / "geometry.json"),
                "selection": study["selection"],
                "campaign_context": {
                    "plan": approved,
                    "changes": state["changes"],
                    "earlier_attempts": state["attempts"],
                },
            }
            original = manager.folder(policy.mesh_run_id) / "desktop-context.json"
            if original.exists():
                cad["desktop_context"] = read_json(original)
            active = manager.submit(request, cad, campaign_id=folder.name)
            state["attempts"].append(
                {
                    "run_id": active["id"],
                    "kind": kind,
                    "study_hash": active["study_hash"],
                    "status": active["status"],
                }
            )
            persist()
            reason = None
            while active["status"] in ACTIVE:
                if stopped or (folder / ".cancel").exists():
                    reason = "cancelled"
                elif budget():
                    reason = "budget"
                if reason:
                    cancel_attempt(manager.folder(active["id"]))
                time.sleep(0.3)
                active = reconcile(manager.folder(active["id"]))
            state["attempts"][-1].update(
                status=active["status"],
                failing_checks=sorted(failed_checks(active.get("result", {}))),
            )
            persist()
            if reason == "cancelled":
                raise InterruptedError("User cancelled recovery.")
            if reason == "budget":
                raise TimeoutError("Aggregate recovery compute or disk budget exhausted.")
            # A terminal status is written before report/manifest finalization.
            # Wait for the attempt owner to release its lock before verification.
            with file_lock(manager.folder(active["id"]) / ".run.lock"):
                verify(manager.folder(active["id"]))
            return active

        try:
            if (
                canonical_hash({k: v for k, v in approved.items() if k != "plan_hash"})
                != state["plan_hash"]
            ):
                raise ValueError("Approved recovery plan changed.")
            state["status"] = "running"
            persist()
            study = approved["study"]
            mesh = reconcile(manager.folder(policy.mesh_run_id))
            previous_flow = None
            while True:
                if mesh["status"] == "passed":
                    flow = run("internal_flow", study, mesh)
                    result = flow.get("result", {})
                    if flow["status"] == "passed":
                        state.update(
                            status="provisional",
                            reason="All quantitative checks passed. Independent CFD review and mesh independence remain unassessed.",
                            result_run_id=flow["id"],
                            sensitivity=sensitivity(previous_flow, result),
                        )
                        break
                    action = next_action("internal_flow", result, study, policy)
                    if "pressure_drop_pa" in result:
                        previous_flow = result
                else:
                    result = mesh.get("result", {})
                    action = next_action("internal_mesh", result, study, policy)
                if action is None:
                    state.update(
                        status="needs_input",
                        reason="Failure is outside the approved recovery ladder or the numerical ceiling was reached.",
                        failing_checks=sorted(failed_checks(result)),
                    )
                    break
                if state["solver_attempts"] >= policy.maximum_solver_attempts:
                    raise TimeoutError("Solver attempt budget exhausted; no extra remesh launched.")
                study, change = action
                plan_study(manager.storage, parse_study(study))
                state["changes"].append(
                    {**change, "at": now(), "study_hash": canonical_hash(study)}
                )
                persist()
                mesh = run("internal_mesh", study)
        except TimeoutError as exc:
            state.update(status="budget_exhausted", reason=str(exc))
        except InterruptedError as exc:
            state.update(status="cancelled", reason=str(exc))
        except ValueError as exc:
            state.update(status="needs_input", reason=str(exc))
        except Exception as exc:
            import traceback

            traceback.print_exc()
            state.update(status="failed", reason=f"Recovery stopped: {type(exc).__name__}")
        finally:
            # Cancel any job launched before a supervisor-side failure, including the
            # intent/receipt crash window. Terminal campaign never releases live work.
            for active in manager.list():
                if active["status"] in ACTIVE and active["request"]["request_id"].startswith(
                    "recovery-" + folder.name + "-"
                ):
                    cancel_attempt(manager.folder(active["id"]))
                    while reconcile(manager.folder(active["id"]))["status"] in ACTIVE:
                        time.sleep(0.3)
            budget()
            state["finished_at"] = now()
            persist()


def main():
    folder = Path(sys.argv[1]).resolve()
    deadline = time.monotonic() + 10
    while not (folder / "launch.json").exists():
        if time.monotonic() >= deadline:
            return
        time.sleep(0.02)
    execute(folder)


if __name__ == "__main__":
    main()
