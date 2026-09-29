"""Sustained loopback HTTP acceptance with no payment or external model transport."""

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_m4_resilience import request_body  # noqa: E402

from venturi.managed_ledger import Ledger  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    ledger = Ledger(folder / "ledger.sqlite")
    account = ledger.register("load-user", "load-only-password")["account_id"]
    session = ledger.login("load-user", "load-only-password")
    with ledger.transaction() as db:
        ledger.entry(db, account, 20_000_000, "test_fixture", "no-payment")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (folder / "server.log").open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests/m4_gateway_process.py"),
                str(folder),
                "load",
                str(port),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        with httpx.Client(
            base_url=url, timeout=10, headers={"Authorization": "Bearer " + session["token"]}
        ) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Loopback gateway exited.")
                try:
                    if client.get("/health").is_success:
                        break
                except httpx.HTTPError:
                    time.sleep(0.05)
            body = request_body(client)
            barrier = threading.Barrier(16)
            started = time.monotonic()

            def worker(index):
                timings, failures, request_ids = [], [], set()
                barrier.wait()
                iteration = 0
                while time.monotonic() - started < args.seconds:
                    # Every second delivery repeats the previous request ID.
                    request_id = f"soak-{index:02}-{iteration // 2:06}"
                    began = time.monotonic()
                    response = client.post("/v1/requests", json={**body, "request_id": request_id})
                    timings.append(time.monotonic() - began)
                    if response.status_code != 200 or response.json().get("state") != "completed":
                        failures.append({"code": response.status_code, "body": response.text[:200]})
                    request_ids.add(request_id)
                    iteration += 1
                    time.sleep(max(0, 0.25 - (time.monotonic() - began)))
                return timings, failures, request_ids

            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(worker, range(16)))
            latencies = sorted(t for times, _, _ in results for t in times)
            failures = [f for _, errors, _ in results for f in errors]
            unique = set().union(*(ids for _, _, ids in results))
            provider_calls = len((folder / "provider-calls").read_text().splitlines())
            wallet = ledger.wallet(account)
            with ledger.transaction() as db:
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                rows = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
                charges = db.execute("SELECT SUM(cost) FROM requests").fetchone()[0]
            report = {
                "elapsed_seconds": time.monotonic() - started,
                "concurrency": 16,
                "http_requests": len(latencies),
                "unique_requests": len(unique),
                "provider_calls": provider_calls,
                "failures": failures,
                "p50_seconds": latencies[len(latencies) // 2],
                "p95_seconds": latencies[int(len(latencies) * 0.95)],
                "p99_seconds": latencies[int(len(latencies) * 0.99)],
                "ledger_requests": rows,
                "cost_microusd": charges,
                "available_microusd": wallet["available_microusd"],
                "integrity": integrity,
                "external_model_calls": 0,
                "payment_calls": 0,
            }
            report["passed"] = (
                not failures
                and rows == provider_calls == len(unique)
                and charges == len(unique) * 1250
                and wallet["available_microusd"] + charges == 20_000_000
                and integrity == "ok"
            )
            (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1
    finally:
        process.terminate()
        process.wait(10)


if __name__ == "__main__":
    raise SystemExit(main())
