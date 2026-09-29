# Testing Venturi milestones 1a and 1b

## Quick manual run

Follow the root README setup, then launch `uv run python scripts/dev.py` (browser) or add `--desktop` (native Tauri). After building the desktop, launch it without rebuilding with `uv run python scripts/dev.py --desktop --built`. Linux setup downloads roughly 130 MB of solver packages plus Python/CAD dependencies. The `.tools` runtime and all generated artifacts stay local and are ignored by Git.

1. Confirm both readiness checks pass. An absent solver must produce an actionable diagnostic.
2. Inspect the 10 mm × 100 mm fluid volume. Click the wall and both end faces; check that their roles and areas agree with the face list.
3. Orbit/zoom, toggle wall transparency and the section cut, and reset the camera.
4. Change the only inlet to a wall and save. Expect an error; restore the inlet and save successfully.
5. Run **boundary check**. Expect full mesh-quality success plus inlet/outlet/wall mapping evidence. It should take approximately seconds to tens of seconds on a development workstation; runtime is hardware-dependent.
6. Run **pipe reference**. Expect a pressure drop near 0.323 Pa compared with the 0.320 Pa analytical reference, and all evidence checks passing. A typical run here took about 26 solver seconds; do not use this as a performance promise.
7. Reload the page while solving. The existing run should reappear without starting another solver.
8. Export the completed run. Open the HTML report from the extracted ZIP and inspect `result.json`, `case`, `logs`, VTK fields, and CSV metrics.
9. Start another run and cancel it. Expect a cancelled state and preserved partial logs. Restart the worker and verify that completed runs still appear.

The pipe reference uses a fixed numerical mesh and fixed boundary conditions, independently of the CAD boundary selections. The CAD workflow tests those selections. The interface labels these as separate checks; the separate imported-STEP workflow connects explicitly assigned ports to the solver in milestone 1b.

## Automated checks

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -m 'not integration'
VENTURI_INTEGRATION=1 uv run pytest
cd apps/desktop
npm run build
npm run test:e2e
npm run tauri build -- --no-bundle
```

For the additional parameter and grid regression, run from the repository root:

```bash
uv run --locked python scripts/check_reference_matrix.py --output artifacts/pipe-matrix-new
```

Choose a new directory for every invocation. This runs eight actual solver cases,
declares scaling/grid criteria before execution, and preserves each study, report
and manifest. The fine grid has a 900-second solver limit and the matrix uses a
1,200-second per-attempt total budget. Allow several minutes; the original
300-second fine-grid attempt timed out under concurrent load. The measured
[1a closeout](milestone-1a-closeout.md) includes the retained failure and successful
fresh attempt, clean-install results, and native WSLg smoke-test evidence.

Playwright owns ports 1421 and 8766, starts a real worker with an isolated test token, and performs real OpenFOAM runs. It can run alongside a development session on 1420/8765. Install its browser once with `npx playwright install chromium`; Linux CI uses `--with-deps`. Screenshots are saved under `artifacts/screenshots`. Traces for failures are under `apps/desktop/test-results`.

## Reproduce an exported numerical case

Use `venturi verify <archive.zip>` and `venturi reproduce <archive.zip> --output <new-directory>` for the checked workflow. Reproduction requires the original runtime fingerprint and accepts only native inputs matching the versioned compiler. It recomputes from time zero, produces a fresh evidence report and compares pressure within the recipe's 1e-6 reproduction tolerance. The original is unchanged.

For independent manual inspection, the native `case` contains generated dictionaries, initial/final fields and mesh. In the recorded Foundation-14 environment, `foamRun -case <case>` starts at zero without an AI or Venturi service. Work on a copy because rerunning changes evidence and invalidates the existing manifest. OpenFOAM itself requires a path without spaces/non-ASCII characters; the Venturi adapter handles this for its own runs. Do not claim byte-identical floating point output across CPU architectures.

## Windows / WSL

The Linux Tauri executable defaults to software rendering on WSL because this machine's GPU driver produced a blank 3D framebuffer without reporting a GL error. The browser viewer works normally. Set `VENTURI_HARDWARE_RENDERING=1` only when deliberately testing another WSL graphics configuration. No system graphics settings are changed.

Use Ubuntu 22.04 x86_64 in WSL2 for the qualified worker baseline. Keep solver files on the Linux filesystem; the app accesses artifacts through the worker rather than continuously reading a Windows-mounted case directory.

For a **native Windows shell**, clone the frontend on Windows, install Node and the Tauri Windows prerequisites, run `npm ci` under `apps/desktop`, and run `npm run tauri dev`. Start `venturi serve` inside WSL and paste its ephemeral worker token into the connection panel, or set `VENTURI_API_TOKEN` in both environments. The endpoint is `http://127.0.0.1:8765`; verify localhost forwarding first. Do not expose the worker on all network interfaces.

