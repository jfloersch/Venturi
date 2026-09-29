"""Gateway acceptance with explicitly simulated OpenAI and Stripe transports."""

import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from venturi.assistant import Proposal, strict_schema
from venturi.gateway import Settings, create_gateway
from venturi.managed_ledger import Ledger


@pytest.fixture
def service(tmp_path):
    calls, checkouts = [], []
    output = {
        "name": "Water",
        "question": "Pressure drop?",
        "material_source": "User",
        "physics": "laminar",
        "inputs": [],
        "unresolved_questions": ["Flow?"],
        "explanation": "More inputs are required.",
    }

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "response-test",
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 200},
                "output": [
                    {
                        "type": "function_call",
                        "name": "propose_study",
                        "arguments": json.dumps(output),
                    }
                ],
            },
        )

    def payments(request):
        checkouts.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "id": "cs_test_" + str(len(checkouts)),
                "url": "https://checkout.stripe.com/c/test",
            },
        )

    settings = Settings(
        database=tmp_path / "ledger.sqlite",
        public_url="https://managed.example.test",
        openai_key="provider-test-secret",
        stripe_key="sk_test_simulated",
        webhook_secret="whsec_simulated",
        tariff={
            "version": "test-1",
            "model": "test-model",
            "input_usd_per_million": "1",
            "output_usd_per_million": "2",
            "markup": "2.5",
        },
    )
    transport = httpx.Client(
        base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(provider)
    )
    app = create_gateway(
        settings,
        transport,
        httpx.Client(
            base_url="https://api.stripe.com/v1/", transport=httpx.MockTransport(payments)
        ),
    )
    client = TestClient(app)
    account = client.post(
        "/v1/accounts",
        json={"username": "test-user", "password": "long-test-password", "accept_terms": True},
    ).json()
    session = client.post(
        "/v1/sessions", json={"username": "test-user", "password": "long-test-password"}
    ).json()
    client.headers["Authorization"] = "Bearer " + session["token"]
    return client, app.state.ledger, settings, calls, checkouts, transport, account


def webhook(client, settings, event, stamp=None):
    stamp = str(stamp or int(time.time()))
    raw = json.dumps(event).encode()
    signature = hmac.new(
        settings.webhook_secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/v1/payments/webhook",
        content=raw,
        headers={"Stripe-Signature": f"t={stamp},v1={signature}"},
    )


def fund(service):
    client, ledger, settings, _, checkouts, _, account = service
    order = client.post(
        "/v1/checkout", json={"request_id": "checkout-first", "amount_cents": 2000}
    ).json()
    assert len(checkouts) == 1 and order["url"].startswith("https://checkout.stripe.com/")
    event = {
        "id": "evt_payment",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_test_1",
                "mode": "payment",
                "currency": "usd",
                "amount_total": 2000,
                "payment_status": "paid",
                "payment_intent": "pi_test_1",
                "metadata": {"venturi_order": order["order_id"]},
            }
        },
    }
    assert webhook(client, settings, event).status_code == 200
    return event


def inference(client, **values):
    return {
        "request_id": "request-first",
        "study": "study-first",
        "study_limit_microusd": 1000000,
        "maximum_microusd": 1000000,
        "tariff_hash": client.get("/v1/catalog").json()["tariff_hash"],
        "share_context": True,
        "payload": {
            "instructions": "Propose inputs, never execute.",
            "input": "What else do you need?",
            "tools": [{"name": "propose_study", "parameters": strict_schema(Proposal)}],
        },
        **values,
    }


def test_authenticated_accounts_recovery_and_revocation(service):
    client, ledger, settings, _, _, _, account = service
    assert TestClient(client.app).get("/v1/wallet").status_code == 401
    assert client.get("/v1/wallet").json()["available_microusd"] == 0
    assert "long-test-password" not in settings.database.read_bytes().decode(errors="ignore")
    recovery = client.post(
        "/v1/recover",
        json={
            "username": "test-user",
            "password": "new-long-password",
            "recovery_code": account["recovery_code"],
        },
    )
    assert recovery.status_code == 200
    assert client.get("/v1/wallet").status_code == 401
    assert recovery.json()["recovery_code"] != account["recovery_code"]
    assert (
        client.post(
            "/v1/sessions", json={"username": "test-user", "password": "long-test-password"}
        ).status_code
        == 409
    )


