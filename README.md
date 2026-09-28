# Venturi

A local CFD workbench with inspectable geometry, deterministic execution, and evidence attached to every result.

This checkout implements **milestone 1a**, a reproducible laminar-pipe reference workflow with versioned studies, persistent attempts, deterministic checks, and verified native exports. It also retains the milestone-0 STEP-to-mesh boundary check and Tauri/React workbench. Criteria remain provisional and fixture-specific; independent CFD review and native platform qualification are pending.

## Try it on Ubuntu 22.04 / WSL2

Prerequisites: Python 3.11+ for the setup script, [uv](https://docs.astral.sh/uv/), and Node.js 22.12+ **inside Linux**. The native desktop additionally needs Rust and the [Tauri Linux prerequisites](https://v2.tauri.app/start/prerequisites/). Browser mode does not need Rust.

```bash
python3 scripts/bootstrap_worker.py
uv sync --locked --extra cad --extra dev
cd apps/desktop
npm ci
cd ../..
uv run venturi doctor
uv run python scripts/dev.py
```

The launcher starts an authenticated loopback worker and opens the browser workbench. Select faces, save boundary roles, run the CAD boundary check, then run the pipe reference. Results, logs, native cases, and reports are kept in `artifacts/workbench`. Use **Export complete run** to download a portable ZIP.

For the native Tauri window:

```bash
uv run python scripts/dev.py --desktop
```

Once the desktop has been built, launch it without rebuilding:

```bash
uv run python scripts/dev.py --desktop --built
```

The compiled executable is created by running `npm run tauri build -- --no-bundle` in `apps/desktop`.

Jobs run in independent local processes. Closing the browser/native UI or stopping/restarting the API does not stop an active job. Reopening reconnects to its saved state. Use **Cancel run** or `uv run venturi cancel <run-id>` to stop it. A dead runner is recorded as interrupted with a failure report; an explicit retry creates a new attempt from iteration zero. There is no automatic solver resume.

## Milestone 1a: study to verified report

With the worker installed, this one command runs the checked-in study and prints
its attempt directory and report location:

```bash
uv run venturi run fixtures/pipe-study.json
```

For a custom bounded pipe experiment, generate and edit a versioned study. All
quantities use SI units; the recipe fixes the physics and acceptance criteria.

```bash
uv run venturi study-template --output artifacts/my-study.json
uv run venturi validate artifacts/my-study.json
uv run venturi run artifacts/my-study.json --request-id my-first-study
uv run venturi status
```

Runs are under `artifacts/workbench/runs/<run-id>`. The CLI and desktop share this
store. `--detach` returns immediately; repeating the same request ID and inputs
returns the original attempt. For cancellation or a new explicit attempt:

```bash
uv run venturi cancel <run-id>
uv run venturi retry <run-id> --reason "Retry after worker interruption"
```

Export and reproduce a completed reference in a **new** directory:

```bash
uv run venturi export artifacts/workbench/runs/<run-id> --output artifacts/reference.zip
uv run venturi verify artifacts/reference.zip
uv run venturi reproduce artifacts/reference.zip --output artifacts/reproduced
```

Reproduction checks the original runtime fingerprint, compares exported native
inputs with the deterministic compiler, reruns those verified inputs, and compares
the pressure result. Modified native dictionaries are refused. Hash verification
checks file integrity, not publisher identity. Output folders/archives are never
silently overwritten. Paths containing spaces and Unicode are supported through
a temporary ASCII solver alias.

Each attempt retains its study and recipe, status, event journal, commands,
runtime/source hashes, native inputs, and a terminal report—even when execution
fails or is cancelled. Successful references add CSV, pressure history, and VTK.
The default resource policy is 900 seconds total elapsed execution time, 4 GiB
virtual address space per tool process, and a 1 GiB polled attempt-disk budget.
This is a bounded trusted worker, not a general process/container sandbox.

## Headless checks

```bash
uv run venturi reference
uv run venturi mesh-spike
uv run venturi inspect fixtures/pipe.step --output artifacts/geometry.json
uv run pytest -m 'not integration'
VENTURI_INTEGRATION=1 uv run pytest
```

Every terminal attempt writes `result.json` and `report.html`. Logs and native files are retained whenever execution reached those stages. Successful references also contain pressure-history plots, CSV metrics, and exported VTK fields. Output directories are never silently reused.

See [the testing guide](docs/testing.md), [milestone-1a acceptance and limitations](docs/milestone-1a.md), and [architecture decisions](docs/architecture.md). Cross-platform CI definitions are included; a CI definition is not evidence that its jobs have passed. Native Windows/macOS qualification remains recorded separately from the Linux/WSL tests.
