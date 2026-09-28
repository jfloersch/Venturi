"""Durable local attempts shared by the CLI and API. Linux worker only."""

import fcntl
import json
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import seal, verify
from .evidence import check, write_report
from .models import InternalFlowStudy, RunRequest, StudySpec, canonical_hash, file_hash, write_json
from .recipes import freeze_study
from .runtime import event, process_alive, process_identity, terminate_group

ACTIVE = {"queued", "running"}
TERMINAL = {"passed", "failed", "cancelled", "interrupted"}
_children: list[subprocess.Popen] = []


def now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def file_lock(path: Path, blocking: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def update_status(folder: Path, **values) -> dict:
    state = read_json(folder / "status.json")
    if state["status"] in TERMINAL:
        raise ValueError("A terminal attempt cannot transition; create a new attempt.")
    state.update(values, updated_at=now())
    write_json(folder / "status.json", state)
    return state


def initialize_attempt(folder: Path, request: RunRequest, cad: dict | None = None) -> dict:
    request = RunRequest.model_validate(request.model_dump())
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "request.json", request.model_dump())
    frozen = freeze_study(request.study or StudySpec()) if request.kind != "cad_mesh" else None
    if frozen:
        write_json(folder / "study.json", frozen["study"])
        write_json(folder / "recipe.json", frozen["recipe"])
    if cad:
        shutil.copyfile(cad["source"], folder / "source.step")
        write_json(folder / "geometry.json", cad["geometry"])
        write_json(folder / "selection.json", cad["selection"])
        if cad.get("desktop_context"):
            write_json(folder / "desktop-context.json", cad["desktop_context"])
        if cad.get("approved_mesh"):
            shutil.copytree(
                cad["approved_mesh"],
                folder / "approved-mesh",
                ignore=shutil.ignore_patterns(".run.lock", ".cancel"),
            )
    state = {
        "schema_version": "venturi.attempt.v1",
        "id": folder.name,
        "kind": request.kind,
        "status": "queued",
        "stage": "Queued",
        "created_at": now(),
        "request": request.model_dump(),
        "request_hash": canonical_hash(request.model_dump()),
        "study_hash": frozen["study_hash"] if frozen else None,
        "recipe_hash": frozen["recipe_hash"] if frozen else None,
        "retry_of": request.retry_of,
        "reason": request.reason,
        "process": None,
    }
    if cad and cad.get("desktop_context"):
        record = cad["desktop_context"]["study"]
        state["desktop_brief"] = {
            k: record[k] for k in ("id", "revision", "question", "material_source", "profile")
        }
    write_json(folder / "status.json", state)
    event(folder, "attempt_created", study_hash=state["study_hash"], reason=request.reason)
    return state


def launch_attempt(folder: Path) -> dict:
    global _children
    _children = [p for p in _children if p.poll() is None]
    with (folder / "runner.log").open("a") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "venturi.runner", str(folder.resolve())],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _children.append(proc)
    state = update_status(folder, process=process_identity(proc.pid))
    # The runner cannot execute until its identity has been durably recorded.
    write_json(folder / "launch.json", {"process": state["process"]})
    return state


def finish_attempt(
    folder: Path, status: str, result: dict | None = None, error: str | None = None
) -> dict:
    if status not in TERMINAL:
        raise ValueError("Invalid terminal state.")
    state = read_json(folder / "status.json")
    if result is None:
        inputs = (
            read_json(folder / "study.json")
            if (folder / "study.json").exists()
            else state.get("request", {})
        )
        result = {
            "status": status,
            "checks": [check("Execution completed", False, error or "Execution stopped")],
            "limitations": [
                "This attempt did not produce a complete verified result. Inspect retained logs before retrying."
            ],
            "failure": {"kind": status, "detail": error},
        }
        environment = (
            read_json(folder / "environment.json")
            if (folder / "environment.json").exists()
            else {"solver_ready": False, "assessment": "not completed"}
        )
        write_report(folder, result, inputs, environment)
    state.update(
        status=status,
        stage="Evidence ready"
        if status in {"passed", "failed"} and not error
        else (error or status),
        result=result,
        finished_at=now(),
        updated_at=now(),
    )
    if error:
        state["error"] = error
    write_json(folder / "status.json", state)
    event(folder, "attempt_finished", status=status, error=error)
    seal(folder)
    return state


