"""Offline M4 fault injection. Payments are forbidden; balances are test fixtures."""

import json
import multiprocessing
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from venturi.assistant import Proposal, strict_schema
from venturi.gateway import Settings, create_gateway
from venturi.managed_ledger import Ledger


def settings(path):
    return Settings(
        database=path,
        public_url="https://offline.example.test",
        openai_key="NEVER_SENT_PROVIDER_SECRET",
        stripe_key="UNUSED_PAYMENT_SECRET",
        webhook_secret="UNUSED_WEBHOOK_SECRET",
        tariff=dict(
            version="offline-1",
            model="offline",
            input_usd_per_million="1",
            output_usd_per_million="2",
            markup="2.5",
        ),
    )


def forbidden_payment(request):
    pytest.fail("Payment testing is excluded from this campaign.")


def proposal_response():
    return httpx.Response(
        200,
        json={
            "id": "offline-response",
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 200},
            "output": [
                {
                    "type": "function_call",
                    "name": "propose_study",
                    "arguments": json.dumps(
                        {
                            "name": "Water",
                            "question": "Pressure drop?",
                            "material_source": "User",
                            "physics": "laminar",
                            "inputs": [],
                            "unresolved_questions": ["Flow?"],
                            "explanation": "More inputs are required.",
                        }
                    ),
                }
            ],
        },
    )


@pytest.fixture
def offline(tmp_path):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return proposal_response()

    config = settings(tmp_path / "ledger.sqlite")
    transport = httpx.Client(
        base_url="https://offline.invalid/", transport=httpx.MockTransport(provider)
    )
    payments = httpx.Client(transport=httpx.MockTransport(forbidden_payment))
    app = create_gateway(config, transport, payments)
    ledger = app.state.ledger
    account = ledger.register("offline-user", "OFFLINE_PASSWORD_LONG")
    session = ledger.login("offline-user", "OFFLINE_PASSWORD_LONG")
    with ledger.transaction() as db:
        ledger.entry(db, account["account_id"], 20_000_000, "test_fixture", "no-payment")
    with TestClient(
        app, headers={"Authorization": "Bearer " + session["token"]}, raise_server_exceptions=False
    ) as client:
        yield client, ledger, account, calls, transport, config, session
    transport.close()
    payments.close()


def request_body(client, request_id="offline-request"):
    return dict(
        request_id=request_id,
        study="offline-study",
        study_limit_microusd=20_000_000,
        maximum_microusd=1_000_000,
        tariff_hash=client.get("/v1/catalog").json()["tariff_hash"],
        share_context=True,
        payload={
            "instructions": "Propose; do not execute.",
            "input": "PRIVATE_PROMPT_SENTINEL",
            "tools": [{"name": "propose_study", "parameters": strict_schema(Proposal)}],
        },
    )


def reserve(ledger, account, request_id, amount=1000, maximum=100_000_000):
    return ledger.reserve(
        account, request_id, request_id, "load-study", amount, maximum, {}, maximum, maximum
    )


def test_thread_load_duplicate_requests_are_charged_once(offline, record_property):
    client, ledger, account, calls, *_ = offline
    body = request_body(client)
    started = time.monotonic()

    def invoke(index):
        return client.post(
            "/v1/requests", json={**body, "request_id": f"load-request-{index % 200:04}"}
        )

    with ThreadPoolExecutor(max_workers=24) as pool:
        responses = list(pool.map(invoke, range(800)))
    assert {r.status_code for r in responses} == {200}
    assert len(calls) == 200
    wallet = ledger.wallet(account["account_id"])
    assert wallet["available_microusd"] == 20_000_000 - 200 * 1250
    assert len(wallet["requests"]) == 200
    assert {r["state"] for r in wallet["requests"]} == {"completed"}
    record_property("http_requests", 800)
    record_property("concurrency", 24)
    record_property("elapsed_seconds", time.monotonic() - started)


def process_reservations(path, account, prefix, queue):
    ledger = Ledger(path)
    accepted = 0
    for i in range(80):
        try:
            _, fresh = reserve(ledger, account, f"{prefix}-{i}", maximum=50_000)
            accepted += fresh
        except ValueError:
            pass
    queue.put(accepted)


