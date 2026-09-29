# Milestone 3 — bounded assistance

The implementation connects a user-supplied OpenAI account to reviewable study proposals, a durable assumption ledger, explicitly approved recovery policies, and evidence from actual solver fields. The independent local worker remains the authority for applicability, mesh quality, quantitative acceptance and resource limits.

Acceptance scope:

- Conversation produces a typed proposal with SI values, sources and unresolved questions. Applying a proposal requires explicit approval of its hash and original study revision. Unknown required inputs block application. No model-supplied shell commands or solver dictionaries are executable.
- OpenAI credentials stay in worker memory or a supported OS credential store. Only the context previewed in the assistant is sent. Usage reservations survive restart; duplicate requests never cause another provider call. Ambiguous outages retain their reservation.
- A frozen recovery policy authorizes a finite iteration/refinement ladder, aggregate compute and disk limits, and sensitivity checks. Geometry, material, flow, physics and acceptance thresholds stay frozen. Every attempt remains inspectable. Interrupted supervisors require reconciliation before another campaign.
- Visual review uses fixed projections/slices of native solver fields, explicit units, legends, geometry/mesh context and quantitative histories. Model observations cannot change a failed numerical result.
- One experimental steady k-omega SST recipe is exposed with an explicit applicability envelope and wall-resolution checks. Numerical benchmark evidence and independent CFD qualification are separate records. Independent review cannot be self-certified by this implementation.

Local acceptance includes provider contract/fault tests, approval and budget tests, real solver recovery and turbulent-flow exercises, browser workflows and a native desktop build. Real OpenAI inference requires a user-configured credential; a simulated API response is labelled as contract testing.

API references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling). Solver reference: [OpenFOAM Foundation 14 turbulence](https://doc.cfd.direct/openfoam/user-guide-v14/turbulence).
