# Milestone 3 — implementation and local acceptance

Date: 2026-09-28. Development version: **0.4.0**. Platform: Ubuntu/WSL2 x86_64,
OpenFOAM Foundation 14, pinned package 20260724. The implementation and local
acceptance work are complete. **The full qualification gate remains open for live
OpenAI acceptance and independent CFD review.** Neither is self-certified by the
automated tests below.

Follow-up: [fresh verification and live Codex evaluations](milestone-3-verification.md)
record an additional automated campaign and fixes found with real model responses.
Codex evaluation is distinct from testing the production Responses/API-key connection;
the original measurements below remain a historical record.

## Delivered

- An optional OpenAI Responses adapter with two strict tools: study proposals and
  artifact-linked visual observations. No model tool can launch a job or execute
  shell commands. Model selection and account prices are explicit.
- Context preview and sharing approval bound to exact geometry metadata, study
  revision, annotations and conversation. Unknown physical inputs, incorrect units,
  unresolved questions and unsupported studies block application. Separate approval
  creates an immutable saved revision and records old/new values, sources, rationale,
  approver and dependent runs. Subsequent attempts freeze the approval ledger.
- Session-memory credentials, optional supported OS credential storage, credential
  redaction, persistent request idempotency and spend reservations. Ambiguous provider
  errors retain their reservation and cannot silently resend or duplicate a charge.
- A detached recovery supervisor with explicit plan approval, at most three solver
  attempts and two separate remesh-review attempts, bounded iteration/refinement
  controls, aggregate time/disk guards, cancellation and interruption reconciliation.
  Physical inputs and recipe criteria remain frozen; all attempts and changes persist.
  Sensitivity over 2% stays provisional. Simulation recovery uses no AI calls.
- Scientific pressure/speed views from actual VTK fields: three fixed central cuts,
  mesh edges, nearby port labels, global per-field ranges, SI legends, quantitative
  checks, histories and boundary conditions. Advisory model observations cannot
  override failed checks. Visual packets, images and observations have a separate
  export with SHA-256 hashes; native run exports remain immutable and replayable.
- A separate experimental `sst-straight-duct/1` recipe. It requires a straight smooth
  circular duct, explicit turbulence intensity/length scale, supported port Reynolds,
  positive turbulence fields, converged equations and y+ 30–300 at every wall face.
  Existing laminar recipes and their input/recipe hashes remain unchanged.
- Desktop controls for the complete workflow, including the experimental recipe,
  proposal approval, recovery policy/history/cancellation, visual review and export.

## Measured checks

**205 distinct Python tests passed**, comprising 177 unit/contract tests and 28
actual native integration tests. This is the aggregate of the latest execution of
each test after fixes, not a claim that the first development run passed unchanged.
The machine-readable closeout identifies the contributing reports and superseded
failures. Formatting/lint checks and frontend type checking passed.

**Eight browser scenarios passed.** The six existing scenarios exercise actual
geometry, solver, persistence, camera restoration, cancellation, resource guards and
export. Two new scenarios cover assistant consent/proposal/approval using an
explicitly simulated provider response, plus real recovery cancellation after reload,
scientific image rendering and required turbulence inputs. The assistant scenario
was rerun after fixing an import-completion race in its test harness.

The **native Linux release build passed**. Native smoke checks confirmed restored SST
inputs, the assistant connection controls and disabled unconnected proposal action,
the recovery approval gate, actual scientific image rendering, and visual export
through the GTK save dialog. The saved visual ZIP contains six files plus its
manifest; every recorded file hash was verified. This is automated native acceptance,
not an observed human pilot or Windows/macOS qualification.

| Case | Observed outcome |
| --- | --- |
| Manifold, initial 100 iterations | Residual failure; approved increase to 200, audited mesh regeneration, then all checks passed. Pressure changed by approximately 0.000497%. |
| Held-out rotated duct 1, prescribed flow 1.6e-6 m³/s | Pressure-stability/residual failures at 100; passed at 200. Pressure changed by approximately 0.000284%. |
| Held-out rotated duct 2, prescribed flow 1.6e-6 m³/s | Pressure-stability/residual failures at 100; passed at 200. Pressure changed by approximately 0.1081%. |
| Recovery time, disk and attempt ceilings | Stopped as budget exhausted; no owned active job remained. Insufficient disk for frozen-input copies blocks before launch. |
| Recovery cancellation | Stopped with retained evidence; reload/restart did not duplicate work. |
| SST pipe, Re 20,000 | Converged equations, but y+ approximately 13.52–46.60: correctly failed wall resolution. |
| SST pipe, Re 60,000 | All quantitative checks passed; y+ approximately 37.19–122.95. Native replay agreed within 1e-6. |
| SST downstream friction diagnostic | Darcy factor 0.0207855 versus Blasius 0.0202162: 2.82% difference, inside the predeclared 15% diagnostic tolerance. |

The two held-out recovery cases were generated independently of the deterministic
recovery controller. Their higher prescribed laminar flow was frozen before execution
to exercise genuine initial convergence failures. Both recovered with unchanged
physical inputs. Earlier lower-flow variants converged immediately and are **not**
counted as recovered failures. This small corpus is not a claim about arbitrary CAD
or every possible failure mode. Mesh refinement triggers have contract coverage;
native iteration recovery exercises the audited remesh path, but a broad corpus of
mesh-quality failures remains future qualification work.

## Retained evidence and limits

Local evidence lives under `artifacts/milestone-3/`: native run directories, JUnit
reports, held-out inputs/attempts, the friction report, screenshots, native build
identity and visual ZIP. A passed SST run is
`rans-re60000/runs/4d46a58cff274441bad57a0be16d4298`; its friction diagnostic is
`sst-benchmark.json`. The closeout JSON lists artifact and implementation hashes.

Development failures were retained and fixed: the initial SST compiler lacked wall
distance configuration; Foundation's dimensionless yPlus syntax required explicit
normalization; initial-time yPlus output was disabled to preserve frozen inputs;
the supervisor now waits for manifest finalization; and a browser test now waits for
the imported geometry revision before assigning ports. The deliberately inadequate
wall-resolution case remains failed; no numerical threshold was relaxed to pass it.

The friction diagnostic uses the empirical smooth-pipe Blasius relation described in
[the technical reference](https://www.osti.gov/servlets/purl/6117246). Its fixed
downstream fitting interval and tolerance are in `scripts/check_sst_benchmark.py`.
It does not establish experimental validation or mesh independence.

Open qualifications:

1. Live OpenAI text/vision inference with a user-configured model, key and prices.
   Provider tests used a simulated HTTP transport; browser proposal data was also
   explicitly simulated. No real key, paid call or invoice validation was used.
2. Independent CFD review and sign-off of SST applicability, wall treatment,
   thresholds, benchmarks and required mesh-sensitivity evidence. The recipe remains
   visibly experimental, and passing campaigns remain provisional.
3. Real OS credential-store persistence on supported deployment hosts, native
   Windows/macOS/ARM qualification, and the previously pending observed-user pilot.
   Session-memory behavior, redaction and unavailable-store refusal were tested.

The original roadmap's full milestone-3 exit gate should remain open until its
provider and independent scientific qualifications are completed.
