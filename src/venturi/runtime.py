"""A narrow, serial OpenFOAM adapter. No user-supplied shell commands."""

import importlib.metadata
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import PROTOCOL_VERSION, __version__
from .models import ResourcePolicy, file_hash, write_json

ALLOWED = {"blockMesh", "checkMesh", "snappyHexMesh", "foamRun", "foamToVTK"}


def find_foam_root() -> Path | None:
    candidates = [os.environ.get("VENTURI_FOAM_ROOT"), "/opt/openfoam14"]
    candidates += [
        str(parent / ".tools/runtime/opt/openfoam14")
        for parent in [Path.cwd(), *Path.cwd().parents]
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "etc/bashrc").is_file():
            return Path(candidate).resolve()
    return None


def foam_environment(root: Path) -> tuple[Path, dict[str, str]]:
    # The M0 worker is serial. The packaged dummy Pstream avoids an MPI launcher.
    variants = list((root / "platforms").glob("linux*GccDPInt32Opt"))
    if len(variants) != 1:
        raise RuntimeError("Expected one double-precision, 32-bit-label Linux OpenFOAM build.")
    build = variants[0]
    lib = build / "lib"
    env = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    env.update(
        {
            "WM_PROJECT_DIR": str(root),
            "WM_PROJECT_VERSION": "14",
            "WM_PROJECT": "OpenFOAM",
            "FOAM_ETC": str(root / "etc"),
            "FOAM_LIBBIN": str(lib),
            "FOAM_APPBIN": str(build / "bin"),
            "FOAM_SIGFPE": "true",
            "OMP_NUM_THREADS": "1",
        }
    )
    extra = root.parent.parent / "usr/lib/x86_64-linux-gnu"
    env["LD_LIBRARY_PATH"] = ":".join([str(lib / "dummy"), str(lib), str(extra)])
    env["PATH"] = str(build / "bin") + os.pathsep + env.get("PATH", "")
    return build, env