`scripts/windows_wsl_probe.py` runs from WSL and uses Windows PowerShell to verify the health endpoint and a Unicode/space-containing file round trip in the test artifact directory. This is a transport/filesystem check, not a native Windows GUI qualification.

## macOS / Apple Silicon

Build the native shell with Node/Rust/Tauri prerequisites on macOS. Run the worker in an Ubuntu Linux VM; evaluate Multipass for lifecycle integration. Port forwarding through an SSH tunnel to local port 8765 is acceptable for the M0 test; the app requires an authenticated loopback endpoint.

The bootstrap script refuses ARM64 until that package set is qualified. `worker/arm64-candidate.json` records the official OpenFOAM candidate; install the matching Foundation version in the test VM, install Python/CAD dependencies, set `VENTURI_FOAM_ROOT`, then run the same CLI and integration suite. Record all dependency versions and compare metrics, not just exit codes. Do not use an emulated x86_64 success as evidence of native ARM performance.

## Platform sign-off checklist

Record OS version, CPU architecture, graphics driver, app/runtime versions, and test date. For each platform record build, launch, visible 3D render, face picking, file selection/export, cancellation, reopen, runtime installation, resource use, and the two numerical/CAD checks. Missing hardware checks stay **not tested**. Unsigned development installers are not a public release.

The worker is a trusted local development tool. It has time/cell limits, per-tool address-space limits, polled disk budgets, process-death reconciliation and explicit retries. It has no complete memory/disk sandbox, automatic solver resume, or arbitrary uploaded-case executor. Keep the test fixture scope explicit.

## Optional ParaView batch check

The baseline run already exports VTK data. To reproduce the tested standalone ParaView render, install its separately pinned official binary (788 MiB download, about 2.5 GiB extracted):

```bash
python3 scripts/bootstrap_paraview.py
.tools/ParaView-6.0.1-MPI-Linux-Python3.12-x86_64/bin/pvpython --force-offscreen-rendering scripts/paraview_render.py artifacts/YOUR-REFERENCE-RUN/case artifacts/paraview
```

Inspect `velocity-slice.png` and `render.json`. The script rejects empty slices, uses a fixed 0–0.02 m/s speed range, labels the figure, and selects software rendering on WSL. A PNG being written is not by itself a visual pass: inspect that the actual colored velocity field appears.


## Milestone 1a acceptance cases

The full Python suite includes actual CLI study submission, duplicate request
reconnection, ZIP export, native replay in a Unicode/space-containing directory,
refusal of modified dictionaries even after rehashing, and runtime mismatch.
It also exercises an actual HTTP API process being killed/restarted during a job,
runner SIGKILL, descendant cleanup, cancellation, explicit retry lineage,
concurrent admission, total deadline, tool memory/disk limits, invalid schemas,
missing/non-finite fields and histories, tampered studies and manifest corruption.
Control-path failure tests use deliberately sleeping/failing executable fixtures;
they do not substitute for the real solver numerical acceptance tests.

Browser acceptance covers visible CAD/picking, invalid selections, real meshing
and solving, reload, provenance display, cancellation, retry and ZIP download.

Manual lifecycle check: start `venturi run fixtures/pipe-study.json --detach`,
stop/restart only the API, and confirm `venturi status` and the workbench reconnect
to the same run. Stopping the development launcher now leaves active jobs running;
use the explicit cancel action when you intend to stop a simulation.

## Milestone 1b acceptance

`tests/test_internal.py` checks strict studies, conservative flow/mesh bounds,
explicit revision-bound planar ports, immutable imports, unsupported geometry,
upload/reopen, missing or stale approval, malformed fields and histories, and
numerical failure conditions. Invalid STEP entities are rejected before OCCT can
silently substitute missing unit values.

`tests/test_internal_integration.py` runs actual CLI import/template/validation,
meshing, review-hash approval, solving and artifact verification for a circular
pipe, 90-degree bend, two-outlet T-manifold and rectangular duct. It generates two
rotated/translated ducts at test time. It checks the pipe's developed pressure
gradient against Poiseuille within 5%, manifold symmetry within 5 percentage
points, portable replay including every outlet flow, and refusal of edited
native dictionaries, source bytes and runtime hashes. A deliberately coarse
manifold must fail mesh checks and be refused for solving.

The browser test imports the manifold, assigns ports, retains study edits across
reload, displays the actual mesh, approves it, solves, checks the two outlet rows,
exports the archive, reopens the result, and verifies that a blank mesh renderer
blocks approval. It runs alongside the original
reference, picking, cancellation and retry checks. Restore reference geometry
before rerunning legacy manual tests after an import.

For retained acceptance evidence, choose a new directory (pytest **clears** an
existing `--basetemp`):

