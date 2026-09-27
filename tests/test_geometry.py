from pathlib import Path

import pytest

pytest.importorskip("OCP")

from venturi.geometry import export_surfaces, fixture_selection, inspect_step, make_pipe
from venturi.models import file_hash

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/pipe.step"


@pytest.mark.cad
def test_step_units_references_and_surface_export(tmp_path):
    original = file_hash(FIXTURE)
    geometry = inspect_step(FIXTURE)
    again = inspect_step(FIXTURE)
    assert geometry["geometry_hash"] == original
    assert [f["id"] for f in geometry["faces"]] == [f["id"] for f in again["faces"]]
    assert geometry["bounds_m"] == pytest.approx([-0.005, -0.005, 0, 0.005, 0.005, 0.1])
    assert geometry["volume_m3"] == pytest.approx(7.853981633974484e-6)
    selections = fixture_selection(geometry)
    mapping = export_surfaces(geometry, selections, tmp_path)
    assert set(mapping) == {"inlet", "outlet", "wall"}
    assert mapping["inlet"]["area_m2"] == pytest.approx(mapping["outlet"]["area_m2"])
    for role in mapping:
        assert (tmp_path / f"{role}.stl").read_text().startswith(f"solid {role}\n")
    assert file_hash(FIXTURE) == original


@pytest.mark.cad
def test_revision_does_not_silently_reuse_selections(tmp_path):
    before = inspect_step(FIXTURE)
    changed = tmp_path / "changed.step"
    make_pipe(changed, radius_m=0.006)
    after = inspect_step(changed)
    with pytest.raises(ValueError, match="remapping"):
        export_surfaces(after, fixture_selection(before), tmp_path / "surfaces")


@pytest.mark.cad
def test_invalid_cad_stops_before_meshing(tmp_path):
    p = tmp_path / "broken.step"
    p.write_text("not a STEP file")
    with pytest.raises(ValueError, match="Could not read STEP"):
        inspect_step(p)
