"""Authenticated loopback API over persistent, independent attempt processes."""

import hmac
import os
import secrets
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import PROTOCOL_VERSION, __version__
from .artifacts import export_bundle, seal, verify
from .geometry import fixture_selection, import_geometry, inspect_step, validate_selection
from .installation import data_root
from .jobs import ACTIVE, RunManager, cancel_attempt, file_lock, read_json, reconcile
from .models import (
    BoundarySelection,
    InternalFlowStudy,
    RANSFlowStudy,
    RunRequest,
    StudySpec,
    file_hash,
    write_json,
)
from .recipes import internal_recipe, pipe_recipe
from .runtime import diagnostics
from .workspace import (
    AnnotationSave,
    StudySave,
    artifact_diff,
    artifact_inventory,
    artifact_text,
    geometry_record,
    notes_record,
    plan_study,
    save_notes,
    save_study,
    study_record,
)

ROOT = data_root()


def create_app(storage: Path, token: str) -> FastAPI:
    if not token:
        raise ValueError("A worker authentication token is required.")
    manager = RunManager(storage)
    storage = manager.storage
    app = FastAPI(title="Venturi worker", version=__version__)
    app.state.manager = manager
    from .support import register as register_support

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Validation diagnostics omit submitted values, including credentials in an
        # otherwise malformed connection object. Field paths still identify the error.
        return JSONResponse(
            status_code=422,
            content={"detail": [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]},
        )

    origins = [
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ]
    if origin := os.environ.get("VENTURI_UI_ORIGIN"):
        origins.append(origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def auth(request: Request):
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(401, "Worker token is missing or incorrect.")

    register_support(app, storage, auth)

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

    def run_folder(run_id: str) -> Path:
        try:
            return manager.folder(run_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(404, "Run not found.") from exc

    def workspace_call(operation):
        try:
            return operation()
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/workspace", dependencies=[Depends(auth)])
    def workspace():
        studies = sorted(
            [read_json(p) for p in (storage / "studies").glob("*/current.json")],
            key=lambda s: s["updated_at"],
            reverse=True,
        )
        active = (
            read_json(storage / "active-study.json")
            if (storage / "active-study.json").exists()
            else None
        )
        return {"studies": studies, "active_id": active.get("id") if active else None}

    @app.post("/v1/studies/plan", dependencies=[Depends(auth)])
    def study_plan(study: RANSFlowStudy | InternalFlowStudy):
        return workspace_call(lambda: plan_study(storage, study))

    @app.post("/v1/studies", dependencies=[Depends(auth)])
    def save_desktop_study(request: StudySave):
        return workspace_call(lambda: save_study(storage, request))

    @app.post("/v1/studies/{study_id}/open", dependencies=[Depends(auth)])
    def open_study(study_id: str):
        def load():
            with geometry_lock:
                record = study_record(storage, study_id)
                key = record["study"]["selection"]["geometry_hash"]
                g = geometry_record(storage, key)
                g["selection"] = record["study"]["selection"]
                g["imported"] = True
                write_json(storage / "geometry" / key / "selection.json", g["selection"])
                write_json(
                    storage / "active-geometry.json", {"geometry_hash": key, "imported": True}
                )
                write_json(storage / "active-study.json", {"id": study_id})
                return {"record": record, "geometry": g}

        return workspace_call(load)

    @app.get("/v1/geometry/{geometry_hash}/annotations", dependencies=[Depends(auth)])
    def get_annotations(geometry_hash: str):
        return workspace_call(lambda: notes_record(storage, geometry_hash))

    @app.post("/v1/annotations", dependencies=[Depends(auth)])
    def set_annotations(request: AnnotationSave):
        return workspace_call(lambda: save_notes(storage, request))

    @app.get("/v1/session", dependencies=[Depends(auth)])
    def session():
        return {"protocol_version": PROTOCOL_VERSION, "app_version": __version__, "status": "ready"}

    @app.get("/v1/health")
    def health():
        return {"protocol_version": PROTOCOL_VERSION, "status": "ready"}

    @app.get("/v1/diagnostics", dependencies=[Depends(auth)])
    def get_diagnostics():
        return diagnostics()

    @app.get("/v1/studies/schema", dependencies=[Depends(auth)])
    def study_schema():
        return StudySpec.model_json_schema()

    @app.get("/v1/studies/template", dependencies=[Depends(auth)])
    def study_template():
        return StudySpec().model_dump()

    @app.get("/v1/recipes/laminar-pipe/1", dependencies=[Depends(auth)])
    def recipe():
        return pipe_recipe()

    @app.get("/v1/geometry", dependencies=[Depends(auth)])
    def get_geometry():
        active = storage / "active-geometry.json"
        if active.exists():
            revision = read_json(active)
            key = revision["geometry_hash"]
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise HTTPException(409, "Invalid saved geometry revision.")
            folder = storage / "geometry" / key
            if not (folder / "source.step").is_file() or file_hash(folder / "source.step") != key:
                raise HTTPException(409, "Stored geometry changed; import and confirm it again.")
            g = workspace_call(lambda: geometry_record(storage, key))
            g["imported"] = revision.get("imported", bool(g.get("imported")))
        else:
            g = fixture()
        selection = read_json(storage / "geometry" / g["geometry_hash"] / "selection.json")
        return {**g, "selection": selection}

    @app.get("/v1/studies/internal/schema", dependencies=[Depends(auth)])
    def internal_schema():
        return InternalFlowStudy.model_json_schema()

    @app.get("/v1/recipes/laminar-internal/1", dependencies=[Depends(auth)])
    def get_internal_recipe():
        return internal_recipe()

    @app.post("/v1/geometry", dependencies=[Depends(auth)], status_code=201)
    async def upload_geometry(request: Request, filename: str = "import.step"):
        total = 0
        with tempfile.TemporaryDirectory(prefix="venturi-import-") as temp:
            source = Path(temp) / "source.step"
            with source.open("wb") as stream:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > 16 * 1024 * 1024:
                        raise HTTPException(413, "STEP exceeds the 16 MiB import limit.")
                    stream.write(chunk)

            def load():
                with geometry_lock:
                    return import_geometry(source, storage, name=filename)

            try:
                return await run_in_threadpool(load)
            except (ValueError, RuntimeError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/geometry/fixture", dependencies=[Depends(auth)])
    def select_fixture():
        g = fixture()
        write_json(
            storage / "active-geometry.json",
            {"geometry_hash": g["geometry_hash"], "imported": False},
        )
        return get_geometry()

    @app.post("/v1/selection", dependencies=[Depends(auth)])
    def save_selection(selection: BoundarySelection):
        g = get_geometry()
        try:
            validate_selection(g, selection, internal=bool(g.get("imported")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with geometry_lock:
            write_json(
                storage / "geometry" / g["geometry_hash"] / "selection.json", selection.model_dump()
            )
        return selection

    @app.get("/v1/runs", dependencies=[Depends(auth)])
    def list_runs():
        return manager.list()

    @app.post("/v1/runs", dependencies=[Depends(auth)], status_code=202)
    def start_run(request: RunRequest):
        cad = None
        if request.kind == "cad_mesh" and not request.retry_of:
            g = fixture()
            if request.geometry_hash != g["geometry_hash"]:
                raise HTTPException(
                    409, "Geometry changed; reload and confirm boundary selections."
                )
            with geometry_lock:
                selection = BoundarySelection.model_validate(
                    read_json(storage / "geometry" / g["geometry_hash"] / "selection.json")
                )
            validate_selection(g, selection)
            cad = {
                "geometry": g,
                "selection": selection.model_dump(),
                "source": storage / "geometry" / g["geometry_hash"] / "source.step",
            }
        try:
            return manager.submit(request, cad)
        except KeyError as exc:
            raise HTTPException(404, "Parent run not found.") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str):
        return reconcile(run_folder(run_id))

    @app.get("/v1/runs/{run_id}/events", dependencies=[Depends(auth)])
    def get_events(run_id: str):
        return [read_json(p) for p in sorted((run_folder(run_id) / "events").glob("*.json"))]

    @app.get("/v1/runs/{run_id}/artifacts", dependencies=[Depends(auth)])
    def list_artifacts(run_id: str):
        return artifact_inventory(run_folder(run_id))

    @app.get("/v1/runs/{run_id}/text", dependencies=[Depends(auth)])
    def view_text(run_id: str, path: str):
        return workspace_call(lambda: artifact_text(run_folder(run_id), path))

    @app.get("/v1/runs/{run_id}/diff", dependencies=[Depends(auth)])
    def view_diff(run_id: str, other: str, path: str):
        return workspace_call(lambda: artifact_diff(run_folder(run_id), run_folder(other), path))

    @app.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel_run(run_id: str):
        try:
            cancel_attempt(run_folder(run_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "cancellation_requested"}

    @app.get("/v1/runs/{run_id}/files/{filename:path}", dependencies=[Depends(auth)])
    def get_file(run_id: str, filename: str):
        folder = run_folder(run_id)
        target = (folder / filename).resolve()
        if not target.is_relative_to(folder.resolve()) or not target.is_file():
            raise HTTPException(404, "Artifact not found.")
        return FileResponse(target, filename=target.name)

    @app.get("/v1/runs/{run_id}/export", dependencies=[Depends(auth)])
    def export_run(run_id: str):
        folder = run_folder(run_id)
        if reconcile(folder)["status"] in ACTIVE:
            raise HTTPException(409, "Wait for the run to finish before exporting.")
        try:
            with file_lock(folder / ".run.lock"):
                # Explicit compatibility path for terminal M0 reports.
                if not (folder / "manifest.json").exists():
                    if (
                        read_json(folder / "status.json").get("schema_version")
                        == "venturi.attempt.v1"
                    ):
                        raise ValueError(
                            "Run finalization is incomplete; no verified export is available."
                        )
                    seal(folder)
                verify(folder)
                archive = storage / "exports" / f"{run_id}.zip"
                if not archive.exists():
                    export_bundle(folder, archive)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return FileResponse(archive, filename=f"venturi-{run_id[:8]}.zip")

    from .assistance_api import register

    register(app, storage, manager, auth)
    return app


def serve(storage: Path, port: int = 8765):
    import uvicorn

    token = os.environ.get("VENTURI_API_TOKEN") or secrets.token_urlsafe(32)
    if not os.environ.get("VENTURI_API_TOKEN"):
        print("Worker token (paste into the workbench connection):", token, flush=True)
    uvicorn.run(create_app(storage, token), host="127.0.0.1", port=port, log_level="info")
