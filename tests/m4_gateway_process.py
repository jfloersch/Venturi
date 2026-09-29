"""Private test gateway: loopback only, offline provider, forbidden payments."""

import sys
import time
from pathlib import Path

import httpx
import uvicorn
from test_m4_resilience import forbidden_payment, proposal_response, settings

from venturi.gateway import create_gateway

folder, phase, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])


def provider(request):
    with (folder / "provider-calls").open("a") as output:
        output.write("offline-call\n")
    if phase == "provider_in_flight":
        (folder / "crash-ready").touch()
        time.sleep(60)
    return proposal_response()


app = create_gateway(
    settings(folder / "ledger.sqlite"),
    httpx.Client(base_url="https://offline.invalid/", transport=httpx.MockTransport(provider)),
    httpx.Client(transport=httpx.MockTransport(forbidden_payment)),
)
if phase == "settled_before_reply":
    settle = app.state.ledger.settle

    def settle_then_wait(*args, **kwargs):
        result = settle(*args, **kwargs)
        (folder / "crash-ready").touch()
        time.sleep(60)
        return result

    app.state.ledger.settle = settle_then_wait

uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, proxy_headers=False)
