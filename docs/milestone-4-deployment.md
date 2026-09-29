# Managed gateway deployment and support

The gateway is a separate service. It does not run OpenFOAM, accept STEP uploads,
or share the desktop's filesystem. The example configuration is deliberately not
a live deployment: replace its hostname, model and illustrative token prices with
reviewed values, and run provider/payment acceptance before enabling registration.

## Install the service

1. Create a dedicated unprivileged `venturi` account on a Linux host. Install the
   release wheel and its locked core dependencies in `/opt/venturi/venv`. CAD and
   solver extras are unnecessary on the gateway.
2. Copy `deploy/gateway.example.json` to `/etc/venturi/gateway.json`. Set the HTTPS
   origin, evaluated model, tariff revision, actual input/output prices, markup,
   and hard per-request/account/global spending limits. All limits are micro-USD:
   1 USD = 1,000,000 micro-USD. Rates are decimal strings. Changing the tariff
   invalidates existing approvals; desktop users must review and reconnect.
3. Create `/etc/venturi/gateway.env`, readable only by the service administrator,
   with `VENTURI_GATEWAY_OPENAI_KEY`, `VENTURI_GATEWAY_STRIPE_KEY`, and
   `VENTURI_GATEWAY_WEBHOOK_SECRET`. Use test-mode Stripe credentials initially.
   These secrets never belong in the desktop installer or a committed file.
4. Install the service and retention units in `deploy/`. Put Caddy or an equivalent
   HTTPS reverse proxy in front of loopback port 8780. Enable both services and
   the retention timer. Proxy request-body limit: 256 KB. Do not log request
   bodies or credentials. Restrict access to the SQLite database and its WAL.
5. Configure Stripe webhooks at `/v1/payments/webhook` for
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `charge.refunded`, and `charge.dispute.created`. Test and live webhook secrets
   are different. No credit is granted by the checkout return page.
6. Complete the live checks below, then enable registrations and build the desktop
   payload with `--managed-url https://your-reviewed-host`. Local development can
   set `VENTURI_MANAGED_URL` and, only for loopback HTTP, `VENTURI_MANAGED_DEVELOPMENT=1`.

Deploy one gateway instance per SQLite database on a local persistent volume.
Back it up using SQLite's online backup API, not a copy of the database file while
WAL writes are active. Keep the database when restarting/upgrading. Horizontal
multi-host deployment requires a different database implementation.

After restoring a backup, keep the gateway offline while checking SQLite integrity,
wallet conservation, outstanding reservations and all activity after the backup's
timestamp. A snapshot cannot recover later provider usage or payment events by
itself. Reconcile that interval against authoritative service records before
reopening access; never resend unresolved inference to discover its outcome.
Revoke restored sessions before reopening so the restore cannot revive a token
that was revoked after the backup. Local fault tests cover a consistent online
snapshot and restart, not reconciliation of an external provider/payment history.

## Accounts and privacy

Accounts use a chosen name and password; no email is sent or required. A one-time
recovery code resets the password and revokes all sessions. Save it in a password
manager. Sessions expire after seven days and can be explicitly disconnected.
The desktop stores them in memory or an available OS credential store. Offline
disconnect forgets the local credential; a remote session may remain until expiry.

The gateway stores account password hashes, hashed sessions/recovery codes,
payment references, reservation/usage records, and audit events. It forwards
approved context without persisting prompts. Successful structured output is
cached for retry recovery; reads stop returning it after 24 hours. The hourly
retention task removes expired caches, so physical cleanup may lag by up to one
hour. Users can delete cached outputs immediately from the desktop. Backups need
a separately documented retention schedule before launch. OpenAI receives
`store=false`; this does not assert zero provider retention.

Project files, solver logs, study history and exports remain local. Support exports
are previewed and contain only allowlisted version/status/count fields. Sending a
support report is a separate user action; the application never uploads it.

## Accounting and incident handling

Amounts are integer microdollars; rates use decimal arithmetic. Every request
reserves its maximum cost in the same transaction that checks wallet, study,
account and global limits. Repeated request IDs cannot cause another provider
call. The tariff charges all input tokens at its published input rate, including
cached input; output usage includes reasoning tokens reported by the provider.
No provider tools or premium route escalation are permitted.

Provider refusals or malformed output with valid usage settle the metered cost.
Timeouts, crashes and missing/inconsistent usage retain the reservation. Never
automatically resend them. The user can refresh accounting after an operator
reconciles the provider's usage record:

```sh
venturi-gateway metrics --database /var/lib/venturi/managed.sqlite
venturi-gateway reconcile --database /var/lib/venturi/managed.sqlite \
  --account ACCOUNT_ID --request REQUEST_ID --cost-microusd 0 \
  --evidence 'Provider invoice/reference or documented software-defect credit'
```

Wait for an in-flight request's deadline before reconciling. The command refuses
the first three minutes. Resolutions preserve an audit entry. Never edit balances
with ad hoc SQL. Monitor unresolved requests, failed-study costs, p50/p95 study
costs and support minutes. The metrics endpoint is an operator CLI, not public.

Payment fulfillment deduplicates both event IDs and checkout sessions. Refund
events debit only the newly refunded amount, including out-of-order retries. A
refund can make the available balance negative if funds were already spent; new
spending then stops. Disputes freeze the account for operator review. A checkout
creation timeout remains unresolved: use the recorded order ID and Stripe
metadata/idempotency key to locate the session before offering another top-up.

Support triage records a case ID, app/runtime version, affected request/order ID,
reproduction steps, severity, minutes spent, resolution and any adjustment.
Request the minimal support export first. Ask separately before requesting any
project, geometry or logs. Never ask for API keys, account passwords or recovery
codes. Critical false-pass reports block the affected recipe/release.

Before charging real customers, publish operator-approved refund and retention
terms. Proposed policy: ordinary metered usage remains chargeable even when a
scientific problem fails; verified Venturi software defects receive a documented
credit/refund. Monetary refunds are issued in Stripe and applied to the ledger by
signed webhook. Purchased balance does not reset monthly. No subscription exists.
Account deletion requests require resolution/refund of remaining balances and
the applicable accounting-retention decision before removing identifying records.

## Live acceptance and publication gate

- Real configured OpenAI text and visual calls, correct usage and invoice matching,
  rate/refusal/timeouts, stale-price rejection and OS credential persistence.
- Stripe test-mode checkout, signed webhook, duplicate deliveries, full/partial
  refund, delayed confirmation and failed-checkout reconciliation, then a reviewed
  live-mode verification before accepting customer funds.
- HTTPS, secret rotation, restore from backup, hourly retention and account recovery.
- Clean installation on the declared native Windows/WSL and Linux matrix;
  independent CFD review, observed-user pilot and distribution/license review.
- Signed Windows installer verification, published checksums and retained source.

These are external release gates. Automated simulated-service tests are not
evidence that a merchant account, provider account or deployment has been qualified.

Implementation references: [OpenAI Responses](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
[Stripe webhook signatures](https://docs.stripe.com/webhooks/signature),
[Tauri resources](https://v2.tauri.app/develop/resources/).