def reconcile(folder: Path) -> dict:
    state = read_json(folder / "status.json")
    if process_alive(state.get("process")):
        return state
    if state["status"] not in ACTIVE:
        if (
            state.get("schema_version") == "venturi.attempt.v1"
            and not (folder / "manifest.json").exists()
        ):
            try:
                with file_lock(folder / ".run.lock", blocking=False):
                    if not (folder / "manifest.json").exists():
                        if (folder / "report.html").is_file() and (
                            folder / "result.json"
                        ).is_file():
                            event(folder, "finalization_recovered", status=state["status"])
                            seal(folder)
                        else:
                            return finish_attempt(
                                folder,
                                "interrupted",
                                error="Runner stopped during report finalization; complete evidence is unavailable.",
                            )
            except BlockingIOError:
                pass
        return state
    # A newly reserved attempt may be between durable creation and launch.
    age = (
        datetime.now(UTC)
        - datetime.fromisoformat(state.get("created_at", "2000-01-01T00:00:00+00:00"))
    ).total_seconds()
    if state["status"] == "queued" and not state.get("process") and age < 10:
        return state
    try:
        with file_lock(folder / ".run.lock", blocking=False):
            state = read_json(folder / "status.json")
            if state["status"] not in ACTIVE or process_alive(state.get("process")):
                return state
            for path in (folder / "commands").glob("*.json"):
                record = read_json(path)
                if record.get("status") == "running":
                    terminate_group(record.get("process"))
                    record.update(
                        status="interrupted",
                        finished_at=now(),
                        returncode=None,
                        error="Runner stopped; exit status unavailable.",
                    )
                    write_json(path, record)
                    event(folder, "tool_interrupted", tool=record["tool"])
            return finish_attempt(
                folder,
                "interrupted",
                error="Runner stopped or worker restarted without a live runner; partial artifacts retained. Start an explicit retry.",
            )
    except BlockingIOError:
        return state


def cancel_attempt(folder: Path) -> None:
    state = reconcile(folder)
    if state["status"] not in ACTIVE:
        raise ValueError("This run is no longer active.")
    write_json(folder / ".cancel", {"requested_at": now()})


def wait_attempt(folder: Path, progress=lambda _: None) -> dict:
    stage = None
    try:
        while True:
            state = reconcile(folder)
            if state["stage"] != stage:
                stage = state["stage"]
                progress(stage)
            if state["status"] not in ACTIVE:
                # Terminal visibility precedes the final manifest by a few writes.
                with file_lock(folder / ".run.lock"):
                    return read_json(folder / "status.json")
            time.sleep(0.2)
    except KeyboardInterrupt:
        cancel_attempt(folder)
        return wait_attempt(folder, progress)


