"""The CLI and desktop use these exact deterministic workflows."""

import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import foam
from .evidence import check, mesh_quality, reference_evidence, write_report
from .geometry import export_surfaces, validate_selection
from .models import BoundarySelection, PipeSpec, file_hash, write_json
from .runtime import diagnostics, run_tool


def reference(
    folder: Path,
    spec: PipeSpec | None = None,
    cancel: threading.Event | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    spec = spec or PipeSpec()
    case = folder / "case"
    if case.exists():
        raise ValueError("Run folder already contains a case; choose a new output folder.")
    write_json(folder / "study.json", spec.model_dump())
    environment = diagnostics()
    if not environment["solver_ready"]:
        raise RuntimeError("Worker diagnostics failed; install the pinned simulation runtime.")
    foam.control(case, spec.max_iterations, functions=False)
    foam.pipe_mesh(case, spec)
    progress("Building reference mesh")
    run_tool("blockMesh", case, 60, cancel)
    quality = mesh_quality(
        run_tool("checkMesh", case, 60, cancel, ("-allTopology", "-allGeometry"))
    )
    if quality["status"] != "pass":
        raise ValueError("Reference mesh failed checkMesh; solver was not launched.")
    foam.compile_solver(case, spec)
    progress("Solving laminar pipe flow")
    run_tool("foamRun", case, spec.timeout_seconds, cancel)
    progress("Checking numerical evidence")
    result = reference_evidence(case, spec)
    result["checks"].insert(0, quality)
    # Stock VTK export tests the post-processing bridge and gives independent viewers data.
    progress("Exporting field data")
    run_tool("foamToVTK", case, 60, cancel, ("-latestTime",))
    result["checks"].append(
        check(
            "Field export",
            any((case / "VTK").rglob("*.vtk")) or any((case / "VTK").rglob("*.vtu")),
            "Native OpenFOAM case and stock VTK field export retained.",
        )
    )
    result["status"] = (
        "passed" if all(c["status"] == "pass" for c in result["checks"]) else "failed"
    )
    write_report(folder, result, spec.model_dump(), environment)
    return result


def mesh_spike(
    folder: Path,
    geometry: dict,
    selection: BoundarySelection,
    source: Path,
    cancel: threading.Event | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    validate_selection(geometry, selection)
    if file_hash(source) != geometry["geometry_hash"]:
        raise ValueError("Source geometry bytes changed; inspect and confirm selections again.")
    case = folder / "case"
    if case.exists():
        raise ValueError("Run folder already contains a case; choose a new output folder.")
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / "source.step")
    write_json(folder / "selection.json", selection.model_dump())
    mapping = export_surfaces(geometry, selection, case / "constant/geometry")
    foam.cad_mesh(case, geometry)
    progress("Building background mesh")
    run_tool("blockMesh", case, 60, cancel)
    progress("Meshing the STEP surfaces")
    run_tool("snappyHexMesh", case, 300, cancel, ("-overwrite",))
    progress("Auditing mesh boundaries against CAD")
    quality = mesh_quality(
        run_tool("checkMesh", case, 60, cancel, ("-allTopology", "-allGeometry"))
    )
    patches = foam.mesh_boundaries(case)
    nonempty = {name for name, patch in patches.items() if patch["count"] > 0}
    checks = [
        quality,
        check(
            "Boundary completeness",
            nonempty == {"inlet", "outlet", "wall"},
            f"Non-empty solver patches: {', '.join(sorted(nonempty))}",
        ),
    ]
    details = {}
    scale = max(geometry["bounds_m"][i + 3] - geometry["bounds_m"][i] for i in range(3))
    for role, expected in mapping.items():
        patch = patches.get(role)
        if patch is None or patch["count"] == 0:
            checks.append(check(f"{role} mapping", False, "Expected patch is missing or empty."))
            continue
        error = abs(patch["area_m2"] - expected["area_m2"]) / expected["area_m2"]
        faces = [f for f in geometry["faces"] if f["id"] in expected["face_ids"]]
        cad_center = np.average(
            [f["centroid_m"] for f in faces], axis=0, weights=[f["area_m2"] for f in faces]
        )
        mesh_center = np.average(patch["centers"], axis=0, weights=patch["areas"])
        distance = float(np.linalg.norm(cad_center - mesh_center))
        checks.append(
            check(
                f"{role} mapping",
                error <= 0.08 and distance <= scale * 0.01,
                f"{patch['count']} mesh faces; CAD area error {error:.3%} (limit 8%); centroid displacement {distance:.3g} m (limit {scale * 0.01:.3g} m).",
            )
        )
        details[role] = {
            "face_ids": expected["face_ids"],
            "mesh_faces": patch["count"],
            "mesh_area_m2": patch["area_m2"],
            "cad_area_m2": expected["area_m2"],
            "centroid_error_m": distance,
        }
    result = {
        "status": "passed" if all(c["status"] == "pass" for c in checks) else "failed",
        "checks": checks,
        "patches": details,
        "geometry_hash": geometry["geometry_hash"],
        "limitations": [
            "CAD meshing spike for the provided convex pipe fixture; no automatic interior-point discovery for arbitrary geometry.",
            "Patch names, areas, and centroids are checked against saved CAD selections. These checks do not establish general topology equivalence.",
            "This geometry check does not run a flow solver.",
        ],
    }
    write_report(folder, result, selection.model_dump(), diagnostics())
    return result
