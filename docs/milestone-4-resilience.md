# Milestone 4 — extended local acceptance

Date: 2026-09-28. Application version: 0.5.0. The campaign excludes Stripe testing
and direct OpenAI API calls. Real model evaluations use saved Codex sign-in through
the inference-only `codex exec` adapter. Simulation tests use real OpenFOAM 14.

## Results

| Check | Result |
| --- | --- |
| Python unit/contract/fault suite, excluding payment tests | **243 passed**, no skips |
| Fresh real solver/CAD/recovery integration | **28 passed**, no skips; 21m19s |
| Distinct latest Python outcomes | **271 passed** across both suites |
| Browser workflows with checkout disabled | **11 passed**, including reconnect recovery |
| Concurrent HTTP retries | 800 requests / 24 threads; exactly 200 model calls and charges |
| Shared accounting across processes | 640 reservation attempts / 8 processes; exactly 50 accepted at the shared limit |
| Sustained real loopback HTTP | 4,347 requests over 120.35 seconds / 16 clients; zero errors; 2,176 unique requests and simulated provider calls |
| Sustained latency on this shared test machine | p50 0.123 s, p95 1.440 s, p99 2.572 s |
| Accounting conservation and database integrity | Exact balances and charges; SQLite integrity check passed |
| Crash boundaries | Process death before/after reservation and settlement commits; HTTP gateway killed during provider wait and after settlement before reply |
| Online backup and restore | Consistent WAL snapshot during 150 concurrent reservation/settlement operations; account, limits, holds and authentication preserved |
| Storage exhaustion | SQLite page limit triggers a real full-database error; settlement rolls back and retains the reservation |
| Installer faults | Corrupt payload, corrupted/interrupted download, injected storage error, failed dependency/solver setup, SIGKILL, locks and active work |
| Real installed worker | Clean install, repair, startup outside checkout, reconnect, private session, preserved project and previous release |
| Codex proposals | **12/12 passed**: missing/conflicting inputs, unit conversion, unsupported physics, stale approval, SST and annotation injection |
| Codex visual evaluations | **2/2 passed** on passing and failing internal-flow fields; source results unchanged; developer image review completed |
| Native Linux/WSLg shell | Extracted final DEB connected to installed worker; WebGL 2.0, 12,571 visible pixels, GL error 0 |
| Installed numerical reference outside checkout | Passed: 0.3226294589544 Pa; 0.821706% analytical difference; mass imbalance 6.11e-12 |
| Native Windows x64 | NSIS build and isolated install passed; guided WSL setup, actual geometry rendering, automatic connection, same-session reconnect and support consent passed |
| Native Windows repair and simulation | Real pipe simulation passed; repair rejected during active work; idle repair and reconnect passed; all three test results retained |
| Credential persistence failures | Unsupported/plaintext backend rejected; failed keyring save revokes the new session; no plaintext fallback |
| Release payloads | Installed Windows and extracted Linux payload hashes verified against current Python and installer source |

The companion JSON and retained logs record final integration and native repair
outcomes. Evidence lives in `artifacts/milestone-4-resilience/`. Updated unsigned
installers and the worker payload are in its `release/` directory.

Test balances are explicit fixtures. The fault/load gateway uses an offline model
transport and rejects any payment transport invocation. Local adapter tests now
seed their own test balances, independently of payment tests. Browser checkout
testing is disabled with `VENTURI_SKIP_PAYMENT_TESTS=1`; tariff consent, wallet,
disconnect and support export still run. The real load test exercises sockets and
the production HTTP/SQLite paths. Its latency is not a production capacity promise.

## Defects found and corrected

1. **Password-recovery race:** login could hash the old password, wait while
   recovery revoked sessions, then create a new session. It now checks the current
   password hash and account state again inside the session-creation transaction.
2. **Interrupted repair:** synchronizing dependencies in place could destroy the
   active application. Each repair now builds a separate application and solver
   environment. The active link changes only after diagnostics pass; old versions
   and projects survive failure, abrupt termination and successful repair.
