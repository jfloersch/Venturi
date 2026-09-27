"""Versioned inputs shared by the CLI, HTTP worker, and desktop."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PipeSpec(StrictModel):
    """The fixed M0 reference, in SI; not an arbitrary internal-flow recipe."""

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
        if self.reynolds > 500:
            raise ValueError(
                "M0 reference is limited to Re <= 500; no turbulence model is qualified."
            )
        if self.length_m < 10 * self.radius_m:
            raise ValueError("M0 pipe length must be at least five diameters.")
        cells = (self.core_cells**2 + 4 * self.core_cells * self.radial_cells) * self.axial_cells
        if cells > 250_000:
            raise ValueError("M0 test harness limits the reference mesh to 250,000 cells.")
        return self


class BoundarySelection(StrictModel):
    geometry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assignments: dict[str, Literal["inlet", "outlet", "wall"]]


class RunRequest(StrictModel):
    kind: Literal["reference", "cad_mesh"]
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    geometry_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)
