"""The CLI and desktop use these exact deterministic workflows."""

import importlib.metadata
import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import foam
from .evidence import check, mesh_quality, reference_evidence, write_report
from .geometry import export_surfaces, validate_selection
from .models import BoundarySelection, PipeSpec, StudySpec, canonical_hash, file_hash, write_json
from .recipes import freeze_study, source_hashes
from .runtime import check_execution, diagnostics, find_foam_root, foam_environment, run_tool


def native_inputs(case: Path) -> dict:
    return {
        p.relative_to(case).as_posix(): file_hash(p)
        for part in ("0", "constant", "system")
        for p in sorted((case / part).rglob("*"))
        if p.is_file()
    }


def environment_snapshot() -> dict:
    environment = diagnostics()
    environment["python_dependencies"] = {
        name: importlib.metadata.version(name) for name in ("pydantic", "numpy", "matplotlib")
    }
    root = find_foam_root()
    if root and environment["solver_ready"]:
        build, _ = foam_environment(root)
        paths = (
            list((build / "lib").glob("*.so"))
            + list((build / "lib/dummy").glob("*.so"))
            + [build / "bin" / name for name in ("blockMesh", "checkMesh", "foamRun", "foamToVTK")]
        )
        environment["runtime"]["file_hashes"] = {
            p.relative_to(build).as_posix(): file_hash(p) for p in sorted(paths) if p.is_file()
        }
    return environment


def reference(
    folder: Path,
    spec: PipeSpec | None = None,
    cancel: threading.Event | None = None,
    progress: Callable[[str], None] = lambda _: None,
    *,
    study: StudySpec | None = None,
    replay_source: Path | None = None,
) -> dict:
    frozen = freeze_study(study or StudySpec(pipe=spec or PipeSpec()))
    study = StudySpec.model_validate(frozen["study"])
    spec = study.pipe
    case = folder / "case"
    if case.exists():
        raise ValueError("Run folder already contains a case; choose a new output folder.")
    write_json(folder / "study.json", frozen["study"])
    write_json(folder / "recipe.json", frozen["recipe"])
    progress("Checking study and runtime")
    environment = environment_snapshot()
    write_json(folder / "environment.json", environment)
    provenance = {
        "schema_version": "venturi.provenance.v1",
        "study_hash": frozen["study_hash"],
        "recipe_hash": frozen["recipe_hash"],
        "worker_sources": source_hashes(),
        "assumptions": frozen["recipe"]["boundaries"],
        "native_inputs_hash": None,
    }
    write_json(folder / "provenance.json", provenance)
    if not environment["solver_ready"]:
        raise RuntimeError("Worker diagnostics failed; install the pinned simulation runtime.")
    if replay_source:
        old = json.loads((replay_source / "environment.json").read_text())
        if old["runtime"] != environment["runtime"]:
            raise ValueError(
                "Reproduction requires the original runtime build and executable/library hashes."
            )
        old_recipe = json.loads((replay_source / "recipe.json").read_text())
        if old_recipe != frozen["recipe"]:
            raise ValueError("Reproduction recipe differs from this worker's recipe.")
    check_execution(cancel)
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
    inputs = native_inputs(case)
    write_json(folder / "native-inputs.json", inputs)
    provenance["native_inputs_hash"] = canonical_hash(inputs)
    provenance["mesh_hash"] = canonical_hash(
        {k: v for k, v in inputs.items() if k.startswith("constant/polyMesh/")}
    )
    write_json(folder / "provenance.json", provenance)
    if replay_source:
        old_inputs = json.loads((replay_source / "native-inputs.json").read_text())
        if old_inputs != inputs or native_inputs(replay_source / "case") != old_inputs:
            raise ValueError(
                "Exported native inputs differ from the deterministic compiler; refusing to execute edited dictionaries."
            )
        # Only byte-verified, compiler-generated native inputs can be executed.
        for name in inputs:
            shutil.copyfile(replay_source / "case" / name, case / name)
        write_json(
            folder / "reproduction.json",
            {
                "source_manifest_hash": file_hash(replay_source / "manifest.json"),
                "source_study_hash": frozen["study_hash"],
                "native_inputs_verified": True,
            },
        )
    progress("Solving laminar pipe flow")
    run_tool("foamRun", case, spec.timeout_seconds, cancel)
    progress("Checking numerical evidence")
    result = reference_evidence(case, spec)
    result["study_hash"] = frozen["study_hash"]
    result["recipe_hash"] = frozen["recipe_hash"]
    result["recipe"] = study.recipe
    result["provenance"] = provenance
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
    result["checks"].append(
        check(
            "Frozen native inputs",
            native_inputs(case) == inputs,
            "Generated mesh, initial fields and dictionaries remained unchanged during execution.",
        )
    )
    result["checks"].append(
        check(
            "Frozen study and recipe",
            canonical_hash(json.loads((folder / "study.json").read_text())) == frozen["study_hash"]
            and canonical_hash(json.loads((folder / "recipe.json").read_text()))
            == frozen["recipe_hash"],
            "Saved study and recipe still match the inputs used to compile this attempt.",
        )
    )
    if replay_source:
        original = json.loads((replay_source / "result.json").read_text())
        difference = abs(result["pressure_drop_pa"] - original["pressure_drop_pa"]) / abs(
            original["pressure_drop_pa"]
        )
        tolerance = frozen["recipe"]["criteria"]["reproduction_relative_difference"]
        result["reproduction_relative_difference"] = difference
        result["checks"].append(
            check(
                "Reproduction agreement",
                difference <= tolerance,
                f"Pressure-drop relative difference {difference:.6g}; limit {tolerance:g}.",
            )
        )
    check_execution(cancel)
    result["status"] = (
        "passed" if all(c["status"] == "pass" for c in result["checks"]) else "failed"
    )
    write_report(folder, result, study.model_dump(), environment)
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
    if source.resolve() != (folder / "source.step").resolve():
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
