"""Real STEP -> reviewed mesh -> flow -> export/replay acceptance corpus."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from venturi.artifacts import seal, verify
from venturi.jobs import RunManager, read_json
from venturi.models import write_json

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("VENTURI_INTEGRATION") != "1",
        reason="Set VENTURI_INTEGRATION=1 for actual OpenFOAM execution",
    ),
]


def cli(*args, success=True):
    result = subprocess.run(
        [sys.executable, "-m", "venturi.cli", *map(str, args)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=900,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


@pytest.fixture(
    scope="module",
    params=[
        "pipe",
        "bend",
        "manifold",
        "rectangular",
        "held-out-rotated-duct",
        "held-out-rotated-duct-2",
    ],
)
def executed(request, tmp_path_factory):
    name = request.param
    folder = tmp_path_factory.mktemp(f"internal-{name}")
    if name.startswith("held-out"):
        fixtures = folder / "generated"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/make_flow_fixtures.py"),
                "--output",
                str(fixtures),
                "--held-out",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        fixtures = ROOT / "fixtures/internal"
    source = fixtures / f"{name}.step"
    original = read_json(fixtures / f"{name}.study.json")
    imported = json.loads(cli("import-step", source, "--storage", folder / "store").stdout)
    assert imported["geometry_hash"] == original["selection"]["geometry_hash"]
    ports = original["selection"]["assignments"]
    options = []
    for face, role in ports.items():
        if role in {"inlet", "outlet"}:
            options += [f"--{role}", face]
    study = folder / "study.json"
    cli(
        "flow-template",
        source,
        *options,
        "--flow-rate",
        original["flow_rate_m3_s"],
        "--cell-size",
        original["mesh"]["cell_size_m"],
        "--output",
        study,
    )
    cli("validate", study, "--step", source)
    store = folder / "store"
    cli("mesh", study, "--storage", store, "--request-id", "acceptance-mesh")
    manager = RunManager(store)
    mesh = manager.list()[0]
    assert mesh["status"] == "passed", mesh
    assert verify(manager.folder(mesh["id"]))
    assert (manager.folder(mesh["id"]) / "mesh-preview.json").is_file()
    # Missing/wrong approval cannot launch a solve.
    cli("solve", mesh["id"], "--storage", store, success=False)
    cli("solve", mesh["id"], "--storage", store, "--approve-mesh", "0" * 64, success=False)
    assert len(manager.list()) == 1
    cli(
        "solve",
        mesh["id"],
        "--storage",
        store,
        "--approve-mesh",
        mesh["result"]["mesh_hash"],
        "--request-id",
        "acceptance-solve",
    )
    run = next(s for s in manager.list() if s["kind"] == "internal_flow")
    # Idempotency preserves the recorded approval and result.
    cli(
        "solve",
        mesh["id"],
        "--storage",
        store,
        "--approve-mesh",
        mesh["result"]["mesh_hash"],
        "--request-id",
        "acceptance-solve",
    )
    assert len(manager.list()) == 2
    return name, manager.folder(run["id"])


def test_real_prepared_geometry_flow(executed):
    name, folder = executed
    result = read_json(folder / "result.json")
    assert result["status"] == "passed", result["checks"]
    assert all(c["status"] == "pass" for c in result["checks"])
    assert result["mass_imbalance"] <= 0.001
    assert result["pressure_drop_pa"] > 0
    assert result["mesh_hash"] == read_json(folder / "mesh-approval.json")["mesh_hash"]
    assert result["environment"]["mesher"]["snappyHexMesh"]
    assert (folder / "outlet-flows.csv").is_file()
    assert list((folder / "case/VTK").rglob("*.vtk"))
    assert verify(folder)
    if name == "manifold":
        shares = [b["flow_fraction"] for b in result["outlets"].values()]
        assert len(shares) == 2 and abs(shares[0] - shares[1]) <= 0.05
    if name == "pipe":
        import vtk
        from vtk.util.numpy_support import vtk_to_numpy

        reader = vtk.vtkUnstructuredGridReader()
        reader.SetFileName(str(next((folder / "case/VTK").glob("case_*.vtk"))))
        reader.ReadAllScalarsOn()
        reader.ReadAllVectorsOn()
        reader.Update()
        grid = reader.GetOutput()
        centers = vtk.vtkCellCenters()
        centers.SetInputData(grid)
        centers.Update()
        z = vtk_to_numpy(centers.GetOutput().GetPoints().GetData())[:, 2]
        p = vtk_to_numpy(grid.GetCellData().GetArray("p"))
        selected = (z > 0.04) & (z < 0.08)
        slope = float(np.polyfit(z[selected], p[selected], 1)[0]) * 1000
        expected = -8 * 0.001 * 0.002 / 0.005**2
        error = abs(slope - expected) / abs(expected)
        write_json(
            folder.parent.parent.parent / "developed-gradient.json",
            {
                "measured_pa_m": slope,
                "expected_pa_m": expected,
                "relative_error": error,
                "limit": 0.05,
            },
        )
        assert error <= 0.05


def test_portable_replay_and_tampering(executed, tmp_path):
    name, folder = executed
    archive = tmp_path / f"{name}.zip"
    cli("export", folder, "--output", archive)
    cli("verify", archive)
    if name != "manifold":
        return
    output = tmp_path / "replayed Ω with spaces"
    cli("reproduce", archive, "--output", output)
    result = read_json(output / "result.json")
    assert result["status"] == "passed", result["checks"]
    assert result["reproduction_relative_difference"] <= 1e-6
    assert read_json(output / "native-inputs.json") == read_json(folder / "native-inputs.json")
    assert verify(output)
    for mutation in ("dictionary", "mesher", "source"):
        edited = tmp_path / mutation
        shutil.copytree(folder, edited)
        if mutation == "dictionary":
            p = edited / "case/system/controlDict"
            p.write_text(p.read_text() + "\nfunctions { arbitrary { type coded; } }\n")
        elif mutation == "mesher":
            p = edited / "environment.json"
            data = read_json(p)
            data["mesher"]["snappyHexMesh"] = "0" * 64
            write_json(p, data)
        else:
            p = edited / "source.step"
            p.write_text(p.read_text() + "\n")
        seal(edited)
        rejected = tmp_path / f"refused-{mutation}"
        cli("reproduce", edited, "--output", rejected, success=False)
        assert read_json(rejected / "status.json")["status"] == "failed"
        assert not (rejected / "logs/foamRun.log").exists()
        assert verify(rejected)


def test_failed_mesh_blocks_solver(tmp_path):
    # This coarse T-junction is retained as a known quality rejection.
    study = read_json(ROOT / "fixtures/internal/manifold.study.json")
    study["mesh"]["cell_size_m"] = 0.0008
    path = tmp_path / "coarse.json"
    write_json(path, study)
    store = tmp_path / "store"
    cli(
        "mesh",
        path,
        "--step",
        ROOT / "fixtures/internal/manifold.step",
        "--storage",
        store,
        success=False,
    )
    manager = RunManager(store)
    mesh = manager.list()[0]
    assert mesh["status"] == "failed"
    assert (
        next(c for c in mesh["result"]["checks"] if c["name"] == "Mesh quality")["status"] == "fail"
    )
    cli(
        "solve",
        mesh["id"],
        "--storage",
        store,
        "--approve-mesh",
        mesh["result"]["mesh_hash"],
        success=False,
    )
    assert len(manager.list()) == 1
    assert not (manager.folder(mesh["id"]) / "logs/foamRun.log").exists()


def test_internal_cancel_and_retry_preserve_frozen_geometry(tmp_path):
    import time

    from venturi.geometry import import_geometry
    from venturi.jobs import cancel_attempt, wait_attempt
    from venturi.models import InternalFlowStudy, RunRequest

    source = ROOT / "fixtures/internal/bend.step"
    spec = InternalFlowStudy.model_validate_json(
        (ROOT / "fixtures/internal/bend.study.json").read_text()
    )
    import_geometry(source, tmp_path)
    manager = RunManager(tmp_path)
    first = manager.submit(
        RunRequest(kind="internal_mesh", request_id="cancel-internal-mesh", study=spec)
    )
    cancel_attempt(manager.folder(first["id"]))
    assert wait_attempt(manager.folder(first["id"]))["status"] == "cancelled"
    retry = manager.submit(
        RunRequest(
            kind="internal_mesh",
            request_id="retry-internal-mesh",
            retry_of=first["id"],
            study=spec,
            reason="Test explicit retry",
        )
    )
    mesh = wait_attempt(manager.folder(retry["id"]))
    assert mesh["status"] == "passed", mesh
    assert mesh["retry_of"] == first["id"]
    flow = manager.submit(
        RunRequest(
            kind="internal_flow",
            request_id="cancel-internal-flow",
            study=spec,
            mesh_run_id=mesh["id"],
            approved_mesh_hash=mesh["result"]["mesh_hash"],
        )
    )
    folder = manager.folder(flow["id"])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (folder / "logs/foamRun.log").exists():
        time.sleep(0.05)
    assert (folder / "logs/foamRun.log").exists()
    cancel_attempt(folder)
    assert wait_attempt(folder)["status"] == "cancelled"
    assert verify(folder)
    assert (folder / "approved-mesh/manifest.json").is_file()