def test_process_load_cannot_exceed_shared_limits(tmp_path, record_property):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    account = ledger.register("load", "load-test-password")["account_id"]
    with ledger.transaction() as db:
        ledger.entry(db, account, 1_000_000, "test_fixture", "no-payment")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=process_reservations, args=(ledger.path, account, i, queue))
        for i in range(8)
    ]
    for process in processes:
        process.start()
    accepted = sum(queue.get(timeout=60) for _ in processes)
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert accepted == 50
    assert ledger.wallet(account)["available_microusd"] == 950_000
    record_property("attempts", 640)
    record_property("processes", 8)


def crash_transaction(path, account, operation, phase):
    ledger = Ledger(path)
    if phase == "before_commit":
        original = ledger.transaction

        @contextmanager
        def die_before_commit():
            with original() as db:
                yield db
                os._exit(73)

        ledger.transaction = die_before_commit
    if operation == "reserve":
        reserve(ledger, account, "crash-request")
    else:
        ledger.settle(account, "crash-request", 250, "completed", response={"safe": True})
    os._exit(73)


@pytest.mark.parametrize("operation", ["reserve", "settle"])
@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_process_death_preserves_transaction_atomicity(tmp_path, operation, phase):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    account = ledger.register("crash", "crash-test-password")["account_id"]
    with ledger.transaction() as db:
        ledger.entry(db, account, 10_000, "test_fixture", "no-payment")
    if operation == "settle":
        reserve(ledger, account, "crash-request")
    process = multiprocessing.get_context("spawn").Process(
        target=crash_transaction, args=(ledger.path, account, operation, phase)
    )
    process.start()
    process.join(20)
    assert process.exitcode == 73
    reopened = Ledger(ledger.path)
    wallet = reopened.wallet(account)
    expected = (
        (10_000 if phase == "before_commit" else 9000)
        if operation == "reserve"
        else (9000 if phase == "before_commit" else 9750)
    )
    assert wallet["available_microusd"] == expected
    with reopened.transaction() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        count = db.execute("SELECT COUNT(*) FROM entries WHERE kind='settlement'").fetchone()[0]
        assert count == int(operation == "settle" and phase == "after_commit")
    if wallet["requests"]:
        _, fresh = reserve(reopened, account, "crash-request")
        assert not fresh


