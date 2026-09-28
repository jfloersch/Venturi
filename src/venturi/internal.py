"""Prepared STEP laminar recipe: classified geometry, mesh approval, solve and evidence."""

import json
import math
import re
from pathlib import Path

import numpy as np

from . import foam
from .artifacts import verify
from .evidence import check, field_values, mesh_quality, read_series, validate_field, write_report
from .geometry import export_surfaces, inspect_step, interior_point, validate_selection
from .models import InternalFlowStudy, canonical_hash, file_hash, write_json
from .recipes import freeze_study, internal_recipe, source_hashes
from .runtime import check_execution, find_foam_root, foam_environment, run_tool
from .workflows import environment_snapshot, native_inputs


def study_geometry(source: Path, study: InternalFlowStudy) -> dict:
    if file_hash(source) != study.selection.geometry_hash:
        raise ValueError("Source STEP differs from the confirmed geometry revision.")
    g = inspect_step(source)
    validate_selection(g, study.selection, internal=True)
    applicability = internal_recipe()["applicability"]
    port_reynolds = {}
    for face in g["faces"]:
        if study.selection.assignments[face["id"]] == "wall":
            continue
        diameter = 4 * face["area_m2"] / face["perimeter_m"]
        reynolds = (
            study.density_kg_m3
            * study.flow_rate_m3_s
            * diameter
            / (study.dynamic_viscosity_pa_s * face["area_m2"])
        )
        if (
            not math.isfinite(reynolds)
            or reynolds <= 0
            or reynolds > applicability["maximum_port_reynolds_full_flow"]
        ):
            raise ValueError(
                "Internal flow requires full-flow Reynolds <=200 at every port; change the study or use a qualified different method."
            )
        if (
            diameter / study.mesh.cell_size_m
            < applicability["minimum_cells_per_port_diameter"] - 1e-8
        ):
            raise ValueError(
                "Mesh requires at least 10 cells per port hydraulic diameter; choose a smaller cell size."
            )
        port_reynolds[face["id"]] = reynolds
    g["port_reynolds_full_flow"] = port_reynolds
    return g


def mesh_hash(case: Path) -> str:
    paths = sorted((case / "constant/polyMesh").glob("*"))
    return canonical_hash({p.name: file_hash(p) for p in paths if p.is_file()})


def mesh_preview(case: Path, mapping: dict, geometry: dict) -> dict:
    mesh = case / "constant/polyMesh"
    points = [
        float(n)
        for v in re.findall(r"\(([^()]+)\)", foam._body(mesh / "points"))
        for n in v.split()
    ]
    faces = [
        [int(n) for n in v.split()]
        for v in re.findall(r"\d+\(([^()]+)\)", foam._body(mesh / "faces"))
    ]
    patches = []
    for name, body in re.findall(r"(\w+)\s*\{([^}]+)\}", foam._body(mesh / "boundary")):
        count, start = (
            int(re.search(r"nFaces\s+(\d+)", body)[1]),
            int(re.search(r"startFace\s+(\d+)", body)[1]),
        )
        if count == 0:
            continue
        cells = []
        for face in faces[start : start + count]:
            # VTK accepts polygon cells; these are the actual boundary faces.
            cells.extend([len(face), *face])
        patches.append(
            {
                "id": name,
                "index": len(patches),
                "area_m2": mapping[name]["mesh_area_m2"],
                "centroid_m": mapping[name]["mesh_centroid_m"],
                "surface_type": "mesh patch",
                "cells": cells,
            }
        )
    return {
        "geometry_hash": mesh_hash(case),
        "units": "m",
        "source_units": ["m"],
        "bounds_m": geometry["bounds_m"],
        "volume_m3": geometry["volume_m3"],
        "points": points,
        "faces": patches,
        "selection": {
            "geometry_hash": mesh_hash(case),
            "assignments": {
                f["id"]: "outlet" if f["id"].startswith("outlet_") else f["id"] for f in patches
            },
        },
    }


