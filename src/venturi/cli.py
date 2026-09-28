"""CLI for typed studies, durable jobs, verification and portable reproduction."""

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import export_bundle, unpack_bundle, verify
from .geometry import fixture_selection, import_geometry, inspect_step
from .jobs import (
    ACTIVE,
    RunManager,
    cancel_attempt,
    file_lock,
    initialize_attempt,
    launch_attempt,
    read_json,
    reconcile,
    wait_attempt,
)
from .models import (
    BoundarySelection,
    InternalFlowStudy,
    InternalMeshSpec,
    RunRequest,
    StudySpec,
    parse_study,
    write_json,
)
from .recipes import freeze_study
from .runtime import diagnostics


def read_study(path: Path | None) -> StudySpec | InternalFlowStudy:
    return parse_study(json.loads(path.read_text(encoding="utf-8"))) if path else StudySpec()


def direct_attempt(
    folder: Path,
    study: StudySpec | InternalFlowStudy,
    source: Path | None = None,
    cad: dict | None = None,
) -> dict:
    if isinstance(study, InternalFlowStudy):
        if source is None:
            raise ValueError("Use mesh and solve for a prepared STEP study.")
        original = RunRequest.model_validate(read_json(source / "request.json"))
        request = RunRequest(
            kind="internal_flow",
            request_id=uuid.uuid4().hex,
            study=study,
            mesh_run_id=original.mesh_run_id,
            approved_mesh_hash=original.approved_mesh_hash,
            reason="Explicit native reproduction",
        )
        cad = {
            "source": source / "source.step",
            "geometry": read_json(source / "geometry.json"),
            "selection": study.selection.model_dump(),
            "approved_mesh": source / "approved-mesh",
        }
    else:
        request = RunRequest(
            kind="cad_mesh" if cad else "reference",
            request_id=uuid.uuid4().hex,
            study=None if cad else study,
            geometry_hash=cad["geometry"]["geometry_hash"] if cad else None,
            reason="Explicit native reproduction" if source else "User requested execution",
        )
    initialize_attempt(folder, request, cad)
    if source:
        shutil.copytree(
            source, folder / "replay-source", ignore=shutil.ignore_patterns(".run.lock", ".cancel")
        )
    return launch_attempt(folder)


