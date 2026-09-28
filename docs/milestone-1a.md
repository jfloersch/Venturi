# Milestone 1a — trustworthy reference result

## Acceptance contract

Baseline: milestone 0, commit `26f8e40`. Target: Ubuntu 22.04 x86_64 / WSL2,
OpenFOAM Foundation 14. This milestone hardens the prescribed laminar pipe
reference; numerical criteria remain provisional pending independent CFD review.

1. A versioned, strictly validated study identifies the physics, requested
   quantity, SI inputs, recipe version, and resource limits. Unsupported versions,
   unknown fields, invalid units/values, and out-of-envelope physics stop before
   execution. Canonical hashes bind every attempt to frozen inputs.
2. A versioned deterministic recipe declares boundaries, assumptions, compiler,
   applicability and acceptance criteria. The existing 0.32000 Pa analytical
   reference and thresholds are unchanged; callers cannot loosen thresholds.
3. CLI and authenticated worker API use the same persistent attempt machinery.
   Requests are recorded before launch; duplicate requests cannot duplicate work.
   Jobs survive UI/API restarts. Cancellation and deadlines stop child processes.
   Dead runners are reconciled as interrupted, with partial artifacts and a report.
   Retrying creates a new attempt and retains its parent and reason; no automatic
   solver continuation or numerical recovery is promised.
4. Every attempt retains its frozen study/recipe, chronological events, commands,
   process identity, environment and executable/source hashes, mesh/input hashes,
   logs, typed checks, and terminal outcome. Resource limits are enforced for the
   local Linux execution path; this is not an arbitrary-code sandbox.
5. Successful runs include pressure/flow/residual evidence, CSV, convergence plot,
   native case and VTK fields. Missing, non-finite, inconsistent or incomplete
   evidence must never yield a pass. Failed/cancelled/interrupted runs have an
   understandable report and retained logs.
6. A portable ZIP contains a manifest covering the result, report, native files
   and provenance. Verification rejects changed/missing files. Reproduction uses
   verified, generated native inputs in a fresh directory, checks the pinned
   runtime, and compares the result within declared tolerance without an AI call.
7. Tests exercise real solver/mesher execution, export and native replay, invalid
   studies, false passes, concurrent/idempotent requests, cancellation, resource
   stops, API restart and runner death. Existing desktop workflows remain usable.

## Scope boundaries

Supported studies describe the prescribed straight circular pipe with normalized
parabolic inlet, no-slip walls, zero-gauge-pressure outlet, constant Newtonian
properties, steady incompressible laminar flow and a single grid. User-specified
pipe parameters inside the recipe's bounded envelope are provisional benchmark
experiments, not qualified engineering coverage. STEP-to-flow is milestone 1b.

Native Windows/macOS/ARM qualification, a reviewed container image, independent
CFD review, pilot discovery, installers, RANS, AI and billing remain separately
tracked. They do not prevent implementation or local testing of this contract.

## Verification results

**Status: implemented and locally verified, 2026-09-27. Worker/application version: 0.1.0.**

The subsequent [development closeout](milestone-1a-closeout.md) completed a fresh
Ubuntu userspace installation/replay, 72 Python tests, native Linux GUI lifecycle
and export checks, and eight physical/grid cases. It records the setup/test fixes
and the decision to proceed with milestone 1b development. The table below records
the original implementation acceptance run.

| Check | Measured outcome |
|---|---|
| Python acceptance suite | **70 passed**, including real CLI solve/export/replay and CAD meshing |
| Browser acceptance | **3 scenarios passed**: authentication; real geometry/solve/reload/export; cancellation/retry/provenance |
| Static checks | Ruff lint/format, frontend TypeScript/production build, Prettier, Rust formatting and Git whitespace checks passed |
| Native Linux desktop | Release binary built successfully; no new Windows/macOS/ARM qualification claimed |
| Reference pressure difference | **0.3226294589544 Pa** versus **0.32000 Pa** analytical; **0.8217%** relative difference |
| Normalized net flow imbalance | **6.1116 × 10⁻¹²** |
| Verified native replay | Passed from an exported ZIP into a path with spaces/Unicode; relative pressure difference **0.0** on this same runtime (limit 1e-6) |
| Failure handling | Actual API-process restart, runner SIGKILL, descendants, cancellation, retry lineage, resource limits, concurrent/duplicate requests and finalization recovery passed |
| Evidence integrity | Altered archives, edited native dictionaries, runtime mismatch, missing/non-finite evidence and a study edited during a live solve were refused or failed correctly |

The tracked [evidence summary](milestone-1a-evidence.json) records study/recipe
identities, measured values and manifest/archive hashes. Complete locally retained
artifacts are under ignored `artifacts/milestone-1a/`: `reference/report.html`,
`reference.zip`, `reproduced/report.html` and `reproduced.zip`. Tests preserve their
own independent cases under pytest's temporary directories. Earlier development
failures remain under `artifacts/m1a-acceptance/`.

The new pipeline preserves the M0 pressure result and original scientific
thresholds. It adds durable attempts, strict contracts, stronger field/evidence
checks and reproducibility controls. A benchmark pass still does not establish
mesh independence, experimental validation or general CFD accuracy.

Known non-blocking build/test notices: the pinned Starlette test client reports
an httpx deprecation, and Vite reports the existing large visualization bundle.

## Reproduce the acceptance workflow

```bash
uv run venturi run fixtures/pipe-study.json --request-id reference-acceptance
uv run venturi status
uv run venturi export artifacts/workbench/runs/<run-id> --output artifacts/reference-export.zip
uv run venturi verify artifacts/reference-export.zip
uv run venturi reproduce artifacts/reference-export.zip --output artifacts/reference-replay
```

Choose unused output/archive names when repeating. The README documents custom
study templates, detached execution, cancellation and explicit retries. The
[testing guide](testing.md) contains the automated commands and platform gates.
