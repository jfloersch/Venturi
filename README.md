# Venturi

A local CFD workbench with inspectable geometry, deterministic execution, and evidence attached to every result.

This checkout implements **milestone 0**, a compatibility workbench. It contains a real laminar pipe benchmark, a separate STEP-to-mesh boundary check, and a Tauri/React desktop shell. The checks use provisional fixture-specific criteria. This is not yet a general-purpose or qualified engineering application.

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

Closing a browser tab does not cancel a worker job. Closing the native window ends its launcher session. Stopping the launcher stops its worker and cancels active work while preserving artifacts. Worker restart marks unfinished jobs interrupted; resuming solver iterations is a later milestone.

## Headless checks

```bash
uv run venturi reference
uv run venturi mesh-spike
uv run venturi inspect fixtures/pipe.step --output artifacts/geometry.json
uv run pytest -m 'not integration'
VENTURI_INTEGRATION=1 uv run pytest
```

Each run writes `result.json`, `report.html`, solver logs, and a native case. Reference runs also contain pressure-history plots, CSV metrics, and exported VTK fields. Output directories are never silently reused.

See [the testing guide](docs/testing.md), [milestone acceptance and limitations](docs/milestone-0.md), and [architecture decisions](docs/architecture.md). Cross-platform CI definitions are included; a CI definition is not evidence that its jobs have passed. Native Windows/macOS qualification remains recorded separately from the Linux/WSL tests.