def test_duplicate_payments_and_refunds_are_exactly_once(service):
    event = fund(service)
    client, ledger, settings, _, checkouts, _, account = service
    for event_id in ["evt_payment", "evt_another_delivery"]:
        event["id"] = event_id
        assert webhook(client, settings, event).status_code == 200
    assert (
        client.post(
            "/v1/checkout", json={"request_id": "checkout-first", "amount_cents": 2000}
        ).status_code
        == 200
    )
    assert len(checkouts) == 1
    assert ledger.wallet(account["account_id"])["available_microusd"] == 20000000
    event.update(
        id="evt_refund",
        type="charge.refunded",
        data={"object": {"payment_intent": "pi_test_1", "amount_refunded": 500, "currency": "usd"}},
    )
    for _ in range(2):
        assert webhook(client, settings, event).status_code == 200
    assert client.get("/v1/wallet").json()["available_microusd"] == 15000000
    assert Ledger(settings.database).wallet(account["account_id"])["available_microusd"] == 15000000


def test_payment_forgery_wrong_amount_and_old_signatures_fail(service):
    event = fund(service)
    client, _, settings, *_ = service
    assert client.post("/v1/payments/webhook", json=event).status_code == 400
    assert webhook(client, settings, event, int(time.time()) - 400).status_code == 400
    event["id"] = "evt_wrong"
    event["data"]["object"]["amount_total"] = 99999
    assert webhook(client, settings, event).status_code == 409
    assert client.get("/v1/wallet").json()["available_microusd"] == 20000000


def test_reserve_settle_replay_and_no_provider_key_leaks(service):
    fund(service)
    client, ledger, settings, calls, _, _, account = service
    request = inference(client)
    first = client.post("/v1/requests", json=request)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "completed"
    assert first.json()["cost"] == 1250
    assert client.post("/v1/requests", json=request).json() == first.json()
    assert len(calls) == 1 and calls[0]["store"] is False
    assert calls[0]["max_output_tokens"] == 4000
    assert ledger.wallet(account["account_id"])["available_microusd"] == 20000000 - 1250
    assert "provider-test-secret" not in settings.database.read_bytes().decode(errors="ignore")
    request["payload"]["input"] = "changed"
    assert client.post("/v1/requests", json=request).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        "empty-wallet",
        "request-limit",
        "study-limit",
        "old-tariff",
        "unapproved",
        "external-tool",
        "remote-image",
    ],
)
def test_preflight_blocks_without_provider_spending(service, change):
    client, _, _, calls, *_ = service
    if change != "empty-wallet":
        fund(service)
    request = inference(client)
    if change == "request-limit":
        request["maximum_microusd"] = 1
    elif change == "study-limit":
        request["study_limit_microusd"] = 1
    elif change == "old-tariff":
        request["tariff_hash"] = "0" * 64
    elif change == "unapproved":
        request["share_context"] = False
    elif change == "external-tool":
        request["payload"]["tools"].append({"type": "web_search"})
    elif change == "remote-image":
        request["payload"]["input"] = [
            {"type": "input_image", "image_url": "https://private.example/image", "detail": "low"}
        ]
    assert client.post("/v1/requests", json=request).status_code in {409, 422}
    assert calls == []


def test_timeouts_hold_funds_and_reconcile_without_resending(service):
    fund(service)
    client, ledger, settings, _, _, transport, account = service
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("upstream-secret-must-not-leak")

    transport._transport = httpx.MockTransport(timeout)
    request = inference(client)
    result = client.post("/v1/requests", json=request).json()
    assert result["state"] == "ambiguous" and result["cost"] > 0
    assert "upstream-secret" not in json.dumps(result)
    assert client.post("/v1/requests", json=request).json() == result and len(calls) == 1
    with ledger.transaction() as db:
        db.execute("UPDATE requests SET created=created-200")
    ledger.reconcile(
        account["account_id"], request["request_id"], 0, "Test provider evidence: no charge"
    )
    assert client.get("/v1/wallet").json()["available_microusd"] == 20000000
    assert client.post("/v1/requests", json=request).json()["state"] == "reconciled"
    assert len(calls) == 1


