"""Local Managed adapter. Account secrets never enter studies or solver processes."""

import json
import math
import os
from decimal import ROUND_CEILING, Decimal
from urllib.parse import urlparse

import httpx
from pydantic import Field

from .jobs import file_lock, now, read_json
from .models import StrictModel, canonical_hash, write_json


class ManagedLogin(StrictModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,60}$")
    password: str = Field(min_length=12, max_length=256, repr=False)
    remember: bool = False
    budget_usd: float = Field(gt=0, le=1000)
    study_budget_usd: float = Field(gt=0, le=1000)
    tariff_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GatewayRejected(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class ManagedClient:
    def __init__(self, storage, client=None):
        self.folder = storage / "managed"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.url = os.environ.get("VENTURI_MANAGED_URL", "").rstrip("/")
        self.client = client or httpx.Client(timeout=110)
        self.secret = None
        self.service = "venturi-managed-" + canonical_hash(str(storage.resolve()))[:20]

    def config(self):
        path = self.folder / "connection.json"
        return read_json(path) if path.exists() else {}

    def token(self):
        if self.secret:
            return self.secret
        if self.config().get("remember"):
            from .assistant import secure_keyring

            try:
                return secure_keyring().get_password(self.service, "session")
            except Exception:
                return None
        return None

    def call(self, path, payload=None, authenticated=True, method=None, snapshot=None):
        parsed = urlparse(self.url)
        development = os.environ.get("VENTURI_MANAGED_DEVELOPMENT") == "1"
        if not self.url or (
            parsed.scheme != "https"
            and not (
                development
                and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )
        ):
            raise ValueError(
                "Managed service is not configured. Local and BYO workflows remain available."
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid Managed service address.")
        config = snapshot[0] if snapshot else self.config()
        if authenticated and config.get("url") != self.url:
            raise ValueError("Managed service address changed. Sign in again.")
        token = (snapshot[1] if snapshot else self.token()) if authenticated else None
        if authenticated and not token:
            raise ValueError("Sign in to Venturi Managed.")
        try:
            response = self.client.request(
                method or ("POST" if payload is not None else "GET"),
                self.url + "/v1" + path,
                json=payload,
                headers={"Authorization": "Bearer " + token} if token else {},
            )
            if response.status_code >= 400:
                # Gateway never sends credential/provider payloads in its error messages.
                detail = response.json().get("detail", "Managed request failed.")
                raise GatewayRejected(str(detail)[:500], response.status_code)
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Managed service is unavailable. Check connection and refresh request status before retrying."
            ) from exc

    def catalog(self):
        if not self.url:
            return {"configured": False, "connected": False}
        return {
            "configured": True,
            "url": self.url,
            "connected": bool(self.token()),
            **self.call("/catalog", authenticated=False),
        }

    def login(self, request):
        from .assistant import secure_keyring

        catalog = self.catalog()
        if catalog.get("tariff_hash") != request.tariff_hash:
            raise ValueError("Price changed. Refresh and approve the current tariff.")
        session = self.call(
            "/sessions",
            {"username": request.username, "password": request.password},
            authenticated=False,
        )
        try:
            if request.remember:
                secure_keyring().set_password(self.service, "session", session["token"])
            elif self.config().get("remember"):
                secure_keyring().delete_password(self.service, "session")
        except Exception as exc:
            # Revoke the newly minted token when persistence fails.
            try:
                self.client.post(
                    self.url + "/v1/logout", headers={"Authorization": "Bearer " + session["token"]}
                )
            except httpx.HTTPError:
                pass
            raise ValueError(
                "OS credential storage failed. Sign in with session-only storage."
            ) from exc
        self.secret = session["token"]
        write_json(
            self.folder / "connection.json",
            {
                "url": self.url,
                "account_id": session["account_id"],
                "remember": request.remember,
                "study_budget_usd": request.study_budget_usd,
                "tariff_hash": request.tariff_hash,
                "tariff": catalog["tariff"],
                "maximum_request_microusd": catalog["maximum_request_microusd"],
            },
        )
        return catalog

    def logout(self):
        from .assistant import secure_keyring

        # Local removal works offline; remote sessions expire within seven days.
        revoked = False
        try:
            self.call("/logout", {})
            revoked = True
        except ValueError:
            pass
        if self.config().get("remember"):
            try:
                secure_keyring().delete_password(self.service, "session")
            except Exception as exc:
                raise ValueError("Could not remove the stored session. Try again.") from exc
        self.secret = None
        (self.folder / "connection.json").unlink(missing_ok=True)
        return {"revoked": revoked}

    def set_limit(self, request):
        wallet = self.call("/limits", request.model_dump())
        config = self.config()
        config.setdefault("study_limits", {})[request.study] = request.maximum_microusd
        write_json(self.folder / "connection.json", config)
        return wallet

    def complete(self, assistant, request_id, identity, payload, schema, metadata):
        path = assistant.folder / "requests" / f"{request_id}.json"
        fingerprint = canonical_hash(identity)
        with assistant.lock, file_lock(assistant.folder / ".usage.lock"):
            if path.exists():
                previous = read_json(path)
                if previous["request_hash"] != fingerprint:
                    raise ValueError("Request ID already belongs to different inputs.")
                return previous
            config, local = self.config(), assistant.config()
            token = self.token()
            if not self.token() or not config:
                raise ValueError("Sign in to Venturi Managed.")
            # Extra schema framing is covered here; gateway calculates the exact reservation.
            bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 8192
            tariff = config["tariff"]
            reserved = int(
                (
                    (
                        bound * Decimal(tariff["input_usd_per_million"])
                        + 4000 * Decimal(tariff["output_usd_per_million"])
                    )
                    * Decimal(tariff["markup"])
                ).to_integral_value(rounding=ROUND_CEILING)
            )
            if reserved > config["maximum_request_microusd"]:
                raise ValueError("This request exceeds the Managed per-request limit.")
            if sum(r["cost_microusd"] for r in assistant.records()) + reserved > math.floor(
                local["budget_usd"] * 1e6
            ):
                raise ValueError("Workspace AI budget exhausted.")
            # A stable opaque key lets the gateway enforce the same study across restarts.
            study = canonical_hash(
                {
                    "workspace": self.service,
                    "study": metadata.get("study_id")
                    or metadata.get("selection", {}).get("geometry_hash")
                    or metadata.get("run_id")
                    or "workspace",
                }
            )
            # Keep pre-save proposals, later revisions and visual reviews in one
            # spending scope after a proposal becomes a saved study.
            if metadata.get("study_id"):
                for previous in assistant.records():
                    if previous.get("managed_study") and (
                        previous.get("applied", {}).get("id") == metadata["study_id"]
                        or previous.get("approved_study_id") == metadata["study_id"]
                    ):
                        study = previous["managed_study"]
                        break
            record = {
                "id": request_id,
                "request_hash": fingerprint,
                "created_at": now(),
                "status": "reserved",
                "cost_microusd": reserved,
                "provider": "managed",
                "managed_account": config["account_id"],
                "managed_study": study,
                "pricing": tariff,
                **metadata,
            }
            write_json(path, record)
        try:
            remote = self.call(
                "/requests",
                {
                    "request_id": request_id,
                    "study": study,
                    "study_limit_microusd": config.get("study_limits", {}).get(
                        study, math.floor(config["study_budget_usd"] * 1e6)
                    ),
                    "maximum_microusd": reserved,
                    "tariff_hash": config["tariff_hash"],
                    "share_context": True,
                    "payload": payload,
                },
                snapshot=(config, token),
            )
            record = self.apply_remote(record, remote, schema)
        except GatewayRejected as exc:
            # All gateway 4xx errors happen before inference; no new charge was authorized.
            if exc.status < 500:
                record.update(
                    status="failed", cost_microusd=0, managed_state="rejected", error=str(exc)
                )
            else:
                record.update(
                    status="failed",
                    error="Gateway failure; reservation retained until accounting is refreshed.",
                )
        except ValueError:
            record.update(
                status="failed",
                error="Managed request interrupted or rejected. Refresh its accounting status before making a new request; the local reservation is retained.",
            )
        with file_lock(assistant.folder / ".usage.lock"):
            write_json(path, record)
        return record

    @staticmethod
    def apply_remote(record, remote, schema):
        record.update(
            cost_microusd=remote["cost"],
            managed_state=remote["state"],
            provider_response_id=remote.get("provider_id"),
            usage=remote.get("usage"),
        )
        if remote["state"] == "completed" and remote.get("response") is not None:
            record.update(
                status="completed", output=schema.model_validate(remote["response"]).model_dump()
            )
        else:
            record.update(
                status="failed",
                error="Managed request has no usable response. Its accounting status is "
                + remote["state"]
                + ". No study changes were applied.",
            )
        return record

    def refresh_accounting(self, assistant, request_id):
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", request_id):
            raise ValueError("Invalid request ID.")
        path = assistant.folder / "requests" / f"{request_id}.json"
        with file_lock(assistant.folder / ".usage.lock"):
            record = read_json(path)
            if record.get("provider") != "managed" or record.get(
                "managed_account"
            ) != self.config().get("account_id"):
                raise ValueError("This request belongs to another account or billing route.")
            remote = self.call("/requests/" + request_id)
            if "message" in record:
                from .assistant import Proposal

                schema = Proposal
            else:
                from .visual_review import VisualObservations

                schema = VisualObservations
            if remote.get("response") and not record.get("output"):
                record = self.apply_remote(record, remote, schema)
                record.pop("error", None)
            else:
                record.update(
                    cost_microusd=remote["cost"],
                    managed_state=remote["state"],
                    usage=remote.get("usage"),
                )
            write_json(path, record)
        return assistant.finalize_proposal(record) if "message" in record else record
