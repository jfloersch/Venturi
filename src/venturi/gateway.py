"""Separate hosted Managed service. Never runs a simulation or accepts a CAD file."""

import argparse
import hashlib
import hmac
import json
import os
import time
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field, field_validator, model_validator

from .assistant import Proposal, strict_schema
from .managed_ledger import Ledger
from .models import StrictModel, canonical_hash
from .visual_review import VisualObservations


class Tariff(StrictModel):
    version: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,80}$")
    model: str = Field(pattern=r"^[a-zA-Z0-9._:-]{1,120}$")
    input_usd_per_million: Decimal = Field(gt=0, le=10000, strict=False)
    output_usd_per_million: Decimal = Field(gt=0, le=10000, strict=False)
    markup: Decimal = Field(ge=1, le=10, strict=False)

    def charge(self, incoming, outgoing):
        return int(
            (
                (incoming * self.input_usd_per_million + outgoing * self.output_usd_per_million)
                * self.markup
            ).to_integral_value(rounding=ROUND_CEILING)
        )


class Settings(StrictModel):
    database: Path = Field(strict=False)
    public_url: str
    openai_key: str = Field(min_length=10, repr=False)
    stripe_key: str = Field(min_length=10, repr=False)
    webhook_secret: str = Field(min_length=10, repr=False)
    tariff: Tariff
    development: bool = False
    account_daily_microusd: int = Field(default=50_000_000, gt=0)
    global_daily_microusd: int = Field(default=500_000_000, gt=0)
    maximum_request_microusd: int = Field(default=5_000_000, gt=0)
    top_up_cents: list[int] = Field(default_factory=lambda: [2000, 5000])
    registrations_enabled: bool = True

    @model_validator(mode="after")
    def secure(self):
        parsed = urlparse(self.public_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Gateway URL must not contain credentials, query or fragment.")
        if parsed.scheme != "https" and not (
            self.development
            and parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError("A public HTTPS URL is required.")
        if not self.top_up_cents or any(
            type(v) is not int or not 100 <= v <= 100000 for v in self.top_up_cents
        ):
            raise ValueError("Top-ups must be whole cents between $1 and $1,000.")
        return self


class Credentials(StrictModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,60}$")
    password: str = Field(min_length=12, max_length=256, repr=False)


class Registration(Credentials):
    accept_terms: Literal[True]

    @field_validator("accept_terms", mode="before")
    @classmethod
    def explicit_terms(cls, value):
        if value is not True:
            raise ValueError("Explicit acceptance is required.")
        return value


class Recovery(Credentials):
    recovery_code: str = Field(min_length=30, max_length=100, repr=False)


class Limit(StrictModel):
    study: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    maximum_microusd: int = Field(ge=0, le=1_000_000_000)


class Checkout(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    amount_cents: int = Field(ge=100, le=100000)


class Inference(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    study: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    study_limit_microusd: int = Field(gt=0, le=1_000_000_000)
    maximum_microusd: int = Field(gt=0, le=5_000_000)
    tariff_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    share_context: Literal[True]
    payload: dict

    @field_validator("share_context", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Explicit sharing consent is required.")
        return value


def bounded_payload(payload, tariff):
    allowed = {"instructions", "input", "tools", "tool_choice", "parallel_tool_calls"}
    if set(payload) - allowed or not isinstance(payload.get("instructions"), str):
        raise ValueError("Unsupported model request fields.")
    tools = payload.get("tools", [])
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
        raise ValueError("Exactly one supported proposal tool is required.")
    name = tools[0].get("name")
    schema = (
        {"propose_study": Proposal, "review_evidence": VisualObservations}.get(name)
        if isinstance(name, str)
        else None
    )
    if not schema or tools[0].get("parameters") != strict_schema(schema):
        raise ValueError("Only versioned Venturi proposal schemas are supported.")
    # Server fixes tools, model, storage, output limit and disables external tools.
    body = {
        "instructions": payload["instructions"],
        "input": payload.get("input"),
        "tools": [
            {
                "type": "function",
                "name": name,
                "description": "Return observations for local human review.",
                "strict": True,
                "parameters": strict_schema(schema),
            }
        ],
        "tool_choice": {"type": "function", "name": name},
        "parallel_tool_calls": False,
        "model": tariff.model,
        "max_output_tokens": 4000,
        "store": False,
    }

    # Allow only self-contained text and inline evidence. Provider item/file
    # references could otherwise access content outside this account's context.
    data = body["input"]
    if not isinstance(data, (str, list)) or not data:
        raise ValueError("A text or inline-evidence input is required.")
    if isinstance(data, list):
        for message in data:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] != "user"
                or not isinstance(message["content"], list)
                or not message["content"]
            ):
                raise ValueError("Only self-contained user messages are supported.")
            for part in message["content"]:
                if not isinstance(part, dict):
                    raise ValueError("Invalid message content.")
                if part.get("type") == "input_text":
                    if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                        raise ValueError("Invalid text content.")
                elif part.get("type") == "input_image":
                    if (
                        set(part) != {"type", "image_url", "detail"}
                        or part["detail"] != "low"
                        or not isinstance(part["image_url"], str)
                        or not part["image_url"].startswith("data:image/png;base64,")
                    ):
                        raise ValueError("Only low-detail inline PNG evidence is supported.")
                else:
                    raise ValueError("Unsupported input content; remote references are forbidden.")
    bound = len(json.dumps(body, ensure_ascii=False).encode()) + 4096
    if bound > 160000:
        raise ValueError("Context is too large. Reduce the shared context.")
    return body, bound, schema


def public_request(row):
    result = {k: row[k] for k in ("id", "state", "reserved", "cost", "created", "provider_id")}
    result["usage"] = json.loads(row["usage"]) if row.get("usage") else None
    result["response"] = (
        json.loads(row["response"])
        if row.get("response") and row["created"] > time.time() - 86400
        else None
    )
    return result


def verify_webhook(body: bytes, signature: str, secret: str):
    try:
        pieces = [part.split("=", 1) for part in signature.split(",")]
        stamp = next(v for k, v in pieces if k == "t")
        signatures = [v for k, v in pieces if k == "v1"]
        expected = hmac.new(
            secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if abs(time.time() - int(stamp)) > 300 or not any(
            hmac.compare_digest(expected, v) for v in signatures
        ):
            raise ValueError("Invalid signature")
        return json.loads(body)
    except (ValueError, StopIteration) as exc:
        raise ValueError("Invalid payment webhook signature.") from exc


def create_gateway(settings: Settings, provider=None, payments=None):
    ledger = Ledger(settings.database)
    provider = provider or httpx.Client(base_url="https://api.openai.com/v1/", timeout=90)
    payments = payments or httpx.Client(base_url="https://api.stripe.com/v1/", timeout=30)
    app = FastAPI(title="Venturi Managed", docs_url=None, redoc_url=None)
    app.state.ledger = ledger
    tariff = settings.tariff.model_dump(mode="json")
    tariff_hash = canonical_hash(tariff)
    security_headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

    @app.middleware("http")
    async def boundaries(request, call_next):
        # Gateway requests are intentionally small. Do not log request bodies.
        if request.method == "POST":
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 256000:
                    return JSONResponse(
                        {"detail": "Request too large."}, status_code=413, headers=security_headers
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers.update(security_headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Invalid request fields."}, status_code=422)

    @app.exception_handler(ValueError)
    async def rejected(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    def bearer(request):
        return request.headers.get("Authorization", "").removeprefix("Bearer ")

    def account(request: Request):
        try:
            return ledger.authenticate(bearer(request))
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from exc

    authenticated_account = Depends(account)

    def throttle(request, username):
        # Deploy with trusted proxy handling disabled unless explicitly configured.
        ledger.throttle("auth-ip:" + (request.client.host if request.client else "unknown"), 60)
        ledger.throttle("auth-user:" + username.lower(), 15)

    @app.get("/health")
    def health():
        return {"status": "ready", "protocol": "venturi.managed.v1"}

    @app.get("/v1/catalog")
    def catalog():
        return {
            "tariff": tariff,
            "tariff_hash": tariff_hash,
            "top_up_cents": settings.top_up_cents,
            "maximum_request_microusd": settings.maximum_request_microusd,
            "registrations_enabled": settings.registrations_enabled,
            "privacy": {
                "simulation": "local",
                "provider": "OpenAI",
                "provider_store": False,
                "gateway_output_retention_hours": 24,
                "automatic_diagnostics": False,
            },
            "terms_version": "2026-09-beta-1",
        }

    @app.post("/v1/accounts")
    def register(body: Registration, request: Request):
        if not settings.registrations_enabled:
            raise HTTPException(403, "New registrations are closed.")
        throttle(request, body.username)
        return ledger.register(body.username, body.password)

    @app.post("/v1/sessions")
    def login(body: Credentials, request: Request):
        throttle(request, body.username)
        return ledger.login(body.username, body.password)

    @app.post("/v1/recover")
    def recover(body: Recovery, request: Request):
        throttle(request, body.username)
        return ledger.recover(body.username, body.recovery_code, body.password)

    @app.post("/v1/logout")
    def logout(request: Request, user=authenticated_account):
        ledger.logout(bearer(request))
        return {"status": "disconnected"}

    @app.get("/v1/wallet")
    def wallet(user=authenticated_account):
        return ledger.wallet(user)

    @app.post("/v1/limits")
    def limit(body: Limit, user=authenticated_account):
        ledger.set_limit(user, body.study, body.maximum_microusd)
        return ledger.wallet(user)

    @app.post("/v1/privacy/delete-outputs")
    def purge(user=authenticated_account):
        ledger.purge_outputs(user)
        return {"status": "deleted", "retained": "Accounting and security records"}

    @app.post("/v1/checkout")
    def checkout(body: Checkout, user=authenticated_account):
        if body.amount_cents not in settings.top_up_cents:
            raise ValueError("Choose a published top-up amount.")
        order, fresh = ledger.order(user, body.request_id, body.amount_cents)
        if not fresh:
            return {"order_id": order["id"], "url": order["url"], "status": order["state"]}
        try:
            response = payments.post(
                "checkout/sessions",
                headers={
                    "Authorization": "Bearer " + settings.stripe_key,
                    "Idempotency-Key": order["id"],
                },
                data={
                    "mode": "payment",
                    "payment_method_types[0]": "card",
                    "line_items[0][price_data][currency]": "usd",
                    "line_items[0][price_data][unit_amount]": str(body.amount_cents),
                    "line_items[0][price_data][product_data][name]": "Venturi prepaid AI balance",
                    "line_items[0][quantity]": "1",
                    "metadata[venturi_order]": order["id"],
                    "payment_intent_data[metadata][venturi_order]": order["id"],
                    "success_url": settings.public_url.rstrip("/") + "/checkout/return",
                    "cancel_url": settings.public_url.rstrip("/") + "/checkout/return",
                },
            )
            response.raise_for_status()
            result = response.json()
            parsed = urlparse(result["url"])
            if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com":
                raise ValueError("Unexpected checkout URL.")
            ledger.complete_order(order["id"], result["id"], result["url"])
            return {"order_id": order["id"], "url": result["url"], "status": "pending"}
        except (httpx.HTTPError, ValueError, KeyError):
            # A timeout does not authorize a second payment session.
            return {
                "order_id": order["id"],
                "url": None,
                "status": "unresolved",
                "detail": "Checkout outcome is unknown. Contact support with this order ID before retrying.",
            }

    @app.get("/checkout/return", response_class=HTMLResponse)
    def payment_return():
        return "<!doctype html><title>Venturi</title><h1>Return to Venturi</h1><p>Refresh your wallet in the app. A payment is credited only after confirmation from the payment service.</p>"

    @app.post("/v1/payments/webhook")
    async def webhook(request: Request):
        try:
            event = verify_webhook(
                await request.body(),
                request.headers.get("Stripe-Signature", ""),
                settings.webhook_secret,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if bool(event.get("livemode")) != settings.stripe_key.startswith("sk_live_"):
            raise HTTPException(400, "Payment mode mismatch.")
        if event["type"] in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }:
            ledger.payment(event["id"], event["data"]["object"])
        elif event["type"] == "charge.refunded":
            ledger.refund(event["id"], event["data"]["object"])
        elif event["type"] == "charge.dispute.created":
            # Freeze new spending while the operator handles a disputed payment.
            with ledger.transaction() as db:
                intent = event["data"]["object"].get("payment_intent")
                row = db.execute(
                    "SELECT account FROM orders WHERE payment_intent=?", (intent,)
                ).fetchone()
                if row:
                    db.execute("UPDATE accounts SET disabled=1 WHERE id=?", (row["account"],))
                    ledger.audit(db, row["account"], "payment_dispute", {"event": event["id"]})
        return {"received": True}

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, user=authenticated_account):
        with ledger.transaction() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE account=? AND id=?", (user, request_id)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Request not found.")
            return public_request(dict(row))

    @app.post("/v1/requests")
    def inference(request: Inference, user=authenticated_account):
        if request.tariff_hash != tariff_hash:
            raise ValueError("Tariff changed. Review the current price and reconnect.")
        body, bound, schema = bounded_payload(request.payload, settings.tariff)
        maximum = settings.tariff.charge(bound, 4000)
        if maximum > min(request.maximum_microusd, settings.maximum_request_microusd):
            raise ValueError("Request exceeds the authorized maximum cost.")
        row, fresh = ledger.reserve(
            user,
            request.request_id,
            canonical_hash(request.model_dump()),
            request.study,
            maximum,
            request.study_limit_microusd,
            tariff,
            settings.account_daily_microusd,
            settings.global_daily_microusd,
        )
        if not fresh:
            return public_request(row)
        usage, provider_id, cost = None, None, maximum
        try:
            response = provider.post(
                "responses",
                json=body,
                headers={
                    "Authorization": "Bearer " + settings.openai_key,
                    "X-Client-Request-Id": user + "-" + request.request_id,
                },
            )
            response.raise_for_status()
            result = response.json()
            incoming, outgoing = (
                result.get("usage", {}).get("input_tokens"),
                result.get("usage", {}).get("output_tokens"),
            )
            if (
                type(incoming) is not int
                or type(outgoing) is not int
                or not (0 <= incoming <= bound and 0 <= outgoing <= 4000)
            ):
                raise ValueError("Missing or inconsistent provider usage.")
            usage = {"input_tokens": incoming, "output_tokens": outgoing}
            provider_id = result.get("id")
            cost = settings.tariff.charge(incoming, outgoing)
            calls = [v for v in result.get("output", []) if v.get("type") == "function_call"]
            if (
                result.get("status") != "completed"
                or len(calls) != 1
                or calls[0].get("name") != body["tools"][0]["name"]
            ):
                raise ValueError("Provider did not return the approved proposal.")
            parsed = schema.model_validate_json(calls[0]["arguments"])
            row = ledger.settle(
                user, request.request_id, cost, "completed", usage, provider_id, parsed.model_dump()
            )
        except Exception:
            if usage:
                row = ledger.settle(user, request.request_id, cost, "failed", usage, provider_id)
            else:
                row = ledger.ambiguous(user, request.request_id)
        return public_request(row)

    return app


def load_settings():
    path = Path(os.environ["VENTURI_GATEWAY_CONFIG"])
    values = json.loads(path.read_text())
    # Secrets are accepted only through deployment environment, never desktop config.
    values.update(
        openai_key=os.environ["VENTURI_GATEWAY_OPENAI_KEY"],
        stripe_key=os.environ["VENTURI_GATEWAY_STRIPE_KEY"],
        webhook_secret=os.environ["VENTURI_GATEWAY_WEBHOOK_SECRET"],
    )
    return Settings.model_validate(values)


def main():
    parser = argparse.ArgumentParser(
        description="Venturi Managed gateway and accounting operations"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8780)
    for name in ("metrics", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("--database", type=Path, required=True)
        if name == "reconcile":
            p.add_argument("--account", required=True)
            p.add_argument("--request", required=True)
            p.add_argument("--cost-microusd", type=int, required=True)
            p.add_argument("--evidence", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            create_gateway(load_settings()),
            host=args.host,
            port=args.port,
            access_log=False,
            proxy_headers=False,
            limit_concurrency=64,
        )
    else:
        ledger = Ledger(args.database)
        if args.command == "metrics":
            print(json.dumps(ledger.metrics(), indent=2))
        else:
            ledger.reconcile(args.account, args.request, args.cost_microusd, args.evidence)
            print("Reservation reconciled; audit entry recorded.")


if __name__ == "__main__":
    main()
