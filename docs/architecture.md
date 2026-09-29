# Architecture decisions — milestones 1–4

## Shared desktop, independent numerical worker

The desktop is Tauri 2 + React/TypeScript, with vtk.js for the CAD viewer. A Python service owns deterministic simulation tools. The desktop calls `venturi.worker.v1` over authenticated loopback HTTP. The same Python workflows power the CLI. React contains no solver dictionaries or acceptance rules.

The development shell connects to an already-running worker on port 8765.
`scripts/dev.py` starts both processes and supplies an ephemeral token. Milestone 4
release bundles embed a worker wheel, pinned bootstrap binary and hash-locked
dependency list. Guided setup installs a private Python environment and the pinned
solver; `desktop_host.py` starts or reconnects an installed worker using a private
local session file. Windows launches the same worker through Ubuntu-22.04 in WSL2.
Release signing is configured separately and requires deployment credentials.

Windows uses WSL2 for the worker; macOS will use a Linux VM, with Multipass a candidate; Linux can execute the worker directly. Windows/macOS native shells share the interface. OS-specific launch/provisioning belongs outside the scientific implementation. The current automated bootstrap deliberately accepts only Ubuntu 22.04 x86_64, including WSL2.

The WSL graphics driver on the development machine produced valid GL contexts but empty geometry frames in both WebKitGTK and ParaView. Chromium rendered correctly. Software rendering (`llvmpipe`) fixed both native paths, and the Tauri executable selects that path on WSL before creating the window. `VENTURI_HARDWARE_RENDERING=1` opts into hardware testing. The viewer checks that its initial framebuffer actually contains visible geometry and reports a failure instead of leaving an unexplained blank canvas. This is a WSL-specific compatibility decision, not a general claim about native Windows or macOS GPU performance.

## Version baseline

- OpenFOAM **Foundation 14**, package **20260724**, double precision, 32-bit labels, serial execution. `worker/runtime.lock.json` pins the official package URL and SHA-256.
- Runtime libraries are extracted into `.tools/runtime` from the exact packages and hashes in `worker/system-dependencies.lock.json`. Setup needs no root, apt source changes, or shell-profile modifications. The Ubuntu C/C++ base runtime remains an OS prerequisite; this is a package-pinned development environment, not a qualified container image digest.
- Python 3.12; all resolved Python dependencies are in `uv.lock`.
- Open CASCADE through `cadquery-ocp==7.8.1.1.post1`, the binding version exercised by the spike. Newer bindings must pass the geometry tests before migration.
- Frontend dependencies in `package-lock.json`; Rust dependencies in `Cargo.lock`.
- Each run records the actual solver's reported build identifier and executable SHA-256, alongside the worker and CAD versions. The setup package pins describe the development baseline; an overridden runtime is identifiable in its report.
- OpenFOAM's stock `foamToVTK` plus deterministic plots provides the baseline post-processing path. ParaView batch rendering is separately smoke-tested when installed.

Do not mix OpenFOAM Foundation dictionaries with ESI/OpenCFD dictionaries. This spike encountered a real version-specific difference in the surface-field function-object syntax; the generated dictionaries follow the selected Foundation-14 implementation.

## Geometry references

Original STEP bytes stay unchanged. The importer reads declared STEP length units and explicitly asks OCCT to output metres. Geometry revision identity is the SHA-256 of the original bytes. Face references derive from topology enumeration, area, centroid, and surface type, and are valid only with that exact geometry hash and the pinned importer. Reopening the same bytes restores the same references. Modified files require remapping; no general cross-revision identity claim is made.

The triangle representation retains each CAD face's identity. Saved boundary roles generate named STL surfaces plus a CAD-to-surface manifest. The actual mesh boundary names, areas, and centroids are audited against that manifest. The original M0 boundary check retains its fixture recipe. Imported prepared fluid volumes use the separate milestone-1b compiler, with CAD-classified interior points, planar named ports and a user-declared cell size.

## Scientific evidence

Two independent workflows avoid confusing numerical verification with CAD meshing:

1. The **reference** uses a deterministic full 3D pipe mesh and a normalized parabolic inlet profile. It compares area-averaged pressure loss with Hagen–Poiseuille, with units and pressure conventions checked in the actual output.
2. The **CAD boundary check** imports the STEP fixture, exports named surfaces, runs `snappyHexMesh`, and checks the final mesh and mappings. It does not solve flow.

