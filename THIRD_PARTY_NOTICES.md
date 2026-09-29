# Third-party components and distribution inventory

Venturi application code is licensed under GPL-3.0-only; see LICENSE. Dependencies
retain their own licenses. This inventory is not a substitute for the distribution
review required before public installers are published.

| Component | Upstream and license information | Distribution |
| --- | --- | --- |
| OpenFOAM Foundation 14 | https://github.com/OpenFOAM/OpenFOAM-14 — GPLv3 | Installer downloads the pinned upstream Ubuntu package; version and SHA-256 are in worker/runtime.lock.json |
| Ubuntu runtime libraries | https://packages.ubuntu.com/jammy/ — per-package copyright files | Installer downloads the exact packages in worker/system-dependencies.lock.json; extracted copyright files remain in the installation |
| uv 0.12.7 | https://github.com/astral-sh/uv/tree/0.12.7 — MIT OR Apache-2.0 | Bundled Linux installer bootstrap; upstream license texts are included in release payloads |
| CPython | https://docs.python.org/3/license.html | Private managed Python installation, downloaded by uv |
| python-build-standalone | https://github.com/astral-sh/python-build-standalone | Supplies the managed interpreter and its dependency notices |
| Open CASCADE / cadquery-ocp | https://github.com/CadQuery/OCP | Python wheels with native CAD bindings; preserve wheel license files |
| VTK / vtk.js | https://vtk.org/about/ and https://github.com/Kitware/vtk-js | Python scientific rendering and desktop visualization |
| Tauri and Rust dependencies | https://github.com/tauri-apps/tauri | Desktop shell; exact dependencies in Cargo.lock |
| React, Vite and other JavaScript dependencies | Exact dependencies and upstream metadata in apps/desktop/package-lock.json | Desktop UI |
| Python libraries | Exact versions and artifact hashes in uv.lock / release requirements.txt | Worker and gateway |

Release preparation must retain dependency license files, generate dependency
inventories, archive the corresponding source and build scripts for distributed
components as required by their licenses, and have the final combination reviewed.
The private product roadmap is excluded from source packages. A binary artifact
or CI signature does not establish licensing review or scientific qualification.
