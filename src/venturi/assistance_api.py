"""Authenticated assistant, recovery and scientific-review endpoints."""

from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import Field

from .artifacts import verify
from .assistant import Assistant, Connection, Message
from .jobs import ACTIVE, file_lock, read_json, reconcile
from .managed_client import ManagedLogin
from .models import StrictModel, canonical_hash, file_hash
from .recovery import RecoveryPolicy, RecoveryStart, campaign_folder, list_campaigns, plan, start
from .visual_review import VisualObservations, create_packet, review_payload


class Approval(StrictModel):
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewRequest(StrictModel):
    connection_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    packet_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    share_context: bool = False


def register(app, storage: Path, manager, auth):
    assistant = Assistant(storage)
    app.state.assistant = assistant
    dependencies = [Depends(auth)]

    def call(operation):
        try:
            return operation()
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/assistant", dependencies=dependencies)
    def status():
        return assistant.status()

    @app.post("/v1/assistant/connect", dependencies=dependencies)
    def connect(request: Connection):
        return call(lambda: assistant.connect(request))

    @app.post("/v1/assistant/disconnect", dependencies=dependencies)
    def disconnect():
        return call(assistant.disconnect)

    @app.get("/v1/managed", dependencies=dependencies)
    def managed_status():
        return call(assistant.managed.catalog)

    @app.post("/v1/managed/connect", dependencies=dependencies)
    def managed_connect(request: ManagedLogin):
        return call(lambda: assistant.connect_managed(request))

    # Reuse the strict gateway wire contracts, including explicit terms acceptance.
    from .gateway import Checkout, Limit, Recovery, Registration

    @app.post("/v1/managed/register", dependencies=dependencies)
    def managed_register(request: Registration):
        return call(
            lambda: assistant.managed.call("/accounts", request.model_dump(), authenticated=False)
        )

    @app.post("/v1/managed/recover", dependencies=dependencies)
    def managed_recover(request: Recovery):
        return call(
            lambda: assistant.managed.call("/recover", request.model_dump(), authenticated=False)
        )

    @app.get("/v1/managed/wallet", dependencies=dependencies)
    def managed_wallet():
        return call(lambda: assistant.managed.call("/wallet"))

    @app.post("/v1/managed/checkout", dependencies=dependencies)
    def managed_checkout(request: Checkout):
        return call(lambda: assistant.managed.call("/checkout", request.model_dump()))

    @app.post("/v1/managed/limits", dependencies=dependencies)
    def managed_limit(request: Limit):
        return call(lambda: assistant.managed.set_limit(request))

    @app.post("/v1/managed/privacy/delete-outputs", dependencies=dependencies)
    def managed_delete_outputs():
        return call(lambda: assistant.managed.call("/privacy/delete-outputs", {}))

    @app.post("/v1/managed/requests/{request_id}/refresh", dependencies=dependencies)
    def managed_refresh(request_id: str):
        return call(lambda: assistant.managed.refresh_accounting(assistant, request_id))

    @app.post("/v1/assistant/context", dependencies=dependencies)
    def context(request: Message):
        def preview():
            context = assistant.context(request)
            return {**context, "estimate": assistant.estimate(context, request.message)}

        return call(preview)

    @app.post("/v1/assistant/propose", dependencies=dependencies)
    def propose(request: Message):
        return call(lambda: assistant.propose(request))

    @app.post("/v1/assistant/proposals/{request_id}/approve", dependencies=dependencies)
    def approve(request_id: str, request: Approval):
        return call(lambda: assistant.approve(request_id, request.proposal_hash))

    @app.get("/v1/recovery", dependencies=dependencies)
    def campaigns():
        return call(lambda: list_campaigns(storage))

    @app.post("/v1/recovery/plan", dependencies=dependencies)
    def recovery_plan(request: RecoveryPolicy):
        return call(lambda: plan(manager, request))

    @app.post("/v1/recovery", dependencies=dependencies)
    def recovery_start(request: RecoveryStart):
        return call(lambda: start(manager, request))

    @app.post("/v1/recovery/{campaign_id}/cancel", dependencies=dependencies)
    def recovery_cancel(campaign_id: str):
        def cancel():
            folder = campaign_folder(storage, campaign_id)
            (folder / ".cancel").touch()
            return {"status": "cancellation_requested"}

        return call(cancel)

    def packet(run_id):
        folder = manager.folder(run_id)
        if reconcile(folder)["status"] in ACTIVE:
            raise ValueError("Wait for the run to finish before preparing visual evidence.")
        output = storage / "visual-reviews" / run_id
        with file_lock(folder / ".run.lock"), file_lock(output / ".review.lock"):
            verify(folder)
            if (output / "packet.json").exists():
                value = read_json(output / "packet.json")
                if (
                    canonical_hash({k: v for k, v in value.items() if k != "packet_hash"})
                    != value["packet_hash"]
                ):
                    raise ValueError("Visual packet changed.")
                for a in value["artifacts"]:
                    if (
                        file_hash(output / a["path"]) != a["sha256"]
                        or file_hash(output / a["provider_path"]) != a["provider_sha256"]
                    ):
                        raise ValueError("Visual evidence changed.")
                if file_hash(folder / "result.json") != value["source"]["result_sha256"]:
                    raise ValueError("Result changed after visual review preparation.")
                return value
            return create_packet(folder, output)

    @app.post("/v1/runs/{run_id}/visual-review", dependencies=dependencies)
    def prepare_review(run_id: str):
        def prepare():
            import base64

            value = packet(run_id)
            images = {
                a["id"]: "data:image/png;base64,"
                + base64.b64encode(
                    (storage / "visual-reviews" / run_id / a["path"]).read_bytes()
                ).decode()
                for a in value["artifacts"]
            }
            return {
                **value,
                "images": images,
                "connection_hash": canonical_hash(
                    {
                        "route": assistant.config(),
                        "account": assistant.managed.config().get("account_id"),
                    }
                ),
                "estimate": assistant.estimate_payload(
                    review_payload(value, storage / "visual-reviews" / run_id)
                ),
            }

        return call(prepare)

    @app.get("/v1/runs/{run_id}/visual-review/{image_id}", dependencies=dependencies)
    def review_image(run_id: str, image_id: str):
        def get():
            value = packet(run_id)
            artifact = next((a for a in value["artifacts"] if a["id"] == image_id), None)
            if not artifact:
                raise ValueError("Unknown visual artifact.")
            return FileResponse(
                storage / "visual-reviews" / run_id / artifact["path"], media_type="image/png"
            )

        return call(get)

    @app.get("/v1/runs/{run_id}/visual-review-export", dependencies=dependencies)
    def export_review(run_id: str):
        def export():
            import hashlib
            import io
            import json
            import zipfile

            value = packet(run_id)
            output = storage / "visual-reviews" / run_id
            observations = [
                r
                for r in assistant.records()
                if r.get("run_id") == run_id and r.get("packet_hash") == value["packet_hash"]
            ]
            files = {
                "packet.json": (output / "packet.json").read_bytes(),
                "observations.json": json.dumps(observations, indent=2).encode(),
            }
            for artifact in value["artifacts"]:
                for key in ("path", "provider_path"):
                    files[artifact[key]] = (output / artifact[key]).read_bytes()
            manifest = {
                "schema_version": "venturi.visual-export.v1",
                "source_run_id": run_id,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            }
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            return Response(
                stream.getvalue(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="venturi-visual-{run_id[:8]}.zip"'
                },
            )

        return call(export)

    @app.post("/v1/runs/{run_id}/visual-review/assist", dependencies=dependencies)
    def assist_review(run_id: str, request: ReviewRequest):
        def review():
            if not request.share_context:
                raise ValueError("Approve sharing the evidence packet before contacting OpenAI.")
            if request.connection_hash != canonical_hash(
                {
                    "route": assistant.config(),
                    "account": assistant.managed.config().get("account_id"),
                }
            ):
                raise ValueError("AI connection changed. Prepare and approve a new visual review.")
            value = packet(run_id)
            if value["packet_hash"] != request.packet_hash:
                raise ValueError("Review approval is stale.")
            desktop_context = manager.folder(run_id) / "desktop-context.json"
            study_id = (
                read_json(desktop_context).get("study", {}).get("id")
                if desktop_context.exists()
                else None
            )
            result = assistant.complete(
                request.request_id,
                {"run_id": run_id, **request.model_dump()},
                review_payload(value, storage / "visual-reviews" / run_id),
                VisualObservations,
                {
                    "run_id": run_id,
                    "study_id": study_id,
                    "packet_hash": value["packet_hash"],
                    "quantitative_status": value["quantitative_status"],
                },
            )
            ids = {a["id"] for a in value["artifacts"]}
            if result["status"] == "completed" and any(
                o["artifact_id"] not in ids for o in result["output"]["observations"]
            ):
                return {
                    **result,
                    "status": "failed",
                    "output": None,
                    "error": "Visual observations refer to unknown artifacts.",
                }
            return result

        return call(review)