3. **Invalid and cross-context model input:** malformed tool/input containers could
   return 500 errors or reach the provider, and provider item references were not
   excluded. Only the versioned tool schema, self-contained user text and inline
   low-detail PNG evidence are accepted before any reservation/provider call.
4. **Consent coercion:** the number `1` passed a literal-true check. Gateway terms,
   sharing consent and local assistant sharing now require the actual boolean
   `true`. Oversized-body responses also receive the no-store security headers.
5. **Unsupported install paths:** a real install into a path containing spaces and
   Unicode reached the solver before failing. Setup now rejects unsupported paths
   before making changes and explains the allowed characters. Payload extraction
   paths with spaces/quotes/Unicode remain covered by shell-quoting tests.
6. **Existing Windows WSL distributions:** the desktop required the literal name
   `Ubuntu-22.04` even when `Ubuntu` was already running 22.04. It now reads the
   installed distribution list, handles redirected UTF-16 output and verifies the
   actual OS release. Native acceptance uses this machine's existing `Ubuntu`.

7. **Windows reconnection after repair:** WSL's localhost forwarding briefly lagged
   behind the restarted worker. Initial workspace reads now retry bounded network
   failures, and both connection controls trigger a fresh attempt even when the
   session token is unchanged. Authentication failures are not retried.

Regression cases preserve the original failures. No numerical acceptance threshold
was relaxed. Installer fault injection uses dependency substitutes to select exact
failure points; separate fresh installs and repair runs exercise the real payload.
The version-change/rollback test uses a synthetic next-version payload; no future
database-schema migration is claimed.

## Retained harness findings

- The initial campaign detected seven gateway/authentication failures, three
  installer-preservation failures and five malformed-input failures. Fixed runs
  retain the same assertions and pass.
- The first visual input was an analytical pipe-reference artifact, which has no
  internal-flow field packet. It was rejected before inference. A supported
  passing internal-flow artifact was then evaluated successfully; the failure is
  retained alongside it.
- An interrupted browser launch left its own test servers running. They were
  stopped before the final complete suite. The final run explicitly disabled
  payment actions and passed all ten workflows. The final suite adds an eleventh regression
  for temporary network failure and reconnecting with the same session.
- The Windows repair probe initially matched lowercase text against the rendered
  uppercase `RUNNING` label. The corrected assertion is case-insensitive. The
  original simulation completed successfully and was retained. A later repair
  run found the real reconnect defect above; that failure is retained separately.
- Computer Use could not initialize because this chat has a WSL working directory.
  Native Windows UI checks used Playwright connected to the installed app's actual
  WebView2 through a temporary local debugging port. These are native-shell and
  real WSL-worker checks, not a browser mockup of the desktop.

The installed Windows executable differs from Cargo's unbundled executable only
in Tauri's three-byte bundle-type marker (`NSS` versus `UNK`). That exact packaging
change is checked separately from the complete payload hashes.

## Qualification limits

This covers one Windows x64 host with an existing Ubuntu 22.04 WSL2 installation,
plus the Linux/WSLg shell. It does not qualify first-time WSL installation,
administrator/reboot prompts, clean Windows VMs, macOS/ARM or a multi-machine
support matrix. The Windows installer is confirmed **unsigned**. Release signing
requires release credentials and remains untested here.

This WSL session has no supported OS keyring backend. Session-only credentials
work; failure handling is tested, but persistent OS credential storage remains
unqualified. No real Stripe or OpenAI API tests were run. Codex evaluations do not
verify Responses HTTP behavior, production pricing, usage metering or invoices.

The backup test proves consistency of the saved snapshot. Reconciling service
activity newer than a restored snapshot requires the deployment procedure and
external records. Physical host power loss and filling the host's actual disk were
not induced; process death, database capacity limits and injected storage errors
provide bounded, repeatable fault coverage.

Independent CFD/security/license review, an observed-user pilot, external service
acceptance and release sign-off remain separate gates. This campaign does not
declare a launched or independently qualified public release.
