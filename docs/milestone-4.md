# Milestone 4 — public beta and Managed paygo

This contract implements the roadmap's fourth milestone on the existing local
simulation and BYO workflow. Simulation compute stays on the user's machine.

## Deliverables

- A separately deployable Managed gateway: self-service accounts, revocable
  sessions, hosted prepaid checkout, signed payment webhooks, durable usage and
  payment ledgers, reservations, per-study/account/global limits, reconciliation,
  and operator diagnostics. Monetary arithmetic uses integer microdollars.
- Desktop Managed connection, explicit selection of the billing route, wallet
  and usage display, top-up links, sharing approval, and local-only operation.
- Guided Ubuntu 22.04 / Windows WSL2 setup, versioned worker installation and
  repair, desktop-managed worker startup, distributable build and signing jobs.
- Privacy controls, a previewable minimal support export, onboarding, deployment,
  support/refund procedures, and an evidence-based release checklist.

## Acceptance

Tests must prove authentication isolation, payment signature/amount validation,
duplicate and concurrent delivery safety, reservation hard stops, retry safety,
ambiguous-provider reconciliation, persistence across restart, privacy boundaries,
desktop connection/consent/billing behavior, and installation integrity. Existing
scientific and BYO tests remain required. External service tests are labelled as
simulated unless actually run against the service.

Code completion does not imply publication. Signed Windows/macOS distribution,
live payment/provider acceptance, independent CFD review, licensing review and
observed-user qualification require their own measured evidence. The supported
initial worker remains Ubuntu 22.04 x86_64, including WSL2. macOS/ARM solver
qualification is not established by building the desktop shell.
