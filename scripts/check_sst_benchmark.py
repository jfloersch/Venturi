#!/usr/bin/env python3
"""Compare downstream native pressure gradient with a smooth-pipe correlation.

This diagnostic is separate from recipe acceptance and independent CFD qualification.
The fixed comparison interval is 50–90% of duct length; tolerance is 15%. A short
developing duct may fail this comparison even when its numerical checks pass.
"""

import argparse
import math
from pathlib import Path

import numpy as np
from vtkmodules.vtkCommonDataModel import vtkPlane
from vtkmodules.vtkFiltersCore import vtkCutter
from vtkmodules.vtkIOLegacy import vtkUnstructuredGridReader

from venturi.artifacts import verify
from venturi.jobs import read_json
from venturi.models import file_hash, write_json


def evaluate(folder: Path):
    verify(folder)
    result, study, geometry = [
        read_json(folder / name) for name in ("result.json", "study.json", "geometry.json")
    ]
    if result.get("recipe") != "sst-straight-duct/1":
        raise ValueError("This benchmark requires the SST straight-duct recipe.")
    inlet = next(
        f for f in geometry["faces"] if study["selection"]["assignments"][f["id"]] == "inlet"
    )
    outlet = next(
        f for f in geometry["faces"] if study["selection"]["assignments"][f["id"]] == "outlet"
    )
    origin = np.array(inlet["centroid_m"])
    direction = np.array(outlet["centroid_m"]) - origin
    length = float(np.linalg.norm(direction))
    direction /= length
    diameter = math.sqrt(4 * inlet["area_m2"] / math.pi)
    mean_speed = study["flow_rate_m3_s"] / inlet["area_m2"]
    reynolds = study["density_kg_m3"] * mean_speed * diameter / study["dynamic_viscosity_pa_s"]
    source = folder / "case/VTK" / f"case_{result['iterations']}.vtk"
    reader = vtkUnstructuredGridReader()
    reader.SetFileName(str(source))
    reader.ReadAllScalarsOn()
    reader.Update()
    samples = []
    for fraction in (0.5, 0.6, 0.7, 0.8, 0.9):
        plane = vtkPlane()
        plane.SetOrigin(*(origin + direction * length * fraction))
        plane.SetNormal(*direction)
        cut = vtkCutter()
        cut.SetInputData(reader.GetOutput())
        cut.SetCutFunction(plane)
        cut.Update()
        grid = cut.GetOutput()
        p = grid.GetCellData().GetArray("p")
        weighted, area = 0.0, 0.0
        for i in range(grid.GetNumberOfCells()):
            points = grid.GetCell(i).GetPoints()
            if points.GetNumberOfPoints() < 3:
                continue
            vertices = np.array([points.GetPoint(j) for j in range(points.GetNumberOfPoints())])
            a = sum(
                float(
                    np.linalg.norm(
                        np.cross(vertices[j] - vertices[0], vertices[j + 1] - vertices[0])
                    )
                    / 2
                )
                for j in range(1, len(vertices) - 1)
            )
            weighted += a * p.GetTuple1(i) * study["density_kg_m3"]
            area += a
        if not area:
            raise ValueError("Benchmark slice has no fluid area.")
        samples.append(
            {"distance_m": fraction * length, "pressure_pa": weighted / area, "area_m2": area}
        )
    gradient = float(
        np.polyfit([s["distance_m"] for s in samples], [s["pressure_pa"] for s in samples], 1)[0]
    )
    measured = -gradient * diameter / (0.5 * study["density_kg_m3"] * mean_speed**2)
    reference = 0.3164 * reynolds**-0.25
    error = abs(measured - reference) / reference
    return {
        "schema_version": "venturi.sst-benchmark.v1",
        "run_id": folder.name,
        "result_sha256": file_hash(folder / "result.json"),
        "field_sha256": file_hash(source),
        "reynolds": reynolds,
        "length_diameters": length / diameter,
        "samples": samples,
        "pressure_gradient_pa_m": gradient,
        "darcy_friction_factor": measured,
        "blasius_reference": reference,
        "relative_difference": error,
        "tolerance": 0.15,
        "status": "pass" if result["status"] == "passed" and error <= 0.15 else "fail",
        "quantitative_status": result["status"],
        "reference": "https://www.osti.gov/servlets/purl/6117246",
        "limitations": [
            "Blasius is an empirical fully developed smooth-pipe correlation, not an exact solution.",
            "This developing-inlet comparison does not establish mesh independence or broad RANS applicability.",
            "Independent CFD review remains pending; numerical acceptance and qualification are separate.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.run)
    write_json(args.output, result)
    print(
        f"{result['status']}: Darcy f={result['darcy_friction_factor']:.6g}, reference={result['blasius_reference']:.6g}, difference={result['relative_difference']:.2%}"
    )
    raise SystemExit(0 if result["status"] == "pass" else 1)
