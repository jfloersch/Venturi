#!/usr/bin/env python3
"""Run a declared pipe-parameter/grid regression matrix with the real CLI."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from venturi.artifacts import verify
from venturi.models import StudySpec, write_json

CASES = {
    "baseline": ({}, 1.0),
    "double_length": ({"length_m": 0.2, "axial_cells": 120}, 2.0),
    "half_radius": ({"radius_m": 0.0025}, 4.0),
    "double_velocity": ({"mean_velocity_m_s": 0.02}, 2.0),
    "double_density": ({"density_kg_m3": 2000.0}, 1.0),
    "double_viscosity": ({"dynamic_viscosity_pa_s": 0.002}, 2.0),
    "coarse": ({"core_cells": 6, "radial_cells": 4, "axial_cells": 30}, 1.0),
    "fine": (
        {"core_cells": 18, "radial_cells": 12, "axial_cells": 90, "timeout_seconds": 900},
        1.0,
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(
        args.output / "plan.json",
        {
            "cases": CASES,
            "criteria": {
                "recipe_pass": True,
                "pressure_ratio_relative_error": 0.05,
                "grid_trend": "fine analytical error < baseline error < coarse error",
            },
            "scope": "Provisional pipe regression and grid sanity check; not formal mesh independence or CFD qualification.",
        },
    )
    rows = []
    for name, (changes, expected_ratio) in CASES.items():
        data = StudySpec().model_dump()
        data["name"] = f"Closeout: {name}"
        data["pipe"].update(changes)
        data["resources"]["wall_time_seconds"] = 1200
        study = StudySpec.model_validate(data)
        source = args.output / "studies" / f"{name}.json"
        write_json(source, study.model_dump())
        folder = args.output / "runs" / name
        print(f"Running {name} ({study.pipe.reynolds:g} Reynolds)", flush=True)
        with (args.output / f"{name}.cli.log").open("w") as log:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "venturi.cli",
                    "reference",
                    "--study",
                    str(source),
                    "--output",
                    str(folder),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1300,
            )
        result = json.loads((folder / "result.json").read_text())
        integrity = bool(verify(folder))
        row = {
            "case": name,
            "status": result["status"],
            "exit_code": completed.returncode,
            "integrity": integrity,
            "expected_ratio": expected_ratio,
            "cells": (
                study.pipe.core_cells**2 + 4 * study.pipe.core_cells * study.pipe.radial_cells
            )
            * study.pipe.axial_cells,
            **{
                key: result.get(key)
                for key in [
                    "pressure_drop_pa",
                    "expected_pressure_drop_pa",
                    "relative_error",
                    "mass_imbalance",
                    "pressure_stability",
                    "reynolds",
                ]
            },
        }
        if rows and rows[0].get("pressure_drop_pa") and result.get("pressure_drop_pa") is not None:
            row["measured_ratio"] = result["pressure_drop_pa"] / rows[0]["pressure_drop_pa"]
            row["ratio_relative_error"] = (
                abs(row["measured_ratio"] - expected_ratio) / expected_ratio
            )
        rows.append(row)
        write_json(args.output / "results.json", {"cases": rows, "complete": False})
        print(json.dumps(row), flush=True)
    by_name = {row["case"]: row for row in rows}
    passed = all(
        row["status"] == "passed"
        and row["integrity"]
        and row["exit_code"] == 0
        and row.get("ratio_relative_error", 0) <= 0.05
        for row in rows
    )
    errors = [by_name[name]["relative_error"] for name in ("fine", "baseline", "coarse")]
    grid_trend = all(value is not None for value in errors) and errors[0] < errors[1] < errors[2]
    summary = {
        "complete": True,
        "passed": passed and grid_trend,
        "grid_trend_passed": grid_trend,
        "cases": rows,
    }
    write_json(args.output / "results.json", summary)
    print(
        f"Matrix {'PASSED' if summary['passed'] else 'FAILED'}: {args.output / 'results.json'}",
        flush=True,
    )
    if not summary["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