The first CAD mesh had four concave cells under `checkMesh -allGeometry`. It was rejected. The final fixed-fixture mesh uses a uniformly fine background and aligned end planes, eliminating the failed cells without relaxing the checks. Every failed development artifact remains local under `artifacts`.

## Execution and storage

Worker commands are allowlisted argument arrays, never user-interpolated shell commands. Only generated cases are executed. The worker records each request before execution, limits itself to one active job, rejects conflicting idempotency keys, and retains separate attempt directories. Cancel and timeout terminate the process group. Core scientific status never depends on model output.

Portable JSON remains authoritative. Each attempt owns an atomically replaced status, frozen request/study/recipe, chronological event files, command records and final artifact manifest. A Linux advisory lock serializes admission across API/CLI processes; idempotency keys are checked against persisted requests. A second lock gives one runner ownership of each attempt. SQLite remains unnecessary for this small serial store; it can later index these portable records.

The API launches a detached runner and records its boot ID, PID and process start ticks before releasing a launch marker. Active jobs survive API/UI restart. Dead process identities cannot be confused with reused PIDs; reconciliation labels abandoned work interrupted and produces an exportable report. Terminal-report finalization can recover after a crash. Explicit retries retain their parent ID and reason and start from iteration zero. No automatic numerical recovery or solver checkpoint continuation is implemented.

Every numerical tool runs in its own process group through a small Linux supervisor. Cancellation, timeout and runner death terminate that group, including descendants. The supervisor applies `RLIMIT_AS` to itself and tool children; the runner polls elapsed time and disk usage between stages and during tool execution. Disk use can overshoot by one polling interval and final diagnostic writes; the memory limit is per process virtual address space, not aggregate RAM or a cgroup. These controls are not a general sandbox. Solver environments contain only selected locale/path variables and OpenFOAM settings, not application/provider tokens.

OpenFOAM rejects spaces and non-ASCII case paths. For those paths the adapter creates a private ASCII symlink under `/tmp` for each tool, preserving artifacts in the original directory. Normal completion removes the alias; an abrupt runner kill can leave an inert temporary alias. It is never a project identifier.

Simulation execution makes no external AI calls. Optional milestone-3 assistant and visual-review requests contact OpenAI only after explicit sharing approval. Setup downloads dependencies. The worker binds to loopback, requires a token for project data/actions, and restricts browser origins. A future remote worker needs an authenticated transport; do not expose this development server directly to a network.

## Bounded assistance and independent recovery

`assistant.py` sends strict Responses API function-call requests to a fixed OpenAI
endpoint. The two tools propose a study or record visual observations; neither
tool has execution authority. Model output is revalidated locally. Context
approval binds a hash covering geometry metadata, exact study revision, annotations
and recent conversation. Applying a proposal records user approval, old/new values,
sources, rationale and dependent run identifiers. Immutable study revisions retain
the approval identifier; subsequent desktop attempts freeze the approval ledger.

Keys remain in worker memory or an explicitly selected supported OS credential
store. The connection file contains only model, declared prices and budget.
Validation/error messages omit submitted credentials. Solver and recovery processes
receive an environment with provider secrets and API tokens removed. Every provider
request reserves its conservative input/output allowance before network I/O under a
cross-process lock. Its idempotency key is durable. Missing or ambiguous responses
retain their reservation and are never automatically resent. Actual reported usage
settles successful reservations at user-declared rates; these estimates are not a
provider billing guarantee. Images use low detail and text/encoded-image byte bounds.

`recovery.py` is a detached process owning a frozen plan and explicit finite policy.
It reserves each job intent before launch; the job manager rejects unowned or
unexpected work while a campaign is active. A stability/residual failure can double
iterations within the ceiling; supported mesh-quality/mapping failures can reduce
cell size by the approved factor. Changed studies get a fresh audited mesh, with
the existing exact-mesh regeneration check retained for the solve. Geometry,
materials, flow, physics and quantitative criteria remain frozen. Resource guards
check aggregate elapsed time and retained attempt bytes, including approved-mesh
copies. Input copies are checked against remaining disk before launch. Runtime disk
growth is monitored, not an OS filesystem quota; termination has polling latency.
The policy counts separate mesh-review jobs; each solver job also regenerates its
approved mesh as part of its existing reproducibility check.

