"""Guardrails for prepared STEP studies and evidence; no solver stubs claim accuracy."""

import copy
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from venturi.artifacts import seal
from venturi.evidence import field_values
from venturi.geometry import (
    import_geometry,
    inspect_step,
    interior_point,
    read_step_shape,
    validate_selection,
)
from venturi.internal import flow_evidence, study_geometry
from venturi.jobs import RunManager, initialize_attempt, read_json
from venturi.models import BoundarySelection, InternalFlowStudy, RunRequest, write_json
from venturi.recipes import freeze_study
from venturi.server import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/internal"
HEADERS = {"Authorization": "Bearer internal-test-token"}


def study(name="pipe"):
    return InternalFlowStudy.model_validate_json((FIXTURES / f"{name}.study.json").read_text())


@pytest.mark.parametrize(
    "change",
    [
        {"flow_rate_m3_s": 0},
        {"density_kg_m3": float("nan")},
        {"dynamic_viscosity_pa_s": -1},
        {"inlet_profile": "parabolic"},
        {"outlet_pressure_pa": 10},
        {"units": "mm"},
        {"recipe": "custom"},
        {"mesh": {"cell_size_m": 0.001, "maximum_cells": 400001}},
        {"custom_dictionary": "code"},
    ],
)
def test_invalid_study_is_rejected(change):
    with pytest.raises(ValueError):
        InternalFlowStudy.model_validate(study().model_dump() | change)


@pytest.mark.parametrize("case", ["pipe", "bend", "manifold", "rectangular"])
def test_prepared_cases_and_deterministic_port_mapping(case):
    g = study_geometry(FIXTURES / f"{case}.step", study(case))
    assert all(0 < r <= 200 for r in g["port_reynolds_full_flow"].values())
    assert len(g["port_reynolds_full_flow"]) == (3 if case == "manifold" else 2)


@pytest.mark.parametrize("cell_size", [0.0008, 0.000625, 0.0005, 0.0004])
def test_mesh_point_cell_is_inside_curved_fluid(cell_size):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    source = FIXTURES / "bend.step"
    geometry = inspect_step(source)
    bounds = geometry["bounds_m"]
    low = [bounds[i] - 2 * cell_size for i in range(3)]
    point = interior_point(source, geometry, cell_size, low)
    # blockMesh fits an integer number of cells across the padded domain.
    # The point and its actual background-cell center must select fluid.
    center = []
    for i in range(3):
        extent = bounds[i + 3] + 2 * cell_size - low[i]
        width = extent / math.ceil(extent / cell_size)
        position = (point[i] - low[i]) / width
        assert abs(position - round(position)) > 1e-4
        center.append(low[i] + (math.floor(position) + 0.5) * width)
    shape, _ = read_step_shape(source)
    classifier = BRepClass3d_SolidClassifier(shape)
    for candidate in (point, center):
        classifier.Perform(gp_Pnt(*candidate), min(cell_size * 1e-5, 1e-8))
        assert classifier.State() == TopAbs_IN


@pytest.mark.parametrize(
    "change,match",
    [
        ({"flow_rate_m3_s": 1e-3}, "Reynolds"),
        ({"mesh": {"cell_size_m": 0.005}}, "10 cells"),
    ],
)
def test_geometry_applicability_rejects_out_of_range(change, match):
    spec = InternalFlowStudy.model_validate(study().model_dump() | change)
    with pytest.raises(ValueError, match=match):
        study_geometry(FIXTURES / "pipe.step", spec)


