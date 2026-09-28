# Milestone 1a development closeout

**Decision: ready to begin milestone 1b development on the Ubuntu 22.04 x86_64 / WSL2 baseline.**

Completed 2026-09-27 (America/Chicago), following implementation commit `6e23f4f`.
The [machine-readable evidence](milestone-1a-closeout.json) records measured
results, source hashes, run identities, artifact locations and hashes. Original
failures are retained alongside successful follow-up attempts.

## Clean installation and replay

A checksum-verified Ubuntu Base 22.04.5 filesystem was provisioned in a rootless
Bubblewrap container. The source came from a Git archive; Python, CAD, OpenFOAM
and Node dependencies were downloaded into fresh caches. The container shared
the WSL kernel and the host's uv executable; this was not a separate VM or a
native Windows test.

- Both worker readiness checks passed after installing the documented OS libraries.
- The original reference ZIP verified and reproduced with **zero pressure difference**.
- The replay exported again and its ZIP verified successfully.
- The final Python suite passed **72 tests in 145.50 seconds**, including real OpenFOAM execution.
- The runner-death test passed **10 additional repetitions**.
- Fresh Node 22.23.3 `npm ci` and the frontend production build passed.
- The host's targeted runtime/job tests passed **15 tests**; Ruff lint/format and Git whitespace checks passed.

The diagnostic and asynchronous-test corrections described below were copied
into the clean checkout before the final suite. No numerical compiler, recipe,
or acceptance threshold changed. Logs and the replay archive are retained under
`artifacts/m1a-closeout/`.

## Native desktop smoke test

The actual Linux Tauri release window was exercised through GTK accessibility
and X11 input, with WSLg and the application's llvmpipe rendering default.
Screenshots were inspected. The Windows computer-use bridge could not initialize
for this WSL workspace, so it was not used as evidence of Windows qualification.

The following passed:

- Visible geometry; wall and both end-face picking; orbit, zoom, transparency,
  section cut and camera reset.
- Invalid inlet-to-wall assignment was refused; restoring the inlet saved correctly.
- The actual CAD mesh passed quality, boundary completeness, area and centroid checks.
- During actual OpenFOAM execution, the window closed normally, the API restarted,
  and the reopened window showed the **same active run and runner identity**.
- UI cancellation stopped the solver processes and retained partial evidence.
- UI retry created a new attempt linked to the cancelled parent and passed.
- The native save dialog wrote a ZIP to a path containing spaces and Unicode.
  Integrity verification passed; the extracted HTML and convergence image rendered
  without page errors and were visually inspected.

Native records, screenshots, the ZIP and the extracted report are under
`artifacts/m1a-closeout/native/`. The temporary test session was stopped and the
usual development launcher was reopened against the original workspace store.

## Parameter and grid checks

Each completed case passed the unchanged recipe checks and artifact verification.
Expected pressure scaling was declared before execution, with a 5% relative
ratio tolerance. Radius scaling holds mean inlet velocity fixed. The largest
scaling discrepancy among the five physical variations was **0.1571%**.

| Case | Cells | Pressure drop (Pa) | Analytical error |
|---|---:|---:|---:|
| Baseline | 31,680 | 0.32262946 | 0.8217% |
| Double length | 63,360 | 0.64497787 | 0.7778% |
| Half radius | 31,680 | 1.28918395 | 0.7175% |
| Double mean velocity | 31,680 | 0.64627245 | 0.9801% |
| Double density | 31,680 | 0.32313622 | 0.9801% |
| Double viscosity | 31,680 | 0.64463272 | 0.7239% |
| Coarse grid | 3,960 | 0.32887592 | 2.7737% |
| Fine grid | 106,920 | 0.32133585 | 0.4175% |

The grid trend meets the declared criterion: fine error < baseline error < coarse
error. This is a grid sensitivity sanity check, not a formal mesh-independence
study or experimental validation.

## Findings and corrections

1. **Clean Ubuntu lacked `libGL.so.1`.** Installing the CAD Python extra alone was
   insufficient. README and Linux CI now explicitly install `libgl1` and
   `libxrender1`, alongside the C/C++ runtime dependencies. Diagnostics distinguish
   a missing Python package from an installed binding that cannot load native
   libraries; two regression cases cover the distinction.
2. **The runner-death test asserted too early.** One clean-suite run observed the
   previous running state while process/file-lock cleanup was still in flight.
   The test now uses bounded reconciliation polling, matching the API/CLI, and
   still requires interrupted state, stopped descendants, retained logs, verified
   artifacts and explicit retry. The full suite and ten further repetitions passed.
3. **The fine grid reached its 300-second solver limit under concurrent load.**
   The failure was correctly retained and did not report a numerical pass. An
   explicit fresh attempt used the same grid, physics and 500 iterations with a
   900-second solver limit and 1,200-second total budget; all checks passed.
   The reusable matrix driver includes this budget and handles unavailable metrics
   in failed cases. The first attempt remains under `matrix/runs/fine`; the completed
   follow-up is under `fine-retry`.

To repeat the complete declared matrix, choose a new output directory:

```bash
uv run --locked python scripts/check_reference_matrix.py --output artifacts/pipe-matrix-new
```

## Remaining scope

Native Windows/macOS shells, native ARM execution, a fresh separate kernel/VM,
independent CFD review and milestone 1b's geometry corpus remain unqualified.
These results support starting 1b implementation. They do not establish a
commercial release, broad STEP support, or general engineering accuracy.