def test_concurrent_reservations_cannot_overspend_or_repeat(service):
    fund(service)
    client, ledger, settings, _, _, transport, account = service
    entered, release = threading.Event(), threading.Event()
    calls = []

    def delayed(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        raise httpx.ReadTimeout("ambiguous")

    transport._transport = httpx.MockTransport(delayed)
    request = inference(client, study_limit_microusd=50000)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, "/v1/requests", json=request)
        try:
            assert entered.wait(5)
            assert client.post("/v1/requests", json=request).json()["state"] == "reserved"
            blocked = client.post("/v1/requests", json={**request, "request_id": "request-second"})
            assert blocked.status_code == 409
        finally:
            release.set()
        assert future.result().json()["state"] == "ambiguous"
    assert len(calls) == 1


def test_accounts_cannot_read_each_others_requests(service):
    fund(service)
    client, *_ = service
    assert client.post("/v1/requests", json=inference(client)).status_code == 200
    assert (
        client.post(
            "/v1/accounts",
            json={
                "username": "other-user",
                "password": "other-long-password",
                "accept_terms": True,
            },
        ).status_code
        == 200
    )
    token = client.post(
        "/v1/sessions", json={"username": "other-user", "password": "other-long-password"}
    ).json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    assert client.get("/v1/requests/request-first").status_code == 404
    assert client.get("/v1/wallet").json()["available_microusd"] == 0


def test_privacy_deletes_cached_output_but_preserves_accounting(service):
    fund(service)
    client, *_ = service
    request = inference(client)
    result = client.post("/v1/requests", json=request).json()
    assert result["response"] is not None
    assert client.post("/v1/privacy/delete-outputs").status_code == 200
    repeated = client.post("/v1/requests", json=request).json()
    assert repeated["response"] is None and repeated["cost"] == result["cost"]


@pytest.mark.parametrize("scope", ["account", "global"])
def test_operator_daily_caps_block_before_provider_call(service, scope):
    fund(service)
    client, _, settings, calls, *_ = service
    if scope == "account":
        settings.account_daily_microusd = 1
    else:
        settings.global_daily_microusd = 1
    assert client.post("/v1/requests", json=inference(client)).status_code == 409
    assert not calls


def test_provider_failure_with_known_usage_settles_and_refunds_unused_reservation(service):
    fund(service)
    client, _, _, _, _, transport, _ = service
    transport._transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "status": "incomplete",
                "id": "failed-with-usage",
                "output": [],
                "usage": {"input_tokens": 100, "output_tokens": 200},
            },
        )
    )
    result = client.post("/v1/requests", json=inference(client)).json()
    assert result["state"] == "failed" and result["cost"] == 1250
    assert client.get("/v1/wallet").json()["available_microusd"] == 20000000 - 1250


def test_explicit_study_limit_change_is_required(service):
    fund(service)
    client, *_ = service
    first = inference(client, study_limit_microusd=40000)
    assert client.post("/v1/requests", json=first).status_code == 200
    assert (
        client.post(
            "/v1/limits", json={"study": "study-first", "maximum_microusd": 1250}
        ).status_code
        == 200
    )
    assert (
        client.post("/v1/requests", json=inference(client, request_id="second-request")).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/limits", json={"study": "study-first", "maximum_microusd": 1000000}
        ).status_code
        == 200
    )
    assert (
        client.post("/v1/requests", json=inference(client, request_id="second-request")).status_code
        == 200
    )


def test_credit_cannot_be_duplicated_by_concurrent_webhooks(service):
    event = fund(service)
    client, _, settings, *_ = service
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: webhook(client, settings, event), range(8)))
    assert all(r.status_code == 200 for r in results)
    assert client.get("/v1/wallet").json()["available_microusd"] == 20000000


def test_expired_response_and_session_are_not_returned(service):
    fund(service)
    client, ledger, *_ = service
    assert client.post("/v1/requests", json=inference(client)).status_code == 200
    with ledger.transaction() as db:
        db.execute("UPDATE requests SET created=created-90000")
    assert client.get("/v1/requests/request-first").json()["response"] is None
    ledger.metrics()
    with ledger.transaction() as db:
        assert db.execute("SELECT response FROM requests").fetchone()[0] is None
        db.execute("UPDATE sessions SET expires=0")
    assert client.get("/v1/wallet").status_code == 401