A terminal job status can precede manifest finalization. Recovery waits on the
attempt lock before verifying artifacts. A dead supervisor cancels any owned live
job and becomes interrupted; restart never silently repeats a charged request or
simulation. Pressure/branch-flow changes over 2% receive an explicit sensitivity
flag. All numerically passing campaigns remain provisional while independent
qualification and mesh independence are unassessed.

The separate `venturi.rans-study.v1` / `sst-straight-duct/1` contract preserves old
laminar schemas and hashes. It compiles Foundation-14 kOmegaSST with explicit
inlet turbulence, kqR/omega/nut wall functions and meshWave wall distance. Native
k/omega/nut positivity, residuals and every wall-face y+ are checked. The yPlus
function skips initial-time execution so it cannot mutate frozen initial fields.
Only the dimensionless native `[]` syntax is normalized to seven zero exponents;
pressure/velocity dimensional checks remain strict.

`visual_review.py` reads the actual VTK volume export. Three fixed central cuts per
field use native cell values, one global scalar range per field and explicit SI
labels. The packet binds source/result/image hashes and includes numerical checks,
histories and boundary conditions. It is stored outside the immutable run, with a
separate export manifest. Model observations have fixed artifact identifiers and
carry no verdict field or authority to override quantitative status.


## Study, recipe and evidence contracts

`venturi.study.v1` references `laminar-pipe/1`, explicit SI inputs, the requested
pressure quantity and resource policy. Validation rejects unknown fields,
unsupported versions/physics, non-finite inputs, excessive cell budgets and the
out-of-envelope Reynolds/length conditions. Defaults are validated before hashing
so equivalent parsed studies have stable identities. The recipe owns fixed
boundary assumptions and provisional acceptance thresholds; clients cannot
supply relaxed thresholds.

The compiler produces the existing Foundation-14 pipe mesh and solver dictionaries.
The workflow records native input and mesh hashes before execution and checks that
inputs remain unchanged. Evidence requires aligned contiguous quantity histories,
final pressure/velocity fields with correct dimensions and finite values,
prescribed flow, conservation, pressure stability, residuals and complete execution.
Experimental validation, mesh independence and independent CFD review are explicitly
unassessed/pending. A failed check cannot be overridden by an apparently converged solver.

The final portable manifest covers the status, result, report, event journal,
commands and native artifacts. Export refuses missing/changed/extra files and
symlinks. Archive import rejects traversal, duplicate normalized entries, and
oversized bundles. SHA-256 provides integrity, not an authenticity signature.
Replay verifies an export, requires matching recorded OpenFOAM executable/library
hashes, regenerates inputs for comparison, and only executes exported native
inputs that match the deterministic compiler. It never executes arbitrary
uploaded OpenFOAM dictionaries. Reproduction records its source manifest hash
and relative pressure difference (limit 1e-6 for this same-runtime recipe).

## Prepared STEP internal flow

`venturi.internal-study.v1` and `laminar-internal/1` form a separate typed contract
from the analytical pipe benchmark. Raw authenticated STEP uploads are bounded,
copied before inspection and stored by source SHA-256. All imported faces start
as walls; confirmed selections and study drafts are retained by geometry revision.
Planar face normals, perimeter and wire count support conservative port screening.
CAD classification locates an interior background-mesh point even for non-convex
volumes. No compiler path depends on a fixture name or a known axis/origin.

`internal_mesh` and `internal_flow` are separate persistent attempts. Mesh results
include a native boundary-face preview and CAD correspondence audit. Solve requests
carry the passed mesh attempt and exact hash, and must match its frozen study and
recipe. The approved mesh bundle is copied into the flow attempt for portable
provenance. Execution regenerates the mesh and requires the same hash before
writing generated flow conditions and launching the solver. The current bounded
implementation trades another meshing step for deterministic, reviewable inputs.

The prepared recipe extracts sharp surface/port edges with Foundation-14
`surfaceFeatures` and uses explicit feature snapping. It tightens snappyHexMesh's default disabled volume/tet-quality
limits to positive values. Final `checkMesh -allTopology -allGeometry` remains
mandatory; coarse junction cells can still fail and require a new explicit study.
One connected region, patch inventory, areas, centroids and normals are checked
against the CAD. The preview shows actual mesh boundary polygons and edges.

Uniform normal inlet vectors integrate to the requested volume flow on the actual
mesh. All outlets use equal zero-gauge pressure; each retains its CAD face ID.
Function objects record mean kinematic pressure, signed flux and absolute flux for
every boundary. Evidence checks mass, flow direction, backflow, wall leakage,
pressure/branch stability, residuals, history completeness, field dimensions and
cell counts. Final native patch flux sums must agree with recorded histories.
Pressure differences are converted to Pa using the frozen density. Outlet shares
are normalized by total signed outlet flow. No experimental accuracy is inferred.