class RunManager:
    def __init__(self, storage: Path):
        self.storage = storage.resolve()
        self.runs = self.storage / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)

    def folder(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("Invalid run identifier.")
        folder = self.runs / run_id
        if not (folder / "status.json").is_file():
            raise KeyError("Run not found.")
        return folder

    def list(self) -> list[dict]:
        return sorted(
            (reconcile(p.parent) for p in self.runs.glob("*/status.json")),
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )

    def submit(self, request: RunRequest, cad: dict | None = None) -> dict:
        request = RunRequest.model_validate(request.model_dump())
        with file_lock(self.storage / ".admission.lock"):
            states = self.list()
            for state in states:
                previous = state.get("request")
                if previous and previous["request_id"] == request.request_id:
                    previous = RunRequest.model_validate(previous).model_dump()
                    if previous != request.model_dump():
                        raise ValueError("This request ID already belongs to different inputs.")
                    return state
            if any(s["status"] in ACTIVE for s in states):
                raise ValueError("This worker already has an active run. Wait or cancel it.")
            if request.retry_of:
                parent = read_json(self.folder(request.retry_of) / "status.json")
                original = RunRequest.model_validate(parent["request"])
                if parent["status"] not in TERMINAL or original.kind != request.kind:
                    raise ValueError("Retry requires a terminal attempt of the same kind.")
                if (
                    (original.study or StudySpec()) != (request.study or StudySpec())
                    or original.geometry_hash != request.geometry_hash
                    or original.mesh_run_id != request.mesh_run_id
                    or original.approved_mesh_hash != request.approved_mesh_hash
                    or original.desktop_study != request.desktop_study
                ):
                    raise ValueError("Retry must preserve the original study and geometry.")
                if request.kind != "reference":
                    source = self.folder(request.retry_of)
                    cad = {
                        "source": source / "source.step",
                        "geometry": read_json(source / "geometry.json"),
                        "selection": read_json(source / "selection.json"),
                    }
                    if (source / "desktop-context.json").exists():
                        cad["desktop_context"] = read_json(source / "desktop-context.json")
                    if request.kind == "internal_flow":
                        cad["approved_mesh"] = source / "approved-mesh"
            if request.kind.startswith("internal_"):
                if not isinstance(request.study, InternalFlowStudy):
                    raise ValueError("Internal-flow study is required.")
                if not cad:
                    source = self.storage / "geometry" / request.study.selection.geometry_hash
                    if not (source / "source.step").is_file():
                        raise ValueError(
                            "Import the study's STEP geometry before building its mesh."
                        )
                    cad = {
                        "source": source / "source.step",
                        "geometry": read_json(source / "geometry.json"),
                        "selection": request.study.selection.model_dump(),
                    }
                if file_hash(cad["source"]) != request.study.selection.geometry_hash:
                    raise ValueError("Source STEP differs from the study's geometry hash.")
                if request.kind == "internal_flow":
                    mesh = cad.get("approved_mesh") or self.folder(request.mesh_run_id)
                    mesh_state = reconcile(mesh)
                    if mesh_state["status"] != "passed" or mesh_state["kind"] != "internal_mesh":
                        raise ValueError("Only a passed internal mesh can be approved for solving.")
                    with file_lock(mesh / ".run.lock"):
                        verify(mesh)
                    result = read_json(mesh / "result.json")
                    if (
                        result.get("study_hash") != freeze_study(request.study)["study_hash"]
                        or result.get("recipe_hash") != freeze_study(request.study)["recipe_hash"]
                        or result.get("mesh_hash") != request.approved_mesh_hash
                    ):
                        raise ValueError(
                            "Mesh approval is stale or belongs to different study inputs."
                        )
                    cad["approved_mesh"] = mesh
                if request.desktop_study and not request.retry_of:
                    ref = request.desktop_study
                    record_path = (
                        self.storage / "studies" / ref.id / "revisions" / f"{ref.revision}.json"
                    )
                    if not record_path.is_file():
                        raise ValueError("Saved desktop study revision not found.")
                    record = read_json(record_path)
                    if record["study_hash"] != canonical_hash(request.study.model_dump()):
                        raise ValueError("Desktop study revision differs from execution inputs.")
                    notes = (
                        self.storage
                        / "geometry"
                        / request.study.selection.geometry_hash
                        / "annotations.json"
                    )
                    cad["desktop_context"] = {
                        "study": record,
                        "annotations": read_json(notes) if notes.exists() else None,
                    }
            folder = self.runs / uuid.uuid4().hex
            initialize_attempt(folder, request, cad)
            try:
                return launch_attempt(folder)
            except Exception as exc:
                finish_attempt(folder, "failed", error=f"Could not launch runner: {exc}")
                raise