def test_explicit_planar_ports_and_exact_revision_required():
    g = inspect_step(FIXTURES / "pipe.step")
    original = study().selection.model_dump()
    for mutation in ("stale", "missing", "unknown", "curved", "two_inlets"):
        value = copy.deepcopy(original)
        if mutation == "stale":
            value["geometry_hash"] = "0" * 64
        elif mutation == "missing":
            value["assignments"].pop(next(iter(value["assignments"])))
        elif mutation == "unknown":
            value["assignments"]["unrelated-face"] = "wall"
        elif mutation == "curved":
            value["assignments"] = {
                f["id"]: (
                    "inlet"
                    if f["normal"] is None
                    else "outlet"
                    if original["assignments"][f["id"]] == "outlet"
                    else "wall"
                )
                for f in g["faces"]
            }
        else:
            value["assignments"] = {
                k: "inlet" if v == "wall" else v for k, v in value["assignments"].items()
            }
        with pytest.raises(ValueError):
            validate_selection(g, BoundarySelection.model_validate(value), internal=True)


def test_upload_selection_revision_and_restart(tmp_path):
    source = FIXTURES / "manifold.step"
    with TestClient(create_app(tmp_path, "internal-test-token")) as client:
        assert client.post("/v1/geometry", content=source.read_bytes()).status_code == 401
        response = client.post(
            "/v1/geometry?filename=manifold.step", content=source.read_bytes(), headers=HEADERS
        )
        assert response.status_code == 201, response.text
        g = response.json()
        assert g["imported"] and set(g["selection"]["assignments"].values()) == {"wall"}
        assert (
            client.post(
                "/v1/selection", json=study("manifold").selection.model_dump(), headers=HEADERS
            ).status_code
            == 200
        )
        assert client.post("/v1/geometry", content=b"not STEP", headers=HEADERS).status_code == 422
        assert (
            client.get("/v1/geometry", headers=HEADERS).json()["geometry_hash"]
            == g["geometry_hash"]
        )
        assert (
            client.post("/v1/geometry", content=source.read_bytes(), headers=HEADERS).json()[
                "selection"
            ]
            == study("manifold").selection.model_dump()
        )
    with TestClient(create_app(tmp_path, "internal-test-token")) as client:
        assert (
            client.get("/v1/geometry", headers=HEADERS).json()["selection"]
            == study("manifold").selection.model_dump()
        )
        new = client.post(
            "/v1/geometry", content=(FIXTURES / "bend.step").read_bytes(), headers=HEADERS
        ).json()
        assert set(new["selection"]["assignments"].values()) == {"wall"}
        assert (
            client.post(
                "/v1/selection", json=study("manifold").selection.model_dump(), headers=HEADERS
            ).status_code
            == 422
        )


def test_importing_reference_bytes_requires_explicit_ports(tmp_path):
    with TestClient(create_app(tmp_path, "internal-test-token")) as client:
        client.get("/v1/geometry", headers=HEADERS)
        imported = client.post(
            "/v1/geometry", content=(ROOT / "fixtures/pipe.step").read_bytes(), headers=HEADERS
        ).json()
        assert imported["imported"]
        assert set(imported["selection"]["assignments"].values()) == {"wall"}
        assert not client.post("/v1/geometry/fixture", headers=HEADERS).json()["imported"]


def test_approval_is_required_and_bound_to_study_and_mesh(tmp_path, monkeypatch):
    spec = study()
    with pytest.raises(ValueError, match="approval|approved|mesh"):
        RunRequest(kind="internal_flow", request_id="missing-approval", study=spec)
    g = import_geometry(FIXTURES / "pipe.step", tmp_path)
    mesh = tmp_path / "runs/mesh-test"
    initialize_attempt(
        mesh,
        RunRequest(kind="internal_mesh", request_id="original-mesh", study=spec),
        {"source": FIXTURES / "pipe.step", "geometry": g, "selection": spec.selection.model_dump()},
    )
    state = read_json(mesh / "status.json") | {"status": "passed"}
    write_json(mesh / "status.json", state)
    write_json(
        mesh / "result.json",
        {
            "status": "passed",
            "study_hash": freeze_study(spec)["study_hash"],
            "recipe_hash": freeze_study(spec)["recipe_hash"],
            "mesh_hash": "a" * 64,
        },
    )
    (mesh / "report.html").write_text("Approval test fixture")
    seal(mesh)
    launches = []
    monkeypatch.setattr("venturi.jobs.launch_attempt", lambda p: launches.append(p))
    for changed, approval in [
        (spec, "b" * 64),
        (InternalFlowStudy.model_validate(spec.model_dump() | {"flow_rate_m3_s": 1e-7}), "a" * 64),
    ]:
        with pytest.raises(ValueError, match="stale"):
            RunManager(tmp_path).submit(
                RunRequest(
                    kind="internal_flow",
                    request_id="stale-approval",
                    study=changed,
                    mesh_run_id="mesh-test",
                    approved_mesh_hash=approval,
                )
            )
    assert launches == []


