"""Generated Foundation-14 dictionaries and readers for our ASCII mesh output."""

import math
import re
from pathlib import Path

import numpy as np

from .models import PipeSpec


def dictionary(path: Path, body: str, cls: str = "dictionary") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"FoamFile\n{{ version 2.0; format ascii; class {cls}; object {path.name}; }}\n\n{body}\n"
    )


def control(
    case: Path,
    iterations: int = 500,
    functions: bool = True,
    *,
    patches: list[str] | None = None,
    magnitude_flux: bool = False,
) -> None:
    function_text = ""
    if functions:
        for patch in patches or ("inlet", "outlet"):
            for field, operation in (("p", "areaAverage"), ("phi", "sum")):
                function_text += f"""
                {patch}_{field} {{
                    type surfaceFieldValue;
                    libs (\"libfieldFunctionObjects.so\");
                    writeControl timeStep; writeInterval 1;
                    patch {patch};
                    operation {operation}; fields ({field});
                    writeFields false; log false;
                }}
                """
            if magnitude_flux:
                function_text += f"""
                {patch}_phi_mag {{
                    type surfaceFieldValue; libs ("libfieldFunctionObjects.so");
                    writeControl timeStep; writeInterval 1; patch {patch};
                    operation sumMag; fields (phi); writeFields false; log false;
                }}
                """
    dictionary(
        case / "system/controlDict",
        f"""
        application foamRun; solver incompressibleFluid;
        startFrom startTime; startTime 0; stopAt endTime; endTime {iterations}; deltaT 1;
        writeControl timeStep; writeInterval {iterations}; purgeWrite 0;
        writeFormat ascii; writePrecision 12; writeCompression off;
        timeFormat general; timePrecision 8; runTimeModifiable false;
        functions {{ {function_text} }}
    """,
    )


def pipe_mesh(case: Path, spec: PipeSpec) -> None:
    a, b = spec.radius_m * 0.35, spec.radius_m / math.sqrt(2)
    xy = [(-a, -a), (a, -a), (a, a), (-a, a), (-b, -b), (b, -b), (b, b), (-b, b)]
    vertices = "\n".join(f"({x:.12g} {y:.12g} {z:.12g})" for z in (0, spec.length_m) for x, y in xy)
    n, r, nz = spec.core_cells, spec.radial_cells, spec.axial_cells
    quads = [(0, 1, 2, 3), (4, 5, 1, 0), (1, 5, 6, 2), (3, 2, 6, 7), (4, 0, 3, 7)]
    counts = [(n, n), (n, r), (r, n), (n, r), (r, n)]
    blocks = "\n".join(
        f"hex ({' '.join(str(v) for v in (*q, *(i + 8 for i in q)))}) ({nx} {ny} {nz}) simpleGrading (1 1 1)"
        for q, (nx, ny) in zip(quads, counts, strict=True)
    )
    edges = []
    midpoints = [(0, -spec.radius_m), (spec.radius_m, 0), (0, spec.radius_m), (-spec.radius_m, 0)]
    for offset, z in ((0, 0), (8, spec.length_m)):
        for (i, j), (x, y) in zip(((4, 5), (5, 6), (6, 7), (7, 4)), midpoints, strict=True):
            edges.append(f"arc {i + offset} {j + offset} ({x} {y} {z})")
    inlet = " ".join("(" + " ".join(str(i) for i in reversed(q)) + ")" for q in quads)
    outlet = " ".join("(" + " ".join(str(i + 8) for i in q) + ")" for q in quads)
    dictionary(
        case / "system/blockMeshDict",
        f"""
        convertToMeters 1;
        vertices ({vertices}); blocks ({blocks}); edges ({" ".join(edges)});
        boundary (
            inlet {{ type patch; faces ({inlet}); }}
            outlet {{ type patch; faces ({outlet}); }}
            wall {{ type wall; faces ((4 12 13 5) (5 13 14 6) (6 14 15 7) (7 15 12 4)); }}
        );
    """,
    )


def _body(path: Path) -> str:
    s = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(), flags=re.S)
    if "format binary" in s:
        raise ValueError("This M0 reader requires ASCII output.")
    s = re.sub(r"FoamFile\s*\{.*?\}", "", s, flags=re.S).strip()
    return s


