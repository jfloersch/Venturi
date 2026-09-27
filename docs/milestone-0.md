# Milestone 0 — technical path and platform spike

**Testing baseline:** 2026-09-27. **Stage:** feasibility workbench, pending cross-platform qualification and independent CFD review.

## Implemented scope

- Tauri/React desktop shell, browser testing mode, vtk.js STEP viewer, face picking, transparency, clipping, and a face list.
- Immutable STEP fixture, explicit units, saved revision-bound boundary references, and named surface export.
- Real Foundation-14 reference solve with native case, CSV, convergence plot, field export, and HTML/JSON evidence report.
- Real STEP-to-snappyHexMesh pipeline with mesh quality and CAD-to-boundary checks.
- Authenticated versioned worker API shared with CLI tools, cancel/time limits, persistent run records, duplicate-request prevention, and portable ZIP export.
- Dependency locks, rootless Linux/WSL bootstrap, automated tests, and platform build definitions.

## Fixed reference contract

| Quantity | Value / definition |
|---|---|
| Fluid domain | Circular pipe, radius 0.005 m, length 0.1 m |
| Fluid | Density 1000 kg/m³, dynamic viscosity 0.001 Pa·s |
| Mean inlet speed | 0.01 m/s; normalized parabolic profile |
| Reynolds number | 100 |
| Walls | Stationary, no slip |
| Outlet | Static gauge pressure 0 Pa |
| Requested quantity | Area-averaged inlet minus outlet static pressure |
| Solver pressure | Kinematic pressure, converted to Pa using declared density |
| Analytical reference | Δp = 8 μ L Ū / R² = **0.32000 Pa** |
| Grid | 31,680 cells; five hexahedral blocks, curved outer edges |
| Iteration budget | 500 iterations, 300-second solve deadline |

Provisional acceptance criteria were implemented before evaluating the first reference result: pressure-drop error ≤5%; net flow imbalance ≤0.1%; last-20-iteration pressure range ≤0.1%; final initial equation residuals ≤1e-5; correct prescribed flow direction/magnitude; complete output; full mesh check passes. None of these thresholds are a general CFD accuracy guarantee.

## Measured evidence

| Check | Result on this WSL2 x86_64 machine |
|---|---|
| Pressure drop | **0.32262946 Pa**, **0.8217%** relative analytical difference |
| Net flow imbalance | Approximately **6.1 × 10⁻¹²** of the flow scale |
| Reference mesh | All topology/geometry checks passed |
| CAD mesh | All topology/geometry checks passed after rejecting an earlier concave-cell mesh |
| CAD inlet / outlet area difference | **0.058% / 0.190%** |
| CAD wall area difference | **0.141%** |
| Automated Python tests | 29 passed, including real solver and mesher integration tests |
| Browser tests | 2 passed, including invalid selection, real runs, UI reload, and ZIP download |
| Desktop compilation | Linux release binary built successfully |
| Native Linux/WSLg rendering | Verified with software rendering; the default WSL GPU path produced an empty frame and is not qualified |
| ParaView batch rendering | Official 6.0.1 binary renders the actual solver velocity slice with documented camera, units, and range; software rendering on this WSL machine |
| Windows-to-WSL HTTP | Health/protocol request succeeded from Windows PowerShell |
| Windows/WSL file round trip | UTF-8 Unicode content and a filename containing spaces preserved byte-for-byte |

Tests must be rerun after changes to numerical recipes, dependencies, or runtime builds. Generated reports and screenshots are under ignored `artifacts/`; test code and input fixture are tracked. Build success does not imply installer, rendering, or solver qualification on other OS/CPU combinations.

## Remaining qualification gates

- Native Windows rendering, packaging, file-dialog behavior, and complete workflow on the supported Windows matrix.
- Native macOS rendering/build verification and an actual Apple Silicon ARM64 worker run. The upstream Foundation-14 ARM64 package exists; its candidate details are recorded, but no ARM execution is claimed.
- A reviewed container/image baseline, application-helper bundling, signed/notarized installers, update/recovery UX, and explicit minimum OS versions.
- Pilot recruitment, real customer geometry, and independent CFD review. No customer interviews or external outreach were performed by this implementation task.

The next engineering milestone is a hardened deterministic reference workflow, followed by unseen supported STEP inputs, bends/manifolds, and a guided alpha. AI, RANS qualification, managed billing, and broader CAD preparation retain their separate roadmap gates.
