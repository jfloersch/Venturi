"""Authenticated loopback worker for the milestone-0 desktop harness."""

import asyncio
import hmac
import json
import os
import secrets
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import PROTOCOL_VERSION
from .geometry import fixture_selection, inspect_step, validate_selection
from .models import BoundarySelection, RunRequest, file_hash, write_json
from .runtime import RunCancelled, diagnostics
from .workflows import mesh_spike, reference

ROOT = Path(__file__).resolve().parents[2]


def create_app(storage: Path, token: str) -> FastAPI:
    if not token:
        raise ValueError("A worker authentication token is required.")
    storage = storage.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    runs = storage / "runs"
    runs.mkdir(exist_ok=True)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="venturi-run")
    lock = threading.RLock()
    cancellations: dict[str, threading.Event] = {}
    states: dict[str, dict] = {}
    requests: dict[str, tuple[dict, str]] = {}
    for status_file in runs.glob("*/status.json"):
        state = json.loads(status_file.read_text())
        if state["status"] in {"queued", "running"}:
            state.update(
                status="interrupted",
                stage="Worker restarted; inspect retained logs before starting another attempt.",
            )
            write_json(status_file, state)
        states[state["id"]] = state
        if "request" in state:
            requests[state["request"]["request_id"]] = (state["request"], state["id"])

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for event in cancellations.values():
            event.set()
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    app = FastAPI(title="Venturi worker", version="0.0.1", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def auth(request: Request):
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(401, "Worker token is missing or incorrect.")

    # Expensive CAD imports are serialized: OCCT maintains process-wide state.
    geometry_lock = threading.Lock()
    geometry_cache: dict[str, dict] = {}

    def fixture() -> dict:
        source = ROOT / "fixtures/pipe.step"
        if not source.exists():
            raise HTTPException(503, "Pipe fixture is missing from this development checkout.")
        key = file_hash(source)
        with geometry_lock:
            if key not in geometry_cache:
                folder = storage / "geometry" / key
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, folder / "source.step")
                value = inspect_step(source)
                write_json(folder / "geometry.json", value)
                selection_path = folder / "selection.json"
                if not selection_path.exists():
                    write_json(selection_path, fixture_selection(value).model_dump())
                geometry_cache[key] = value
            return geometry_cache[key]

    def update(run_id: str, **values):
        with lock:
            states[run_id].update(values)
            write_json(runs / run_id / "status.json", states[run_id])

    def execute(run_id: str, request: RunRequest, selection: BoundarySelection | None):
        folder = runs / run_id
        event = cancellations[run_id]
        try:
            if event.is_set():
                raise RunCancelled("Cancelled before execution.")
            update(run_id, status="running", stage="Preparing worker")

            def progress(stage):
                update(run_id, stage=stage)

            if request.kind == "reference":
                result = reference(folder, cancel=event, progress=progress)
            else:
                geometry = fixture()
                result = mesh_spike(
                    folder,
                    geometry,
                    selection,
                    storage / "geometry" / geometry["geometry_hash"] / "source.step",
                    event,
                    progress,
                )
            update(run_id, status=result["status"], stage="Evidence ready", result=result)
        except RunCancelled as e:
            update(run_id, status="cancelled", stage=str(e))
        except Exception as e:
            update(run_id, status="failed", stage="Execution stopped", error=str(e))
        finally:
            update(run_id, finished_at=datetime.now(UTC).isoformat())

    @app.get("/v1/health")
    def health():
        return {"protocol_version": PROTOCOL_VERSION, "status": "ready"}

    @app.get("/v1/diagnostics", dependencies=[Depends(auth)])
    def get_diagnostics():
        return diagnostics()

    @app.get("/v1/geometry", dependencies=[Depends(auth)])
    def get_geometry():
        g = fixture()
        selection = json.loads(
            (storage / "geometry" / g["geometry_hash"] / "selection.json").read_text()
        )
        return {**g, "selection": selection}

    @app.post("/v1/selection", dependencies=[Depends(auth)])
    def save_selection(selection: BoundarySelection):
        g = fixture()
        try:
            validate_selection(g, selection)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        with geometry_lock:
            write_json(
                storage / "geometry" / g["geometry_hash"] / "selection.json", selection.model_dump()
            )
        return selection

    @app.get("/v1/runs", dependencies=[Depends(auth)])
    def list_runs():
        with lock:
            return sorted(
                states.values(), key=lambda state: state.get("created_at", ""), reverse=True
            )

    @app.post("/v1/runs", dependencies=[Depends(auth)], status_code=202)
    def start_run(request: RunRequest):
        with lock:
            if request.request_id in requests:
                previous, run_id = requests[request.request_id]
                if previous != request.model_dump():
                    raise HTTPException(409, "This request ID already belongs to different inputs.")
                return states[run_id]
            if any(s["status"] in {"queued", "running"} for s in states.values()):
                raise HTTPException(
                    409, "This worker already has an active run. Wait or cancel it."
                )
            if not diagnostics()["solver_ready"]:
                raise HTTPException(
                    503, "Simulation runtime is unavailable; check worker diagnostics."
                )
            selection = None
            if request.kind == "cad_mesh":
                g = fixture()
                if request.geometry_hash != g["geometry_hash"]:
                    raise HTTPException(
                        409, "Geometry changed; reload and confirm boundary selections."
                    )
                with geometry_lock:
                    selection = BoundarySelection.model_validate_json(
                        (storage / "geometry" / g["geometry_hash"] / "selection.json").read_text()
                    )
                validate_selection(g, selection)
            run_id = uuid.uuid4().hex
            states[run_id] = {
                "id": run_id,
                "kind": request.kind,
                "status": "queued",
                "stage": "Queued",
                "created_at": datetime.now(UTC).isoformat(),
                "request": request.model_dump(),
            }
            requests[request.request_id] = (request.model_dump(), run_id)
            cancellations[run_id] = threading.Event()
            update(run_id)
            executor.submit(execute, run_id, request, selection)
            return dict(states[run_id])

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str):
        with lock:
            if run_id not in states:
                raise HTTPException(404, "Run not found.")
            return dict(states[run_id])

    @app.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel_run(run_id: str):
        with lock:
            if run_id not in cancellations or states[run_id]["status"] not in {"queued", "running"}:
                raise HTTPException(409, "This run is no longer active.")
            cancellations[run_id].set()
            return {"status": "cancellation_requested"}

    @app.get("/v1/runs/{run_id}/files/{filename:path}", dependencies=[Depends(auth)])
    def get_file(run_id: str, filename: str):
        if run_id not in states:
            raise HTTPException(404, "Run not found.")
        folder = runs / run_id
        target = (folder / filename).resolve()
        if not target.is_relative_to(folder.resolve()) or not target.is_file():
            raise HTTPException(404, "Artifact not found.")
        return FileResponse(target, filename=target.name)

    @app.get("/v1/runs/{run_id}/export", dependencies=[Depends(auth)])
    def export_run(run_id: str):
        with lock:
            if run_id not in states:
                raise HTTPException(404, "Run not found.")
            if states[run_id]["status"] in {"queued", "running"}:
                raise HTTPException(409, "Wait for the run to finish before exporting.")
            exports = storage / "exports"
            exports.mkdir(exist_ok=True)
            archive = exports / f"{run_id}.zip"
            if not archive.exists():
                shutil.make_archive(
                    str(archive.with_suffix("")), "zip", root_dir=runs, base_dir=run_id
                )
            return FileResponse(archive, filename=f"venturi-{run_id[:8]}.zip")

    return app


def serve(storage: Path, port: int = 8765):
    import uvicorn

    token = os.environ.get("VENTURI_API_TOKEN") or secrets.token_urlsafe(32)
    if not os.environ.get("VENTURI_API_TOKEN"):
        print("Worker token (paste into the workbench connection):", token, flush=True)
    uvicorn.run(create_app(storage, token), host="127.0.0.1", port=port, log_level="info")