def build_mesh(folder: Path, geometry: dict, study: InternalFlowStudy, progress, cancel) -> dict:
    case = folder / "case"
    if case.exists():
        raise ValueError("Case already exists; choose a new attempt.")
    h = study.mesh.cell_size_m
    low = [geometry["bounds_m"][i] - 2 * h for i in range(3)]
    inside = interior_point(folder / "source.step", geometry, h, low)
    mapping = export_surfaces(geometry, study.selection, case / "constant/geometry", internal=True)
    foam.cad_mesh(
        case,
        geometry,
        cell_size=h,
        inside_point=inside,
        patches=list(mapping),
        maximum_cells=study.mesh.maximum_cells,
        background_limit=800000,
        strict_quality=True,
    )
    write_json(
        folder / "mesh-plan.json",
        {
            "cell_size_m": h,
            "inside_point_m": inside,
            "patches": mapping,
            "maximum_cells": study.mesh.maximum_cells,
        },
    )
    progress("Building background mesh")
    run_tool("blockMesh", case, study.mesh.timeout_seconds, cancel)
    progress("Extracting sharp surface and port edges")
    run_tool("surfaceFeatures", case, study.mesh.timeout_seconds, cancel)
    progress("Meshing the confirmed STEP boundaries")
    run_tool("snappyHexMesh", case, study.mesh.timeout_seconds, cancel, ("-overwrite",))
    progress("Checking mesh quality and CAD correspondence")
    log = run_tool(
        "checkMesh", case, study.mesh.timeout_seconds, cancel, ("-allTopology", "-allGeometry")
    )
    audit = audit_mesh(case, geometry, study, mapping, log)
    write_json(folder / "mesh-audit.json", audit)
    if all(c["status"] == "pass" for c in audit["checks"]):
        write_json(folder / "mesh-preview.json", mesh_preview(case, audit["patches"], geometry))
    return audit


def audit_mesh(
    case: Path, geometry: dict, study: InternalFlowStudy, mapping: dict, log: Path
) -> dict:
    text = log.read_text(encoding="utf-8")
    criteria = internal_recipe()["criteria"]
    cells = re.search(r"^\s*cells:\s+(\d+)\s*$", text, re.M)
    internal_faces = re.search(r"^\s*internal faces:\s+(\d+)\s*$", text, re.M)
    regions = re.search(r"Number of regions:\s+(\d+)", text)
    volume = re.search(r"Total volume\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", text)
    if not cells or not regions or not volume or not internal_faces:
        raise ValueError("Mesh statistics are incomplete; cannot approve this mesh.")
    cells, regions, volume = int(cells[1]), int(regions[1]), float(volume[1])
    error = abs(volume - geometry["volume_m3"]) / geometry["volume_m3"]
    patches = foam.mesh_boundaries(case)
    checks = [
        mesh_quality(log),
        check(
            "Connected fluid mesh",
            regions == 1,
            f"{regions} connected cell regions; exactly one required.",
        ),
        check(
            "Cell budget",
            0 < cells <= study.mesh.maximum_cells,
            f"{cells} cells; limit {study.mesh.maximum_cells}.",
        ),
        check(
            "Enclosed volume",
            math.isfinite(error) and error <= criteria["mesh_volume_relative_error"],
            f"CAD/mesh volume difference {error:.3%}; limit 5%.",
        ),
        check(
            "Boundary completeness",
            {k for k, v in patches.items() if v["count"]} == set(mapping),
            "Every selected port and wall must survive meshing; no exposed background patches.",
        ),
    ]
    details = {}
    extent = max(geometry["bounds_m"][i + 3] - geometry["bounds_m"][i] for i in range(3))
    for name, expected in mapping.items():
        patch = patches.get(name)
        if not patch or not patch["count"]:
            checks.append(check(f"{name} mapping", False, "Expected patch is missing or empty."))
            continue
        faces = [f for f in geometry["faces"] if f["id"] in expected["face_ids"]]
        cad_center = np.average(
            [f["centroid_m"] for f in faces], axis=0, weights=[f["area_m2"] for f in faces]
        )
        center = np.average(patch["centers"], axis=0, weights=patch["areas"])
        displacement = float(np.linalg.norm(center - cad_center))
        area_error = abs(patch["area_m2"] - expected["area_m2"]) / expected["area_m2"]
        aligned = True
        normal_dot = None
        if name != "wall":
            normal = np.average(patch["normals"], axis=0, weights=patch["areas"])
            normal_dot = float(np.dot(normal, faces[0]["normal"]))
            aligned = normal_dot >= criteria["port_normal_dot"]
        checks.append(
            check(
                f"{name} mapping",
                area_error <= criteria["mesh_area_relative_error"]
                and displacement <= extent * criteria["mesh_centroid_extent_fraction"]
                and aligned,
                f"Area error {area_error:.3%}; centroid displacement {displacement:.3g} m; outward-normal dot {normal_dot if normal_dot is not None else 'not applicable'}.",
            )
        )
        details[name] = {
            "face_ids": expected["face_ids"],
            "mesh_faces": patch["count"],
            "mesh_area_m2": patch["area_m2"],
            "cad_area_m2": expected["area_m2"],
            "mesh_centroid_m": center.tolist(),
            "area_relative_error": area_error,
            "centroid_error_m": displacement,
            "normal_dot": normal_dot,
        }
    return {
        "mesh_hash": mesh_hash(case),
        "cells": cells,
        "internal_faces": int(internal_faces[1]),
        "volume_m3": volume,
        "regions": regions,
        "checks": checks,
        "patches": details,
    }