Internal-flow runtime provenance adds snappyHexMesh, surfaceFeatures and meshing-configuration
hashes to the existing solver/library fingerprint. Replay compares these and the
recipe, regenerates all native inputs, refuses modified dictionaries even if a
bundle is rehashed, and compares pressure plus every branch flow. The 1a recipe,
runtime fingerprint and native compiler remain compatible with earlier exports.


## Guided desktop alpha persistence and inspection

Milestone 2 adds authenticated, additive worker-v1 endpoints. `POST /v1/studies/plan`
normalizes the existing internal study, checks source/ports/Reynolds/resolution and
background-grid bounds, and returns an explicitly uncalibrated geometric cell
range. It does not launch a job. `POST /v1/studies` writes immutable revisions and
an atomic current pointer under a workspace lock. Expected-revision checks prevent
lost updates between windows. `GET /v1/workspace` lists saved studies;
`POST /v1/studies/{id}/open` restores the stored geometry and assignments after
checking the source hash. Browser-local drafts are separate from these durable
records. Presentation preference changes visibility, never acceptance criteria.

CAD metadata now includes exact-kernel edge lengths, centroids, adjacent faces and
sampled edge polylines. Existing face IDs and source hashes remain compatible.
Annotations save entity metadata, a world-space anchor and camera against the
exact STEP hash, with their own optimistic revision. New geometry starts with
new references; no nearest-face or nearest-edge assignment occurs.

A run can cite a saved desktop-study ID and revision. Admission compares the
canonical numerical study with that revision; attempt creation freezes the brief,
material source, preference and annotations in `desktop-context.json`. Those
files are sealed and exported with the case. Retries retain the original context.
No numerical compiler, physics recipe or acceptance threshold is changed.

The read-only viewer inventories each run and exposes bounded UTF-8 text previews
(512 KiB), hashes of the previewed bytes, and unified same-path file differences.
Path traversal and symbolic links are rejected. Large/binary files remain
available through explicit artifact or full-run downloads. Logs can be refreshed
while an attempt runs. React escapes file text; it never executes or edits it.

Review responses are associated with their submitted inputs, so a delayed response
cannot approve a newer draft. The UI compares draft inputs with the mesh study,
requires a rendered mesh and preserves the worker's exact mesh/study/runtime
binding. Reopening an API session restores attempts without resubmission. The
active tab/run survives a browser reload; drafts still invalidate old approvals.

## Managed gateway and release boundary

`gateway.py` is a separate FastAPI service, backed by `managed_ledger.py` and a
persistent SQLite WAL database. It never launches numerical tools. Account sessions
are opaque random tokens stored as hashes, passwords use salted scrypt, and recovery
codes rotate credentials and revoke sessions. The local adapter keeps session
secrets in memory or the OS credential store, outside simulation environments.

The gateway owns its model route and tariff. Integer micro-USD reservations are
atomic with wallet/study/account/global checks. Usage settlement releases unused
funds; failures with known usage settle their actual tariff charge, while ambiguous
requests remain reserved. Account/request identity and payload hashes prevent
retries from repeating inference. Stripe fulfillment verifies raw-body signatures,
payment mode, order identity, currency and amount; unique orders and events prevent
duplicate credits. Refunds use cumulative deltas, and disputes freeze new spending.

Local proposal approval, applicability and numerical checks are unchanged by the
billing route. Pre-save proposal and subsequent saved-study requests share a
spending scope, and visual reviews use the originating saved study where available.
Changing the billing route clears desktop consent; provider/model/tariff information
is also bound into the shared-context hash. No automatic BYO-to-Managed fallback
exists. Conservative estimates are displayed before text proposal requests.

`support.py` uses an explicit allowlist instead of trying to redact arbitrary logs.
Its export endpoint requires the hash of the reviewed preview. Gateway prompts are
not persisted; structured results have a short recovery cache, with accounting
retained separately. See milestone-4-deployment.md for retention and reconciliation.

The installer locks setup, worker startup and job admission before maintenance,
refuses active runs/campaigns, and stops only its recorded idle API process before
changing dependencies. Successful installation atomically selects the versioned
application directory. Existing projects and old application versions are retained.