def reproduce(source: Path, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="venturi-replay-") as temporary:
        original = (
            unpack_bundle(source, Path(temporary) / "unpacked")
            if source.is_file()
            else source.resolve()
        )
        verify(original)
        if output.resolve().is_relative_to(original.resolve()):
            raise ValueError("Reproduction output must be outside the source run.")
        result = read_json(original / "result.json")
        if (
            result.get("status") != "passed"
            or result.get("recipe") not in {"laminar-pipe/1", "laminar-internal/1"}
            or result.get("workflow") == "internal_mesh"
        ):
            raise ValueError("Reproduction requires a passed pipe or internal-flow result export.")
        study = read_study(original / "study.json")
        if result.get("study_hash") != freeze_study(study)["study_hash"]:
            raise ValueError("Exported result does not match its frozen study.")
        direct_attempt(output, study, source=original)
    return wait_attempt(output, lambda s: print(s, flush=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Venturi: reproducible local CFD reference workflows."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check CAD and the pinned solver runtime")
    p = sub.add_parser("study-template", help="Write a complete versioned reference study")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser(
        "validate", help="Validate a study and display its immutable recipe/input hashes"
    )
    p.add_argument("study", type=Path)
    p.add_argument("--step", type=Path, help="Also validate the study against these STEP bytes")
    p = sub.add_parser(
        "import-step", help="Import and preserve a prepared fluid-volume STEP revision"
    )
    p.add_argument("step", type=Path)
    p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
    p = sub.add_parser(
        "flow-template",
        help="Write a STEP flow study with explicitly selected ports (face ID or 1-based index)",
    )
    p.add_argument("step", type=Path)
    p.add_argument("--inlet", required=True)
    p.add_argument("--outlet", action="append", required=True)
    p.add_argument("--flow-rate", type=float, required=True, help="Volumetric inlet flow, m³/s")
    p.add_argument("--cell-size", type=float, required=True, help="Background cell size, m")
    p.add_argument("--density", type=float, default=1000)
    p.add_argument("--viscosity", type=float, default=0.001)
    p.add_argument("--output", type=Path, required=True)
    for name in ("mesh", "solve"):
        p = sub.add_parser(
            name,
            help="Build a STEP mesh"
            if name == "mesh"
            else "Approve an exact mesh and solve its frozen study",
        )
        if name == "mesh":
            p.add_argument("study", type=Path)
            p.add_argument("--step", type=Path, help="Import this STEP before submission")
        else:
            p.add_argument("mesh_run_id")
            p.add_argument(
                "--approve-mesh", required=True, help="Exact mesh hash displayed by the mesh result"
            )
        p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
        p.add_argument("--request-id")
        p.add_argument("--detach", action="store_true")
    p = sub.add_parser("run", help="Submit a study to the persistent local run store")
    p.add_argument("study", type=Path)
    p.add_argument("--request-id", default=None)
    p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
    p.add_argument("--detach", action="store_true")
    for name in ("status", "cancel", "retry"):
        p = sub.add_parser(name)
        p.add_argument("run_id", nargs="?" if name == "status" else None)
        p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
        if name == "retry":
            p.add_argument("--reason", required=True)
            p.add_argument("--request-id", default=None)
            p.add_argument("--detach", action="store_true")
    for name in ("reference", "mesh-spike"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path)
        if name == "reference":
            p.add_argument("--study", type=Path)
        p.add_argument("--detach", action="store_true")
    p = sub.add_parser("export", help="Export a completed attempt directory")
    p.add_argument("run", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify", help="Check a run directory or ZIP against its artifact manifest")
    p.add_argument("source", type=Path)
    p = sub.add_parser(
        "reproduce", help="Verify and rerun an exported native reference in a fresh directory"
    )
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("inspect")
    p.add_argument("step", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("serve")
    p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
    p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import serve

            serve(args.storage, args.port)
        elif args.command == "doctor":
            value = diagnostics()
            print(json.dumps(value, indent=2))
            if any(c["status"] == "fail" for c in value["checks"]):
                sys.exit(1)
        elif args.command == "study-template":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(StudySpec().model_dump_json(indent=2) + "\n")
            print(args.output.resolve())
        elif args.command == "validate":
            study = read_study(args.study)
            if args.step:
                from .internal import study_geometry

                if not isinstance(study, InternalFlowStudy):
                    raise ValueError("--step applies to an internal-flow study.")
                study_geometry(args.step, study)
            frozen = freeze_study(study)
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "study_hash": frozen["study_hash"],
                        "recipe_hash": frozen["recipe_hash"],
                    },
                    indent=2,
                )
            )
        elif args.command == "import-step":
            value = import_geometry(args.step, args.storage)
            print(
                json.dumps(
                    {k: value[k] for k in ("geometry_hash", "source_name", "units", "bounds_m")}
                    | {
                        "faces": [
                            {k: f[k] for k in ("id", "index", "area_m2", "centroid_m", "normal")}
                            for f in value["faces"]
                        ]
                    },
                    indent=2,
                )
            )
        elif args.command == "flow-template":
            from .internal import study_geometry

            geometry = inspect_step(args.step)

            def face_id(value):
                for face in geometry["faces"]:
                    if value in {face["id"], str(face["index"] + 1)}:
                        return face["id"]
                raise ValueError(f"Unknown face {value}; inspect the STEP before selecting ports.")

            inlet = face_id(args.inlet)
            outlets = [face_id(v) for v in args.outlet]
            if inlet in outlets or len(set(outlets)) != len(outlets):
                raise ValueError("Port selections must be distinct.")
            assignments = {f["id"]: "wall" for f in geometry["faces"]}
            assignments.update({inlet: "inlet", **{n: "outlet" for n in outlets}})
            study = InternalFlowStudy(
                selection=BoundarySelection(
                    geometry_hash=geometry["geometry_hash"], assignments=assignments
                ),
                flow_rate_m3_s=args.flow_rate,
                density_kg_m3=args.density,
                dynamic_viscosity_pa_s=args.viscosity,
                mesh=InternalMeshSpec(cell_size_m=args.cell_size),
            )
            study_geometry(args.step, study)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(study.model_dump_json(indent=2) + "\n")
            print(args.output.resolve())
        elif args.command in {"mesh", "solve"}:
            manager = RunManager(args.storage)
            if args.command == "mesh":
                study = read_study(args.study)
                if args.step:
                    imported = import_geometry(args.step, args.storage)
                    if (
                        not isinstance(study, InternalFlowStudy)
                        or imported["geometry_hash"] != study.selection.geometry_hash
                    ):
                        raise ValueError("Imported geometry does not match the study.")
                request = RunRequest(
                    kind="internal_mesh",
                    study=study,
                    request_id=args.request_id or uuid.uuid4().hex,
                )
            else:
                study = read_study(manager.folder(args.mesh_run_id) / "study.json")
                request = RunRequest(
                    kind="internal_flow",
                    study=study,
                    mesh_run_id=args.mesh_run_id,
                    approved_mesh_hash=args.approve_mesh,
                    request_id=args.request_id or uuid.uuid4().hex,
                    reason="User approved the displayed mesh and study for solving",
                )
            state = manager.submit(request)
            folder = manager.folder(state["id"])
            print(f"Attempt {state['id']}: {folder}", flush=True)
            if not args.detach:
                state = wait_attempt(folder, lambda s: print(s, flush=True))
                print(f"{state['status'].upper()}: {folder / 'report.html'}")
                if state["status"] != "passed":
                    sys.exit(1)
                if args.command == "mesh":
                    print(
                        f"Review mesh-preview.json, mesh-audit.json and report.html. Mesh hash: {state['result']['mesh_hash']}"
                    )
        elif args.command == "inspect":
            value = inspect_step(args.step)
            write_json(args.output, value)
            print(
                f"Inspected {len(value['faces'])} faces; geometry {value['geometry_hash'][:12]}; units: metres."
            )
        elif args.command in {"verify", "export", "reproduce"}:
            if args.command == "verify":
                with tempfile.TemporaryDirectory(prefix="venturi-verify-") as temporary:
                    folder = (
                        unpack_bundle(args.source, Path(temporary) / "unpacked")
                        if args.source.is_file()
                        else args.source
                    )
                    manifest = verify(folder)
                    print(
                        f"VERIFIED: {len(manifest['files'])} files; hashes check integrity, not publisher identity."
                    )
            elif args.command == "export":
                if reconcile(args.run)["status"] in ACTIVE:
                    raise ValueError("Wait for execution to finish before exporting.")
                with file_lock(args.run / ".run.lock"):
                    print(export_bundle(args.run, args.output))
            else:
                state = reproduce(args.source, args.output)
                print(f"{state['status'].upper()}: {args.output.resolve() / 'report.html'}")
                if state["status"] != "passed":
                    sys.exit(1)
        elif args.command in {"run", "status", "cancel", "retry"}:
            manager = RunManager(args.storage)
            if args.command == "status":
                value = reconcile(manager.folder(args.run_id)) if args.run_id else manager.list()
                print(json.dumps(value, indent=2))
                return
            if args.command == "cancel":
                cancel_attempt(manager.folder(args.run_id))
                print("Cancellation requested; partial evidence will be retained.")
                return
            if args.command == "run":
                request = RunRequest(
                    kind="reference",
                    request_id=args.request_id or uuid.uuid4().hex,
                    study=read_study(args.study),
                )
            else:
                original = RunRequest.model_validate(
                    read_json(manager.folder(args.run_id) / "request.json")
                )
                request = RunRequest.model_validate(
                    {
                        **original.model_dump(),
                        "request_id": args.request_id or uuid.uuid4().hex,
                        "retry_of": args.run_id,
                        "reason": args.reason,
                    }
                )
            state = manager.submit(request)
            folder = manager.folder(state["id"])
            print(f"Attempt {state['id']}: {folder}", flush=True)
            if not args.detach:
                state = wait_attempt(folder, lambda s: print(s, flush=True))
                print(f"{state['status'].upper()}: {folder / 'report.html'}")
                if state["status"] != "passed":
                    sys.exit(1)
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            folder = (args.output or Path("artifacts") / f"{args.command}-{stamp}").resolve()
            cad = None
            if args.command == "mesh-spike":
                source = Path(__file__).resolve().parents[2] / "fixtures/pipe.step"
                geometry = inspect_step(source)
                cad = {
                    "source": source,
                    "geometry": geometry,
                    "selection": fixture_selection(geometry).model_dump(),
                }
            direct_attempt(folder, read_study(getattr(args, "study", None)), cad=cad)
            print(f"Attempt directory: {folder}", flush=True)
            if not args.detach:
                state = wait_attempt(folder, lambda s: print(s, flush=True))
                print(f"{state['status'].upper()}: {folder / 'report.html'}")
                if state["status"] != "passed":
                    sys.exit(1)
    except (ValueError, RuntimeError, TimeoutError, OSError, KeyError) as exc:
        print(f"Venturi stopped: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
