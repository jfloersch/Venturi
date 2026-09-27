import pytest
from pydantic import ValidationError

from venturi.geometry import validate_selection
from venturi.models import BoundarySelection, PipeSpec


@pytest.mark.parametrize(
    "values",
    [
        {"density_kg_m3": 0},
        {"radius_m": -1},
        {"mean_velocity_m_s": 1},
        {"dynamic_viscosity_pa_s": float("nan")},
        {"length_m": 0.001},
        {"radius_mm": 5},
        {"core_cells": 40, "radial_cells": 30, "axial_cells": 200},
    ],
)
def test_invalid_or_unsupported_reference_inputs(values):
    with pytest.raises(ValidationError):
        PipeSpec(**values)


def test_reference_dimensions_and_analytical_relationship():
    spec = PipeSpec()
    assert spec.reynolds == pytest.approx(100)
    assert spec.expected_pressure_drop_pa == pytest.approx(0.32)
    # Pressure loss is linear in length and flow, and scales as 1/R^4 at fixed Q.
    larger = PipeSpec(radius_m=spec.radius_m * 2, mean_velocity_m_s=spec.mean_velocity_m_s / 4)
    assert larger.expected_pressure_drop_pa == pytest.approx(spec.expected_pressure_drop_pa / 16)


def test_stale_or_incomplete_selection_is_rejected():
    g = {"geometry_hash": "a" * 64, "faces": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    good = {"a": "inlet", "b": "outlet", "c": "wall"}
    validate_selection(g, BoundarySelection(geometry_hash="a" * 64, assignments=good))
    for key, assignments in [
        ("b" * 64, good),
        ("a" * 64, {"a": "inlet", "b": "outlet"}),
        ("a" * 64, {**good, "c": "inlet"}),
    ]:
        with pytest.raises(ValueError):
            validate_selection(g, BoundarySelection(geometry_hash=key, assignments=assignments))
