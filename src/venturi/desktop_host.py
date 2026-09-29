"""Start/reconnect the installed loopback worker; print its private connection JSON."""

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import PROTOCOL_VERSION, __version__
from .jobs import file_lock, read_json
from .models import write_json
from .runtime import process_identity


def installation_root():
    return Path(
        os.environ.get(
            "VENTURI_INSTALL_DIR",
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "venturi",
        )
    )


def connect(root=None, port=8765):
    root = root or installation_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    url = f"http://127.0.0.1:{port}"
    session = root / "worker-session.json"

    def reachable(token):
        try:
            request = urllib.request.Request(
                url + "/v1/session", headers={"Authorization": "Bearer " + token}
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                value = json.load(response)
                return (
                    value.get("protocol_version") == PROTOCOL_VERSION
                    and value.get("app_version") == __version__
                )
        except (OSError, ValueError):
            return False

    with file_lock(root / ".host.lock"):
        if session.exists():
            previous = read_json(session)
            if reachable(previous["token"]):
                return {"url": url, "token": previous["token"]}
        try:
            urllib.request.urlopen(url + "/v1/health", timeout=1)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                "Port 8765 belongs to another service. Stop it or connect manually."
            ) from exc
        except OSError:
            pass
        else:
            raise ValueError(
                "An existing worker uses another session. Connect manually or stop it before starting the installed worker."
            )
        token = secrets.token_urlsafe(32)
        connection = {"url": url, "token": token}
        write_json(session, connection)
        session.chmod(0o600)
        env = os.environ.copy()
        env["VENTURI_API_TOKEN"] = token
        solver = root / "current/solver/runtime/opt/openfoam14"
        if not solver.exists():
            solver = root / "solver/runtime/opt/openfoam14"  # Earlier installed releases.
        env["VENTURI_FOAM_ROOT"] = str(solver)
        if (root / "managed-url").exists():
            env["VENTURI_MANAGED_URL"] = (root / "managed-url").read_text().strip()
        with (root / "worker.log").open("a") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "venturi.cli",
                    "serve",
                    "--storage",
                    str(root / "projects"),
                    "--port",
                    str(port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
                cwd=root,
            )
        write_json(
            session,
            {**connection, "process": process_identity(process.pid), "app_version": __version__},
        )
        session.chmod(0o600)
        for _ in range(80):
            if reachable(token):
                return connection
            if process.poll() is not None:
                raise ValueError(
                    "Worker startup failed. Inspect the local worker log in the installation folder."
                )
            time.sleep(0.25)
        raise ValueError("Worker startup timed out. Check local diagnostics before retrying.")


def main():
    os.umask(0o077)
    try:
        print(json.dumps(connect()))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
