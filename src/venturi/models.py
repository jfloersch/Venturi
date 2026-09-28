"""Versioned inputs shared by the CLI, HTTP worker, and desktop."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, strict=True, validate_default=True
    )


class CheckResult(StrictModel):
    name: str
    status: Literal["pass", "fail", "pending", "not_assessed"]
    detail: str


class PipeSpec(StrictModel):
    """Bounded laminar pipe inputs, in SI; not a general internal-flow recipe."""

    schema_version: Literal[1] = 1
    radius_m: float = Field(default=0.005, gt=0, le=1)
    length_m: float = Field(default=0.1, gt=0, le=10)
    mean_velocity_m_s: float = Field(default=0.01, gt=0, le=10)
    density_kg_m3: float = Field(default=1000, gt=0, le=20000)
    dynamic_viscosity_pa_s: float = Field(default=0.001, gt=0, le=100)
    axial_cells: int = Field(default=60, ge=10, le=200)
    core_cells: int = Field(default=12, ge=4, le=40)
    radial_cells: int = Field(default=8, ge=2, le=30)
    max_iterations: int = Field(default=500, ge=50, le=2000)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)

    @property
    def reynolds(self) -> float:
        return (
            2
            * self.radius_m
            * self.mean_velocity_m_s
            * self.density_kg_m3
            / self.dynamic_viscosity_pa_s
        )

    @property
    def expected_pressure_drop_pa(self) -> float:
        return (
            8
            * self.dynamic_viscosity_pa_s
            * self.length_m
            * self.mean_velocity_m_s
            / self.radius_m**2
        )

    @model_validator(mode="after")
    def supported(self):
        try:
            derived = (
                self.reynolds,
                self.expected_pressure_drop_pa,
                self.dynamic_viscosity_pa_s / self.density_kg_m3,
            )
        except (ZeroDivisionError, OverflowError) as exc:
            raise ValueError("Pipe inputs exceed the representable numerical range.") from exc
        if not all(math.isfinite(value) and value > 0 for value in derived):
            raise ValueError("Derived pipe quantities must be finite and positive.")
        if self.reynolds > 500:
            raise ValueError(
                "Pipe reference is limited to Re <= 500; no turbulence model is qualified."
            )
        if self.length_m < 10 * self.radius_m:
            raise ValueError("Pipe length must be at least five diameters.")
        cells = (self.core_cells**2 + 4 * self.core_cells * self.radial_cells) * self.axial_cells
        if cells > 250_000:
            raise ValueError("Pipe recipe limits the reference mesh to 250,000 cells.")
        return self


class BoundarySelection(StrictModel):
    geometry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignments: dict[str, Literal["inlet", "outlet", "wall"]]


class RunRequest(StrictModel):
    kind: Literal["reference", "cad_mesh"]
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    geometry_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    study: "StudySpec | None" = None
    retry_of: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    reason: str = Field(default="User requested execution", min_length=1, max_length=500)

    @model_validator(mode="after")
    def compatible(self):
        if self.kind == "cad_mesh" and self.study is not None:
            raise ValueError("The CAD boundary check does not accept a reference study.")
        if self.kind == "reference" and self.geometry_hash is not None:
            raise ValueError("The pipe reference does not solve imported STEP geometry.")
        return self


class ResourcePolicy(StrictModel):
    wall_time_seconds: int = Field(default=900, ge=1, le=3600)
    memory_mb: int = Field(default=4096, ge=256, le=16384)
    disk_mb: int = Field(default=1024, ge=1, le=8192)


class StudySpec(StrictModel):
    schema_version: Literal["venturi.study.v1"] = "venturi.study.v1"
    name: str = Field(default="Laminar pipe reference", min_length=1, max_length=160)
    recipe: Literal["laminar-pipe/1"] = "laminar-pipe/1"
    physics: Literal["steady-incompressible-newtonian-laminar"] = (
        "steady-incompressible-newtonian-laminar"
    )
    units: Literal["SI"] = "SI"
    quantity: Literal["area-mean-inlet-minus-outlet-static-pressure-pa"] = (
        "area-mean-inlet-minus-outlet-static-pressure-pa"
    )
    pipe: PipeSpec = Field(default_factory=PipeSpec)
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)


RunRequest.model_rebuild()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
