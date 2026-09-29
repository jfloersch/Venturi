"""Local worker-to-gateway acceptance; external provider/payment calls are simulated."""

import json

import httpx
import pytest
import test_assistant
import test_gateway
from fastapi.testclient import TestClient
from test_assistant import response

from venturi.assistant import Assistant
from venturi.managed_client import ManagedLogin
from venturi.server import create_app

prepared = test_assistant.prepared
service = test_gateway.service


def fund(service):
    """Local adapter tests need balance, independently of payment integration."""
    _, ledger, _, _, _, _, account = service
    with ledger.transaction() as db:
        ledger.entry(db, account["account_id"], 20_000_000, "test_fixture", "no-payment")


def test_local_proposal_approval_via_managed_and_no_secrets_in_projects(
    service, prepared, monkeypatch
):
    fund(service)
    gateway, ledger, settings, _, _, transport, account = service
    storage, study, proposal = prepared
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response(proposal))

    transport._transport = httpx.MockTransport(provider)
    monkeypatch.setenv("VENTURI_MANAGED_URL", settings.public_url)
    app = create_app(storage, "test-worker")
    app.state.assistant.managed.client = gateway
    client = TestClient(app, headers={"Authorization": "Bearer test-worker"})
    catalog = client.get("/v1/managed").json()
    result = client.post(
        "/v1/managed/connect",
        json={
            "username": "test-user",
            "password": "long-test-password",
            "budget_usd": 5.0,
            "study_budget_usd": 1.0,
            "tariff_hash": catalog["tariff_hash"],
            "remember": False,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["connection"]["provider"] == "managed"
    message = {
        "request_id": "managed-study-first",
        "selection": study["selection"],
        "message": "Use my stated inputs.",
        "share_context": True,
    }
    context = client.post("/v1/assistant/context", json=message).json()
    assert context["estimate"]["maximum_microusd"] > 0
    message["context_hash"] = context["context_hash"]
    proposed = client.post("/v1/assistant/propose", json=message)
    assert proposed.status_code == 200, proposed.text
    record = proposed.json()
    assert record["status"] == "completed" and record["can_apply"], record
    assert record["cost_microusd"] == 1000
    assert client.post("/v1/assistant/propose", json=message).json() == record
    assert len(calls) == 1
    applied = client.post(
        "/v1/assistant/proposals/managed-study-first/approve",
        json={"proposal_hash": record["proposal_hash"]},
    )
    assert applied.status_code == 200 and applied.json()["revision"] == 1
    assert not list((storage / "runs").glob("*/request.json"))
    assert client.get("/v1/managed/wallet").json()["available_microusd"] == 20000000 - 1000
    # Disconnect keeps every local study, result and export usable.
    assert client.post("/v1/assistant/disconnect").status_code == 200
    assert client.get("/v1/workspace").json()["studies"]
    assert not Assistant(storage).status()["connected"]
    persisted = "".join(p.read_text() for p in storage.rglob("*.json"))
    assert "long-test-password" not in persisted and "provider-test-secret" not in persisted


def test_rejected_managed_call_releases_local_reservation(service, prepared, monkeypatch):
    gateway, _, settings, calls, *_ = service
    storage, _, _ = prepared
    monkeypatch.setenv("VENTURI_MANAGED_URL", settings.public_url)
    assistant = Assistant(storage)
    assistant.managed.client = gateway
    catalog = assistant.managed.catalog()
    assistant.connect_managed(
        ManagedLogin(
            username="test-user",
            password="long-test-password",
            budget_usd=5.0,
            study_budget_usd=1.0,
            tariff_hash=catalog["tariff_hash"],
        )
    )
    from venturi.assistant import Proposal

    payload = assistant.proposal_payload({}, "Missing inputs")
    result = assistant.complete("without-balance", {"message": "test"}, payload, Proposal, {})
    assert result["status"] == "failed" and result["cost_microusd"] == 0
    assert not calls


def test_support_preview_export_excludes_arbitrary_project_content(prepared):
    storage, _, _ = prepared
    (storage / "secret-study.json").write_text(
        json.dumps({"password": "DO_NOT_EXPORT", "geometry": "private customer CAD"})
    )
    client = TestClient(
        create_app(storage, "support-test"), headers={"Authorization": "Bearer support-test"}
    )
    preview = client.get("/v1/support/preview")
    assert preview.status_code == 200
    assert "DO_NOT_EXPORT" not in preview.text and str(storage) not in preview.text
    assert client.post("/v1/support/export", json={"preview_hash": "0" * 64}).status_code == 409
    report = client.post(
        "/v1/support/export", json={"preview_hash": preview.json()["preview_hash"]}
    )
    assert report.status_code == 200
    assert report.json() == preview.json()["data"]
    assert TestClient(client.app).get("/v1/support/preview").status_code == 401


def test_gateway_address_change_does_not_forward_saved_session(service, tmp_path, monkeypatch):
    gateway, _, settings, *_ = service
    monkeypatch.setenv("VENTURI_MANAGED_URL", settings.public_url)
    assistant = Assistant(tmp_path)
    assistant.managed.client = gateway
    assistant.connect_managed(
        ManagedLogin(
            username="test-user",
            password="long-test-password",
            budget_usd=1.0,
            study_budget_usd=1.0,
            tariff_hash=assistant.managed.catalog()["tariff_hash"],
        )
    )
    assistant.managed.url = "https://another.example.test"
    with pytest.raises(ValueError, match="address changed"):
        assistant.managed.call("/wallet")


def test_solver_environment_excludes_gateway_and_session_secrets(monkeypatch):
    from venturi.recovery import clean_environment

    for key in (
        "VENTURI_GATEWAY_STRIPE_KEY",
        "VENTURI_GATEWAY_WEBHOOK_SECRET",
        "DB_PASSWORD",
        "ACCESS_TOKEN",
    ):
        monkeypatch.setenv(key, "do-not-inherit")
    assert "do-not-inherit" not in clean_environment().values()


def test_lost_gateway_reply_recovers_proposal_without_a_second_charge(
    service, prepared, monkeypatch
):
    fund(service)
    gateway, _, settings, _, _, transport, _ = service
    storage, study, proposal = prepared
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=response(proposal))

    transport._transport = httpx.MockTransport(provider)
    monkeypatch.setenv("VENTURI_MANAGED_URL", settings.public_url)
    assistant = Assistant(storage)
    assistant.managed.client = gateway
    assistant.connect_managed(
        ManagedLogin(
            username="test-user",
            password="long-test-password",
            budget_usd=5.0,
            study_budget_usd=1.0,
            tariff_hash=assistant.managed.catalog()["tariff_hash"],
        )
    )
    original = assistant.managed.call

    def dropped(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == "/requests":
            raise ValueError("Simulated lost gateway reply after the charge settled")
        return result

    monkeypatch.setattr(assistant.managed, "call", dropped)
    message = test_assistant.message(study)
    context = assistant.context(message)
    message = message.model_copy(update={"context_hash": context["context_hash"]})
    assert assistant.propose(message)["status"] == "failed"
    recovered = assistant.managed.refresh_accounting(assistant, message.request_id)
    assert recovered["can_apply"] and recovered["status"] == "completed"
    assert recovered["cost_microusd"] == 1000
    saved = assistant.approve(recovered["id"], recovered["proposal_hash"])
    assert saved["revision"] == 1 and len(calls) == 1
