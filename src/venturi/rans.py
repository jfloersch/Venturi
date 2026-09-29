"""Experimental Foundation-14 SST recipe, deliberately restricted to smooth ducts."""

import math
import re
from pathlib import Path

import numpy as np

from . import foam
from .evidence import check, field_values, validate_field
from .models import RANSFlowStudy
from .recipes import rans_recipe


def screen_geometry(g: dict, study: RANSFlowStudy):
    faces = g["faces"]
    ports = [f for f in faces if study.selection.assignments[f["id"]] != "wall"]
    walls = [f for f in faces if study.selection.assignments[f["id"]] == "wall"]
    if len(ports) != 2 or len(walls) != 1 or walls[0]["surface_type"] != "GeomAbs_Cylinder":
        raise ValueError(
            "Experimental SST supports only one straight circular duct with one cylindrical wall and two ports."
        )
    a, b = ports
    diameter = math.sqrt(4 * a["area_m2"] / math.pi)
    vector = np.array(b["centroid_m"]) - a["centroid_m"]
    length = float(np.linalg.norm(vector))
    if (
        length <= 0
        or length / diameter < 5
        or abs(a["area_m2"] / b["area_m2"] - 1) > 1e-6
        or any(abs(f["perimeter_m"] ** 2 / (4 * math.pi * f["area_m2"]) - 1) > 1e-6 for f in ports)
        or abs(np.dot(a["normal"], vector / length)) < 0.999999
        or np.dot(a["normal"], b["normal"]) > -0.999999
        or abs(walls[0]["area_m2"] / (math.pi * diameter * length) - 1) > 1e-6
        or abs(g["volume_m3"] / (a["area_m2"] * length) - 1) > 1e-6
    ):
        raise ValueError(
            "Experimental SST requires an unbranched straight circular duct at least five diameters long."
        )
    if study.turbulence_length_scale_m > diameter:
        raise ValueError("Turbulence length scale must not exceed the duct diameter.")


def compile_turbulence(case: Path, study: RANSFlowStudy):
    patches = foam.mesh_boundaries(case)
    speed = study.flow_rate_m3_s / patches["inlet"]["area_m2"]
    k = 1.5 * (speed * study.turbulence_intensity) ** 2
    omega = math.sqrt(k) / (0.09**0.25 * study.turbulence_length_scale_m)
    foam.dictionary(
        case / "constant/momentumTransport",
        "simulationType RAS; RAS { model kOmegaSST; turbulence on; }",
    )
    for field, value, dimensions, wall_type in (
        ("k", k, "0 2 -2 0 0 0 0", "kqRWallFunction"),
        ("omega", omega, "0 0 -1 0 0 0 0", "omegaWallFunction"),
        ("nut", 0, "0 2 -1 0 0 0 0", "nutkWallFunction"),
    ):
        boundaries = []
        for name in patches:
            kind = (
                wall_type
                if name in {"wall", "background"}
                else (
                    "calculated"
                    if field == "nut"
                    else "fixedValue"
                    if name == "inlet"
                    else "zeroGradient"
                )
            )
            boundaries.append(f"{name} {{ type {kind}; value uniform {value:.14g}; }}")
        foam.dictionary(
            case / f"0/{field}",
            f"dimensions [{dimensions}]; internalField uniform {value:.14g}; boundaryField {{ {' '.join(boundaries)} }}",
            "volScalarField",
        )
    foam.dictionary(
        case / "system/fvSchemes",
        """
        ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; }
        divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U);
            div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind;
            div((nuEff*dev2(T(grad(U))))) Gauss linear; }
        laplacianSchemes { default Gauss linear corrected; }
        interpolationSchemes { default linear; } snGradSchemes { default corrected; }
        fluxRequired { default no; p; }
        wallDist { method meshWave; }
    """,
    )
    foam.dictionary(
        case / "system/fvSolution",
        """
        solvers { p { solver GAMG; tolerance 1e-10; relTol 0.01; smoother GaussSeidel; }
            "(U|k|omega)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0.01; } }
        SIMPLE { nNonOrthogonalCorrectors 2; consistent yes; }
        relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.5; omega 0.5; } }
    """,
    )
    path = case / "system/controlDict"
    text = path.read_text()
    text = text.replace(
        "functions {",
        'functions { wallResolution { type yPlus; libs ("libfieldFunctionObjects.so"); executeAtStart false; executeControl writeTime; writeControl writeTime; }',
        1,
    )
    path.write_text(text)


def turbulence_evidence(case: Path, study: RANSFlowStudy, audit: dict):
    final = case / str(study.max_iterations)
    criteria = rans_recipe()["criteria"]
    checks, ranges = [], {}
    for field, dims in (
        ("k", "0 2 -2 0 0 0 0"),
        ("omega", "0 0 -1 0 0 0 0"),
        ("nut", "0 2 -1 0 0 0 0"),
    ):
        validate_field(final / field, dims, 1, audit["cells"])
        values = field_values((final / field).read_text(), "internalField", 1, audit["cells"])
        ranges[field] = [float(values.min()), float(values.max())]
        checks.append(
            check(
                f"Turbulence {field} positivity",
                bool(np.all(values >= 0 if field == "nut" else values > 0)),
                str(ranges[field]),
            )
        )
    validate_field(final / "yPlus", "0 0 0 0 0 0 0", 1, audit["cells"])
    patch = re.search(r"\bwall\s*\{([^}]+)\}", (final / "yPlus").read_text(), re.S)
    if not patch:
        raise ValueError("Wall yPlus evidence is missing.")
    values = field_values(patch[1], "value", 1, audit["patches"]["wall"]["mesh_faces"])
    ranges["yPlus"] = [float(values.min()), float(values.max())]
    checks.append(
        check(
            "Wall resolution",
            bool(
                np.all(
                    (values >= criteria["wall_yplus_minimum"])
                    & (values <= criteria["wall_yplus_maximum"])
                )
            ),
            f"All wall faces require y+ 30–300; measured range {ranges['yPlus']}.",
        )
    )
    log = (case.parent / "logs/foamRun.log").read_text()
    residuals = {
        n: float(v)
        for n, v in re.findall(r"Solving for (k|omega), Initial residual = ([^,\s]+)", log)
    }
    checks.append(
        check(
            "Turbulence equation residuals",
            set(residuals) == {"k", "omega"}
            and all(
                math.isfinite(v) and 0 <= v <= criteria["turbulence_equation_residual"]
                for v in residuals.values()
            ),
            f"Final initial residuals {residuals}; limit 1e-5.",
        )
    )
    return {
        "checks": checks,
        "turbulence_ranges": ranges,
        "qualification": "experimental; independent CFD review pending",
    }
