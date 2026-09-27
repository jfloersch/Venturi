# Testing Venturi milestone 0

## Quick manual run

Follow the root README setup, then launch `uv run python scripts/dev.py` (browser) or add `--desktop` (native Tauri). After building the desktop, launch it without rebuilding with `uv run python scripts/dev.py --desktop --built`. Linux setup downloads roughly 130 MB of solver packages plus Python/CAD dependencies. The `.tools` runtime and all generated artifacts stay local and are ignored by Git.

1. Confirm both readiness checks pass. An absent solver must produce an actionable diagnostic.
2. Inspect the 10 mm × 100 mm fluid volume. Click the wall and both end faces; check that their roles and areas agree with the face list.
3. Orbit/zoom, toggle wall transparency and the section cut, and reset the camera.
4. Change the only inlet to a wall and save. Expect an error; restore the inlet and save successfully.
5. Run **boundary check**. Expect full mesh-quality success plus inlet/outlet/wall mapping evidence. It should take approximately seconds to tens of seconds on a development workstation; runtime is hardware-dependent.
6. Run **pipe reference**. Expect a pressure drop near 0.323 Pa compared with the 0.320 Pa analytical reference, and all evidence checks passing. A typical run here took about 26 solver seconds; do not use this as a performance promise.
7. Reload the page while solving. The existing run should reappear without starting another solver.
8. Export the completed run. Open the HTML report from the extracted ZIP and inspect `result.json`, `case`, `logs`, VTK fields, and CSV metrics.
9. Start another run and cancel it. Expect a cancelled state and preserved partial logs. Restart the worker and verify that completed runs still appear.

The pipe reference uses a fixed numerical mesh and fixed boundary conditions, independently of the CAD boundary selections. The CAD workflow tests those selections. The interface labels these as separate checks; connecting arbitrary study geometry to a solver is milestone 1b.

## Automated checks

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -m 'not integration'
VENTURI_INTEGRATION=1 uv run pytest
cd apps/desktop
npm run build
npm run test:e2e
npm run tauri build -- --no-bundle
```

Playwright owns ports 1420 and 8765 during its test, starts a real worker with an isolated test token, and performs real OpenFOAM runs. Stop the development launcher first. Install its browser once with `npx playwright install chromium`; Linux CI uses `--with-deps`. Screenshots are saved under `artifacts/screenshots`. Traces for failures are under `apps/desktop/test-results`.

## Reproduce an exported numerical case

Use the exact pinned runtime described in the exported `result.json`. Extract the ZIP into a new location. The native case contains its generated input dictionaries, final fields, and mesh. Run `foamRun -case <case>` in a Foundation-14 environment to recompute from time 0; compare quantities within the documented tolerances. No AI or Venturi service is required. The report records all input/artifact hashes; do not claim byte-identical floating point output across CPU architectures.

## Windows / WSL

The Linux Tauri executable defaults to software rendering on WSL because this machine's GPU driver produced a blank 3D framebuffer without reporting a GL error. The browser viewer works normally. Set `VENTURI_HARDWARE_RENDERING=1` only when deliberately testing another WSL graphics configuration. No system graphics settings are changed.

Use Ubuntu 22.04 x86_64 in WSL2 for the qualified worker baseline. Keep solver files on the Linux filesystem; the app accesses artifacts through the worker rather than continuously reading a Windows-mounted case directory.

For a **native Windows shell**, clone the frontend on Windows, install Node and the Tauri Windows prerequisites, run `npm ci` under `apps/desktop`, and run `npm run tauri dev`. Start `venturi serve` inside WSL and paste its ephemeral worker token into the connection panel, or set `VENTURI_API_TOKEN` in both environments. The endpoint is `http://127.0.0.1:8765`; verify localhost forwarding first. Do not expose the worker on all network interfaces.

`scripts/windows_wsl_probe.py` runs from WSL and uses Windows PowerShell to verify the health endpoint and a Unicode/space-containing file round trip in the test artifact directory. This is a transport/filesystem check, not a native Windows GUI qualification.

## macOS / Apple Silicon

Build the native shell with Node/Rust/Tauri prerequisites on macOS. Run the worker in an Ubuntu Linux VM; evaluate Multipass for lifecycle integration. Port forwarding through an SSH tunnel to local port 8765 is acceptable for the M0 test; the app requires an authenticated loopback endpoint.

The bootstrap script refuses ARM64 until that package set is qualified. `worker/arm64-candidate.json` records the official OpenFOAM candidate; install the matching Foundation version in the test VM, install Python/CAD dependencies, set `VENTURI_FOAM_ROOT`, then run the same CLI and integration suite. Record all dependency versions and compare metrics, not just exit codes. Do not use an emulated x86_64 success as evidence of native ARM performance.

## Platform sign-off checklist

Record OS version, CPU architecture, graphics driver, app/runtime versions, and test date. For each platform record build, launch, visible 3D render, face picking, file selection/export, cancellation, reopen, runtime installation, resource use, and the two numerical/CAD checks. Missing hardware checks stay **not tested**. Unsigned development installers are not a public release.

The worker is a trusted local development tool. It has time/cell limits, but no complete memory/disk sandbox, crash-resume system, or arbitrary uploaded-case executor. Keep the test fixture scope explicit.

## Optional ParaView batch check

The baseline run already exports VTK data. To reproduce the tested standalone ParaView render, install its separately pinned official binary (788 MiB download, about 2.5 GiB extracted):

```bash
python3 scripts/bootstrap_paraview.py
.tools/ParaView-6.0.1-MPI-Linux-Python3.12-x86_64/bin/pvpython --force-offscreen-rendering scripts/paraview_render.py artifacts/YOUR-REFERENCE-RUN/case artifacts/paraview
```

Inspect `velocity-slice.png` and `render.json`. The script rejects empty slices, uses a fixed 0–0.02 m/s speed range, labels the figure, and selects software rendering on WSL. A PNG being written is not by itself a visual pass: inspect that the actual colored velocity field appears.
