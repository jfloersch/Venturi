import os
from pathlib import Path

import pytest

from venturi.geometry import fixture_selection, inspect_step
from venturi.workflows import mesh_spike, reference

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("VENTURI_INTEGRATION") != "1",
        reason="Set VENTURI_INTEGRATION=1 to run the pinned OpenFOAM integration suite",
    ),
]


def test_real_reference_solver_and_portable_evidence(tmp_path):
    result = reference(tmp_path)
    assert result["status"] == "passed", result["checks"]
    assert result["relative_error"] < 0.05
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "metrics.csv").is_file()
    assert result["artifact_hashes"]


def test_actual_cad_to_solver_patch_mapping(tmp_path):
    source = Path(__file__).resolve().parents[1] / "fixtures/pipe.step"
    geometry = inspect_step(source)
    result = mesh_spike(tmp_path, geometry, fixture_selection(geometry), source)
    assert result["status"] == "passed", result["checks"]
    assert set(result["patches"]) == {"inlet", "outlet", "wall"}
