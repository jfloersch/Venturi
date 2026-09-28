import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from venturi.artifacts import seal, verify
from venturi.geometry import fixture_selection, inspect_step
from venturi.jobs import RunManager, read_json
from venturi.models import write_json
from venturi.workflows import mesh_spike

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("VENTURI_INTEGRATION") != "1",
        reason="Set VENTURI_INTEGRATION=1 to run the pinned OpenFOAM integration suite",
    ),
]


def cli(*args, success=True):
    result = subprocess.run(
        [sys.executable, "-m", "venturi.cli", *map(str, args)],
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def real_reference(tmp_path_factory):
    folder = tmp_path_factory.mktemp("real-reference")
    study = folder / "study.json"
    cli("study-template", "--output", study)
    cli("validate", study)
    store = folder / "store"
    cli("run", study, "--storage", store, "--request-id", "reproducible-reference")
    manager = RunManager(store)
    states = manager.list()
    assert len(states) == 1
    run = manager.folder(states[0]["id"])
    # Retrying the identical CLI request must return the completed attempt.
    cli("run", study, "--storage", store, "--request-id", "reproducible-reference")
    assert len(manager.list()) == 1
    return run


def test_real_reference_solver_and_portable_evidence(real_reference, tmp_path):
    result = read_json(real_reference / "result.json")
    assert result["status"] == "passed", result["checks"]
    assert result["pressure_drop_pa"] == pytest.approx(0.32262946, rel=1e-6)
    assert result["relative_error"] < 0.05
    assert result["provenance"]["native_inputs_hash"]
    assert result["environment"]["runtime"]["file_hashes"]
    assert (real_reference / "metrics.csv").is_file()
    assert verify(real_reference)
    archive = tmp_path / "export.zip"
    cli("export", real_reference, "--output", archive)
    cli("verify", archive)
    replay = tmp_path / "reproduced Ω with spaces"
    cli("reproduce", archive, "--output", replay)
    reproduced = read_json(replay / "result.json")
    assert reproduced["status"] == "passed", reproduced["checks"]
    assert reproduced["reproduction_relative_difference"] <= 1e-6
    assert read_json(replay / "native-inputs.json") == read_json(
        real_reference / "native-inputs.json"
    )
    assert verify(replay)


def test_edited_native_case_is_not_executed_even_with_new_manifest(real_reference, tmp_path):
    source = tmp_path / "edited"
    shutil.copytree(real_reference, source)
    dictionary = source / "case/system/controlDict"
    dictionary.write_text(dictionary.read_text() + "\nfunctions { injected { type coded; } }\n")
    # Hashes establish integrity, not trusted provenance. Compiler equality is required.
    seal(source)
    output = tmp_path / "refused"
    cli("reproduce", source, "--output", output, success=False)
    state = read_json(output / "status.json")
    assert state["status"] == "failed"
    assert "differ from the deterministic compiler" in state["error"]
    assert not (output / "logs/foamRun.log").exists()
    assert verify(output)


def test_reproduction_requires_matching_runtime(real_reference, tmp_path):
    source = tmp_path / "other-runtime"
    shutil.copytree(real_reference, source)
    environment = read_json(source / "environment.json")
    environment["runtime"]["build"] = "different-build"
    write_json(source / "environment.json", environment)
    seal(source)
    output = tmp_path / "refused-runtime"
    cli("reproduce", source, "--output", output, success=False)
    assert "original runtime" in read_json(output / "status.json")["error"]
    assert not (output / "commands").exists()
    assert verify(output)


def test_actual_cad_to_solver_patch_mapping(tmp_path):
    source = Path(__file__).resolve().parents[1] / "fixtures/pipe.step"
    geometry = inspect_step(source)
    result = mesh_spike(tmp_path, geometry, fixture_selection(geometry), source)
    assert result["status"] == "passed", result["checks"]
    assert set(result["patches"]) == {"inlet", "outlet", "wall"}


def test_live_study_edit_cannot_receive_a_pass(tmp_path):
    import time

    from venturi.jobs import wait_attempt

    manager = RunManager(tmp_path / "store")
    from venturi.models import RunRequest

    state = manager.submit(RunRequest(kind="reference", request_id="live-edit-request"))
    folder = manager.folder(state["id"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (folder / "logs/foamRun.log").exists():
        time.sleep(0.05)
    assert (folder / "logs/foamRun.log").exists()
    study = read_json(folder / "study.json")
    study["pipe"]["length_m"] = 0.2
    write_json(folder / "study.json", study)
    finished = wait_attempt(folder)
    assert finished["status"] == "failed"
    assert (
        next(c for c in finished["result"]["checks"] if c["name"] == "Frozen study and recipe")[
            "status"
        ]
        == "fail"
    )
    assert verify(folder)