def test_online_backup_during_writes_restores_consistent_accounting(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    account = ledger.register("backup", "backup-test-password")["account_id"]
    session = ledger.login("backup", "backup-test-password")
    with ledger.transaction() as db:
        ledger.entry(db, account, 1_000_000, "test_fixture", "no-payment")
    reserve(ledger, account, "ambiguous-request")
    ledger.ambiguous(account, "ambiguous-request")
    active = threading.Event()

    def writer():
        for i in range(150):
            reserve(ledger, account, f"backup-{i}")
            ledger.settle(account, f"backup-{i}", 250, "completed")
            if i == 10:
                active.set()
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert active.wait(10)
        restored = tmp_path / "restored.sqlite"
        with sqlite3.connect(ledger.path) as source, sqlite3.connect(restored) as target:
            source.backup(target, pages=1, sleep=0.001)
        future.result(timeout=30)
    clone = Ledger(restored)
    assert clone.authenticate(session["token"]) == account
    with clone.transaction() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        spent = db.execute("SELECT SUM(cost) FROM requests").fetchone()[0]
        assert clone.balance(db, account) + spent == 1_000_000
        assert (
            db.execute("SELECT state FROM requests WHERE id='ambiguous-request'").fetchone()[0]
            == "ambiguous"
        )
    _, fresh = reserve(clone, account, "ambiguous-request")
    assert not fresh
    clone.logout(session["token"])
    with pytest.raises(ValueError):
        clone.authenticate(session["token"])
    assert ledger.authenticate(session["token"]) == account


@pytest.mark.parametrize("bad", [None, 1, True, "wrong", {"name": "wrong"}])
def test_malformed_tool_containers_are_rejected_without_spending(offline, bad):
    client, ledger, account, calls, *_ = offline
    body = request_body(client)
    body["payload"]["tools"] = bad
    result = client.post("/v1/requests", json=body)
    assert result.status_code in {409, 422}, result.text
    assert not calls
    assert ledger.wallet(account["account_id"])["available_microusd"] == 20_000_000


@pytest.mark.parametrize(
    "change",
    [
        {"study_limit_microusd": True},
        {"maximum_microusd": -1},
        {"maximum_microusd": 1.2},
        {"share_context": False},
        {"share_context": 1},
        {"request_id": "../../escape"},
        {"tariff_hash": "0" * 64},
        {"unexpected": "PRIVATE_PASSWORD_SENTINEL"},
    ],
)
def test_tampered_contracts_do_not_call_provider(offline, change):
    client, ledger, account, calls, *_ = offline
    result = client.post("/v1/requests", json={**request_body(client), **change})
    assert result.status_code in {409, 422}, result.text
    assert "PRIVATE_PASSWORD_SENTINEL" not in result.text
    assert not calls
    assert ledger.wallet(account["account_id"])["available_microusd"] == 20_000_000


def test_oversized_chunked_body_and_auth_boundaries(offline):
    client, _, _, calls, *_ = offline
    result = client.post("/v1/requests", content=(b"x" * 100000 for _ in range(3)))
    assert result.status_code == 413
    assert result.headers.get("cache-control") == "no-store"
    for method, path, body in [
        ("GET", "/v1/wallet", None),
        ("GET", "/v1/requests/unknown", None),
        ("POST", "/v1/requests", request_body(client)),
        ("POST", "/v1/privacy/delete-outputs", {}),
    ]:
        for token in ["", "Bearer forged", "Basic forged"]:
            result = client.request(method, path, json=body, headers={"Authorization": token})
            assert result.status_code == 401
    assert not calls


def test_plaintext_credentials_and_prompts_absent_from_database(offline):
    client, ledger, account, _, _, config, session = offline
    assert client.post("/v1/requests", json=request_body(client)).status_code == 200
    with sqlite3.connect(ledger.path) as db:
        content = "\n".join(db.iterdump())
    for secret in [
        "OFFLINE_PASSWORD_LONG",
        account["recovery_code"],
        session["token"],
        "PRIVATE_PROMPT_SENTINEL",
        config.openai_key,
        config.stripe_key,
        config.webhook_secret,
    ]:
        assert secret not in content
    assert ledger.path.stat().st_mode & 0o777 == 0o600


def test_auth_throttling_survives_restart(offline):
    client, ledger, *_ = offline
    for _ in range(15):
        assert (
            client.post(
                "/v1/sessions", json={"username": "unknown", "password": "wrong-password"}
            ).status_code
            == 409
        )
    assert (
        "Too many attempts"
        in client.post(
            "/v1/sessions", json={"username": "unknown", "password": "wrong-password"}
        ).text
    )
    with pytest.raises(ValueError, match="Too many"):
        Ledger(ledger.path).throttle("auth-user:unknown", 15)


def test_login_racing_recovery_cannot_create_an_old_password_session(tmp_path, monkeypatch):
    import venturi.managed_ledger as module

    ledger = Ledger(tmp_path / "ledger.sqlite")
    account = ledger.register("race-user", "original-password")
    password_checked = threading.Event()
    recovered = threading.Event()
    original = module.password_hash

    def pause_old_password(password, salt):
        result = original(password, salt)
        if password == "original-password":
            password_checked.set()
            assert recovered.wait(10)
        return result

    monkeypatch.setattr(module, "password_hash", pause_old_password)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(ledger.login, "race-user", "original-password")
        assert password_checked.wait(10)
        ledger.recover("race-user", account["recovery_code"], "replacement-password")
        recovered.set()
        with pytest.raises(ValueError):
            pending.result(10)


def test_same_account_request_replay_after_gateway_restart(offline):
    client, ledger, _, calls, transport, config, session = offline
    body = request_body(client)
    first = client.post("/v1/requests", json=body).json()
    payments = httpx.Client(transport=httpx.MockTransport(forbidden_payment))
    with TestClient(
        create_gateway(config, transport, payments),
        headers={"Authorization": "Bearer " + session["token"]},
    ) as restarted:
        assert restarted.post("/v1/requests", json=body).json() == first
        assert (
            restarted.post("/v1/requests", json={**body, "study": "tampered-study"}).status_code
            == 409
        )
    assert len(calls) == 1
    assert ledger.metrics()["unresolved"] == 0


@pytest.mark.parametrize("phase", ["provider_in_flight", "settled_before_reply"])
def test_killed_http_gateway_recovers_without_resending(offline, tmp_path, phase):
    client, ledger, account, _, _, _, session = offline
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    body = request_body(client)

    def start(mode):
        output = (tmp_path / f"gateway-{mode}.log").open("w")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("m4_gateway_process.py")),
                str(tmp_path),
                mode,
                str(port),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        output.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert process.poll() is None
            try:
                if httpx.get(url + "/health", timeout=0.2).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.03)
        process.kill()
        process.wait()
        pytest.fail("Offline gateway did not become ready.")

    headers = {"Authorization": "Bearer " + session["token"]}
    process = start(phase)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = pool.submit(
                httpx.post, url + "/v1/requests", json=body, headers=headers, timeout=15
            )
            deadline = time.monotonic() + 10
            while not (tmp_path / "crash-ready").exists() and time.monotonic() < deadline:
                time.sleep(0.03)
            assert (tmp_path / "crash-ready").exists()
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(5)
            with pytest.raises(httpx.HTTPError):
                response.result(5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    process = start("restarted")
    try:
        result = httpx.post(url + "/v1/requests", json=body, headers=headers).json()
        assert result["state"] == ("reserved" if phase == "provider_in_flight" else "completed")
        assert (tmp_path / "provider-calls").read_text().splitlines() == ["offline-call"]
        assert (
            ledger.wallet(account["account_id"])["available_microusd"]
            == 20_000_000 - result["cost"]
        )
        if phase == "provider_in_flight":
            with ledger.transaction() as db:
                db.execute("UPDATE requests SET created=created-181")
            ledger.reconcile(
                account["account_id"], body["request_id"], 0, "offline test: no paid provider"
            )
            assert ledger.wallet(account["account_id"])["available_microusd"] == 20_000_000
    finally:
        process.terminate()
        process.wait(10)


def test_sqlite_full_during_settlement_keeps_reservation(offline, monkeypatch):
    _, ledger, account, *_ = offline
    user = account["account_id"]
    reserve(ledger, user, "storage-full")
    original = ledger.transaction

    @contextmanager
    def constrained():
        with original() as db:
            count = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={count + 1}")
            yield db

    monkeypatch.setattr(ledger, "transaction", constrained)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        ledger.settle(user, "storage-full", 250, "completed", response={"text": "x" * 100_000})
    monkeypatch.setattr(ledger, "transaction", original)
    wallet = ledger.wallet(user)
    assert wallet["available_microusd"] == 20_000_000 - 1000
    assert wallet["requests"][0]["state"] == "reserved"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        123,
        {},
        [{"role": "user", "content": [{"type": []}]}],
        [{"role": "user", "content": [{"type": "input_file", "file_id": "file-secret"}]}],
        [{"type": "item_reference", "id": "provider-private-item"}],
    ],
)
def test_malformed_or_remote_inputs_never_leave_gateway(offline, bad):
    client, ledger, account, calls, *_ = offline
    body = request_body(client)
    body["payload"]["input"] = bad
    result = client.post("/v1/requests", json=body)
    assert result.status_code in {409, 422}
    assert not calls
    assert ledger.wallet(account["account_id"])["available_microusd"] == 20_000_000


