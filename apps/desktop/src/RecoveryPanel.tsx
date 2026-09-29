import { useEffect, useState } from "react";
import type { Api, Run } from "./types";

type Policy = {
  mesh_run_id: string;
  study_hash: string;
  mesh_hash: string;
  maximum_solver_attempts: number;
  maximum_remesh_attempts: number;
  maximum_iterations: number;
  refinement_factor: number;
  wall_time_seconds: number;
  disk_mb: number;
};
type Campaign = {
  id: string;
  status: string;
  reason: string;
  solver_attempts: number;
  remesh_attempts: number;
  request: { policy: Policy };
  attempts: {
    run_id: string;
    kind: string;
    status: string;
    failing_checks?: string[];
  }[];
  changes: {
    field: string;
    old: number;
    new: number;
    failing_checks: string[];
  }[];
  sensitivity?: { status: string; relative_changes?: Record<string, number> };
  elapsed_seconds?: number;
  retained_disk_mb?: number;
};
export function RecoveryPanel({
  api,
  run,
  disabled,
  onRun,
}: {
  api: Api;
  run: Run;
  disabled: boolean;
  onRun: (id: string) => void;
}) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [policy, setPolicy] = useState<Policy>({
    mesh_run_id: run.id,
    study_hash: run.result?.study_hash || "",
    mesh_hash: run.result?.mesh_hash || "",
    maximum_solver_attempts: 3,
    maximum_remesh_attempts: 2,
    maximum_iterations: 2000,
    refinement_factor: 0.8,
    wall_time_seconds: 7200,
    disk_mb: 4096,
  });
  const [plan, setPlan] = useState<{
    plan_hash: string;
    controls: Record<string, string>;
    cost: string;
  } | null>(null);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requestId] = useState(() => crypto.randomUUID());
  useEffect(() => {
    let live = true;
    const refresh = () =>
      api("/recovery")
        .then((v) => {
          if (live) setCampaigns(v);
        })
        .catch((e) => {
          if (live) setError(String(e));
        });
    void refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => {
      live = false;
      clearInterval(timer);
    };
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
  const active = campaigns.some((c) =>
    ["queued", "running", "stopping"].includes(c.status),
  );
  const relevant = campaigns.filter(
    (c) =>
      c.request.policy.mesh_run_id === run.id ||
      c.attempts.some((a) => a.run_id === run.id),
  );
  return (
    <details className="assistance-panel">
      <summary>Bounded numerical recovery</summary>
      {run.kind === "internal_mesh" && (
        <>
          <p>
            Approve a finite recovery policy for this study and reviewed mesh.
            The worker may increase iterations or refine a mesh after specific
            failed checks. Flow, fluid properties, geometry, physics and
            acceptance thresholds stay fixed. Every attempt is retained.
          </p>
          <div className="flow-fields">
            {(
              [
                ["Maximum solver attempts", "maximum_solver_attempts", 1, 3, 1],
                ["Maximum remesh attempts", "maximum_remesh_attempts", 0, 2, 1],
                ["Iteration ceiling", "maximum_iterations", 100, 2000, 1],
                [
                  "Cell size multiplier on refinement",
                  "refinement_factor",
                  0.5,
                  0.95,
                  0.05,
                ],
                [
                  "Total recovery time (seconds)",
                  "wall_time_seconds",
                  1,
                  14400,
                  1,
                ],
                ["Total recovery disk (MB)", "disk_mb", 1, 16384, 1],
              ] as const
            ).map(([label, key, min, max, step]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min={min}
                  max={max}
                  step={step}
                  value={policy[key]}
                  onChange={(e) => {
                    setPolicy({ ...policy, [key]: Number(e.target.value) });
                    setPlan(null);
                    setApproved(false);
                  }}
                />
              </label>
            ))}
          </div>
          <button
            className="secondary"
            disabled={busy || active || disabled || !policy.mesh_hash}
            onClick={() =>
              void perform(async () => {
                setPlan(
                  await api("/recovery/plan", {
                    method: "POST",
                    body: JSON.stringify(policy),
                  }),
                );
                setApproved(false);
              })
            }
          >
            Review recovery plan
          </button>
          {plan && (
            <div>
              <p>{plan.cost}</p>
              {Object.entries(plan.controls).map(([key, value]) => (
                <p key={key}>{value}</p>
              ))}
              <label className="confirmation">
                <input
                  type="checkbox"
                  checked={approved}
                  onChange={(e) => setApproved(e.target.checked)}
                />
                I approve this study, mesh and bounded recovery policy
              </label>
              <button
                disabled={busy || !approved || active || disabled}
                onClick={() =>
                  void perform(async () => {
                    await api("/recovery", {
                      method: "POST",
                      body: JSON.stringify({
                        request_id: requestId,
                        policy,
                        approved_plan_hash: plan.plan_hash,
                      }),
                    });
                    setCampaigns(await api("/recovery"));
                  })
                }
              >
                Start approved recovery
              </button>
            </div>
          )}
        </>
      )}
      {relevant.map((c) => (
        <article key={c.id}>
          <h3>Recovery: {c.status.replaceAll("_", " ")}</h3>
          <p>{c.reason}</p>
          <p>
            {c.solver_attempts} solver attempts · {c.remesh_attempts} remesh
            attempts · {Math.round(c.elapsed_seconds || 0)} seconds ·{" "}
            {(c.retained_disk_mb || 0).toFixed(1)} MB · $0 AI spend
          </p>
          {c.changes.map((change, i) => (
            <p key={i}>
              {change.field}: {change.old} → {change.new}. Trigger:{" "}
              {change.failing_checks.join(", ")}
            </p>
          ))}
          {c.sensitivity && (
            <p>
              Answer sensitivity: {c.sensitivity.status.replaceAll("_", " ")}
              {c.sensitivity.relative_changes
                ? ` · ${JSON.stringify(c.sensitivity.relative_changes)}`
                : ""}
            </p>
          )}
          {c.attempts.map((a) => (
            <button
              key={a.run_id}
              className="secondary"
              onClick={() => onRun(a.run_id)}
            >
              {a.kind === "internal_mesh" ? "Mesh" : "Solve"}{" "}
              {a.run_id.slice(0, 8)} · {a.status}
            </button>
          ))}
          {["queued", "running", "stopping"].includes(c.status) && (
            <button
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api(`/recovery/${c.id}/cancel`, { method: "POST" });
                  setCampaigns(await api("/recovery"));
                })
              }
            >
              Stop recovery
            </button>
          )}
        </article>
      ))}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
