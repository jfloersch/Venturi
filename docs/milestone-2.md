# Milestone 2 — guided desktop alpha

The roadmap contract is a supported study completed through the desktop: STEP
viewer, references and annotations, study forms, resource controls, mesh gate,
result page, read-only case viewer, and save/reopen. This milestone builds on the
bounded `laminar-internal/1` recipe; it does not add physics or automatic recovery.

Implementation acceptance:

- Inspect dimensions, units, faces and edges; isolate, clip and annotate references
  bound to the exact STEP revision, with position and camera context.
- Save named, versioned studies in the worker with operating inputs, material
  source, question, presentation preference and enforced resource limits.
  Reopen after UI/API restart; detect conflicting saves and changed geometry.
- Review a validated study summary before meshing. Keep input provenance explicit,
  show the fixed recipe assumptions and provisional cell estimate, and require
  acknowledgement of the fluid domain and study before execution.
- Approve only the mesh actually rendered for the saved inputs. Identify historical
  results after draft changes, and never transfer approval to edited inputs.
- Inspect outputs, checks, convergence, native case files, logs, commands and file
  differences without editing solver files; download reports, CSV and full archives.
- Exercise the complete workflow with the real worker in browser automation, plus
  worker/API tests for persistence, revision conflicts and safe artifact access.

The human pilot exit gate requires an observed target user. Automated UI acceptance
is implementation evidence and does not substitute for that usability observation.
Native Windows/macOS and independent CFD qualification remain separate gates.

## Pilot walkthrough

Launch `uv run python scripts/dev.py` (browser) or add `--desktop` for Tauri.
The local environment prerequisites in the README still apply. Release packaging
and guided Windows/WSL installation belong to milestone 4.

Use `fixtures/internal/manifold.step` for a first observed session:

1. Import it and confirm the 60 × 10 × 55 mm bounding dimensions and millimetre
   source units. The displayed volume is the fluid, not the surrounding material.
2. Assign Face 3 as inlet and Faces 4 and 5 as outlets. Leave Faces 1 and 2 as
   walls. Save assignments. Add a named face or edge note; orbit the model and
   click the note to restore its saved camera and selection.
3. Enter a study name and question about outlet flow balance. Enter inlet flow
   `1.5707963267948966e-7` m³/s, density `1000` kg/m³, dynamic viscosity `0.001`
   Pa·s, and cell size `0.625` mm. Identify these as the supplied fixture fluid
   assumptions in the property-source field. Review resource limits.
4. Save, close/reopen the UI, then reopen the saved study from the sidebar. Confirm
   all inputs and annotations. Review the study, acknowledge the plan and build
   the mesh. Inspect the boundary rendering, cuts and numerical checks.
5. Edit the inlet flow in the draft and return to the mesh: it must be labeled
   historical and cannot be approved. Reopen the original saved study to restore
   its inputs; inspect and approve the corresponding mesh.
6. Solve. Inspect pressure loss (approximately 0.04249 Pa for this fixture), two
   approximately equal outlet shares, convergence, and the stated limitations.
   Expand the native case viewer, inspect `case/0/U`, `case/0/p`, mesh mapping and
   solver logs, and compare `study.json` with the mesh attempt.
7. Download metrics and the full run. Verify with `uv run venturi verify <zip>`.
   Confirm the export contains the frozen engineering question, material source,
   annotations, STEP, case, fields and checks.

Record hands-on time separately from meshing/solving. Record every place the user
needs help or edits outside the UI. The roadmap's human exit gate remains open
until an observed target user completes this workflow without case-file editing.

## Boundaries of this alpha

- The solver is the existing serial Linux/WSL worker. CPU count, automatic attempt
  counts and AI spending are fixed, explained in the resource plan, and not
  presented as configurable capabilities.
- Cell-count ranges are geometric estimates, not calibrated memory or runtime
  predictions. Actual mesh checks, cell budget and process limits still decide
  whether an attempt may complete. Limits apply independently to each attempt.
- Annotation anchors use a vertex of the selected face/edge, with exact entity
  metadata and the current camera. There is no arbitrary CAD remapping or repair.
- The case viewer is read-only and has a 512 KiB text-preview limit; large/binary
  artifacts are downloadable. It provides dictionary highlighting, file differences,
  field-entry shortcuts and the CAD-to-patch mapping record.
- UI reload restores the selected run/tab and browser drafts. Saved study revisions
  and annotations are worker-owned and survive browser-storage removal and API
  restart. Reopening a saved study deliberately restores the saved version.
- Independent CFD review, broader customer CAD, native Windows/macOS execution and
  release packaging are not qualified by this implementation.

## Automated acceptance record

See [the implementation closeout](milestone-2-closeout.md) for measured test
results, portable replay, budget-stop evidence and remaining gates.
