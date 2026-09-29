import { useEffect, useState } from "react";
import type { Api, Run } from "./types";

type Packet = {
  packet_hash: string;
  connection_hash: string;
  estimate?: { maximum_microusd: number } | null;
  quantitative_status: string;
  limits: string[];
  images: Record<string, string>;
  artifacts: { id: string; units: string; range: number[] }[];
  failed_checks: { name: string; detail: string }[];
};
type Review = {
  status: string;
  error?: string;
  output?: {
    observations: {
      artifact_id: string;
      location: string;
      observation: string;
      hypothesis: string;
      requested_check: string;
      suggested_action: string;
    }[];
    limitations: string[];
  };
};
export function VisualReview({
  api,
  run,
  onExport,
}: {
  api: Api;
  run: Run;
  onExport: () => Promise<void>;
}) {
  const [packet, setPacket] = useState<Packet | null>(null);
  const [shared, setShared] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const [route, setRoute] = useState("OpenAI using the selected billing route");
  useEffect(() => {
    const refresh = () => {
      setShared(false);
      setRequestId(crypto.randomUUID());
      void api("/assistant")
        .then((s) =>
          setRoute(
            s.connection?.provider === "managed"
              ? "OpenAI through Venturi Managed"
              : "your OpenAI account",
          ),
        )
        .catch(() => {});
    };
    refresh();
    window.addEventListener("venturi-ai-changed", refresh);
    return () => window.removeEventListener("venturi-ai-changed", refresh);
  }, [api]);
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
  return (
    <details className="assistance-panel">
      <summary>Visual evidence review</summary>
      <p>
        Inspect pressure and speed on three fixed planes, with mesh edges, port
        labels and explicit units. These views come from the actual solver
        fields.
      </p>
      <button
        className="secondary"
        disabled={busy}
        onClick={() =>
          void perform(async () => {
            setPacket(
              await api(`/runs/${run.id}/visual-review`, { method: "POST" }),
            );
            setShared(false);
          })
        }
      >
        Prepare scientific views
      </button>
      {packet && (
        <>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => void perform(onExport)}
          >
            Export visual review
          </button>
          <p>
            <strong>
              Quantitative status: {packet.quantitative_status}. Visual
              observations cannot change this result.
            </strong>
          </p>
          {packet.failed_checks.map((c) => (
            <p key={c.name}>
              {c.name}: {c.detail}
            </p>
          ))}
          {packet.artifacts.map((a) => (
            <figure key={a.id}>
              <img
                className="scientific-view"
                src={packet.images[a.id]}
                alt={`${a.id}: three fixed central slices with mesh edges and ${a.units} legend`}
              />
              <figcaption>
                {a.id} · range{" "}
                {a.range.map((n) => n.toPrecision(5)).join(" to ")} {a.units}
              </figcaption>
            </figure>
          ))}
          {packet.limits.map((l, i) => (
            <p key={i}>{l}</p>
          ))}
          {packet.estimate && (
            <p>
              Estimated charge: $0–$
              {(packet.estimate.maximum_microusd / 1e6).toFixed(6)}. The maximum
              is reserved before sending these images.
            </p>
          )}
          <label className="confirmation">
            <input
              type="checkbox"
              checked={shared}
              onChange={(e) => setShared(e.target.checked)}
            />
            Share these images, numerical checks and boundary conditions with
            {route} for advisory review
          </label>
          <button
            disabled={busy || !shared}
            onClick={() =>
              void perform(async () =>
                setReview(
                  await api(`/runs/${run.id}/visual-review/assist`, {
                    method: "POST",
                    body: JSON.stringify({
                      request_id: requestId,
                      packet_hash: packet.packet_hash,
                      connection_hash: packet.connection_hash,
                      share_context: shared,
                    }),
                  }),
                ),
              )
            }
          >
            Request visual observations
          </button>
        </>
      )}
      {review?.output?.observations.map((o, i) => (
        <article key={i}>
          <h4>
            {o.artifact_id} · {o.location}
          </h4>
          <p>{o.observation}</p>
          <p>Hypothesis: {o.hypothesis}</p>
          <p>Requested check: {o.requested_check}</p>
          <p>Suggested action: {o.suggested_action}</p>
        </article>
      ))}
      {review?.output?.limitations.map((l, i) => (
        <p key={i}>{l}</p>
      ))}
      {(error || review?.error) && <p role="alert">{error || review?.error}</p>}
    </details>
  );
}
