# Install and use the beta workbench

The first worker release targets Ubuntu 22.04 x86_64, including Windows WSL2.
The macOS shell can be built, but its ARM/VM solver workflow remains unqualified.
Use the measured platform matrix in the closeout before making support claims.

## Native desktop

1. Install the provided DEB or Windows installer. Verify its published checksum
   and signature when using a signed release.
2. Open Venturi. On Windows, choose **Install Ubuntu 22.04 with WSL** if needed.
   Windows may ask for administrator approval and a restart. Open Ubuntu once,
   choose your Linux account, then return to Venturi.
   An existing Ubuntu 22.04 distribution named `Ubuntu` is recognized as well;
   the desktop checks its actual OS release before using it.
3. In **Guided local setup**, install Ubuntu prerequisites if they are missing,
   then choose **Install or repair local worker**. The installer downloads a
   private Python runtime, locked CAD/application dependencies and the pinned
   solver. You do not need a developer checkout, Node, Rust, or your own Python.
4. Wait for setup to complete, then choose **Start installed worker**. Future
   launches reconnect to that worker automatically. Setup details stay local.
5. Open the bundled pipe demonstration without any AI account. For your own
   geometry, import a prepared watertight fluid-volume STEP file, confirm units,
   identify inlet/outlet faces, enter sourced inputs, inspect and approve the
   generated mesh, then solve and inspect the numerical checks and report.

Runtime downloads require internet access and several gigabytes of disk space.
Installed files are under `~/.local/share/venturi` inside Linux/WSL. Versioned
application environments and their solver runtimes are in `releases/`; projects
and results are in `projects/`; verified solver downloads are cached in `solver/`.
Uninstalling the desktop
does not delete your projects. Export and back up projects before removing them.
The `current` link selects the active application environment; older versions are
retained for recovery. Stop active work before repairing or changing runtimes.
Repair builds a separate environment before changing `current`, so a failed or
interrupted installation preserves the previous working version. A hard power-off
may leave an unused installation directory; it does not become the active version.

If setting `VENTURI_INSTALL_DIR`, use an absolute Linux path containing only ASCII
letters, digits, `/`, `.`, `_` and `-`. OpenFOAM cannot run from a path with spaces
or other characters. Setup rejects such paths before changing the installation.
The extracted installer payload itself can be in a directory containing spaces.

Closing the desktop does not cancel a simulation or stop the installed worker.
Use the application to cancel a run. The private worker-session file is restricted
to the local account. Do not share it or the installation logs without review.
The worker listens only on loopback; it is not a remote worker service.

## Choose AI access

Local simulation works without AI. The study assistant can connect to your own
OpenAI account using a supported model and declared prices. Alternatively, open
**Account, privacy and support → Venturi Managed** in a build configured with a
gateway. Review its tariff, create an account and save the recovery code, then
sign in and explicitly select Managed billing with workspace and study limits.

Managed uses prepaid dollars. **Add $20.00** or another published amount creates
a hosted checkout link. Open it in your browser and complete payment there, then
refresh the wallet. Payment details do not pass through Venturi's desktop. A
return from checkout does not itself credit the wallet; signed payment
confirmation does. If checkout is unresolved, retain the order ID for support.

Preview shared context and the maximum reservation before requesting a proposal.
Approval to send context is separate from approval to apply the proposed inputs.
Visual review is advisory; a model cannot overrule failed numerical checks.
Disconnecting Managed leaves all local studies and exports available. Switching
to BYO is an explicit connection action, and there is no automatic paid fallback.

## Troubleshooting and support

- **Missing runtime:** run installation/repair and inspect the local setup details.
- **Missing graphics libraries:** use **Install Ubuntu prerequisites**, then retry.
- **Wrong Linux version or ARM:** use the declared Ubuntu 22.04 x86_64 environment.
- **Port already in use:** an existing worker may belong to another session.
  Connect using its token or stop that API process before starting the installed
  worker. Do not terminate a simulation process to clear an API-port conflict.
- **Expired Managed session:** sign in again; balances and usage remain on the gateway.
- **Changed tariff:** review the new prices and reconnect before authorizing a call.
- **Uncertain AI charge:** refresh accounting; an operator must reconcile ambiguous
  provider outcomes before the reservation is released. Repeating a request ID
  never sends another model call.
- **Study limit:** explicitly approve a new limit in the wallet's **Study limits**.
  Workspace and service-wide limits still apply.
- **Need support:** preview and save the minimal support report. Send it through
  the release's documented support channel. Never send passwords, API keys,
  recovery codes or the private worker-session file.

## Build artifacts from source

```sh
uv run python scripts/build_release.py
cd apps/desktop
npm ci
npm run tauri build -- --bundles deb --config ../../artifacts/release/tauri.release.json
```

The payload builder requires Linux x86_64 and pinned uv 0.12.7. It includes the
application wheel, hash-locked requirements, dependency inventories, bootstrap
binary and license notices. It also emits a source archive and release manifest.
For the direct worker installer, extract the generated worker archive and run
`bash worker-payload/install-worker.sh` on the supported Ubuntu baseline.

The **Build reviewable beta installers** CI workflow builds the Linux payload and
native Linux/Windows installers. Windows Authenticode and detached artifact
signing are opt-in inputs that require the protected release environment's
credentials. It uploads artifacts for review; it does not publish a release.
No auto-update mechanism or background update installation is introduced.
