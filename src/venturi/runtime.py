"""A narrow, serial OpenFOAM adapter. No user-supplied shell commands."""

import importlib.metadata
import os
import platform
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import PROTOCOL_VERSION, __version__
from .models import file_hash

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
    env = os.environ.copy()
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
    env["LD_LIBRARY_PATH"] = ":".join(
        [str(lib / "dummy"), str(lib), str(extra), env.get("LD_LIBRARY_PATH", "")]
    )
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


def run_tool(
    name: str,
    case: Path,
    timeout: float,
    cancel: threading.Event | None = None,
    args: tuple[str, ...] = (),
) -> Path:
    if name not in ALLOWED:
        raise ValueError("Tool is not in the worker allowlist.")
    root = find_foam_root()
    if root is None:
        raise RuntimeError("OpenFOAM 14 is unavailable. Run the worker setup first.")
    build, env = foam_environment(root)
    logs = case.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{name}.log"
    deadline = time.monotonic() + timeout
    with log.open("w") as output:
        proc = subprocess.Popen(
            [str(build / "bin" / name), "-case", str(case.resolve()), *args],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise RunCancelled("Cancelled by the user; partial evidence was preserved.")
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"{name} exceeded its {timeout:g}s limit. Partial evidence was preserved."
                    )
                time.sleep(0.1)
            if proc.returncode != 0:
                tail = log.read_text(errors="replace")[-2200:]
                raise RuntimeError(f"{name} exited with status {proc.returncode}.\n{tail}")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
    return log
