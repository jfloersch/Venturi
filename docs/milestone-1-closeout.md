# Milestone 1 closeout

This record supplements the [1a closeout](milestone-1a-closeout.md) and
[1b closeout](milestone-1b-closeout.md) for version 0.2.0, baseline commit
`340e379`, on Ubuntu 22.04 x86_64 / WSL2 with pinned OpenFOAM Foundation 14.

**Milestone 1 development acceptance is complete for the bounded Linux/WSL
workflow. Development can proceed to milestone 2, the guided desktop alpha.**
The qualification work listed below remains open before broader engineering or
release claims.

## Additional closeout testing

The final checks target gaps in the earlier acceptance: a fresh installation of
1b, replay of a previously exported case, mesh sensitivity for imported curved
and branching passages, physical-unit scaling, and one uninterrupted browser
suite including the final mesh-render approval guard.

The numerical matrix declares all studies and criteria before execution. Every
mesh and flow must pass the existing recipe checks. Comparing the two mesh
resolutions allows at most 5% pressure-drop change and one percentage point of
outlet-share change. Doubling density and dynamic viscosity at unchanged flow
keeps kinematic viscosity constant: pressure in Pa must double, and flow must
remain unchanged, within 1e-6 relative error. These are provisional regression
criteria, not a formal grid-convergence or physical-validation claim.

## Final results

- **130 Python tests passed** in the isolated Ubuntu environment, including all
  19 actual-runtime integration tests and four new CAD-point regressions. All six
  prepared geometry workflows passed, including both rotated/translated variants.
- **Four browser scenarios passed in one run**, including forced response-order
  races for retry/mesh/solve and the blank-render mesh-approval guard.
- The **native Linux release build**, TypeScript/Vite, Python lint/formatting,
  Prettier and Rust formatting passed. Existing dependency deprecation and bundle
  size warnings remain non-blocking. Remote CI was not run in this session.
- **Fresh installation and portable replay passed.** Ubuntu Base 22.04.5 was
  extracted into a new rootless environment from its checksum-verified official
  archive. Python, CAD libraries and the pinned solver were installed into empty
  caches using the documented prerequisites. The original 107 non-integration
  tests passed there; the exact source/test patch was then copied into that
  checkout for the final 130-test suite. No host Python environment or solver
  installation was reused.
- The corrected worker reproduced the existing manifold ZIP in a Unicode/space
  path with **zero measured difference in pressure and either branch flow**.
  Hash verification passed, and the initial clean-install replay was also
  re-exported and verified.

| Study | Cells | Pressure drop (Pa) | Outcome |
|---|---:|---:|---|
| Bend, 0.8 mm | 7,599 | 0.0351523337 | Passed |
| Bend, 0.5 mm | 31,828 | 0.0357421950 | Passed |
| Bend, doubled density and viscosity | 7,599 | 0.0703046674 | Passed |
| Manifold, 0.625 mm | 35,168 | 0.0424917146 | Passed |
| Manifold, 0.4 mm | 129,298 | 0.0431759133 | Passed |

Bend pressure sensitivity was **1.67801%**;
manifold sensitivity was **1.61019%**.
The largest manifold outlet-share change was
**0.0001639 percentage points**.
The density/viscosity scaling case had **zero measured relative error** in
pressure scaling and outlet flow. Every case passed all mesh/flow checks and
artifact verification, without changing the original criteria or run budgets.

The [machine-readable evidence](milestone-1-closeout.json) records the source and
artifact hashes, exact studies, case metrics, checks, clean-environment details
and log digests. Local logs and full results are in
`artifacts/milestone-1-closeout/`; isolated-test case paths are recorded in the
JSON. Run `scripts/check_internal_matrix.py` to repeat the five-case matrix in a
new directory.

## Defects found and corrected

**Run-list race.** A polling response could display a newly created attempt
before its submission response arrived. Retry, mesh creation and mesh approval
then appended the same run twice, producing duplicate React keys. All submission
paths now replace an existing entry with the same ID. Browser regression tests
hold the real submission response until polling has displayed the attempt,
then require one entry and no duplicate-key errors. The regression failed on
the original code and passed after the fix.

**Curved-passage mesh seed.** At 0.5 mm, the bend's selected point was just inside
the CAD, while its actual background-cell center was outside. The mesher retained
the exterior. Existing volume, boundary-inventory and port-normal checks rejected
that attempt; no solver was launched. The volume discrepancy was 244.163%.
Point selection now checks both the candidate and its cell center against the
CAD, using the actual blockMesh spacing, and avoids background-cell planes.
The corrected 31,828-cell mesh has 0.312% volume error and passes all mesh checks.
CAD regressions at 0.625/0.5 mm failed before the fix; all four tested resolutions
pass afterward. No numerical acceptance limit was relaxed. The six previously
accepted cases retain their original point selections.

The first failed matrix, failing regression logs and browser trace remain under
`artifacts/milestone-1-closeout/`. Fresh attempts use new directories; the failed
results were not overwritten or relabeled as passes.

## Scope of remaining qualification

Independent CFD review of recipe applicability and tolerances, formal grid
convergence, experimental validation and broader customer-CAD qualification
remain pending. Native Windows/macOS/ARM tests and release packaging/signing also
remain separate work. The isolated clean install uses a new Ubuntu userspace and
empty dependency/runtime caches, but shares the host WSL kernel and uv executable;
it is not a separate physical machine or a native Windows/macOS qualification.

The previous native Linux/WSLg interactive smoke remains the GUI evidence for
file pickers, visible rendering, close/reopen, API restart and native export.
This closeout rebuilds the native executable and tests the shared UI through
Playwright; it does not claim a new full native GUI smoke run.