def mesh_boundaries(case: Path) -> dict:
    mesh = case / "constant/polyMesh"
    points = np.array(
        [[float(n) for n in v.split()] for v in re.findall(r"\(([^()]+)\)", _body(mesh / "points"))]
    )
    faces = [
        [int(n) for n in v.split()] for v in re.findall(r"\d+\(([^()]+)\)", _body(mesh / "faces"))
    ]
    result = {}
    for name, body in re.findall(r"(\w+)\s*\{([^}]+)\}", _body(mesh / "boundary")):
        count_match = re.search(r"nFaces\s+(\d+)", body)
        start_match = re.search(r"startFace\s+(\d+)", body)
        if count_match is None or start_match is None:
            raise ValueError("Malformed mesh boundary.")
        count, start = int(count_match[1]), int(start_match[1])
        centers, areas, normals = [], [], []
        for indices in faces[start : start + count]:
            p = points[indices]
            vector = sum(
                (np.cross(p[i] - p[0], p[i + 1] - p[0]) / 2 for i in range(1, len(p) - 1)),
                start=np.zeros(3),
            )
            area = float(np.linalg.norm(vector))
            if area <= 0:
                raise ValueError("Zero-area mesh boundary face.")
            centers.append(p.mean(axis=0).tolist())
            areas.append(area)
            normals.append((vector / area).tolist())
        result[name] = {
            "count": count,
            "centers": centers,
            "areas": areas,
            "normals": normals,
            "area_m2": sum(areas),
        }
    return result


def compile_solver(case: Path, spec: PipeSpec) -> None:
    patch = mesh_boundaries(case)["inlet"]
    centers = np.array(patch["centers"])
    areas = np.array(patch["areas"])
    profile = np.maximum(0, 1 - (centers[:, 0] ** 2 + centers[:, 1] ** 2) / spec.radius_m**2)
    # Integrate the prescribed profile to exactly the declared volumetric flow.
    flow = math.pi * spec.radius_m**2 * spec.mean_velocity_m_s
    velocity = profile * flow / float(np.sum(profile * areas))
    values = "\n".join(f"(0 0 {u:.12g})" for u in velocity)
    dictionary(
        case / "0/U",
        f"""
        dimensions [0 1 -1 0 0 0 0]; internalField uniform (0 0 {spec.mean_velocity_m_s});
        boundaryField {{
            inlet {{ type fixedValue; value nonuniform List<vector> {len(velocity)} ({values}); }}
            outlet {{ type zeroGradient; }}
            wall {{ type noSlip; }}
        }}
    """,
        "volVectorField",
    )
    dictionary(
        case / "0/p",
        """
        dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;
        boundaryField { inlet { type zeroGradient; } outlet { type fixedValue; value uniform 0; } wall { type zeroGradient; } }
    """,
        "volScalarField",
    )
    dictionary(
        case / "constant/physicalProperties",
        f"viscosityModel constant; nu {spec.dynamic_viscosity_pa_s / spec.density_kg_m3:.14g};",
    )
    dictionary(
        case / "constant/momentumTransport", "simulationType laminar; laminar { model Stokes; }"
    )
    dictionary(
        case / "system/fvSchemes",
        """
        ddtSchemes { default steadyState; }
        gradSchemes { default Gauss linear; }
        divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear; }
        laplacianSchemes { default Gauss linear corrected; }
        interpolationSchemes { default linear; }
        snGradSchemes { default corrected; }
        fluxRequired { default no; p; }
    """,
    )
    dictionary(
        case / "system/fvSolution",
        """
        solvers {
            p { solver GAMG; tolerance 1e-10; relTol 0.01; smoother GaussSeidel; }
            U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0.01; }
        }
        SIMPLE { nNonOrthogonalCorrectors 1; consistent yes; }
        relaxationFactors { equations { U 0.9; } }
    """,
    )
    control(case, spec.max_iterations)