def compile_flow(case: Path, study: InternalFlowStudy) -> None:
    patches = foam.mesh_boundaries(case)
    inlet = patches["inlet"]
    speed = study.flow_rate_m3_s / inlet["area_m2"]
    velocity = -np.array(inlet["normals"]) * speed
    values = "\n".join("(" + " ".join(f"{v:.12g}" for v in u) + ")" for u in velocity)
    ubc, pbc = [], []
    for name in patches:
        if name == "inlet":
            ubc.append(
                f"inlet {{ type fixedValue; value nonuniform List<vector> {len(velocity)} ({values}); }}"
            )
            pbc.append("inlet { type zeroGradient; }")
        elif name.startswith("outlet_"):
            ubc.append(f"{name} {{ type zeroGradient; }}")
            pbc.append(f"{name} {{ type fixedValue; value uniform 0; }}")
        elif name in {"wall", "background"}:
            ubc.append(f"{name} {{ type noSlip; }}")
            pbc.append(f"{name} {{ type zeroGradient; }}")
        else:
            raise ValueError("Unexpected patch in the approved mesh.")
    foam.dictionary(
        case / "0/U",
        f"dimensions [0 1 -1 0 0 0 0]; internalField uniform (0 0 0); boundaryField {{ {' '.join(ubc)} }}",
        "volVectorField",
    )
    foam.dictionary(
        case / "0/p",
        f"dimensions [0 2 -2 0 0 0 0]; internalField uniform 0; boundaryField {{ {' '.join(pbc)} }}",
        "volScalarField",
    )
    foam.dictionary(
        case / "constant/physicalProperties",
        f"viscosityModel constant; nu {study.dynamic_viscosity_pa_s / study.density_kg_m3:.14g};",
    )
    foam.dictionary(
        case / "constant/momentumTransport", "simulationType laminar; laminar { model Stokes; }"
    )
    foam.dictionary(
        case / "system/fvSchemes",
        """
        ddtSchemes { default steadyState; }
        gradSchemes { default Gauss linear; }
        divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear; }
        laplacianSchemes { default Gauss linear corrected; }
        interpolationSchemes { default linear; } snGradSchemes { default corrected; }
        fluxRequired { default no; p; }
    """,
    )
    foam.dictionary(
        case / "system/fvSolution",
        """
        solvers { p { solver GAMG; tolerance 1e-10; relTol 0.01; smoother GaussSeidel; }
                  U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0.01; } }
        SIMPLE { nNonOrthogonalCorrectors 2; consistent yes; }
        relaxationFactors { fields { p 0.3; } equations { U 0.7; } }
    """,
    )
    foam.control(
        case,
        study.max_iterations,
        patches=[p for p in patches if p != "background"],
        magnitude_flux=True,
    )


