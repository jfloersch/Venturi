# Milestone 3 — fresh verification and live Codex evaluations

Date: 2026-09-28. Version: 0.4.0. Platform: Ubuntu/WSL2 x86_64 with the pinned
OpenFOAM Foundation 14 runtime. This is a fresh automated verification campaign,
including live inference through `codex exec`. It is not independent CFD sign-off
or qualification of the production OpenAI Responses connection.

## Results

| Check | Result |
| --- | --- |
| Existing full Python suite | 205 passed in 17 minutes, including all 28 real native integration tests |
| Final unit/contract suite after fixes | 181 passed, including four new Codex reader regression cases |
| Distinct latest Python outcomes | 209 passed: 181 unit/contract and 28 native integration |
| Browser workflows | All eight passed in one run |
| Frontend and native Linux release builds | Passed |
| Lint, formatting and whitespace checks | Passed |
| Browser-exported manifold archive | All 180 recorded file hashes verified |
| Live proposal suite after the prompt correction | All 12 scenarios passed; two additional unit-conversion repetitions also passed |
| Live visual evaluations | Four passed: two reviews each of a passing and a failing solver result |
| Fresh SST friction diagnostic | 2.82% difference from Blasius, within the declared 15% diagnostic tolerance |

The distinct Python count combines the fresh full-suite report with the final
181-case unit report, taking only the latest result for each test. It does not add
repeated executions together. No native integration tests were skipped in the full run.

The real solver tests cover approved recovery, API reconnection, cancellation,
time/disk/attempt ceilings, two held-out rotated ducts, unchanged physical inputs,
SST wall-resolution acceptance/rejection, scientific field images and native replay.
The Re 20,000 SST case remains failed for wall resolution. The Re 60,000 case passes
its quantitative checks and reproduces within the original 1e-6 tolerance. No
acceptance criterion was relaxed.

## Live proposal evaluations

The harness uses the already configured `gpt-6-astra` model at `xhigh` effort and
saved ChatGPT sign-in. Twelve predefined cases test:

1. Complete SI inputs.
2. Conversion from L/min, g/cm³, cP and mm.
3. Missing flow.
4. Missing material properties.
5. Contradictory flow measurements.
6. Unsupported compressibility, heating and boiling.
7. Flow outside the supported laminar/SST Reynolds envelopes.
8. Changing one saved input while preserving the others.
9. Rejecting approval after a newer revision is saved.
10. SST without required turbulence inputs.
11. Complete experimental SST inputs.
12. An annotation attempting to substitute density, hide the change and declare success.

Expected numerical values and blocking behavior are specified before inference.
Real output passes through Venturi's proposal validation, geometry screening and
approval code. Assertions also require no study mutation or solver launch before
approval, preserved port selection, one revision per approval and no second model
call when the same request is repeated. The model does not grade itself.

The initial 24 executions produced 23 passes and one unnecessary clarification.
The corrected instructions passed all twelve scenarios, and unit conversion passed
three times after the correction. Original failures and responses remain available.

## Issues found and fixed

- **Unnecessary clarification:** a complete laminar proposal asked whether the inlet
  was fully developed, because the model had introduced a Hagen–Poiseuille estimate.
  Venturi's numerical recipe specifies a uniform-normal inlet and does not require
  that analytical assumption. The prompt now distinguishes required numerical-study
  inputs from analytical estimates and places convergence/mesh limitations in the
  explanation. Genuine missing, contradictory and unsupported requests still block.
- **Unicode in the test reader:** two successful visual responses contained literal
  Unicode that the new harness tried to decode using an ASCII locale. Explicit
  UTF-8 handling fixes this. The original responses were revalidated against the
  same payloads without further provider calls. Four offline regression cases cover
  Unicode, forbidden tool events and incomplete turns.
- **Formatting:** six existing Python files needed formatting. Their numerical
  behavior and acceptance thresholds were preserved.

The first frontend build invocation selected Windows npm through the WSL PATH.
Using the repository's existing Linux Node runtime resolved the environment issue;
both frontend and native builds then passed.

## Visual evidence and qualification limits

Visual evaluations use actual pressure/speed slices, SI scales, native mesh sampling,
port locations and numerical checks. Code validates the observation schema, artifact
references and unchanged source results. Developer inspection additionally compares
observations with the supplied fields: axial pressure decrease, faster core/slower
near-wall cells, nonzero adjacent-cell speed versus no-slip wall values, and the
inability to infer recirculation from speed magnitude alone.

Both failed-case reviews explicitly retain the failed result, cite y+ 13.52–46.60
against the existing all-wall-face 30–300 requirement, and request a wall y+ map
before revising first-cell spacing. They do not recommend overriding the criteria
or assume that refining the mesh will repair a low-y+ failure.

This developer inspection is not independent scientific qualification. The live
Codex adapter uses structured final JSON in place of function arguments and has a
different surrounding inference environment from the production Responses adapter.
Its tests do not exercise real API-key authentication, Responses HTTP behavior,
usage charging or OS credential-store persistence.

Remaining qualifications are the live production OpenAI connection with configured
credentials/model/prices, independent CFD review including mesh sensitivity, and
the previously pending deployment-platform and observed-user acceptance. The SST
recipe remains experimental and recovered campaigns remain provisional.

## Reproduction and evidence

Use [the testing guide](testing.md#live-codex-evaluations) and
[`check_codex_assistance.py`](../scripts/check_codex_assistance.py). The opt-in
harness limits each case to one model invocation, a 300-second timeout, a fresh
context, disabled optional execution tools and a read-only sandbox. It retains
prompts, schemas, raw event streams, responses, expected outcomes and assertions.

Evidence is in `artifacts/milestone-3-verification-20260928/`; the companion
`milestone-3-verification.json` records report and source hashes. Earlier browser
screenshots and exports were backed up, restored and checked so this rerun did not
replace the historical milestone evidence.
