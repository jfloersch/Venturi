# Milestone 4 — implementation and local acceptance

Date: 2026-09-28. Version: **0.5.0**. Baseline: Ubuntu 22.04 / WSL2 x86_64,
OpenFOAM Foundation 14 package 20260724.

Follow-up: [extended resilience and Windows acceptance](milestone-4-resilience.md)
records subsequent fixes, fresh non-payment tests, native Windows installation
and current artifact locations. Counts and hashes below describe the initial
acceptance snapshot; the follow-up is authoritative for the updated implementation.

**The implementation is built and locally tested. The public-beta release gate
remains open for live services, signing and independent/platform qualification.**
No public service was deployed, installer published, customer charged or real
OpenAI API request made during this milestone's acceptance tests.

## Delivered

- Separate Managed gateway with self-service account creation, salted password
  hashes, expiring/revocable sessions and rotating account recovery codes.
- Hosted prepaid checkout, raw-body webhook signature verification, durable
  payment/order deduplication, partial/full refund handling and dispute freezes.
- Integer-microdollar wallet accounting and atomic reservations; workspace,
  study, per-request, account and global limits; explicit tariff approval;
  bounded provider calls; retained ambiguous reservations and audited settlement.
- Desktop Managed selection, wallet and payment history, checkout, per-study
  limit approval, cached-output deletion, estimates and sharing consent.
  Interrupted response recovery restores a reviewable proposal without another
  inference call. BYO and manual local execution remain available.
- Guided Ubuntu/Windows-WSL setup and repair, private Python/CAD/solver installation,
  installed-worker startup/reconnection, versioned application directories,
  active-work protection, package hashes and bundled demonstration resources.
- A built Linux DEB, worker payload and source archive; Linux/Windows release CI;
  optional Windows Authenticode and detached signatures using release credentials.
- GPL-3.0-only application license, third-party inventory, onboarding, deployment,
  retention/reconciliation instructions and support/refund workflow. Final
  distribution review is still required before publication.
- Allowlisted support previews/exports that exclude project content, prompts,
  logs, credentials, filesystem paths and account identifiers.

## Acceptance evidence

| Check | Measured result |
| --- | --- |
| Final Python unit/contract suite | **208 passed**, 28 real integrations selected separately |
| Real solver/CAD/recovery integration suite | **28 passed**, no integration skips; 17m52s |
| Distinct Python outcomes | **236 passed** across the unit and integration suites |
| Gateway and local Managed adapter acceptance | **27 passed**; included in the unit count |
| Full browser workflow suite | **10 passed** in the final complete run |
| Frontend and native Linux release build | Passed; Linux DEB produced |
| Embedded payload integrity | Every recorded payload SHA-256 verified after extracting the DEB |
| Fresh installation and repair | Passed in an isolated installation folder |
| Installed worker startup outside checkout | Authentication, CAD fixture, diagnostics and same-session reconnection passed; session file mode 0600 |
| Simulation from installed environment outside checkout | Pipe reference passed: **0.3226294589544 Pa**, analytical error **0.821706%**, mass imbalance **6.11e-12** |
| Packaged native desktop | Extracted DEB launched under Linux/WSLg and automatically connected to the installed worker; account/privacy/support and geometry controls present |
| Native geometry rendering | WebGL reported **12,571 visible pixels**, GL error **0** |
| Installed gateway | Entry point and real HTTP readiness/catalog checked with registrations closed; no external service calls |
| Python lint/formatting, Rust formatting, whitespace | Passed |

The gateway tests use simulated OpenAI and Stripe transports. They exercise account
isolation/recovery/revocation, invalid/expired payment signatures, amount mismatch,
concurrent duplicate fulfillment, refunds, reservation races, exhausted balances,
study and daily caps, changed tariffs, unauthorized tools/images, provider failures,
timeouts, restart persistence, cached-output retention/deletion, proposal approval
and recovery after a lost gateway response. Provider keys and account credentials
are checked for absence from persisted project records and support exports.

Browser tests retain real local geometry, meshing, solving, recovery and export
workflows; Managed payment/provider responses in the new UI test are explicitly
simulated. The installed numerical run and the 28 integration tests use the real
pinned OpenFOAM executable. No numerical acceptance threshold was relaxed.

Local evidence is under `artifacts/milestone-4/`; release artifacts are under
`artifacts/release/` and `apps/desktop/src-tauri/target/release/bundle/deb/`.
The companion JSON record binds selected evidence and artifacts by SHA-256.

## Retained development findings

- Initial tariff validation rejected decimal strings under strict model validation;
  the trusted tariff fields now explicitly parse decimals while payment amounts
  and request limits remain strict integers.
- A lost gateway response initially left only local accounting to refresh. Recovery
  now restores and validates the cached proposal, preserving its original approval
  hash and preventing a second provider call.
- Installer maintenance now locks startup and admission, refuses active work, and
  stops only its recorded idle API process before changing dependencies.
- The first targeted rerun of the existing recovery browser scenario omitted the
  earlier tests that create its manifold fixture. It failed at the missing-fixture
  assertion; the final full ten-test run passed with its required setup.
- The first native accessibility probe looked for an anonymous connection-status
  span by accessible name. The corrected probe scopes itself to the app process
  and verifies loaded geometry/account controls, installed-worker startup and the
  renderer's visible-pixel report.

Non-blocking build/test output includes the existing frontend chunk-size notice,
Starlette/NumPy/VTK deprecation notices and a local GLib proxy warning. Native CAD
rendering and the tested workflows passed despite those environment notices.

## Remaining release gates

1. Deploy to an approved HTTPS hostname using real provider/merchant credentials;
   validate live OpenAI behavior, actual prices/usage, Stripe test/live payment
   reconciliation, backups, retention and operational support.
2. Supply signing credentials, run the release workflow and verify the resulting
   signatures. The locally built artifacts are **unsigned**.
3. Run native Windows/WSL clean-install and OS credential-store acceptance on the
   supported machine matrix. Linux/WSLg success is not native Windows evidence.
   macOS/ARM worker support remains unqualified.
4. Complete the observed-user pilot, independent CFD review and the roadmap's
   licensing/security/distribution review; approve final customer-facing terms.

These external gates remain visible. This closeout does not declare a launched,
scientifically qualified, cross-platform public beta.