def flow_evidence(case: Path, study: InternalFlowStudy, audit: dict) -> dict:
    criteria = internal_recipe()["criteria"]
    names = list(audit["patches"])
    series = {
        f"{patch}_{field}": read_series(
            case / "postProcessing" / f"{patch}_{field}" / "0/surfaceFieldValue.dat"
        )
        for patch in names
        for field in ("p", "phi", "phi_mag")
    }
    times = series["inlet_p"][:, 0]
    if any(not np.array_equal(s[:, 0], times) for s in series.values()):
        raise ValueError("Quantity histories do not refer to the same iterations.")
    if times[0] not in {0, 1} or not np.array_equal(
        times, np.arange(times[0], times[0] + len(times))
    ):
        raise ValueError("Missing or non-integral iterations in flow evidence.")
    final = case / f"{times[-1]:g}"
    validate_field(final / "p", "0 2 -2 0 0 0 0", 1, audit["cells"])
    validate_field(final / "U", "0 1 -1 0 0 0 0", 3, audit["cells"])
    validate_field(final / "phi", "0 3 -1 0 0 0 0", 1, audit["internal_faces"])
    flux_text = (final / "phi").read_text(encoding="utf-8")
    tolerance = criteria["field_history_flow_fraction"] * study.flow_rate_m3_s
    for name in names:
        net, magnitude = series[f"{name}_phi"][:, 1], series[f"{name}_phi_mag"][:, 1]
        if np.any(magnitude < 0) or np.any(magnitude + tolerance < np.abs(net)):
            raise ValueError("Inconsistent signed and absolute flux histories.")
        patch = re.search(rf"\b{name}\s*\{{([^}}]+)\}}", flux_text, re.S)
        if not patch:
            raise ValueError("Final flux field is missing a required patch.")
        values = field_values(patch[1], "value", 1, audit["patches"][name]["mesh_faces"])
        if (
            abs(float(values.sum()) - net[-1]) > tolerance
            or abs(float(np.abs(values).sum()) - magnitude[-1]) > tolerance
        ):
            raise ValueError("Final native flux field differs from the reported history.")
    outlets = [n for n in names if n.startswith("outlet_")]
    flows = np.array([series[f"{n}_phi"][:, 1] for n in outlets])
    total = flows.sum(axis=0)
    qi = -series["inlet_phi"][:, 1]
    weights = flows / np.maximum(np.abs(total), 1e-30)
    pressure = study.density_kg_m3 * (
        series["inlet_p"][:, 1]
        - np.sum(weights * np.array([series[f"{n}_p"][:, 1] for n in outlets]), axis=0)
    )
    if not np.all(np.isfinite(pressure)):
        raise ValueError("Non-finite pressure difference.")
    imbalance = np.abs(qi - total) / np.maximum(np.maximum(np.abs(qi), np.abs(total)), 1e-30)
    window = criteria["stability_window"]
    if len(times) < window:
        raise ValueError("Insufficient convergence history.")
    stability = float(np.ptp(pressure[-window:]) / max(abs(pressure[-1]), 1e-30))
    flow_stability = float(max(np.ptp(f[-window:]) / max(abs(f[-1]), 1e-30) for f in flows))
    backflows = {
        n: float(
            max(0, (series[f"{n}_phi_mag"][-1, 1] - series[f"{n}_phi"][-1, 1]) / 2)
            / study.flow_rate_m3_s
        )
        for n in outlets
    }
    leakage = float(series["wall_phi_mag"][-1, 1] / study.flow_rate_m3_s)
    log = (case.parent / "logs/foamRun.log").read_text(encoding="utf-8")
    residuals = {
        field: float(value)
        for field, value in re.findall(r"Solving for (\w+), Initial residual = ([^,\s]+)", log)
    }
    residual_pass = {"p", "Ux", "Uy", "Uz"} <= residuals.keys() and all(
        math.isfinite(residuals[n]) and 0 <= residuals[n] <= criteria["equation_residual"]
        for n in ("p", "Ux", "Uy", "Uz")
    )
    checks = [
        check(
            "Solver completed",
            bool(re.search(r"^End\s*$", log, re.M)) and times[-1] == study.max_iterations,
            f"Recorded iteration {times[-1]:g} of {study.max_iterations}.",
        ),
        check(
            "Prescribed inlet flow",
            qi[-1] > 0
            and abs(qi[-1] - study.flow_rate_m3_s) / study.flow_rate_m3_s
            <= criteria["prescribed_flow_relative_error"],
            f"Measured {qi[-1]:.8g} m³/s; prescribed {study.flow_rate_m3_s:.8g} m³/s.",
        ),
        check(
            "Mass conservation",
            imbalance[-1] <= criteria["mass_imbalance"],
            f"Net flow imbalance {imbalance[-1]:.4%}; limit 0.1%.",
        ),
        check(
            "Pressure-drop stability",
            stability <= criteria["pressure_stability"] and pressure[-1] > 0,
            f"Positive inlet-to-outlet drop; final-window variation {stability:.4%}; limit 0.1%.",
        ),
        check(
            "Outlet flow stability",
            flow_stability <= criteria["flow_stability"],
            f"Largest final-window branch variation {flow_stability:.4%}; limit 0.1%.",
        ),
        check(
            "Equation residuals", residual_pass, f"Final initial residuals {residuals}; limit 1e-5."
        ),
        check(
            "Outlet direction and backflow",
            all(f[-1] > 0 for f in flows)
            and max(backflows.values()) <= criteria["outlet_backflow_fraction"],
            f"Reverse outlet flows / prescribed inlet: {backflows}; limit 0.1%.",
        ),
        check(
            "Impermeable walls",
            0 <= leakage <= criteria["wall_leakage_fraction"],
            f"Absolute wall flux / inlet flow {leakage:.6g}; limit 1e-7.",
        ),
    ]
    branches = {
        n: {
            "face_ids": audit["patches"][n]["face_ids"],
            "volume_flow_m3_s": float(flows[i, -1]),
            "flow_fraction": float(weights[i, -1]),
            "pressure_drop_pa": float(
                study.density_kg_m3 * (series["inlet_p"][-1, 1] - series[f"{n}_p"][-1, 1])
            ),
            "backflow_fraction": backflows[n],
        }
        for i, n in enumerate(outlets)
    }
    return {
        "checks": checks,
        "pressure_drop_pa": float(pressure[-1]),
        "mass_imbalance": float(imbalance[-1]),
        "pressure_stability": stability,
        "flow_stability": flow_stability,
        "iterations": int(times[-1]),
        "volume_flow_in_m3_s": float(qi[-1]),
        "volume_flow_out_m3_s": float(total[-1]),
        "outlets": branches,
        "residuals": {k: v if math.isfinite(v) else None for k, v in residuals.items()},
        "criteria": criteria,
        "history": [
            {
                "iteration": int(t),
                "pressure_drop_pa": float(p),
                "mass_imbalance": float(m),
                "outlet_flows_m3_s": {n: float(flows[i, j]) for i, n in enumerate(outlets)},
            }
            for j, (t, p, m) in enumerate(zip(times, pressure, imbalance, strict=True))
        ],
        "pressure_convention": internal_recipe()["quantity"]["pressure_drop_pa"],
        "quantities": internal_recipe()["quantity"],
        "assessments": {
            "numerical_checks": "assessed",
            "experimental_validation": "not_assessed",
            "mesh_independence": "not_assessed",
            "independent_cfd_review": "pending",
        },
    }


