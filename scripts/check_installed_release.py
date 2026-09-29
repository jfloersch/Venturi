#!/usr/bin/env python3
"""Verify the installed worker from outside the checkout; retain no session secret."""

import argparse
import json
import os
import signal
import subprocess
import tempfile
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18764)
    args = parser.parse_args()
    root = args.installation.resolve()
    executable = root / "current/bin/python"
    code = "from pathlib import Path; from venturi.desktop_host import connect; import json,sys; print(json.dumps(connect(Path(sys.argv[1]), int(sys.argv[2]))))"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "VENTURI_API_TOKEN", "VENTURI_FOAM_ROOT"}
    }
    with tempfile.TemporaryDirectory(prefix="venturi-installed-check-") as temporary:

        def connect():
            return json.loads(
                subprocess.check_output(
                    [str(executable), "-c", code, str(root), str(args.port)],
                    cwd=temporary,
                    env=env,
                    text=True,
                )
            )

        connection = connect()
        try:

            def get(path):
                request = urllib.request.Request(
                    connection["url"] + "/v1" + path,
                    headers={"Authorization": "Bearer " + connection["token"]},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)

            session = get("/session")
            diagnostics = get("/diagnostics")
            geometry = get("/geometry")
            support = get("/support/preview")
            assert diagnostics["solver_ready"]
            assert all(c["status"] == "pass" for c in diagnostics["checks"])
            assert geometry["faces"]
            assert connect() == connection
            assert root.joinpath("worker-session.json").stat().st_mode & 0o777 == 0o600
            assert connection["token"] not in json.dumps(support)
            result = {
                "status": "passed",
                "app_version": session["app_version"],
                "diagnostics": diagnostics,
                "bundled_fixture_faces": len(geometry["faces"]),
                "reconnected_same_session": True,
                "session_file_mode": "0600",
                "source_checkout_required": False,
                "support_export_excludes_session": True,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
            print(
                "Installed startup, authentication, CAD fixture, diagnostics and reconnection passed."
            )
        finally:
            # The helper's recorded identity is our isolated acceptance worker.
            record = json.loads((root / "worker-session.json").read_text())
            identity = record.get("process")
            if identity:
                stat = Path(f"/proc/{identity['pid']}/stat")
                if (
                    stat.exists()
                    and stat.read_text().rsplit(")", 1)[1].split()[19] == identity["start_ticks"]
                ):
                    os.kill(identity["pid"], signal.SIGTERM)


if __name__ == "__main__":
    main()