```bash
VENTURI_INTEGRATION=1 uv run pytest --basetemp=artifacts/m1b-acceptance-new
```

These synthetic development/held-out cases establish bounded workflow behavior,
not general accuracy for every CAD passage. Port screening does not detect all
internal restrictions. Independent CFD review, grid-convergence studies and
experimental validation remain outside this implementation acceptance.

## Milestone 1 closeout regression

Run the prepared-STEP mesh-sensitivity and fluid-scaling matrix in a new folder:

```bash
uv run --locked python scripts/check_internal_matrix.py --output artifacts/internal-matrix-new
```

The script writes all five studies and acceptance criteria before launching any
solver. It compares the bend at 0.8/0.5 mm and the manifold at 0.625/0.4 mm, with
limits of 5% pressure-drop difference and one percentage point of outlet-share
difference. A third bend study doubles density and dynamic viscosity together:
unchanged kinematic viscosity should preserve flow and double pressure in Pa
within 1e-6 relative error. Every mesh and flow must independently pass the
normal recipe checks and artifact verification. Failed attempts remain on disk.
These are two-level sensitivity checks, not a formal grid-convergence study.

The curved-geometry unit regression checks both the selected CAD point and its
actual background-cell center at four resolutions. The browser suite deliberately
holds submission responses until polling has already displayed the new attempt,
covering retry, mesh creation and mesh approval. Each attempt must appear once;
duplicate React keys fail the suite.

See [milestone 1 closeout](milestone-1-closeout.md) for the measured final results,
clean-install/replay evidence, fixes found during testing, and remaining release
qualification work.

## Milestone 2 guided desktop alpha

The acceptance contract and observed-user walkthrough are in
[milestone-2.md](milestone-2.md). Tests use the existing numerical criteria.

```bash
mkdir -p artifacts/milestone-2
VENTURI_INTEGRATION=1 uv run pytest -q --basetemp=artifacts/milestone-2/python \
  --junitxml=artifacts/milestone-2/python-results.xml
cd apps/desktop
npm run test:e2e
npm run tauri build -- --no-bundle
```

Use a fresh `--basetemp` for independent evidence retention; pytest replaces its
own base directory. Create its parent first. The browser harness creates a fresh
worker store for each invocation under `artifacts/e2e/session-*`.

`test_workspace.py` covers worker-owned study restoration after API restart,
optimistic revision conflicts and immutable history, geometry/source changes,
recipe/resource rejections before execution, repeatable edge IDs, annotations
with camera and world anchors, cross-revision reference rejection, authenticated
artifact listing, text bounds, path/symlink refusal, file differences and frozen
desktop context. Existing runtime tests enforce time, memory and disk limits.

The six browser scenarios cover repeated annotation camera restoration;
authentication; CAD selection and reference
solve/export; cancellation/retry; a full real manifold study with durable save,
edge annotation, resources, stale-input gates across reload, case inspection/diff
and export; and stale in-flight review rejection, a real one-second budget stop,
and API disconnect/reconnect without submission. The manifold scenario also
retains the blank-render mesh-approval regression. No solver outcomes are mocked.
Screenshots and the portable manifold export are under `artifacts/milestone-2`.
Automated browser completion does not count as an observed human pilot.

Native Linux acceptance also follows the pilot walkthrough with the release
executable and actual native import/save dialogs. Restart the UI and API during
an attempt, reopen the saved study, check stale-mesh rejection after an edit,
inspect/download evidence, cancel and retry, and verify/reproduce the exported
archive. For annotation restoration, click a saved note, orbit, and click the
same note again twice; the saved view must return without clipping the geometry.
The September 28 native evidence uses WSLg for import/render and an isolated Xvfb
display for reliable automated keyboard/mouse input. It is Linux desktop
acceptance, not native Windows/macOS qualification or an observed human pilot.

## Milestone 3 bounded assistance

Use `uv sync --locked --extra cad --extra dev` for the locked Python/VTK/keyring
dependencies. The assistant's HTTP contract tests use a simulated transport and
test-only keys. They make no real provider calls or purchases.

```bash
uv run pytest -q -m 'not integration'
VENTURI_INTEGRATION=1 uv run pytest -q tests/test_milestone3_integration.py
cd apps/desktop
npm run test:e2e
npm run tauri build -- --no-bundle
```

`test_assistant.py` covers strict input/schema validation, unknowns, SI units,
stale revisions/context, explicit approval, approval crash recovery, persistent
idempotency, provider refusal/outage/incomplete output, usage reservations,
concurrent overspend prevention, credential redaction and advisory-only visuals.
`test_recovery.py` covers allowlisted controls, frozen physics/criteria, policy
hashes, worker ownership, dead supervisors, sensitivity and secret isolation.
`test_rans.py` screens physics, geometry and wall-function inputs independently of
the solver. `test_milestone3_integration.py` runs the actual native recovery ladder,
held-out higher-flow rotated ducts, cancellation and budget stops, SST positive and
negative wall-resolution cases, scientific views and exact native replay.