def execute_internal(
    folder: Path,
    study: InternalFlowStudy,
    *,
    solve: bool,
    approved_hash: str | None = None,
    mesh_run_id: str | None = None,
    replay_source: Path | None = None,
    cancel=None,
    progress=lambda _: None,
) -> dict:
    frozen = freeze_study(study)
    study = InternalFlowStudy.model_validate(frozen["study"])
    write_json(folder / "study.json", frozen["study"])
    write_json(folder / "recipe.json", frozen["recipe"])
    geometry = study_geometry(folder / "source.step", study)
    write_json(folder / "geometry.json", geometry)
    write_json(folder / "selection.json", study.selection.model_dump())
    env = environment_snapshot()
    root = find_foam_root()
    if root and env["solver_ready"]:
        build, _ = foam_environment(root)
        env["mesher"] = {
            "snappyHexMesh": file_hash(build / "bin/snappyHexMesh"),
            "surfaceFeatures": file_hash(build / "bin/surfaceFeatures"),
            "configuration": {
                p.relative_to(root).as_posix(): file_hash(p)
                for p in sorted((root / "etc/caseDicts/mesh/generation").rglob("*"))
                if p.is_file()
            },
        }
    write_json(folder / "environment.json", env)
    if not env["solver_ready"]:
        raise ValueError("The pinned OpenFOAM runtime is unavailable.")
    approved = None
    if solve:
        approved = folder / "approved-mesh"
        verify(approved)
        old = json.loads((approved / "result.json").read_text(encoding="utf-8"))
        if (
            old.get("status") != "passed"
            or old.get("workflow") != "internal_mesh"
            or old.get("study_hash") != frozen["study_hash"]
            or old.get("recipe_hash") != frozen["recipe_hash"]
            or old.get("mesh_hash") != approved_hash
        ):
            raise ValueError("Mesh approval does not match this study and passed mesh.")
        if mesh_hash(approved / "case") != approved_hash:
            raise ValueError("Approved mesh contents have changed.")
        previous_environment = json.loads(
            (approved / "environment.json").read_text(encoding="utf-8")
        )
        if any(previous_environment.get(k) != env.get(k) for k in ("runtime", "mesher")):
            raise ValueError("Approved mesh runtime differs; create and approve a new mesh.")
    if replay_source:
        verify(replay_source)
        previous_environment = json.loads(
            (replay_source / "environment.json").read_text(encoding="utf-8")
        )
        if any(previous_environment.get(k) != env.get(k) for k in ("runtime", "mesher")):
            raise ValueError("Reproduction requires the original runtime build and hashes.")
        if (
            json.loads((replay_source / "recipe.json").read_text(encoding="utf-8"))
            != frozen["recipe"]
        ):
            raise ValueError("Reproduction recipe differs from the compiler.")
    audit = build_mesh(folder, geometry, study, progress, cancel)
    checks = audit["checks"][:]
    provenance = {
        "schema_version": "venturi.provenance.v1",
        "study_hash": frozen["study_hash"],
        "recipe_hash": frozen["recipe_hash"],
        "geometry_hash": geometry["geometry_hash"],
        "worker_sources": source_hashes(),
        "mesh_hash": audit["mesh_hash"],
    }
    result = {
        "workflow": "internal_flow" if solve else "internal_mesh",
        "recipe": study.recipe,
        "study_hash": frozen["study_hash"],
        "recipe_hash": frozen["recipe_hash"],
        "geometry_hash": geometry["geometry_hash"],
        "mesh_hash": audit["mesh_hash"],
        "mesh": audit,
        "checks": checks,
        "provenance": provenance,
        "port_reynolds_full_flow": geometry["port_reynolds_full_flow"],
        "limitations": [
            "Provisional steady laminar recipe for prepared fluid volumes; independent CFD review is pending.",
            "Uniform inlet profile and equal zero-gauge outlet pressures; entrance and boundary placement affect pressure loss.",
            "Port Reynolds screening does not certify every internal restriction. Mesh independence and experimental validation are not assessed.",
        ],
    }
    case = folder / "case"
    if solve and all(c["status"] == "pass" for c in checks):
        if audit["mesh_hash"] != approved_hash:
            raise ValueError(
                "Regenerated mesh differs from the approved mesh; review a new mesh before solving."
            )
        write_json(
            folder / "mesh-approval.json",
            {
                "mesh_run_id": mesh_run_id,
                "mesh_hash": approved_hash,
                "study_hash": frozen["study_hash"],
                "geometry_hash": geometry["geometry_hash"],
                "recipe_hash": frozen["recipe_hash"],
            },
        )
        compile_flow(case, study)
        inputs = native_inputs(case)
        write_json(folder / "native-inputs.json", inputs)
        provenance["native_inputs_hash"] = canonical_hash(inputs)
        write_json(folder / "provenance.json", provenance)
        if replay_source:
            original_inputs = json.loads(
                (replay_source / "native-inputs.json").read_text(encoding="utf-8")
            )
            if (
                inputs != original_inputs
                or native_inputs(replay_source / "case") != original_inputs
            ):
                raise ValueError(
                    "Exported native inputs differ from the deterministic compiler; refusing edited dictionaries or mesh."
                )
        progress("Solving laminar flow through the approved STEP mesh")
        run_tool("foamRun", case, study.timeout_seconds, cancel)
        progress("Checking pressure, conservation and outlet flow split")
        evidence = flow_evidence(case, study, audit)
        checks.extend(evidence.pop("checks"))
        result.update(evidence)
        progress("Exporting velocity and pressure fields")
        run_tool("foamToVTK", case, study.mesh.timeout_seconds, cancel, ("-latestTime",))
        checks.append(
            check(
                "Field export",
                any((case / "VTK").rglob("*.vtk")),
                "Native case and VTK fields retained.",
            )
        )
        checks.append(
            check(
                "Frozen native inputs",
                native_inputs(case) == inputs,
                "Approved mesh, dictionaries and initial fields remained unchanged.",
            )
        )
        if replay_source:
            original = json.loads((replay_source / "result.json").read_text(encoding="utf-8"))
            delta = abs(result["pressure_drop_pa"] - original["pressure_drop_pa"]) / max(
                abs(original["pressure_drop_pa"]), 1e-30
            )
            branch_delta = max(
                abs(v["volume_flow_m3_s"] - original["outlets"][n]["volume_flow_m3_s"])
                / max(abs(original["outlets"][n]["volume_flow_m3_s"]), 1e-30)
                for n, v in result["outlets"].items()
            )
            result["reproduction_relative_difference"] = max(delta, branch_delta)
            checks.append(
                check(
                    "Reproduction agreement",
                    max(delta, branch_delta)
                    <= internal_recipe()["criteria"]["reproduction_relative_difference"],
                    f"Largest pressure/branch-flow relative difference {max(delta, branch_delta):.6g}; limit 1e-6.",
                )
            )
    checks.append(
        check(
            "Frozen geometry and study",
            file_hash(folder / "source.step") == geometry["geometry_hash"]
            and canonical_hash(json.loads((folder / "study.json").read_text(encoding="utf-8")))
            == frozen["study_hash"]
            and canonical_hash(json.loads((folder / "recipe.json").read_text(encoding="utf-8")))
            == frozen["recipe_hash"],
            "Source STEP, study and recipe match the executed inputs.",
        )
    )
    write_json(folder / "provenance.json", provenance)
    result["status"] = "passed" if all(c["status"] == "pass" for c in checks) else "failed"
    check_execution(cancel)
    write_report(folder, result, study.model_dump(), env)
    return result