def cad_mesh(
    case: Path,
    geometry: dict,
    *,
    cell_size: float | None = None,
    inside_point: list[float] | None = None,
    patches: list[str] | None = None,
    maximum_cells: int = 400000,
    background_limit: int = 400000,
    strict_quality: bool = False,
) -> None:
    dictionary(
        case / "system/meshQualityDict",
        '#includeEtc "caseDicts/mesh/generation/meshQualityDict.cfg"'
        + ("\nminTetQuality 1e-12; minVol 1e-18;" if strict_quality else ""),
    )
    bounds = geometry["bounds_m"]
    widths = [bounds[i + 3] - bounds[i] for i in range(3)]
    h = cell_size or min(widths) / 16
    low = [bounds[i] - h * 2 for i in range(3)]
    high = [bounds[i + 3] + h * 2 for i in range(3)]
    n = [math.ceil((high[i] - low[i]) / h) for i in range(3)]
    if math.prod(n) > background_limit:
        raise ValueError(f"Geometry exceeds the {background_limit:,}-cell background mesh budget.")
    x, y, z = low
    X, Y, Z = high
    vertices = [
        (x, y, z),
        (X, y, z),
        (X, Y, z),
        (x, Y, z),
        (x, y, Z),
        (X, y, Z),
        (X, Y, Z),
        (x, Y, Z),
    ]
    dictionary(
        case / "system/blockMeshDict",
        f"""
        convertToMeters 1;
        vertices ({" ".join("(" + " ".join(map(str, p)) + ")" for p in vertices)});
        blocks (hex (0 1 2 3 4 5 6 7) ({" ".join(map(str, n))}) simpleGrading (1 1 1));
        edges ();
        boundary (background {{ type patch; faces ((0 3 2 1) (4 5 6 7) (0 1 5 4) (1 2 6 5) (2 3 7 6) (3 0 4 7)); }});
    """,
    )
    # The reference spike uses its convex center; imported CAD supplies a classified point.
    inside = inside_point or [(bounds[i] + bounds[i + 3]) / 2 + h * 0.017 for i in range(3)]
    patch_names = patches or ["inlet", "outlet", "wall"]
    if strict_quality:
        dictionary(
            case / "system/surfaceFeaturesDict",
            "surfaces ("
            + " ".join(f'"{p}.stl"' for p in patch_names)
            + "); includedAngle 150; subsetFeatures { nonManifoldEdges no; openEdges yes; }",
        )
    features = (
        " ".join(f'{{ file "{p}.eMesh"; level 0; }}' for p in patch_names) if strict_quality else ""
    )
    regions = " ".join(
        f'{role} {{ type triSurfaceMesh; file "{role}.stl"; }}' for role in patch_names
    )
    refinement = " ".join(
        f"{role} {{ level (0 0); patchInfo {{ type {'wall' if role == 'wall' else 'patch'}; }} }}"
        for role in patch_names
    )
    dictionary(
        case / "system/snappyHexMeshDict",
        f"""
        #includeEtc "caseDicts/mesh/generation/snappyHexMeshDict.cfg"
        castellatedMesh on; snap on; addLayers off;
        geometry {{ {regions} }}
        castellatedMeshControls {{
            maxLocalCells {max(maximum_cells, math.prod(n))}; maxGlobalCells {max(maximum_cells, math.prod(n))};
            minRefinementCells 0; nCellsBetweenLevels 3;
            features ({features}); refinementSurfaces {{ {refinement} }}
            refinementRegions {{}}
            insidePoint ({" ".join(map(str, inside))});
        }}
        snapControls {{ nSmoothPatch 3; tolerance 2.0; nSolveIter 50; nRelaxIter 5; nFeatureSnapIter 10; implicitFeatureSnap {"false" if strict_quality else "true"}; explicitFeatureSnap {"true" if strict_quality else "false"}; multiRegionFeatureSnap false; }}
        addLayersControls {{ layers {{}} relativeSizes true; expansionRatio 1.2; finalLayerThickness 0.5; minThickness 0.001; }}
        writeFlags (); mergeTolerance 1e-6;
    """,
    )
    dictionary(
        case / "system/fvSchemes",
        "ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } divSchemes { default none; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; }",
    )
    dictionary(case / "system/fvSolution", "solvers {}")
    control(case, functions=False)
