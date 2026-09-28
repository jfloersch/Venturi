"""Durable desktop studies, revision-bound notes and bounded read-only inspection."""

import difflib
import hashlib
import math
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from .geometry import inspect_step
from .internal import study_geometry
from .jobs import file_lock, now, read_json
from .models import InternalFlowStudy, StrictModel, canonical_hash, file_hash, write_json


class StudySave(StrictModel):
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(default=0, ge=0)
    study: InternalFlowStudy
    question: str = Field(min_length=1, max_length=2000)
    material_source: str = Field(min_length=1, max_length=500)
    profile: Literal["guided", "collaborative", "expert"] = "guided"


class Camera(StrictModel):
    position: list[float] = Field(min_length=3, max_length=3)
    focal_point: list[float] = Field(min_length=3, max_length=3)
    view_up: list[float] = Field(min_length=3, max_length=3)


class Annotation(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    entity_id: str = Field(pattern=r"^(face|edge)_[0-9a-f]{16}$")
    label: str = Field(min_length=1, max_length=120)
    text: str = Field(default="", max_length=2000)
    position_m: list[float] = Field(min_length=3, max_length=3)
    camera: Camera


class AnnotationSave(StrictModel):
    geometry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(default=0, ge=0)
    annotations: list[Annotation] = Field(max_length=256)


def geometry_record(storage: Path, key: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Invalid geometry revision.")
    folder = storage / "geometry" / key
    source = folder / "source.step"
    if not source.is_file() or file_hash(source) != key:
        raise ValueError(
            "Stored STEP changed or is missing. Import it and explicitly remap references."
        )
    value = read_json(folder / "geometry.json")
    # Upgrade only presentation metadata; source and existing face identifiers stay unchanged.
    if "edges" not in value:
        value = {**value, **inspect_step(source)}
        write_json(folder / "geometry.json", value)
    return {**value, "selection": read_json(folder / "selection.json")}


def plan_study(storage: Path, study: InternalFlowStudy) -> dict:
    geometry_record(storage, study.selection.geometry_hash)
    g = study_geometry(storage / "geometry" / study.selection.geometry_hash / "source.step", study)
    # An order-of-magnitude geometric estimate, not a runtime/memory prediction.
    h = study.mesh.cell_size_m
    counts = [(g["bounds_m"][i + 3] - g["bounds_m"][i] + 4 * h) / h for i in range(3)]
    if (
        any(not math.isfinite(n) or n > 800000 for n in counts)
        or math.prod(math.ceil(n) for n in counts) > 800000
    ):
        raise ValueError(
            "Geometry exceeds the 800,000-cell background mesh budget. Review the mesh size and supported scope."
        )
    cells = max(1, round(g["volume_m3"] / h / h / h))
    return {
        "study": study.model_dump(),
        "study_hash": canonical_hash(study.model_dump()),
        "port_reynolds": g["port_reynolds_full_flow"],
        "estimated_cells": [max(1, cells // 2), cells * 3],
        "estimate_note": "Volume / cell-size cubed, range 0.5–3×. Snapping changes cell count. Memory and runtime estimates are not calibrated; limits are enforced per attempt.",
        "resources": study.resources.model_dump(),
        "execution": "One serial CPU; one mesh or solver attempt per explicit action; no automatic retries; no AI spend.",
    }


def save_study(storage: Path, request: StudySave) -> dict:
    plan_study(storage, request.study)
    if not request.question.strip() or not request.material_source.strip():
        raise ValueError("Describe the engineering question and fluid property source.")
    key = request.id or uuid.uuid4().hex
    folder = storage / "studies" / key
    with file_lock(storage / ".workspace.lock"):
        previous = (
            read_json(folder / "current.json") if (folder / "current.json").exists() else None
        )
        if request.id and previous is None:
            raise ValueError("Saved study no longer exists. Save a new copy.")
        revision = previous["revision"] if previous else 0
        if revision != request.expected_revision:
            raise ValueError("This study changed in another window. Reopen it before saving.")
        value = {
            **request.model_dump(exclude={"expected_revision"}),
            "schema_version": "venturi.desktop-study.v1",
            "id": key,
            "revision": revision + 1,
            "updated_at": now(),
            "study_hash": canonical_hash(request.study.model_dump()),
        }
        write_json(folder / "revisions" / f"{revision + 1}.json", value)
        write_json(folder / "current.json", value)
        write_json(storage / "active-study.json", {"id": key})
    return value


def study_record(storage: Path, key: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("Invalid study identifier.")
    path = storage / "studies" / key / "current.json"
    if not path.is_file():
        raise ValueError("Saved study not found.")
    return read_json(path)


def notes_record(storage: Path, key: str) -> dict:
    geometry_record(storage, key)
    path = storage / "geometry" / key / "annotations.json"
    return (
        read_json(path)
        if path.exists()
        else {"geometry_hash": key, "revision": 0, "annotations": []}
    )


def save_notes(storage: Path, request: AnnotationSave) -> dict:
    g = geometry_record(storage, request.geometry_hash)
    entities = {e["id"]: e for e in [*g["faces"], *g["edges"]]}
    if len({a.id for a in request.annotations}) != len(request.annotations):
        raise ValueError("Annotation identifiers must be unique.")
    for annotation in request.annotations:
        if annotation.entity_id not in entities:
            raise ValueError("Geometry reference changed. Explicitly select a face or edge again.")
        if not annotation.label.strip():
            raise ValueError("Annotation label cannot be blank.")
        bounds = g["bounds_m"]
        tolerance = max(bounds[i + 3] - bounds[i] for i in range(3)) * 0.01
        if any(
            not bounds[i] - tolerance <= p <= bounds[i + 3] + tolerance
            for i, p in enumerate(annotation.position_m)
        ):
            raise ValueError("Annotation position lies outside this geometry.")
    with file_lock(storage / ".workspace.lock"):
        previous = notes_record(storage, request.geometry_hash)
        if previous["revision"] != request.expected_revision:
            raise ValueError("Annotations changed in another window. Reload before saving.")
        value = {
            "geometry_hash": request.geometry_hash,
            "revision": previous["revision"] + 1,
            "annotations": [
                {
                    **a.model_dump(),
                    "reference": {
                        k: v
                        for k, v in entities[a.entity_id].items()
                        if k not in {"cells", "points"}
                    },
                }
                for a in request.annotations
            ],
        }
        write_json(storage / "geometry" / request.geometry_hash / "annotations.json", value)
    return value


TEXT_LIMIT = 512 * 1024


def artifact_path(folder: Path, name: str) -> Path:
    if "\\" in name or any(p in {"", ".", ".."} for p in name.split("/")):
        raise ValueError("Invalid artifact path.")
    target = folder / name
    if any(p.is_symlink() for p in [target, *target.parents] if p != folder.parent):
        raise ValueError("Symbolic links are not viewable artifacts.")
    if not target.resolve().is_relative_to(folder.resolve()) or not target.is_file():
        raise ValueError("Artifact not found.")
    return target


def artifact_inventory(folder: Path) -> list[dict]:
    files = []
    for path in sorted(folder.rglob("*")):
        name = path.relative_to(folder).as_posix()
        if any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue
        try:
            target = artifact_path(folder, name)
        except ValueError:
            continue
        try:
            size = target.stat().st_size
        except OSError:
            continue
        files.append(
            {
                "path": name,
                "bytes": size,
                "previewable": size <= TEXT_LIMIT
                and target.suffix not in {".png", ".zip", ".gz", ".vtk", ".step"},
            }
        )
        if len(files) >= 5000:
            break
    return files


def artifact_text(folder: Path, name: str) -> dict:
    target = artifact_path(folder, name)
    with target.open("rb") as stream:
        content = stream.read(TEXT_LIMIT + 1)
    if len(content) > TEXT_LIMIT:
        raise ValueError("File exceeds the 512 KiB text preview limit. Download the complete run.")
    try:
        value = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Binary artifacts cannot be displayed as text.") from exc
    if "\x00" in value:
        raise ValueError("Binary artifacts cannot be displayed as text.")
    return {"path": name, "text": value, "sha256": hashlib.sha256(content).hexdigest()}


def artifact_diff(folder: Path, other: Path, name: str) -> dict:
    current = artifact_text(folder, name)
    previous = artifact_text(other, name)
    return {
        "path": name,
        "text": "".join(
            difflib.unified_diff(
                previous["text"].splitlines(keepends=True),
                current["text"].splitlines(keepends=True),
                fromfile=f"{other.name}/{name}",
                tofile=f"{folder.name}/{name}",
            )
        )
        or "Files are identical.\n",
    }
