import json

import pytest
from pydantic import ValidationError

from venturi.models import PipeSpec, RunRequest, StudySpec, canonical_hash
from venturi.recipes import freeze_study, pipe_recipe


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "venturi.study.v2"},
        {"recipe": "rans/1"},
        {"units": "mm"},
        {"physics": "compressible"},
        {"criteria": {"pressure_relative_error": 1}},
        {"pipe": {"mean_velocity_m_s": 1}},
        {"pipe": {"radius_m": 1e-200}},
        {"pipe": {"max_iterations": True}},
        {"resources": {"wall_time_seconds": 0}},
        {"resources": {"memory_mb": 100000}},
    ],
)
def test_unsupported_study_rejected_before_execution(change):
    with pytest.raises(ValidationError):
        StudySpec.model_validate({**StudySpec().model_dump(), **change})


def test_hashes_bind_parameters_but_not_json_formatting():
    study = StudySpec()
    frozen = freeze_study(study)
    formatted = json.loads(json.dumps(study.model_dump(), indent=4, sort_keys=True))
    assert canonical_hash(formatted) == frozen["study_hash"]
    assert (
        freeze_study(StudySpec(pipe=PipeSpec(length_m=0.2)))["study_hash"] != frozen["study_hash"]
    )
    assert frozen["recipe_hash"] == canonical_hash(pipe_recipe())
    with pytest.raises(ValidationError):
        freeze_study(study.model_copy(update={"units": "mm"}))


def test_reference_never_silently_accepts_step_or_cad_study():
    with pytest.raises(ValidationError):
        RunRequest(kind="reference", request_id="test-request", geometry_hash="a" * 64)
    with pytest.raises(ValidationError):
        RunRequest(kind="cad_mesh", request_id="test-request", study=StudySpec())
