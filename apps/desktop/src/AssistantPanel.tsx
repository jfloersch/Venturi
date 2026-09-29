import { useEffect, useState } from "react";
import type { Api, Geometry, Role, StudyRecord } from "./types";

type Input = {
  field: string;
  value: number | null;
  units: string;
  source: string;
  evidence: string;
};
type ProposalRecord = {
  id: string;
  status: string;
  message?: string;
  error?: string;
  can_apply?: boolean;
  validation_error?: string;
  proposal_hash?: string;
  applied?: StudyRecord;
  cost_microusd: number;
  selection?: Geometry["selection"];
  proposal?: {
    name: string;
    question: string;
    material_source: string;
    physics: string;
    explanation: string;
    inputs: Input[];
    unresolved_questions: string[];
  };
  plan?: { study_hash: string; estimated_cells: number[] };
};
type Status = {
  connected: boolean;
  reserved_or_spent_usd: number;
  connection: {
    model: string;
    provider?: string;
    budget_usd: number;
    remember: boolean;
    structured_output_verified: boolean;
  } | null;
  requests: ProposalRecord[];
};

export function AssistantPanel({
  api,
  geometry,
  assignments,
  record,
  disabled,
  onApplied,
}: {
  api: Api;
  geometry: Geometry;
  assignments: Record<string, Role>;
  record: StudyRecord | null;
  disabled: boolean;
  onApplied: (value: StudyRecord) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [inputRate, setInputRate] = useState("");
  const [outputRate, setOutputRate] = useState("");
  const [budget, setBudget] = useState("1");
  const [remember, setRemember] = useState(false);
  const [message, setMessage] = useState("");
  const [context, setContext] = useState<{
    context_hash: string;
    estimate?: { maximum_microusd: number } | null;
  } | null>(null);
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<ProposalRecord | null>(null);
  const [approved, setApproved] = useState(false);
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  useEffect(() => {
    api("/assistant")
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, [api]);
  useEffect(() => {
    const refresh = () => {
      void api("/assistant")
        .then(setStatus)
        .catch((e) => setError(String(e)));
      setContext(null);
      setShared(false);
      setRequestId(crypto.randomUUID());
    };
    window.addEventListener("venturi-ai-changed", refresh);
    return () => window.removeEventListener("venturi-ai-changed", refresh);
  }, [api]);
  const identity = JSON.stringify([
    geometry.geometry_hash,
    assignments,
    record?.id,
    record?.revision,
    message,
  ]);
  useEffect(() => {
    setContext(null);
    setShared(false);
    setProposal(null);
    setApproved(false);
    setRequestId(crypto.randomUUID());
  }, [identity]);
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const payload = () => ({
    request_id: requestId,
    selection: { geometry_hash: geometry.geometry_hash, assignments },
    study_id: record?.id || null,
    expected_revision: record?.revision || 0,
    message,
    share_context: true,
    context_hash: context?.context_hash || null,
  });
  return (
    <details className="assistance-panel">
      <summary>Study assistant · OpenAI</summary>
      <p>
        AI route:{" "}
        {status?.connection?.provider === "managed"
          ? "Venturi Managed"
          : status?.connected
            ? "Your OpenAI account"
            : "Disconnected"}
        . No automatic billing switch.
      </p>
      <p>
        Describe the engineering question and the inputs you know. Review
        assumptions and approve a complete proposal before it changes the saved
        study.
      </p>
      <details>
        <summary>
          {status?.connected
            ? `Connected to ${status.connection?.model}`
            : "Connect your OpenAI account"}
        </summary>
        <p>
          Credentials stay in worker memory for this session, or in a supported
          OS credential store when selected. They are never saved with a study.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              setStatus(
                await api("/assistant/connect", {
                  method: "POST",
                  body: JSON.stringify({
                    model,
                    api_key: key,
                    remember,
                    input_usd_per_million: Number(inputRate),
                    output_usd_per_million: Number(outputRate),
                    budget_usd: Number(budget),
                  }),
                }),
              );
              setKey("");
              window.dispatchEvent(new Event("venturi-ai-changed"));
            });
          }}
        >
          <div className="flow-fields">
            <label>
              OpenAI model ID
              <input
                required
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Model enabled on your account"
              />
            </label>
            <label>
              OpenAI API key
              <input
                required
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <label>
              Input price (USD / million tokens)
              <input
                required
                type="number"
                min="0.000001"
                step="any"
                value={inputRate}
                onChange={(e) => setInputRate(e.target.value)}
              />
            </label>
            <label>
              Output price (USD / million tokens)
              <input
                required
                type="number"
                min="0.000001"
                step="any"
                value={outputRate}
                onChange={(e) => setOutputRate(e.target.value)}
              />
            </label>
            <label>
              Total AI budget (USD)
              <input
                required
                type="number"
                min="0.01"
                max="1000"
                step="0.01"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
            </label>
          </div>
          <p>
            Enter your account’s current token prices. Reservations use these
            declared rates; provider invoices remain authoritative. The budget
            includes all previous requests in this workspace.
          </p>
          <label className="confirmation">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember key in the OS credential store
          </label>
          <button disabled={busy}>Connect OpenAI</button>
          {status?.connected && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  setStatus(
                    await api("/assistant/disconnect", { method: "POST" }),
                  );
                  window.dispatchEvent(new Event("venturi-ai-changed"));
                })
              }
            >
              Disconnect and forget key
            </button>
          )}
        </form>
      </details>
      {status && (
        <p>
          Reserved or spent: ${status.reserved_or_spent_usd.toFixed(6)}
          {status.connection
            ? ` of $${status.connection.budget_usd.toFixed(2)}`
            : ""}
          .{" "}
          {status.connection?.structured_output_verified
            ? "Structured proposals verified."
            : "Structured proposal support is checked on the first response."}
        </p>
      )}
      <label>
        Describe this study
        <textarea
          value={message}
          maxLength={6000}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="State your flow rate, fluid properties and source, desired answer, and any assumptions to review."
        />
      </label>
      <button
        className="secondary"
        disabled={busy || !message.trim()}
        onClick={() =>
          void perform(async () =>
            setContext(
              await api("/assistant/context", {
                method: "POST",
                body: JSON.stringify(payload()),
              }),
            ),
          )
        }
      >
        Preview context to share
      </button>
      {context !== null && (
        <>
          {context.estimate && (
            <p>
              Estimated charge: $0–$
              {(context.estimate.maximum_microusd / 1e6).toFixed(6)}. This
              maximum is reserved before sending; unused funds are released
              after settlement.
            </p>
          )}
          <details>
            <summary>Context sent with your message</summary>
            <pre className="assistant-context">
              {JSON.stringify(context, null, 2)}
            </pre>
          </details>
          <label className="confirmation">
            <input
              type="checkbox"
              checked={shared}
              onChange={(e) => setShared(e.target.checked)}
            />
            Share this message, study, port measurements, annotations and recent
            conversation with OpenAI
            {status?.connection?.provider === "managed"
              ? " through Venturi Managed"
              : ""}
          </label>
        </>
      )}
      <button
        disabled={busy || !status?.connected || !shared || disabled}
        onClick={() =>
          void perform(async () => {
            setProposal(
              await api("/assistant/propose", {
                method: "POST",
                body: JSON.stringify(payload()),
              }),
            );
            setStatus(await api("/assistant"));
            setApproved(false);
          })
        }
      >
        {busy ? "Working…" : "Propose study"}
      </button>
      {proposal && (
        <div className="proposal-review">
          <h3>Review proposed study</h3>
          {proposal.proposal && (
            <>
              <h4>{proposal.proposal.name}</h4>
              <p>{proposal.proposal.explanation}</p>
              <p>
                <strong>Question:</strong> {proposal.proposal.question}
              </p>
              <p>
                <strong>Material source:</strong>{" "}
                {proposal.proposal.material_source || "Unknown"}
              </p>
              <p>
                <strong>Recipe:</strong> {proposal.proposal.physics}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Input</th>
                    <th>Proposed value</th>
                    <th>Source and rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {proposal.proposal.inputs.map((i) => (
                    <tr key={i.field}>
                      <td>{i.field}</td>
                      <td>
                        {i.value ?? "Unknown"} {i.units}
                      </td>
                      <td>
                        {i.source.replaceAll("_", " ")} — {i.evidence}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {proposal.proposal.unresolved_questions.map((q, i) => (
                <p key={i}>Needs input: {q}</p>
              ))}
            </>
          )}
          {(proposal.error || proposal.validation_error) && (
            <p role="alert">{proposal.error || proposal.validation_error}</p>
          )}
          {proposal.status === "reserved" && (
            <p>
              This request is reserved or was interrupted. It will not be sent
              twice. Check your provider usage before submitting a new request.
            </p>
          )}
          {proposal.can_apply && !proposal.applied && (
            <>
              <p>
                Applying this proposal creates a saved revision. Previous meshes
                and results remain historical; build and approve a mesh for the
                new revision.
              </p>
              <label className="confirmation">
                <input
                  type="checkbox"
                  checked={approved}
                  onChange={(e) => setApproved(e.target.checked)}
                />
                I approve these physical inputs, assumptions and study changes
              </label>
              <button
                disabled={!approved || disabled || busy}
                onClick={() =>
                  void perform(async () => {
                    const saved = await api(
                      `/assistant/proposals/${proposal.id}/approve`,
                      {
                        method: "POST",
                        body: JSON.stringify({
                          proposal_hash: proposal.proposal_hash,
                        }),
                      },
                    );
                    onApplied(saved);
                    setProposal({ ...proposal, applied: saved });
                    setStatus(await api("/assistant"));
                  })
                }
              >
                Apply approved proposal
              </button>
            </>
          )}
        </div>
      )}
      <details>
        <summary>
          Conversation and assumption ledger (
          {status?.requests.filter((r) => r.message).length || 0})
        </summary>
        {status?.requests
          .filter(
            (r) =>
              r.selection?.geometry_hash === geometry.geometry_hash &&
              r.message,
          )
          .slice()
          .reverse()
          .map((r) => (
            <article key={r.id}>
              <p>
                <strong>
                  {r.applied
                    ? `Approved · revision ${r.applied.revision}`
                    : r.status}
                </strong>{" "}
                · ${(r.cost_microusd / 1e6).toFixed(6)}
              </p>
              <p>{r.message}</p>
              {r.proposal?.inputs.map((i) => (
                <p key={i.field}>
                  {i.field}: {i.value ?? "unknown"} {i.units} ·{" "}
                  {i.source.replaceAll("_", " ")} · {i.evidence}
                </p>
              ))}
              {r.error && <p>{r.error}</p>}
              {r.can_apply && !r.applied && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => {
                    setProposal(r);
                    setApproved(false);
                  }}
                >
                  Review this proposal
                </button>
              )}
            </article>
          ))}
      </details>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
    </details>
  );
}