`zz_milestone3.spec.ts` adds browser consent/proposal/approval controls using a
clearly simulated provider response; real worker recovery, cancellation after
reload, scientific images and explicit turbulence inputs use the actual API.
Its second scenario uses the completed manifold from `workbench.spec.ts`; run the
complete suite for that scenario. The assistant scenario can run independently.

`scripts/check_sst_benchmark.py <passed-run-directory> --output <report.json>`
checks the declared downstream pressure-gradient diagnostic against Blasius with a
fixed 15% tolerance. This is an empirical numerical comparison, not independent CFD
qualification or experimental validation of arbitrary ducts.

Evidence and open qualifications are in [the milestone-3 closeout](milestone-3-closeout.md).

### Live Codex evaluations

`scripts/check_codex_assistance.py` is an opt-in test adapter using the installed
Codex CLI and its saved sign-in. It exercises real inference through the production
proposal validation, geometry screening, explicit approval, revision conflict and
idempotency paths. It does not replace Venturi's OpenAI provider or test the
Responses HTTP transport, API credentials, token reservations or API billing.
These runs consume the signed-in Codex account's available usage.

Use a fresh output directory and explicitly choose the model to evaluate:

```bash
uv run --locked python scripts/check_codex_assistance.py \
  --output artifacts/codex-eval-new --model YOUR_CODEX_MODEL \
  --repeats 2 --workers 2
```

The twelve proposal cases cover SI inputs, unit conversion, missing flow/material
data, conflicting flow, unsupported physics, transitional flow, preservation of
saved inputs, stale approval, incomplete/complete SST inputs and annotation
injection. The suite records its cases and expected values before inference.
Each case uses a fresh local workspace and one bounded Codex invocation, without
automatic retries. Repeating a proposal or approval must not call the model again
or create an additional saved revision. Failures and raw responses are retained.

For image evaluations, supply verified completed run directories:

```bash
uv run --locked python scripts/check_codex_assistance.py \
  --output artifacts/codex-visual-new --model YOUR_CODEX_MODEL \
  --visual-only --visual-run artifacts/YOUR_PASSED_RUN \
  --visual-run artifacts/YOUR_FAILED_RUN --repeats 2
```

This generates images from the native fields and supplies the same scientific
packet used by the production review. Assertions check typed artifact references,
limitations and unchanged numerical results. The saved observations also require
inspection for scientific accuracy: passing a JSON schema alone does not establish
that an image interpretation is correct or provide independent CFD qualification.

Codex uses a separate temporary working directory with execution, collaboration,
plugins, browser and other optional tools disabled, a read-only sandbox and a
300-second per-call timeout. The harness rejects unexpected tool events. Its
structured final JSON stands in for the production function argument object;
Codex's surrounding instructions and inference environment differ from the direct
Responses call. Keep these results labelled as Codex evaluations.


## Milestone 4 fault and platform acceptance

The non-payment campaign uses directly seeded test wallets. Payment transports are
forbidden in its fault and load harnesses. No OpenAI API key is needed.

```sh
uv run pytest -m 'not integration' --ignore=tests/test_gateway.py
uv run python scripts/check_m4_load.py --output artifacts/m4-load-new --seconds 120
cd apps/desktop
VENTURI_SKIP_PAYMENT_TESTS=1 npm run test:e2e
```

`test_m4_resilience.py` covers thread/process contention, transaction and HTTP
gateway crashes, live WAL backup/restore, exhausted SQLite storage, malformed and
unauthorized inputs, credential persistence failure, and password-recovery races.
`test_m4_installation.py` injects package corruption, download interruption, storage
errors, dependency/solver failure and abrupt setup termination, and checks active
work locks, version switching, rollback and path handling. Dependency substitutes
make these failures deterministic; separate real installer runs qualify the actual
Python/CAD/solver payload. Test wallets are never customer balances.

The local HTTP load test runs 16 clients for two minutes, repeats request IDs and
checks exact ledger conservation, provider invocation counts and database integrity.
It reports latency on the current host; it is not a production capacity guarantee.
Use a fresh output directory each time. The helper imports the offline test gateway
from `tests/` and therefore runs from a development checkout with test dependencies.

The optional existing `scripts/check_codex_assistance.py` harness exercises actual
model reasoning via saved Codex sign-in. It is an inference-only adapter with tools
disabled and does not qualify the OpenAI Responses transport, token billing or
production pricing. Passing and failing visual cases require internal-flow native
field artifacts; the analytical pipe-reference report is not such an artifact.

See [M4 resilience results](milestone-4-resilience.md) for the measured platform
scope, retained failures and remaining external qualification work.
