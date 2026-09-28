# Venturi

A local CFD workbench with inspectable geometry, deterministic execution, and evidence attached to every result.

This checkout implements **milestone 2, the guided desktop alpha**: prepared
STEP → annotated references → saved study and resource plan → reviewed mesh →
laminar flow → inspectable results, native case and export. No AI account is needed.
The milestone-1 analytical pipe reference remains available. Numerical criteria
are provisional; independent CFD review and native Windows/macOS qualification
remain pending.

See the [milestone 2 implementation and pilot guide](docs/milestone-2.md),
[measured acceptance](docs/milestone-2-closeout.md),
[testing guide](docs/testing.md), and [milestone 1 numerical closeout](docs/milestone-1-closeout.md).

## Try it on Ubuntu 22.04 / WSL2

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node.js 22.12+ **inside Linux**. uv installs the project's Python 3.12 interpreter. The CAD bindings also require Linux graphics libraries, even when running the worker without a display. The native desktop additionally needs Rust and the [Tauri Linux prerequisites](https://v2.tauri.app/start/prerequisites/). Browser mode does not need Rust.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates libstdc++6 libgomp1 libgl1 libxrender1
uv python install 3.12
uv run --no-project --python 3.12 python scripts/bootstrap_worker.py
uv sync --locked --extra cad --extra dev
cd apps/desktop
npm ci
cd ../..
uv run venturi doctor
uv run python scripts/dev.py
```

The launcher starts an authenticated loopback worker and opens the browser workbench. Import a prepared STEP fluid volume, assign its inlet/outlets, review fluid properties and mesh size, build the mesh, then approve the saved study and solve. The reference geometry also offers the original CAD boundary check and analytical pipe reference. Results, logs, native cases, and reports are kept in `artifacts/workbench`. Use **Export complete run** to download a portable ZIP.

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

## Guided desktop alpha

1. **Import STEP** representing the enclosed space occupied by fluid. Review the
   declared source units and converted bounding dimensions. Orbit, pan (middle
   drag), zoom, isolate selected references, show CAD edges, or move an X/Y/Z cut.
2. Select faces in the viewer/list and assign one inlet and one to four outlets.
   Save the assignments. Select a face or edge and save a labeled annotation;
   clicking the saved note restores its reference and camera.
3. Enter the engineering question, inlet flow (m³/s or L/min), density, viscosity,
   property source, and cell size. Fluid properties are suggestions to confirm,
   and the inlet flow starts blank. Set elapsed-time, memory, disk and mesh-cell
   limits. Collaborative/Expert presentation also exposes recipe-bounded iteration
   and tool-time limits; all profiles use the same checks.
4. **Save study** retains a named revision in the worker. Use the saved-study list
   to reopen its geometry, ports, inputs and presentation after restarting the UI
   or API. Unsaved form drafts stay in the browser. Opening a saved study restores
   the saved revision; use **New study on this geometry** for a separate study.
5. **Review study**, read the applicability checks and provisional cell estimate,
   and confirm the study and resource plan. **Build mesh for review**, inspect
   the rendered mesh and checks, then **Approve saved study & solve**. Changed
   draft inputs mark earlier results historical and block approval until the
   original saved inputs are reopened or a new mesh is reviewed.
6. Review pressure loss, each outlet's flow split, convergence, checks and
   limitations. Expand **Inspect native case, logs & downloads** to browse actual
   files, compare the same file across runs, or download CSV, plots and reports.
   **Export complete run** includes native inputs/fields and frozen study context
   and annotations. The CLI can verify and replay the archive.

The worker runs one serial attempt per explicit action. Budget exhaustion stops
execution and preserves a failure report. No automatic retries or AI spending
occur. Closing the UI does not cancel a job; **Cancel run** does. An API outage
shows a disconnected state and automatically reconnects without resubmission.

## Milestone 1b: prepared STEP to report

In the desktop, choose **Import STEP**, assign one planar inlet and one to four
planar outlets, and save. Every remaining face is a wall. Enter inlet volumetric
flow, density, viscosity and cell size, choose **Review study**, confirm the plan, then choose **Build mesh for review**.
Inspect the actual mesh, port mapping checks and saved study before choosing
**Approve saved study & solve**. Results show pressure loss, per-outlet flow and
split, numerical checks, and a portable export. Reloading restores selections,
study drafts and recorded attempts.

For a checked-in two-outlet example through the same CLI workflow:

```bash
uv run venturi mesh fixtures/internal/manifold.study.json --step fixtures/internal/manifold.step
# Review the printed run's report.html, mesh-audit.json and mesh-preview.json.
uv run venturi solve <mesh-run-id> --approve-mesh <exact-mesh-hash>
uv run venturi export artifacts/workbench/runs/<flow-run-id> --output artifacts/manifold.zip
uv run venturi reproduce artifacts/manifold.zip --output artifacts/manifold-replayed
```

For your own geometry, `venturi import-step your-fluid.step` prints face IDs,
zero-based indices, areas, normals and centroids. `venturi flow-template` takes
explicit face IDs (or **one-based** face numbers), e.g.:

```bash
uv run venturi import-step your-fluid.step
uv run venturi flow-template your-fluid.step --inlet <face-id> --outlet <face-id> \
  --flow-rate 1e-7 --cell-size 0.0005 --output artifacts/your-study.json
uv run venturi validate artifacts/your-study.json --step your-fluid.step
uv run venturi mesh artifacts/your-study.json
```

Repeat `--outlet` for additional ports. All study quantities use SI units; only
the desktop cell-size input displays millimetres. Source units must be declared.
The supported input is one valid, watertight solid representing the **fluid**,
up to 16 MiB/256 faces, with planar simply connected ports. Solid parts,
assemblies, open surfaces and automatic fluid extraction are unsupported.

`laminar-internal/1` fixes uniform normal inlet flow, equal zero-gauge outlet
pressures, no-slip walls and steady incompressible laminar physics. It screens
full-flow port Reynolds ≤200 and ≥10 cells per port hydraulic diameter. Geometry
and strict mesh checks can still reject an input inside these preliminary bounds;
reduce the declared cell size or repair the geometry and review a new attempt.
The supplied manifold needs 0.625 mm cells; its 0.8 mm attempt is a retained mesh
quality rejection. No failed mesh is solved. Every approval binds the saved study,
source, recipe, runtime and exact mesh hash. Edited settings need a new mesh review.

Flow exports retain the STEP, face assignments, approved mesh bundle, study,
recipe, native case, final fields, logs, plots, CSV and hashes. Replay regenerates
and compares native inputs before executing, then checks pressure and every
outlet flow. Default internal-flow limits are 2,400 seconds per attempt, 4 GiB
virtual memory per tool process, and a 2 GiB polled disk budget. Results are
numerical checks, not experimental validation or design certification.

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

See [the testing guide](docs/testing.md), [1b measured acceptance](docs/milestone-1b-closeout.md), [milestone-1a acceptance and limitations](docs/milestone-1a.md), and [architecture decisions](docs/architecture.md). Cross-platform CI definitions are included; a CI definition is not evidence that its jobs have passed. Native Windows/macOS qualification remains recorded separately from the Linux/WSL tests.
