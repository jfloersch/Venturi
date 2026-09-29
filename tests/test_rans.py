import copy
from pathlib import Path

import pytest

from venturi.evidence import validate_field
from venturi.internal import study_geometry
from venturi.jobs import read_json
from venturi.models import RANSFlowStudy, RunRequest, parse_study
from venturi.recipes import freeze_study, internal_recipe, rans_recipe

ROOT = Path(__file__).resolve().parents[1]


def turbulent(name="pipe", reynolds=60000):
    s = read_json(ROOT / f"fixtures/internal/{name}.study.json")
    for key in ("physics", "recipe"):
        s.pop(key)
    s.update(
        schema_version="venturi.rans-study.v1",
        flow_rate_m3_s=7.853981633974483e-9 * reynolds,
        turbulence_intensity=0.05,
        turbulence_length_scale_m=0.0007,
    )
    return parse_study(s)


def test_rans_has_separate_frozen_contract_and_round_trips_through_request():
    laminar = parse_study(read_json(ROOT / "fixtures/internal/pipe.study.json"))
    s = turbulent()
    assert isinstance(s, RANSFlowStudy)
    request = RunRequest(kind="internal_mesh", request_id="rans-roundtrip-001", study=s)
    assert RunRequest.model_validate(request.model_dump()).study == s
    assert freeze_study(s)["recipe"] == rans_recipe()
    assert freeze_study(laminar)["recipe"] == internal_recipe()
    assert freeze_study(s)["recipe_hash"] != freeze_study(laminar)["recipe_hash"]
    g = study_geometry(ROOT / "fixtures/internal/pipe.step", s)
    assert all(abs(v - 60000) < 0.001 for v in g["port_reynolds_full_flow"].values())


@pytest.mark.parametrize("name", ["bend", "manifold", "rectangular"])
def test_turbulence_does_not_enable_unqualified_geometries(name):
    with pytest.raises(ValueError, match="straight circular duct"):
        study_geometry(ROOT / f"fixtures/internal/{name}.step", turbulent(name))


@pytest.mark.parametrize("reynolds", [10, 3999, 100001])
def test_turbulence_reynolds_screen(reynolds):
    with pytest.raises(ValueError, match="requires port Reynolds"):
        study_geometry(ROOT / "fixtures/internal/pipe.step", turbulent(reynolds=reynolds))


def test_turbulence_properties_must_be_explicit_and_bounded():
    s = turbulent().model_dump()
    for field in ("turbulence_intensity", "turbulence_length_scale_m"):
        invalid = copy.deepcopy(s)
        invalid.pop(field)
        with pytest.raises(ValueError):
            parse_study(invalid)
        invalid[field] = 0.0
        with pytest.raises(ValueError):
            parse_study(invalid)
    s["turbulence_length_scale_m"] = 0.1
    with pytest.raises(ValueError, match="must not exceed"):
        study_geometry(ROOT / "fixtures/internal/pipe.step", parse_study(s))


def test_dimensionless_native_syntax_does_not_accept_wrong_pressure_units(tmp_path):
    field = tmp_path / "field"
    field.write_text("dimensions []; internalField uniform 0;")
    validate_field(field, "0 0 0 0 0 0 0", 1, 3)
    with pytest.raises(ValueError, match="dimensions"):
        validate_field(field, "0 2 -2 0 0 0 0", 1, 3)
    field.write_text("dimensions [madeUpUnits]; internalField uniform 0;")
    with pytest.raises(ValueError, match="dimensions"):
        validate_field(field, "0 0 0 0 0 0 0", 1, 3)
