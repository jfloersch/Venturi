# Architecture decisions — milestone 0

## Shared desktop, independent numerical worker

The desktop is Tauri 2 + React/TypeScript, with vtk.js for the CAD viewer. A Python service owns deterministic simulation tools. The desktop calls `venturi.worker.v1` over authenticated loopback HTTP. The same Python workflows power the CLI. React contains no solver dictionaries or acceptance rules.

The development shell connects to an already-running worker on port 8765. `scripts/dev.py` starts both processes and supplies an ephemeral token to the native shell or browser session. The compiled shell alone does not install or bundle Python/OpenFOAM. Packaging the application helper, runtime setup wizard, and signed public installers are subsequent work.

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

The triangle representation retains each CAD face's identity. Saved boundary roles generate named STL surfaces plus a CAD-to-surface manifest. The actual mesh boundary names, areas, and centroids are audited against that manifest. The M0 mesher intentionally targets the supplied convex pipe. Arbitrary STEP files can be inspected by the CLI; general interior-point finding and supported STEP-to-solver workflows belong to milestone 1b.

## Scientific evidence

Two independent workflows avoid confusing numerical verification with CAD meshing:

1. The **reference** uses a deterministic full 3D pipe mesh and a normalized parabolic inlet profile. It compares area-averaged pressure loss with Hagen–Poiseuille, with units and pressure conventions checked in the actual output.
2. The **CAD boundary check** imports the STEP fixture, exports named surfaces, runs `snappyHexMesh`, and checks the final mesh and mappings. It does not solve flow.

The first CAD mesh had four concave cells under `checkMesh -allGeometry`. It was rejected. The final fixed-fixture mesh uses a uniformly fine background and aligned end planes, eliminating the failed cells without relaxing the checks. Every failed development artifact remains local under `artifacts`.

## Execution and storage

Worker commands are allowlisted argument arrays, never user-interpolated shell commands. Only generated cases are executed. The worker records each request before execution, limits itself to one active job, rejects conflicting idempotency keys, and retains separate attempt directories. Cancel and timeout terminate the process group. Core scientific status never depends on model output.

JSON manifests and relative artifact paths are authoritative in M0; SQLite indexing is deferred until the persistent study model is needed. The app reloads completed runs from disk. Worker restart labels unfinished attempts interrupted; automatic resume and reconciliation of externally orphaned processes are not implemented. Execution has per-tool deadlines and fixture cell-count bounds. Per-job RAM/disk enforcement is a later requirement; do not advertise a sandbox or a complete resource-governance system.

No external AI, billing service, account, or network upload is involved in a run. Setup downloads dependencies. The worker binds to loopback, requires a token for project data/actions, and restricts browser origins. A future remote worker needs an authenticated transport; do not expose this development server directly to a network.