def evidence_fixture(folder):
    spec = study()
    spec = InternalFlowStudy.model_validate(spec.model_dump() | {"max_iterations": 100})
    q = spec.flow_rate_m3_s
    values = {"inlet": (0.00007, -q, q), "outlet_1": (0, q, q), "wall": (0.000035, 0, 0)}
    for patch, row in values.items():
        for field, value in zip(("p", "phi", "phi_mag"), row, strict=True):
            p = folder / "case/postProcessing" / f"{patch}_{field}/0/surfaceFieldValue.dat"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("".join(f"{i} {value}\n" for i in range(1, 101)))
    final = folder / "case/100"
    final.mkdir()
    (final / "p").write_text("dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;")
    (final / "U").write_text("dimensions [0 1 -1 0 0 0 0]; internalField uniform (0 0 .002);")
    (final / "phi").write_text(
        "dimensions [0 3 -1 0 0 0 0]; internalField uniform 0; boundaryField {"
        + "".join(f"{p} {{ value uniform {v[1]}; }}" for p, v in values.items())
        + "}"
    )
    (folder / "logs").mkdir()
    (folder / "logs/foamRun.log").write_text(
        "\n".join(f"Solving for {f}, Initial residual = 1e-9," for f in ["p", "Ux", "Uy", "Uz"])
        + "\nEnd\n"
    )
    audit = {
        "cells": 2,
        "internal_faces": 1,
        "patches": {p: {"mesh_faces": 1, "face_ids": [p]} for p in values},
    }
    return spec, audit


@pytest.mark.parametrize(
    "corruption",
    ["field_count", "native_flux", "negative_magnitude", "missing_branch", "time_gap", "nan"],
)
def test_corrupted_flow_evidence_fails_closed(tmp_path, corruption):
    spec, audit = evidence_fixture(tmp_path)
    case = tmp_path / "case"
    assert all(c["status"] == "pass" for c in flow_evidence(case, spec, audit)["checks"])
    if corruption == "field_count":
        (case / "100/p").write_text(
            "dimensions [0 2 -2 0 0 0 0]; internalField nonuniform List<scalar> 1 (0);"
        )
    elif corruption == "native_flux":
        p = case / "100/phi"
        p.write_text(
            p.read_text().replace(f"value uniform {spec.flow_rate_m3_s}", "value uniform 0")
        )
    else:
        p = case / "postProcessing/outlet_1_phi_mag/0/surfaceFieldValue.dat"
        if corruption == "missing_branch":
            p.unlink()
        elif corruption == "negative_magnitude":
            p.write_text("".join(f"{i} -1\n" for i in range(1, 101)))
        elif corruption == "time_gap":
            p.write_text("\n".join(p.read_text().splitlines()[1:]))
        else:
            p.write_text("1 nan\n")
    with pytest.raises(ValueError):
        flow_evidence(case, spec, audit)


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("backflow", "Outlet direction and backflow"),
        ("mass", "Mass conservation"),
        ("drift", "Pressure-drop stability"),
        ("residual", "Equation residuals"),
        ("unfinished", "Solver completed"),
    ],
)
def test_numerical_checks_cannot_be_overridden_by_small_residuals(tmp_path, failure, expected):
    spec, audit = evidence_fixture(tmp_path)
    case = tmp_path / "case"
    if failure in {"backflow", "mass"}:
        net = spec.flow_rate_m3_s * (1 if failure == "backflow" else 0.98)
        mag = spec.flow_rate_m3_s * (1.1 if failure == "backflow" else 0.98)
        for field, value in [("phi", net), ("phi_mag", mag)]:
            (case / f"postProcessing/outlet_1_{field}/0/surfaceFieldValue.dat").write_text(
                "".join(f"{i} {value}\n" for i in range(1, 101))
            )
        audit["patches"]["outlet_1"]["mesh_faces"] = 2
        p = case / "100/phi"
        p.write_text(
            p.read_text().replace(
                f"outlet_1 {{ value uniform {spec.flow_rate_m3_s}; }}",
                f"outlet_1 {{ value nonuniform List<scalar> 2 ({(mag + net) / 2} {(net - mag) / 2}); }}",
            )
        )
    elif failure == "drift":
        (case / "postProcessing/inlet_p/0/surfaceFieldValue.dat").write_text(
            "".join(f"{i} {i * 1e-6}\n" for i in range(1, 101))
        )
    else:
        p = tmp_path / "logs/foamRun.log"
        p.write_text(
            p.read_text().replace("1e-9", ".01")
            if failure == "residual"
            else p.read_text().replace("End", "")
        )
    result = flow_evidence(case, spec, audit)
    assert next(c for c in result["checks"] if c["name"] == expected)["status"] == "fail"


