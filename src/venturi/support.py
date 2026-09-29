"""Allowlisted support exports. Raw diagnostics, paths and project data stay local."""

import platform
from collections import Counter

from fastapi import Depends, HTTPException
from pydantic import Field

from . import PROTOCOL_VERSION, __version__
from .jobs import read_json
from .models import StrictModel, canonical_hash
from .runtime import diagnostics


class SupportExport(StrictModel):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def snapshot(storage):
    diagnostic = diagnostics()
    # All free text and paths are excluded, including diagnostic error strings.
    runs = Counter()
    for path in (storage / "runs").glob("*/status.json"):
        status = read_json(path).get("status")
        runs[
            status
            if status in {"queued", "running", "passed", "failed", "cancelled", "interrupted"}
            else "other"
        ] += 1
    data = {
        "schema": "venturi.support.v1",
        "application": __version__,
        "protocol": PROTOCOL_VERSION,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "is_wsl": diagnostic["is_wsl"],
        "checks": [{"name": c["name"], "status": c["status"]} for c in diagnostic["checks"]],
        "run_counts": dict(runs),
        "automatic_upload": False,
        "excluded": [
            "CAD",
            "fields",
            "study inputs",
            "annotations",
            "prompts",
            "model responses",
            "credentials",
            "paths",
            "logs",
            "account identifiers",
        ],
    }
    return {"data": data, "preview_hash": canonical_hash(data)}


def register(app, storage, auth):
    dependencies = [Depends(auth)]

    @app.get("/v1/support/preview", dependencies=dependencies)
    def preview():
        return snapshot(storage)

    @app.post("/v1/support/export", dependencies=dependencies)
    def export(request: SupportExport):
        current = snapshot(storage)
        if request.preview_hash != current["preview_hash"]:
            raise HTTPException(
                409, "Diagnostics changed. Preview the current report before exporting."
            )
        return current["data"]
