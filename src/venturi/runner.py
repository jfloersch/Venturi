"""Detached attempt owner; API lifetime does not control a simulation job."""

import signal
import sys
import time
from pathlib import Path

from .artifacts import verify
from .jobs import file_lock, finish_attempt, read_json, update_status
from .models import BoundarySelection, ResourcePolicy, RunRequest, StudySpec, canonical_hash
from .recipes import freeze_study
from .runtime import RunCancelled, check_execution, event, execution_context
from .workflows import mesh_spike, reference


class Cancellation:
    def __init__(self, folder: Path):
        self.folder = folder
        self.signalled = False

    def is_set(self):
        return self.signalled or (self.folder / ".cancel").exists()

    def signal(self, *_):
        self.signalled = True


def execute(folder: Path) -> None:
    with file_lock(folder / ".run.lock"):
        state = read_json(folder / "status.json")
        if state["status"] != "queued":
            return
        cancel = Cancellation(folder)
        signal.signal(signal.SIGTERM, cancel.signal)
        signal.signal(signal.SIGINT, cancel.signal)
        try:
            request = RunRequest.model_validate(read_json(folder / "request.json"))
            if canonical_hash(request.model_dump()) != state["request_hash"]:
                raise ValueError("Frozen request was modified before execution.")
            study = request.study or StudySpec()
            if request.kind != "cad_mesh":
                frozen = read_json(folder / "study.json")
                if canonical_hash(frozen) != state["study_hash"] or frozen != study.model_dump():
                    raise ValueError("Frozen study was modified before execution.")
                recipe = read_json(folder / "recipe.json")
                if (
                    canonical_hash(recipe) != state["recipe_hash"]
                    or recipe != freeze_study(study)["recipe"]
                ):
                    raise ValueError("Frozen recipe was modified before execution.")
            update_status(folder, status="running", stage="Preparing attempt")
            event(folder, "attempt_started")

            def progress(stage):
                check_execution(cancel)
                update_status(folder, stage=stage)
                event(folder, "stage", stage=stage)

            policy = study.resources if request.kind != "cad_mesh" else ResourcePolicy()
            with execution_context(folder, policy):
                check_execution(cancel)
                if request.kind == "reference":
                    replay = folder / "replay-source"
                    if replay.exists():
                        verify(replay)
                    result = reference(
                        folder,
                        study=study,
                        cancel=cancel,
                        progress=progress,
                        replay_source=replay if replay.exists() else None,
                    )
                elif request.kind.startswith("internal_"):
                    from .internal import execute_internal

                    replay = folder / "replay-source"
                    result = execute_internal(
                        folder,
                        study,
                        solve=request.kind == "internal_flow",
                        approved_hash=request.approved_mesh_hash,
                        mesh_run_id=request.mesh_run_id,
                        replay_source=replay if replay.exists() else None,
                        cancel=cancel,
                        progress=progress,
                    )
                else:
                    result = mesh_spike(
                        folder,
                        read_json(folder / "geometry.json"),
                        BoundarySelection.model_validate(read_json(folder / "selection.json")),
                        folder / "source.step",
                        cancel,
                        progress,
                    )
                check_execution(cancel)
            finish_attempt(folder, result["status"], result=result)
        except RunCancelled as exc:
            finish_attempt(folder, "cancelled", error=str(exc))
        except Exception as exc:
            finish_attempt(folder, "failed", error=f"{type(exc).__name__}: {exc}")


def main():
    folder = Path(sys.argv[1]).resolve()
    deadline = time.monotonic() + 10
    while not (folder / "launch.json").exists():
        if time.monotonic() >= deadline:
            return  # Parent died before recording the process identity; no tools ran.
        time.sleep(0.02)
    execute(folder)


if __name__ == "__main__":
    main()
