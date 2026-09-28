#!/usr/bin/env python3
"""Measure prepared-STEP mesh sensitivity and fluid scaling with the real CLI."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from venturi.artifacts import verify
from venturi.jobs import RunManager, read_json
from venturi.models import InternalFlowStudy, write_json

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "bend_baseline": ("bend", 0.0008, 1.0),
    "bend_fine": ("bend", 0.0005, 1.0),
    "bend_double_density_and_viscosity": ("bend", 0.0008, 2.0),
    "manifold_baseline": ("manifold", 0.000625, 1.0),
    "manifold_fine": ("manifold", 0.0004, 1.0),
}
CRITERIA = {
    "all_mesh_and_flow_checks_pass": True,
    "grid_pressure_relative_difference": 0.05,
    "grid_outlet_fraction_absolute_difference": 0.01,
    "same_kinematic_viscosity_pressure_scaling_relative_error": 1e-6,
    "same_kinematic_viscosity_outlet_flow_relative_difference": 1e-6,
}


def execute(name: str, source: Path, study: Path, output: Path) -> dict:
    store = output / "stores" / name
    manager = RunManager(store)
    row = {"case": name, "status": "failed", "study": str(study)}
    with (output / f"{name}.cli.log").open("w", encoding="utf-8") as log:

        def cli(*args):
            completed = subprocess.run(
                [sys.executable, "-m", "venturi.cli", *map(str, args)],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=2500,
            )
            if completed.returncode:
                raise RuntimeError(f"{args[0]} exited {completed.returncode}; see CLI log")

        try:
            cli("mesh", study, "--step", source, "--storage", store)
            mesh = manager.list()[0]
            row["mesh_run"] = str(manager.folder(mesh["id"]))
            assert verify(manager.folder(mesh["id"]))
            cli(
                "solve",
                mesh["id"],
                "--approve-mesh",
                mesh["result"]["mesh_hash"],
                "--storage",
                store,
            )
            flow = next(s for s in manager.list() if s["kind"] == "internal_flow")
            folder = manager.folder(flow["id"])
            result = read_json(folder / "result.json")
            row.update(
                status=result["status"],
                flow_run=str(folder),
                integrity=bool(verify(folder)),
                manifest_sha256=hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(),
                cells=result["mesh"]["cells"],
                pressure_drop_pa=result["pressure_drop_pa"],
                outlets=result["outlets"],
                checks=result["checks"],
                mesh_hash=result["mesh_hash"],
                study_hash=result["study_hash"],
            )
        except (RuntimeError, ValueError, AssertionError, subprocess.TimeoutExpired) as error:
            row["error"] = str(error)
            row["attempts"] = [
                {"id": s["id"], "status": s["status"], "kind": s["kind"]} for s in manager.list()
            ]
    return row


def comparisons(rows: list[dict]) -> list[dict]:
    by_name = {row["case"]: row for row in rows}
    checks = []
    for geometry in ("bend", "manifold"):
        baseline = by_name[f"{geometry}_baseline"]
        fine = by_name[f"{geometry}_fine"]
        if baseline["status"] != "passed" or fine["status"] != "passed":
            checks.append({"name": f"{geometry} mesh sensitivity", "passed": False})
            continue
        pressure = abs(fine["pressure_drop_pa"] / baseline["pressure_drop_pa"] - 1)
        fraction = max(
            abs(fine["outlets"][key]["flow_fraction"] - outlet["flow_fraction"])
            for key, outlet in baseline["outlets"].items()
        )
        checks.append(
            {
                "name": f"{geometry} mesh sensitivity",
                "pressure_relative_difference": pressure,
                "max_outlet_fraction_absolute_difference": fraction,
                "passed": fine["cells"] > baseline["cells"]
                and pressure <= CRITERIA["grid_pressure_relative_difference"]
                and fraction <= CRITERIA["grid_outlet_fraction_absolute_difference"],
            }
        )
    baseline = by_name["bend_baseline"]
    scaled = by_name["bend_double_density_and_viscosity"]
    if baseline["status"] == scaled["status"] == "passed":
        pressure_error = abs(scaled["pressure_drop_pa"] / baseline["pressure_drop_pa"] / 2 - 1)
        flow_error = max(
            abs(scaled["outlets"][key]["volume_flow_m3_s"] / outlet["volume_flow_m3_s"] - 1)
            for key, outlet in baseline["outlets"].items()
        )
        checks.append(
            {
                "name": "same kinematic viscosity fluid scaling",
                "pressure_scaling_relative_error": pressure_error,
                "max_outlet_flow_relative_difference": flow_error,
                "passed": pressure_error
                <= CRITERIA["same_kinematic_viscosity_pressure_scaling_relative_error"]
                and flow_error
                <= CRITERIA["same_kinematic_viscosity_outlet_flow_relative_difference"],
            }
        )
    else:
        checks.append({"name": "same kinematic viscosity fluid scaling", "passed": False})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    studies = {}
    for name, (geometry, cell_size, multiplier) in CASES.items():
        data = read_json(ROOT / "fixtures/internal" / f"{geometry}.study.json")
        data["name"] = f"Milestone 1 closeout: {name}"
        data["mesh"]["cell_size_m"] = cell_size
        data["density_kg_m3"] *= multiplier
        data["dynamic_viscosity_pa_s"] *= multiplier
        study = InternalFlowStudy.model_validate(data)
        studies[name] = study.model_dump()
        write_json(output / "studies" / f"{name}.json", studies[name])
    write_json(
        output / "plan.json",
        {
            "criteria": CRITERIA,
            "studies": studies,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": "Two-level mesh sensitivity and invariant-nu scaling regression; "
            "not formal grid convergence, experimental validation or independent CFD review.",
        },
    )
    rows = []
    for name, (geometry, _, _) in CASES.items():
        print(f"Running {name}", flush=True)
        row = execute(
            name,
            ROOT / "fixtures/internal" / f"{geometry}.step",
            output / "studies" / f"{name}.json",
            output,
        )
        rows.append(row)
        write_json(output / "results.json", {"complete": False, "cases": rows})
        print(json.dumps(row), flush=True)
    checks = comparisons(rows)
    passed = all(
        row["status"] == "passed"
        and row.get("integrity")
        and all(check["status"] == "pass" for check in row["checks"])
        for row in rows
    ) and all(check["passed"] for check in checks)
    write_json(
        output / "results.json",
        {"complete": True, "passed": passed, "comparisons": checks, "cases": rows},
    )
    print(f"Matrix {'PASSED' if passed else 'FAILED'}: {output / 'results.json'}", flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