def test_keyring_failure_revokes_new_session_without_plaintext_fallback(
    offline, tmp_path, monkeypatch
):
    from venturi.managed_client import ManagedClient, ManagedLogin

    client, ledger, _, _, _, config, _ = offline
    monkeypatch.setenv("VENTURI_MANAGED_URL", config.public_url)
    managed = ManagedClient(tmp_path / "desktop", client=client)

    class LockedStore:
        def set_password(self, *args):
            raise RuntimeError("locked")

    monkeypatch.setattr("venturi.assistant.secure_keyring", lambda: LockedStore())
    with ledger.transaction() as db:
        before = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    with pytest.raises(ValueError, match="credential storage failed"):
        managed.login(
            ManagedLogin(
                username="offline-user",
                password="OFFLINE_PASSWORD_LONG",
                remember=True,
                budget_usd=5.0,
                study_budget_usd=1.0,
                tariff_hash=client.get("/v1/catalog").json()["tariff_hash"],
            )
        )
    with ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before
    assert not managed.config()
    assert managed.token() is None
    assert not list((tmp_path / "desktop").rglob("*.json"))


def test_plaintext_keyring_backend_is_rejected(monkeypatch):
    import keyring

    from venturi.assistant import secure_keyring

    class PlaintextBackend:
        pass

    monkeypatch.setattr(keyring, "get_keyring", lambda: PlaintextBackend())
    with pytest.raises(ValueError, match="supported OS credential store"):
        secure_keyring()
