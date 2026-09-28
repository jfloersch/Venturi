# Milestone 1b development closeout

Version **0.2.0**, measured on Ubuntu 22.04 x86_64 / WSL2 with the pinned
OpenFOAM Foundation 14 runtime. Baseline: `20efcfa` (1a closeout).

**The bounded STEP-to-report implementation acceptance passed.** The desktop and
CLI import prepared STEP fluid volumes, retain explicitly confirmed ports, compile
and audit the mesh, require approval of its frozen study and exact mesh, solve,
report pressure loss and outlet flow split, and export/reproduce the native case.
The original 1a analytical pipe workflow is retained.

## Measured numerical corpus

Every case below passed mesh and flow checks using generated dictionaries only.
The input dimensions, units, source/study/mesh hashes, per-outlet metrics and run
locations are in [the machine-readable record](milestone-1b-closeout.json).

| Prepared fluid volume | Cells | Pressure drop (Pa) | Outlet shares |
|---|---:|---:|---|
| bend | 7,599 | 0.0351523337 | 100.00000% |
| held-out-rotated-duct-2 | 29,751 | 0.0342271647 | 100.00000% |
| held-out-rotated-duct | 20,640 | 0.0323677834 | 100.00000% |
| manifold | 35,168 | 0.0424917146 | 49.99915% / 50.00085% |
| pipe | 17,125 | 0.0683623644 | 100.00000% |
| rectangular | 20,700 | 0.0303001984 | 100.00000% |

The imported pipe uses a uniform inlet. Its developed pressure gradient was
−0.641281783 Pa/m versus −0.640000000 Pa/m from Poiseuille: **0.20028% error**,
inside the predeclared 5% tolerance. Its whole-domain pressure loss includes
entrance effects and is not the same experiment as the parabolic-inlet 1a reference.

The manifold split was **49.99915% / 50.00085%**, comfortably inside the
predeclared 5-percentage-point symmetry criterion. A portable ZIP replay in a
Unicode/space-containing path reproduced pressure and both branch flows with
**zero measured relative difference**. Edited source bytes, mesher fingerprints
and native dictionaries were refused, including dictionaries in a rehashed bundle.

The first rotated/translated duct became a regression case after exposing a
meshing deficiency. A second variant was declared after the fix, generated during
testing, and passed without further compiler changes or manual case-file edits.
These are synthetic hold-outs, not an independent customer-CAD qualification set.

## Verification

- **126 Python tests passed**, including 19 actual-runtime integration tests.
  This includes the 1a reference/export/replay, six prepared geometry workflows,
  stale/missing approvals, bad geometry/units, field/history corruption,
  backflow/conservation/convergence checks, and real cancellation/retry.
- **Four browser scenarios passed** with the real worker and solver. After adding
  the blank-render approval gate, the three legacy scenarios passed again and the
  prepared-manifold scenario passed in a targeted rerun, including a deliberately
  blank framebuffer that must leave approval disabled.
- The **native Linux Tauri release built and passed a WSLg smoke test**: file
  chooser, STEP import, explicit roles, visible geometry and mesh edges, approval,
  solve, close/reopen, API restart, native ZIP export, and archive verification.
  The final binary also rendered the mesh and enabled approval after rendering.
- Python lint/formatting, TypeScript/Vite build, Prettier and Rust formatting passed.
  Existing dependency deprecation and Vite chunk-size warnings remain non-blocking.

Local evidence is under `artifacts/milestone-1b/`: `acceptance.log`, `acceptance/`,
`browser-acceptance.log`, `browser-guard-verified.log`, `browser-*.png`,
`native-build-final.log`, and `native/smoke.json`. Full artifacts remain local;
portable manifests and hashes are summarized in the JSON record. CI was updated
for the larger suite and evidence retention; remote CI jobs have not been claimed
as executed here.

## Failures retained and resolved

- Initial volume-statistic parsing consumed a trailing period in a checkMesh line;
  numeric parsing was corrected.
- Default snappy quality settings allowed invalid decomposition tetrahedra at the
  T-junction. The compiler now requires positive tet/volume quality. The manifold
  still fails final mesh checks at 0.8 mm; the explicitly declared 0.625 mm study
  passes. The coarse failure is an automated rejection case, and is never solved.
- The first oblique duct had two concave cells at sharp edges. Finer cells alone
  did not fix it. Foundation-14 `surfaceFeatures` and explicit edge snapping fixed
  it at the original resolution without relaxing acceptance checks. The compiler
  applies this method generically, without fixture-name or axis branches.
- OCCT could substitute a default for an invalid unit enumeration; import now
  rejects reader entity errors before transferring geometry.
- VTK changed the parent test process's locale. Explicit UTF-8 report/file handling
  and test subprocess decoding fixed the resulting Unicode errors; a report
  regression test covers the C locale.
- Browser assertions initially used a stale table selector and a run label with
  its display prefix. The selectors were corrected. An interrupted rerun and a
  temporary test-port conflict are retained in the local logs. Neither changed
  the solver acceptance criteria.

## Support limits and remaining gates

The [1b contract](milestone-1b.md) governs support: one valid prepared fluid solid,
planar simply connected ports, one inlet and up to four outlets, uniform inlet
flow, equal zero-gauge outlet pressures, and bounded steady laminar physics.
Geometry and mesh checks may reject an input within the preliminary size/Reynolds
limits. Fluid extraction, assemblies, turbulence, automatic numerical recovery,
and general CAD repair are outside this milestone.

Independent CFD review, grid-convergence/mesh-independence studies, experimental
validation, and native Windows/macOS/ARM qualification remain **pending or not
assessed**. The tests support development acceptance, not certified engineering
accuracy or a signed public release. The next roadmap phase is the guided desktop
alpha, with these qualification limits carried forward.