def diagnostics() -> dict:
    root = find_foam_root()
    checks = []
    cad = None
    try:
        cad = importlib.metadata.version("cadquery-ocp")
        from OCP.STEPControl import STEPControl_Reader  # noqa: F401

        checks.append(
            {"name": "STEP geometry", "status": "pass", "detail": f"Open CASCADE bindings {cad}"}
        )
    except (ImportError, importlib.metadata.PackageNotFoundError) as e:
        checks.append(
            {"name": "STEP geometry", "status": "fail", "detail": f"Install the cad extra: {e}"}
        )
    available = False
    runtime = None
    if root:
        try:
            build, env = foam_environment(root)
            run = subprocess.run(
                [str(build / "bin/foamRun"), "-help"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            available = run.returncode == 0 and "OpenFOAM-14" in (run.stdout + run.stderr)
            if available:
                build_label = re.search(r"^Build:\s*(.+)$", run.stdout + run.stderr, re.MULTILINE)
                runtime = {
                    "distribution": "OpenFOAM Foundation",
                    "version": "14",
                    "build": build_label.group(1).strip() if build_label else "unreported",
                    "platform_build": build.name,
                    "foam_run_sha256": file_hash(build / "bin/foamRun"),
                }
            detail = (
                "OpenFOAM Foundation 14 (serial worker)"
                if available
                else (run.stdout + run.stderr)[-1200:]
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
            detail = str(e)
    else:
        detail = "OpenFOAM 14 not found. Run scripts/bootstrap_worker.py or set VENTURI_FOAM_ROOT."
    checks.append(
        {"name": "Simulation runtime", "status": "pass" if available else "fail", "detail": detail}
    )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "app_version": __version__,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "is_wsl": "microsoft" in platform.release().lower(),
        "python": platform.python_version(),
        "cad_version": cad,
        "solver_ready": available,
        "runtime": runtime,
        "checks": checks,
    }


class RunCancelled(RuntimeError):
    pass


class ResourceExceeded(RuntimeError):
    pass


def process_identity(pid: int) -> dict | None:
    """Boot + start ticks prevent accidentally acting on a reused Linux PID."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "pid": pid,
            "start_ticks": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, IndexError):
        return None


def process_alive(identity: dict | None) -> bool:
    return bool(identity and process_identity(identity["pid"]) == identity)


def terminate_group(identity: dict | None) -> None:
    if process_alive(identity):
        try:
            os.killpg(identity["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def event(folder: Path, kind: str, **details) -> None:
    stamp = time.time_ns()
    write_json(
        folder / "events" / f"{stamp}.json",
        {
            "time": datetime.now(UTC).isoformat(),
            "event": kind,
            **details,
        },
    )


@dataclass
class Execution:
    folder: Path
    policy: ResourcePolicy
    deadline: float

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Attempt exceeded its total wall-time limit.")
        size = 0
        for path in self.folder.rglob("*"):
            try:
                if path.is_file():
                    size += path.stat().st_size
            except FileNotFoundError:
                continue  # Atomic control-file replacement can race this inventory.
        if size > self.policy.disk_mb * 1024 * 1024:
            raise ResourceExceeded(f"Attempt exceeded its {self.policy.disk_mb} MiB disk limit.")


_execution: ContextVar[Execution | None] = ContextVar("execution", default=None)


@contextmanager
def execution_context(folder: Path, policy: ResourcePolicy):
    token = _execution.set(Execution(folder, policy, time.monotonic() + policy.wall_time_seconds))
    try:
        yield
    finally:
        _execution.reset(token)


def check_execution(cancel=None):
    if cancel is not None and cancel.is_set():
        raise RunCancelled("Cancelled by the user; partial evidence was preserved.")
    if context := _execution.get():
        context.check()


def run_tool(
    name: str,
    case: Path,
    timeout: float,
    cancel: threading.Event | None = None,
    args: tuple[str, ...] = (),
) -> Path:
    with tool_case_path(case) as execution_case:
        return _run_tool(name, case, timeout, cancel, args, execution_case)


@contextmanager
def tool_case_path(case: Path):
    resolved = case.resolve()
    if re.fullmatch(r"[A-Za-z0-9_./+-]+", str(resolved)):
        yield resolved
    else:
        # OpenFOAM's fileName parser rejects spaces and non-ASCII even with argv.
        # An ASCII symlink keeps every generated artifact in the user's folder.
        with tempfile.TemporaryDirectory(prefix="venturi-case-", dir="/tmp") as temporary:
            alias = Path(temporary) / "case"
            alias.symlink_to(resolved, target_is_directory=True)
            yield alias


def _run_tool(name, case, timeout, cancel, args, execution_case):
    if name not in ALLOWED:
        raise ValueError("Tool is not in the worker allowlist.")
    check_execution(cancel)
    root = find_foam_root()
    if root is None:
        raise RuntimeError("OpenFOAM 14 is unavailable. Run the worker setup first.")
    build, env = foam_environment(root)
    logs = case.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{name}.log"
    deadline = time.monotonic() + timeout
    context = _execution.get()
    command = [str(build / "bin" / name), "-case", str(execution_case), *args]
    record_path = case.parent / "commands" / f"{time.time_ns()}-{name}.json"
    record = {
        "tool": name,
        "argv": [name, "-case", "case", *args],
        "executable_sha256": file_hash(build / "bin" / name),
        "timeout_seconds": timeout,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "starting",
    }
    write_json(record_path, record)
    if sys.platform.startswith("linux"):
        command = [
            sys.executable,
            str(Path(__file__).with_name("process.py")),
            str(os.getpid()),
            str(context.policy.memory_mb if context else 0),
            *command,
        ]
    with log.open("w") as output:
        proc = subprocess.Popen(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        record.update(status="running", process=process_identity(proc.pid))
        write_json(record_path, record)
        event(case.parent, "tool_started", tool=name, process=record["process"])
        try:
            while proc.poll() is None:
                check_execution(cancel)
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"{name} exceeded its {timeout:g}s limit. Partial evidence was preserved."
                    )
                time.sleep(0.1)
            if proc.returncode != 0:
                tail = log.read_text(errors="replace")[-2200:]
                raise RuntimeError(f"{name} exited with status {proc.returncode}.\n{tail}")
            check_execution(cancel)
            record["status"] = "completed"
        except BaseException as exc:
            record.update(status="stopped", error=str(exc))
            raise
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            record.update(returncode=proc.returncode, finished_at=datetime.now(UTC).isoformat())
            write_json(record_path, record)
            event(
                case.parent,
                "tool_finished",
                tool=name,
                status=record["status"],
                returncode=proc.returncode,
            )
    return log
