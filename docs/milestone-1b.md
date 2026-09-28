# Milestone 1b — prepared STEP to report

## Implementation acceptance contract

Baseline: milestone 1a closeout, commit `20efcfa`. Target: Ubuntu 22.04 x86_64 / WSL2,
OpenFOAM Foundation 14. Numerical thresholds below are provisional and declared
before evaluating the new cases. Independent CFD review remains outstanding.

- Import immutable STEP bytes with declared units and exactly one valid, closed
  fluid solid. Reject assemblies, loose surfaces, open/invalid solids, missing
  units and excessive size. This workflow does not extract fluid from a solid part.
- Confirm every face against its geometry revision: one inlet, one to four outlets,
  and walls. Ports must be planar and simply connected. No automatic port inference
  for a user's import. A new revision does not inherit old selections.
- Use a strictly typed SI study, uniform normal inlet volumetric flow, constant
  density/viscosity, stationary no-slip walls, equal zero-gauge-pressure outlets,
  and steady incompressible laminar physics. The full-flow Reynolds estimate at
  every port must be <=200. This is a conservative port screen, not proof that
  every internal constriction remains within a laminar regime.
- Generate a background mesh and snappyHexMesh surfaces for any input meeting the
  contract, including non-convex bends/manifolds. Find and verify an interior point
  with CAD classification and use explicit surface/port-edge snapping. Require >=10 background cells per port hydraulic diameter;
  bound background and final cell counts. Unresolved geometry or mesh failures block
  solving; do not silently simplify/coarsen geometry or loosen criteria.
- Before solving, require checkMesh success, one connected mesh region, exact patch
  inventory, CAD/mesh area error <=8%, volume error <=5%, centroid error <=1% of
  the geometry extent, and outward port-normal agreement >=0.98.
- Mesh and solve are separate durable attempts. Approval binds the study, geometry,
  recipe and exact mesh hash. Show the mesh surface preview and checks. Regeneration
  must match the approved mesh before execution; changed inputs need a new approval.
- Report Pa pressure differences and each outlet's signed flow/split. Require finite
  fields and synchronized complete histories; prescribed inlet flow error <=1e-5,
  mass imbalance <=0.1%, pressure and branch-flow variation <=0.1% over the final
  30 iterations, residuals <=1e-5, outlet backflow <=0.1%, wall leakage <=1e-7 of
  inlet flow, and completed execution. A failed check must not produce a pass.
  Final field counts must match the mesh; native patch fluxes must match their
  recorded sums and absolute sums within 1e-7 of the prescribed inlet flow.
- Retain source STEP, selections, studies, recipe, approval, mesh/solver provenance,
  logs, CSV/plots, native case and VTK fields in portable verified exports. Replay
  regenerates compiler inputs in the pinned runtime, refuses edited native inputs,
  and compares pressure and branch flows within 1e-6 relative tolerance.
- CLI and thin desktop must complete import -> ports -> mesh review/approval ->
  solve -> report/export. Retain the independent 1a pipe reference and existing
  cancellation, retry, idempotency, interruption and resource-limit behavior.

## Predeclared development corpus

Positive cases: prepared circular pipe; a non-convex 90-degree passage; a symmetric
T-manifold with two outlets; a rectangular duct; and perturbed/rotated/translated
geometry generated only for the held-out run, without compiler branches keyed to
fixture names. A second variant (11.7 × 6.8 × 79 mm, oblique rotation and translated
origin) was declared after the first held-out run exposed the need for explicit
feature edges. The original failure remains in the development record. Symmetric manifold outlet shares must agree within 5 percentage
points. A second identical execution must reproduce within the recipe tolerance.
For the uniform-inlet straight-pipe check, report developing-flow entrance effects;
do not compare it to 1a's prescribed parabolic inlet as though the conditions match.
Fit the developed pressure gradient between 40% and 80% of the straight pipe's
length and compare it with Poiseuille's gradient within 5%.

Negative cases cover invalid/open/multiple solids, loose faces, missing units,
scale, stale or incomplete/unknown selections, multiple inlets, missing/too many
outlets, non-planar ports, excessive Reynolds number, insufficient port resolution,
cell budget, failed/disconnected/incorrectly mapped mesh, stale/absent approval,
source/study/recipe/native-input tampering, missing/non-finite/incomplete numerical
histories/fields, backflow/non-conservation/non-convergence, and cancellation/retry.

## Status

Development acceptance passed in version **0.2.0**: 126 Python tests, four browser
scenarios, native Linux/WSLg smoke, six real STEP mesh/flow cases, and verified
portable replay. See the [measured closeout](milestone-1b-closeout.md) and
[machine-readable evidence](milestone-1b-closeout.json). Independent CFD and
platform qualification remain separate gates.

## Method references

The compiler targets the Foundation-14 [snappyHexMesh guide](https://doc.cfd.direct/openfoam/user-guide-v14/snappyhexmesh)
and [surfaceFieldValue implementation](https://cpp.openfoam.org/v14/classFoam_1_1functionObjects_1_1fieldValues_1_1surfaceFieldValue.html).
Runtime source and executable hashes are retained with each result.