def test_field_reader_rejects_nonfinite_and_wrong_counts():
    for text in (
        "internalField uniform nan;",
        "internalField nonuniform List<scalar> 2 (1);",
        "internalField nonuniform List<vector> 2 ((1 2 3) (4 5 6));",
    ):
        with pytest.raises(ValueError):
            field_values(text, "internalField", 1, 2)


@pytest.mark.parametrize("kind", ["open", "multiple", "loose", "scale", "units", "oversize"])
def test_unsupported_geometry_stops_before_import(tmp_path, kind):
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    source = tmp_path / "unsupported.step"
    if kind == "oversize":
        with source.open("wb") as output:
            output.truncate(16 * 1024 * 1024 + 1)
    elif kind == "units":
        source.write_text(
            (FIXTURES / "pipe.step").read_text().replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,$)")
        )
    else:
        face = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(100, 0, 0), gp_Dir(0, 0, 1)), 0, 10, 0, 10
        ).Face()
        if kind == "open":
            shape = face
        elif kind == "scale":
            shape = BRepPrimAPI_MakeBox(100001, 1, 1).Shape()
        else:
            shape = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(shape)
            builder.Add(shape, BRepPrimAPI_MakeBox(10, 10, 10).Shape())
            builder.Add(
                shape,
                face
                if kind == "loose"
                else BRepPrimAPI_MakeBox(gp_Pnt(30, 0, 0), 10, 10, 10).Shape(),
            )
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(str(source))
    with pytest.raises(ValueError):
        import_geometry(source, tmp_path / "store")
    assert not (tmp_path / "store/active-geometry.json").exists()


def test_mesh_budget_is_checked_before_native_execution(tmp_path):
    from venturi.foam import cad_mesh

    g = inspect_step(FIXTURES / "pipe.step")
    with pytest.raises(ValueError, match="background mesh budget"):
        cad_mesh(tmp_path / "case", g, cell_size=1e-5, background_limit=800000)
    assert not (tmp_path / "case/system/blockMeshDict").exists()


def test_annular_port_is_unsupported(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = BRepAlgoAPI_Cut(
        BRepPrimAPI_MakeCylinder(5, 100).Shape(), BRepPrimAPI_MakeCylinder(2, 100).Shape()
    ).Shape()
    source = tmp_path / "annulus.step"
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(source))
    g = inspect_step(source)
    ports = [f for f in g["faces"] if f["normal"] is not None]
    selection = BoundarySelection(
        geometry_hash=g["geometry_hash"],
        assignments={
            f["id"]: "inlet" if f == ports[0] else "outlet" if f == ports[1] else "wall"
            for f in g["faces"]
        },
    )
    with pytest.raises(ValueError, match="one closed boundary wire"):
        validate_selection(g, selection, internal=True)
