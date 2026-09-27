from pathlib import Path

import pytest

from venturi.evidence import mesh_quality, read_series, reference_evidence
from venturi.models import PipeSpec


def histories(
    folder: Path,
    *,
    inlet_p: float = 0.00032,
    inlet_flux: float = -7.853981633974484e-7,
    outlet_flux: float = 7.853981633974484e-7,
    drift: bool = False,
):
    for name, value in {
        "inlet_p": inlet_p,
        "outlet_p": 0,
        "inlet_phi": inlet_flux,
        "outlet_phi": outlet_flux,
    }.items():
        p = folder / "postProcessing" / name / "0/surfaceFieldValue.dat"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "# time value\n"
            + "".join(
                f"{i} {value * (1 + i / 500 if drift and name == 'inlet_p' else 1)}\n"
                for i in range(1, 501)
            )
        )
    p = folder / "500/p"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dimensions [0 2 -2 0 0 0 0];")
    p = folder.parent / "logs/foamRun.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(
            f"Solving for {field}, Initial residual = 1e-9" for field in ["p", "Ux", "Uy", "Uz"]
        )
        + "\nEnd\n"
    )


def test_residual_convergence_cannot_override_wrong_answer(tmp_path):
    case = tmp_path / "case"
    histories(case, inlet_p=0.32)  # The classic kinematic/physical pressure factor error.
    result = reference_evidence(case, PipeSpec())
    assert result["status"] == "failed"
    assert (
        next(c for c in result["checks"] if c["name"] == "Equation residuals")["status"] == "pass"
    )
    assert result["checks"][0]["status"] == "fail"


def test_mass_balance_uses_non_cancelling_flow_scale(tmp_path):
    case = tmp_path / "case"
    histories(case, outlet_flux=7.853981633974484e-7 * 0.98)
    result = reference_evidence(case, PipeSpec())
    assert result["mass_imbalance"] == pytest.approx(0.02)
    assert result["status"] == "failed"


def test_unstable_engineering_quantity_fails_with_small_residuals(tmp_path):
    case = tmp_path / "case"
    histories(case, drift=True)
    result = reference_evidence(case, PipeSpec())
    assert (
        next(c for c in result["checks"] if c["name"] == "Pressure-drop stability")["status"]
        == "fail"
    )


def test_missing_and_nonfinite_evidence_is_not_a_pass(tmp_path):
    with pytest.raises(ValueError, match="Missing evidence"):
        read_series(tmp_path / "missing")
    p = tmp_path / "series"
    p.write_text("1 nan\n")
    with pytest.raises(ValueError, match="non-finite"):
        read_series(p)


def test_mesh_exit_success_is_not_sufficient(tmp_path):
    p = tmp_path / "checkMesh.log"
    p.write_text("Failed 1 mesh checks.\nEnd\n")
    assert mesh_quality(p)["status"] == "fail"


def test_pressure_dimensions_must_match_before_conversion(tmp_path):
    case = tmp_path / "case"
    histories(case)
    (case / "500/p").write_text("dimensions [1 -1 -2 0 0 0 0];")
    with pytest.raises(ValueError, match="Pressure dimensions"):
        reference_evidence(case, PipeSpec())
