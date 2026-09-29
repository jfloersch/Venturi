# Milestone 2 implementation closeout

Version **0.3.0**, tested on Ubuntu/WSL x86_64 with the pinned OpenFOAM
Foundation 14 runtime. The guided desktop alpha is implemented and its automated
acceptance passed. **The roadmap's observed target-user pilot exit gate is still
pending.** This is development acceptance, not release or CFD qualification.

## Delivered workflow

- STEP inspection with source units, converted dimensions, selectable face/edge
  references, isolation, transparency and movable X/Y/Z section cuts.
- Revision-bound annotations with saved reference metadata, world anchors and
  camera restoration. Changed geometry receives no automatic reference mapping.
- Named, worker-owned study revisions with the engineering question, fluid
  property source, SI-normalized operating conditions, presentation preference,
  mesh settings and resource limits. Reopen restores geometry and boundary roles;
  conflicting saves are rejected. Browser drafts remain separate.
- A validated study and resource review before meshing. Required flow has no
  default; suggested fluid properties are explained and require confirmation.
  The geometric cell estimate is explicitly uncalibrated. All profiles preserve
  the same recipe and critical checks.
- Exact rendered-mesh approval, historical-result labeling for changed drafts,
  persisted selected run/tab, and connection recovery without resubmission.
- Pressure loss, outlet split, convergence, checks and limitations, with the
  original question and material source retained alongside the result.
- Read-only case/artifact browsing, dictionary highlighting, field-entry and
  mapping shortcuts, logs/commands, bounded text preview, file differences, and
  individual report/CSV/SVG downloads. Full export freezes the desktop study and
  annotations alongside the source, native case and numerical evidence.

## Measured acceptance

| Check | Result |
|---|---|
| Full Python suite | **144 passed**, including 19 actual-runtime integration tests; 623.70 seconds |
| New persistence/inspection coverage | 14 workspace/API tests passed |
| Browser suite | **6 scenarios passed** together on the final code, including repeated annotation camera restoration |
| TypeScript and Vite | Passed |
| Native Linux Tauri release, no bundle | Passed |
| Ruff check/format, Prettier, Rust format | Passed |
| Browser-created manifold archive | 180 files verified; frozen question, source and edge annotation present |
| Browser archive replay | Passed; **zero relative difference** in pressure and both outlet flows |
| Native Linux desktop workflow | Passed on September 28: import, annotations, save/reopen, mesh approval, real solve, case inspection, downloads, cancellation and retry |
| Remote CI / clean installation | Not rerun in this session |

The browser manifold produced **0.04249171460614 Pa**, with outlet shares
**49.9991485814% / 50.0008514186%**, using the 35,168-cell mesh. All 20 numerical
checks passed. The deliberately one-second study failed with
`TimeoutError: Attempt exceeded its total wall-time limit.` It retained a report
and could not be approved for solving.

The browser scenarios exercise saved studies and annotations after clearing
browser storage, stale approval after edits and reload, real CAD picking and
rendering, save/submission response races, a delayed study review, cancellation,
retry, file inspection/diff, downloads, API outage/reconnection, and a blank
framebuffer that cannot authorize a mesh. Numerical outcomes are not mocked.
The full worker suite additionally covers six prepared geometry workflows,
including two rotated/translated variants, replay/tampering, failed mesh gates,
runner/API death, memory and disk limits.

During implementation, review responses were bound to their submitted inputs to
prevent a delayed response authorizing a newer draft. Draft restoration was also
applied when reopening directly into a result tab, so reload cannot make a stale
mesh current. Form inputs are locked while saving/submitting to avoid discarding
edits made during a pending write. Each behavior has browser regression coverage.

The initial full-suite command hit fixture-setup errors because the parent of the
pytest output directory had not been created. After creating it, the complete
suite passed. Existing dependency deprecation notices and the vtk.js bundle-size
warning remain non-blocking. No acceptance criterion was loosened.

The [machine-readable record](milestone-2-closeout.json) contains hashes, checks,
exact study/context, the budget failure and replay comparison. Local evidence is
under `artifacts/milestone-2/`; the JSON records the corresponding browser worker
store under `artifacts/e2e/`. The [pilot guide](milestone-2.md) and
[testing instructions](testing.md) describe repetition.

## Native follow-up — September 28

The release executable imported the manifold through its native file picker and
rendered it on WSLg. The complete interactive workflow then ran on an isolated
Linux Xvfb display, using AT-SPI and keyboard/mouse input. This avoids WSLg input
injection limitations; it does not qualify a native Windows executable.

The named study, question, material source, boundary assignments and edge note
survived restarting both the desktop and worker. An edited draft disabled mesh
approval, including after closing/reopening and selecting the mesh again.
Reopening the saved study restored approval. Restarting the desktop and API
during execution preserved the same attempt and process without duplicate work.
The solve passed all 20 checks with the same 0.04249171460614 Pa pressure loss and
outlet flows as the browser result.

The native case viewer displayed velocity/pressure boundary entries and CAD
mapping; comparison with the mesh attempt reported identical study files. The
metrics download matched the worker file. A native save dialog exported a ZIP
with spaces and Unicode in its filename; all 180 manifest files verified, and
replay in a separate Unicode path matched pressure and both outlet flows exactly.
Two native cancellation actions retained reports, and retry recorded a distinct
attempt linked to the original.

This testing found a camera-restoration defect: clicking the same note again
reused the previous restore request, and orbiting left a different near/far
clipping range. Each click now issues a fresh request and recalculates clipping
for the restored camera. The new browser regression failed before correction and
checks two consecutive orbit/restore cycles against the saved rendered image.
The rebuilt native executable also passed two cycles with **zero differing
pixels** in the restored viewport. All six browser scenarios passed together
after the correction; TypeScript/Vite, the native release build and formatting
checks passed. The unchanged Python worker retains its 144-test acceptance.

Local screenshots, native logs, restart records, exports, replay and regression
logs are retained under `artifacts/milestone-2-native-20260928/`. The test-display
setup initially lacked a keyboard compiler; resolving the isolated harness
dependencies required no system installation. No numerical criterion changed.

## Remaining gates

Observe a target user completing the supported study without case-file editing
and record hands-on time and interventions. Windows/macOS/ARM qualification,
independent CFD review, broader customer CAD and experimental validation remain
open. Packaging and self-service installation
are milestone 4 work. Restart an already-running older worker before using the
new desktop endpoints; restarting the API does not cancel independent attempts.
