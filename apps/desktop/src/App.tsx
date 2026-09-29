import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Box,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Cpu,
  FileCheck2,
  FlaskConical,
  LoaderCircle,
  PlugZap,
  ShieldCheck,
  Square,
  TriangleAlert,
  Wind,
  XCircle,
} from "lucide-react";
import { Viewer } from "./Viewer";
import { FlowSetup, loadStudyValues, studyFromValues } from "./FlowSetup";
import { MeshReview } from "./MeshReview";
import { Annotations } from "./Annotations";
import { CaseViewer } from "./CaseViewer";
import { SetupPanel, SupportPanel } from "./SetupPanel";
import { ManagedPanel } from "./ManagedPanel";
import { AssistantPanel } from "./AssistantPanel";
import { RecoveryPanel } from "./RecoveryPanel";
import { VisualReview } from "./VisualReview";
import type {
  Check as EvidenceCheck,
  Diagnostics,
  Geometry,
  Role,
  Run,
  RunKind,
  InternalStudy,
  StudyRecord,
  StudySave,
  Camera,
} from "./types";

const API = import.meta.env.VITE_VENTURI_API || "http://127.0.0.1:8765";
const protocol = "venturi.worker.v1";
const active = (run?: Run | null) =>
  !!run && ["queued", "running"].includes(run.status);
const runLabel = (kind: RunKind) =>
  ({
    reference: "Pipe-flow reference",
    cad_mesh: "CAD boundary check",
    internal_mesh: "STEP mesh review",
    internal_flow: "STEP flow result",
  })[kind];

function studyIdentity(study: InternalStudy | null | undefined): string {
  if (!study) return "";
  return JSON.stringify({
    recipe: study.recipe,
    turbulenceIntensity: study.turbulence_intensity,
    turbulenceLengthScale: study.turbulence_length_scale_m,
    name: study.name,
    geometry: study.selection.geometry_hash,
    assignments: Object.entries(study.selection.assignments).sort(([a], [b]) =>
      a.localeCompare(b),
    ),
    flow: study.flow_rate_m3_s,
    density: study.density_kg_m3,
    viscosity: study.dynamic_viscosity_pa_s,
    cell: study.mesh.cell_size_m,
    cells: study.mesh.maximum_cells ?? 250000,
    meshTime: study.mesh.timeout_seconds ?? 600,
    iterations: study.max_iterations ?? 600,
    solveTime: study.timeout_seconds ?? 900,
    wall: study.resources?.wall_time_seconds ?? 2400,
    memory: study.resources?.memory_mb ?? 4096,
    disk: study.resources?.disk_mb ?? 2048,
  });
}

function CheckRow({ item }: { item: EvidenceCheck }) {
  return (
    <div className={`check-row ${item.status}`}>
      <span className="check-icon">
        {item.status === "pass" ? (
          <CheckCircle2 size={17} />
        ) : item.status === "fail" ? (
          <XCircle size={17} />
        ) : (
          <Circle size={17} />
        )}
      </span>
      <div>
        <strong>{item.name}</strong>
        <p>{item.detail}</p>
      </div>
      <span className="check-outcome">{item.status}</span>
    </div>
  );
}

function Plot({ run }: { run: Run }) {
  const history = run.result?.history;
  if (!history?.length) return null;
  const expected = run.result?.expected_pressure_drop_pa || 0;
  const max = Math.max(
    expected * 1.15,
    ...history.map((p) => p.pressure_drop_pa),
  );
  const min = Math.min(0, ...history.map((p) => p.pressure_drop_pa));
  const y = (p: number) => 145 - ((p - min) / (max - min || 1)) * 125;
  const path = history
    .map(
      (p, i) =>
        `${i ? "L" : "M"}${42 + (i / Math.max(1, history.length - 1)) * 630},${y(p.pressure_drop_pa)}`,
    )
    .join(" ");
  return (
    <div className="plot">
      <div className="plot-title">
        Pressure-drop convergence <span>Pa · iteration history</span>
      </div>
      <svg
        viewBox="0 0 700 175"
        role="img"
        aria-label="Pressure drop by solver iteration"
      >
        <line x1="42" y1="145" x2="680" y2="145" stroke="#d5dfdc" />
        {run.result?.expected_pressure_drop_pa !== undefined && (
          <line
            x1="42"
            y1={y(expected)}
            x2="680"
            y2={y(expected)}
            stroke="#bc8748"
            strokeDasharray="5 5"
          />
        )}
        <path d={path} fill="none" stroke="#168876" strokeWidth="2.2" />
        <text x="0" y={y(expected) + 4}>
          {expected ? expected.toFixed(2) : "Pa"}
        </text>
        <text x="40" y="168">
          {history[0].iteration}
        </text>
        <text x="640" y="168">
          {history.at(-1)?.iteration}
        </text>
      </svg>
      <div className="plot-legend">
        <span className="teal">— OpenFOAM</span>
        {run.result?.expected_pressure_drop_pa !== undefined && (
          <span className="gold">- - Analytical reference</span>
        )}
      </div>
    </div>
  );
}

export function App() {
  const [token, setToken] = useState(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const supplied = hash.get("token");
    if (supplied) {
      history.replaceState(null, "", window.location.pathname);
      sessionStorage.setItem("venturi-token", supplied);
    }
    return supplied || sessionStorage.getItem("venturi-token") || "";
  });
  const [tokenInput, setTokenInput] = useState(token);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [assignments, setAssignments] = useState<Record<string, Role>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(() =>
    sessionStorage.getItem("venturi-run"),
  );
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"geometry" | "evidence">(() =>
    sessionStorage.getItem("venturi-tab") === "evidence"
      ? "evidence"
      : "geometry",
  );
  const [showConnection, setShowConnection] = useState(false);
  const [offline, setOffline] = useState(false);
  const [studies, setStudies] = useState<StudyRecord[]>([]);
  const [record, setRecord] = useState<StudyRecord | null>(null);
  const [draft, setDraft] = useState<InternalStudy | null>(null);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [restoreCamera, setRestoreCamera] = useState<Camera | null>(null);
  const [openGeneration, setOpenGeneration] = useState(0);
  const [caseOpen, setCaseOpen] = useState(false);
  const run = runs.find((r) => r.id === runId) || runs[0];
  const hasActive = runs.some(active);
  const currentStudy =
    !!run &&
    (!run.kind.startsWith("internal_") ||
      (!!draft &&
        !dirty &&
        geometry?.geometry_hash === draft.selection.geometry_hash &&
        studyIdentity(draft) ===
          studyIdentity(run.request?.study as InternalStudy)));
  const shownStudy = run?.request?.study as InternalStudy | undefined;
  const face = geometry?.faces.find((f) => f.id === selected);
  const studyPipe =
    tab === "evidence" && run?.kind === "reference"
      ? (run.request?.study && "pipe" in run.request.study
          ? run.request.study.pipe
          : undefined) || run.result?.inputs?.pipe
      : undefined;
  const shownRadius = studyPipe?.radius_m ?? 0.005;
  const shownInternal =
    tab === "evidence" ? run?.kind.startsWith("internal_") : geometry?.imported;
  const shownGeometryName =
    tab === "evidence" ? run?.request?.study?.name : geometry?.source_name;
  const shownLength = studyPipe?.length_m ?? 0.1;
  const shownReynolds = studyPipe
    ? (2 *
        shownRadius *
        (studyPipe.mean_velocity_m_s ?? 0.01) *
        (studyPipe.density_kg_m3 ?? 1000)) /
      (studyPipe.dynamic_viscosity_pa_s ?? 0.001)
    : 100;
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const api = useCallback(async (path: string, init: RequestInit = {}) => {
    const response = await fetch(API + "/v1" + path, {
      ...init,
      headers: {
        Authorization: `Bearer ${tokenRef.current}`,
        "Content-Type": "application/json",
        ...init.headers,
      },
    });
    if (!response.ok) {
      const value = await response
        .json()
        .catch(() => ({ detail: "Worker request failed." }));
      throw new Error(
        typeof value.detail === "string"
          ? value.detail
          : JSON.stringify(value.detail),
      );
    }
    return response.json();
  }, []);

  useEffect(() => {
    if ("__TAURI_INTERNALS__" in window && !token) {
      import("@tauri-apps/api/core")
        .then(({ invoke }) => invoke<{ token: string }>("worker_connection"))
        .then((c) => {
          if (c.token) {
            setToken(c.token);
            setTokenInput(c.token);
          }
        })
        .catch((e) => setError(String(e)));
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    let disposed = false;
    setConnecting(true);
    setError("");
    const loadWorkspace = async () => {
      // Windows localhost forwarding can lag behind a restarted WSL worker.
      // Retry only reads after network failures; HTTP/auth failures stay explicit.
      for (let attempt = 0; ; attempt++) {
        try {
          return await Promise.all([
            api("/diagnostics"),
            api("/geometry"),
            api("/runs"),
            api("/workspace"),
          ]);
        } catch (error) {
          if (disposed || !(error instanceof TypeError) || attempt >= 19)
            throw error;
          await new Promise((resolve) => window.setTimeout(resolve, 250));
        }
      }
    };
    loadWorkspace()
      .then(
        ([d, g, r, workspace]: [
          Diagnostics,
          Geometry,
          Run[],
          { studies: StudyRecord[]; active_id: string | null },
        ]) => {
          if (disposed) return;
          if (d.protocol_version !== protocol)
            throw new Error(
              "Worker protocol differs from this desktop. Install matching versions.",
            );
          setDiagnostics(d);
          setOffline(false);
          setStudies(workspace.studies);
          const current =
            workspace.studies.find(
              (s) =>
                s.id === workspace.active_id &&
                s.study.selection.geometry_hash === g.geometry_hash,
            ) || null;
          setRecord(current);
          setDraft(
            g.imported
              ? studyFromValues(
                  loadStudyValues(g, current),
                  g,
                  g.selection.assignments,
                )
              : null,
          );
          setGeometry(g);
          setAssignments(g.selection.assignments);
          setSelected(g.faces[0]?.id || null);
          setRuns(r);
          setDirty(false);
          setShowConnection(false);
          sessionStorage.setItem("venturi-token", token);
        },
      )
      .catch((e) => {
        if (!disposed) {
          setError(String(e));
          setDiagnostics(null);
        }
      })
      .finally(() => {
        if (!disposed) setConnecting(false);
      });
    return () => {
      disposed = true;
    };
  }, [token, api, connectionAttempt]);

  useEffect(() => {
    if (!diagnostics) return;
    const id = window.setInterval(
      () =>
        api("/runs")
          .then((r) => {
            setRuns(r);
            setOffline(false);
            setError((old) =>
              old.startsWith("Worker connection lost:") ? "" : old,
            );
          })
          .catch((e) => {
            setOffline(true);
            setError(
              `Worker connection lost: ${String(e)}. Saved runs will reconnect automatically.`,
            );
          }),
      1500,
    );
    return () => window.clearInterval(id);
  }, [diagnostics, api]);

  useEffect(() => {
    sessionStorage.setItem("venturi-tab", tab);
    if (runId) sessionStorage.setItem("venturi-run", runId);
  }, [tab, runId]);

  async function action(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    await api("/selection", {
      method: "POST",
      body: JSON.stringify({
        geometry_hash: geometry?.geometry_hash,
        assignments,
      }),
    });
    setDirty(false);
  }
  async function start(kind: "reference" | "cad_mesh") {
    await action(async () => {
      if (kind === "cad_mesh" && dirty) await save();
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          kind,
          request_id: crypto.randomUUID(),
          geometry_hash: kind === "cad_mesh" ? geometry?.geometry_hash : null,
        }),
      });
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setRunId(value.id);
      setTab("evidence");
    });
  }
  async function download(path?: string, visual = false) {
    if (!run) return;
    await action(async () => {
      const response = await fetch(
        `${API}/v1/runs/${run.id}/${visual ? "visual-review-export" : path ? `files/${path.split("/").map(encodeURIComponent).join("/")}` : "export"}`,
        {
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      if (!response.ok)
        throw new Error(
          "Could not export this run. Wait for execution to finish.",
        );
      const filename =
        path?.split("/").at(-1) ||
        `venturi-${visual ? "visual-" : ""}${run.id.slice(0, 8)}.zip`;
      if ("__TAURI_INTERNALS__" in window) {
        const { save } = await import("@tauri-apps/plugin-dialog");
        const { writeFile } = await import("@tauri-apps/plugin-fs");
        const destination = await save({
          defaultPath: filename,
          filters: path
            ? undefined
            : [{ name: "Venturi run archive", extensions: ["zip"] }],
        });
        if (destination)
          await writeFile(
            destination,
            new Uint8Array(await response.arrayBuffer()),
          );
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  async function retry() {
    if (!run?.request) return;
    await action(async () => {
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          ...run.request,
          request_id: crypto.randomUUID(),
          retry_of: run.id,
          reason: "Manual retry from the workbench",
        }),
      });
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setRunId(value.id);
    });
  }

  function displayGeometry(g: Geometry) {
    setRecord(null);
    setDraft(null);
    setCamera(null);
    setRestoreCamera(null);
    setOpenGeneration((n) => n + 1);
    setGeometry(g);
    setAssignments(g.selection.assignments);
    setSelected(g.faces[0]?.id || null);
    setDirty(false);
    setTab("geometry");
  }
  async function importStep(file: File) {
    await action(async () => {
      if (file.size > 16 * 1024 * 1024)
        throw new Error("STEP exceeds the 16 MiB import limit.");
      const response = await fetch(
        `${API}/v1/geometry?filename=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${tokenRef.current}`,
            "Content-Type": "application/octet-stream",
          },
          body: file,
        },
      );
      const value = await response.json();
      if (!response.ok)
        throw new Error(
          typeof value.detail === "string"
            ? value.detail
            : "Could not import this prepared fluid volume.",
        );
      displayGeometry(value);
    });
  }
  async function saveStudy(value: StudySave): Promise<StudyRecord> {
    if (dirty) await save();
    const saved: StudyRecord = await api("/studies", {
      method: "POST",
      body: JSON.stringify({
        ...value,
        id: record?.id || null,
        expected_revision: record?.revision || 0,
      }),
    });
    setRecord(saved);
    setDraft(saved.study);
    setStudies((old) => [saved, ...old.filter((s) => s.id !== saved.id)]);
    return saved;
  }
  async function openStudy(id: string) {
    await action(async () => {
      const value = await api(`/studies/${id}/open`, { method: "POST" });
      displayGeometry(value.geometry);
      setRecord(value.record);
      setDraft(value.record.study);
      localStorage.removeItem(
        `venturi-alpha-draft-${value.geometry.geometry_hash}-${id}`,
      );
    });
  }
  async function buildInternalMesh(payload: StudySave) {
    setBusy(true);
    setError("");
    try {
      const saved = await saveStudy(payload);
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          kind: "internal_mesh",
          request_id: crypto.randomUUID(),
          study: saved.study,
          desktop_study: { id: saved.id, revision: saved.revision },
        }),
      });
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setRunId(value.id);
      setTab("evidence");
    } finally {
      setBusy(false);
    }
  }
  async function approveMesh() {
    if (!run || run.kind !== "internal_mesh" || !currentStudy || offline)
      return;
    await action(async () => {
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          kind: "internal_flow",
          request_id: crypto.randomUUID(),
          study: run.request?.study,
          desktop_study: run.request?.desktop_study,
          mesh_run_id: run.id,
          approved_mesh_hash: run.result?.mesh_hash,
          reason:
            "User reviewed and approved this mesh and saved study in the workbench",
        }),
      });
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setRunId(value.id);
    });
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Wind size={25} />
          </div>
          <span>
            venturi<span className="brand-dot">.</span>
          </span>
        </div>
        <div className="workspace-label">LOCAL WORKSPACE</div>
        <div className="project">
          <div className="project-icon">
            <FlaskConical size={20} />
          </div>
          <div>
            <strong>First principles</strong>
            <span>Milestone 4 · Public beta preparation</span>
          </div>
        </div>
        <nav aria-label="Workbench navigation">
          <button
            className={tab === "geometry" ? "current" : ""}
            onClick={() => setTab("geometry")}
          >
            <Box size={18} />
            Geometry & boundaries
            <ChevronRight size={14} />
          </button>
          <button
            className={tab === "evidence" ? "current" : ""}
            onClick={() => setTab("evidence")}
          >
            <Activity size={18} />
            Runs & evidence
            <ChevronRight size={14} />
          </button>
        </nav>
        <div className="sidebar-divider" />
        <div className="workspace-label">SAVED STUDIES</div>
        <div className="saved-studies">
          {studies.length ? (
            studies.map((s) => (
              <button
                key={s.id}
                disabled={busy || hasActive || offline}
                onClick={() => openStudy(s.id)}
                className={record?.id === s.id ? "selected" : ""}
              >
                <strong>{s.study.name}</strong>
                <small>
                  Revision {s.revision} · {s.profile}
                </small>
              </button>
            ))
          ) : (
            <p>Save a study to reopen it here.</p>
          )}
        </div>
        <div className="workspace-label">
          RECENT RUNS <span>{runs.length.toString().padStart(2, "0")}</span>
        </div>
        <div className="run-list">
          {runs.length === 0 ? (
            <p className="empty-sidebar">
              Your first result starts with a reference run.
            </p>
          ) : (
            runs.slice(0, 8).map((r) => (
              <button
                key={r.id}
                onClick={() => {
                  setRunId(r.id);
                  setTab("evidence");
                }}
                className={
                  run?.id === r.id && tab === "evidence" ? "selected-run" : ""
                }
              >
                <span className={`run-dot ${r.status}`} />
                <div>
                  <strong>{runLabel(r.kind)}</strong>
                  <small>
                    {r.id.slice(0, 8)} · {r.status}
                  </small>
                </div>
              </button>
            ))
          )}
        </div>
        <div className="sidebar-bottom">
          <div>
            <ShieldCheck size={17} />
            <span>Simulations run locally</span>
          </div>
          <p>Optional AI sharing by approval.</p>
          <button onClick={() => setShowConnection(!showConnection)}>
            <PlugZap size={15} />
            Worker connection
          </button>
          <small>VENTURI / DEVELOPMENT BUILD 0.5.0</small>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <span>First principles</span>
          </div>
          <div
            className={`connection-status ${diagnostics && !offline ? "connected" : ""}`}
          >
            <span />
            {diagnostics && !offline
              ? `${diagnostics.is_wsl ? "WSL" : diagnostics.platform} worker connected`
              : "Worker disconnected"}
          </div>
        </header>
        <div className="page">
          <div className="page-title">
            <div>
              <div className="eyebrow">LOCAL FLOW WORKBENCH / 001</div>
              <h1>
                {tab === "geometry"
                  ? geometry?.imported
                    ? "From fluid volume to evidence."
                    : "Start with the fundamentals."
                  : "Every result has a record."}
              </h1>
              <p>
                {tab === "geometry"
                  ? geometry?.imported
                    ? "Confirm the inputs, review the mesh, and solve a bounded flow study."
                    : "A simple pipe. Known physics. An inspectable path from geometry to evidence."
                  : "Review the checks, keep the artifacts, and see exactly what has been established."}
              </p>
            </div>
            <span className="milestone-badge">
              M4 <span>PREVIEW</span>
            </span>
          </div>
          {error && (
            <div className="notice error" role="alert">
              <TriangleAlert size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                ×
              </button>
            </div>
          )}
          {(!diagnostics || showConnection) && (
            <SetupPanel
              onConnected={(value) => {
                setToken(value);
                setTokenInput(value);
                setConnectionAttempt((attempt) => attempt + 1);
              }}
            />
          )}
          {(!diagnostics || showConnection) && (
            <section className="connection-card">
              <div>
                <PlugZap size={23} />
                <h2>Connect your local worker</h2>
                <p>
                  Start the worker with the development launcher, or paste the
                  token from <code>venturi serve</code>.
                </p>
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setToken(tokenInput.trim());
                  setConnectionAttempt((attempt) => attempt + 1);
                }}
              >
                <label htmlFor="worker-token">Worker token</label>
                <div>
                  <input
                    id="worker-token"
                    type="password"
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder="Local session token"
                    autoComplete="off"
                  />
                  <button
                    className="primary"
                    disabled={connecting || !tokenInput.trim()}
                  >
                    {connecting ? (
                      <LoaderCircle className="spin" size={16} />
                    ) : (
                      <ArrowRight size={16} />
                    )}
                    Connect
                  </button>
                </div>
              </form>
              <small>Endpoint: {API} · authenticated local connection</small>
            </section>
          )}
          {diagnostics && (
            <>
              <details className="assistance-panel">
                <summary>Account, privacy and support</summary>
                <ManagedPanel
                  api={api}
                  onChanged={async () => {
                    window.dispatchEvent(new Event("venturi-ai-changed"));
                  }}
                />
                <SupportPanel api={api} />
              </details>
              <ol className="workflow-steps" aria-label="Study workflow">
                {[
                  "Import fluid volume",
                  "Assign ports & study",
                  "Review mesh",
                  "Solve & inspect",
                ].map((label, i) => (
                  <li
                    key={label}
                    className={
                      (
                        tab === "geometry"
                          ? i < 2
                          : run?.kind === "internal_mesh"
                            ? i === 2
                            : i === 3
                      )
                        ? "current"
                        : ""
                    }
                  >
                    <span>{i + 1}</span>
                    {label}
                  </li>
                ))}
              </ol>
              <div className="summary-strip">
                <div>
                  <Cpu size={17} />
                  <span>
                    OpenFOAM Foundation<strong>14 · serial worker</strong>
                  </span>
                </div>
                <div>
                  <Box size={17} />
                  <span>
                    {shownInternal ? "Prepared geometry" : "Reference geometry"}
                    <strong>
                      {shownInternal
                        ? shownGeometryName
                        : `Ø ${(shownRadius * 2000).toLocaleString()} × ${(shownLength * 1000).toLocaleString()} mm pipe`}
                    </strong>
                  </span>
                </div>
                <div>
                  <Wind size={17} />
                  <span>
                    Flow regime
                    <strong>
                      {shownInternal
                        ? (tab === "evidence" ? shownStudy : draft)?.recipe ===
                          "sst-straight-duct/1"
                          ? "Experimental SST · Re 4,000–100,000"
                          : "Laminar · port Re ≤ 200"
                        : `Laminar · Re ${shownReynolds.toFixed(0)}`}
                    </strong>
                  </span>
                </div>
                <div>
                  <ShieldCheck size={17} />
                  <span>
                    Evidence level<strong>Provisional verification</strong>
                  </span>
                </div>
              </div>
              {tab === "geometry" && geometry && (
                <>
                  <section className="import-card">
                    <div>
                      <strong>Bring a prepared fluid volume</strong>
                      <p>
                        Import a watertight STEP of the space occupied by fluid.
                        Solid parts and assemblies require preparation in your
                        CAD tool.
                      </p>
                    </div>
                    <label className="secondary import-button">
                      Import STEP
                      <input
                        aria-label="Import STEP"
                        type="file"
                        accept=".step,.stp"
                        disabled={busy || hasActive}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) void importStep(file);
                          e.target.value = "";
                        }}
                      />
                    </label>
                    {geometry.imported && (
                      <button
                        className="secondary"
                        disabled={busy || hasActive}
                        onClick={() =>
                          action(async () =>
                            displayGeometry(
                              await api("/geometry/fixture", {
                                method: "POST",
                              }),
                            ),
                          )
                        }
                      >
                        Reference geometry
                      </button>
                    )}
                  </section>
                  <section className="geometry-grid">
                    <div className="geometry-panel">
                      <div className="panel-heading">
                        <h2>Fluid domain</h2>
                        <span>
                          {geometry.source_name || "pipe.step"}{" "}
                          <span className="muted">
                            / {geometry.faces.length} faces
                          </span>
                        </span>
                      </div>
                      <Viewer
                        geometry={geometry}
                        assignments={assignments}
                        selected={selected}
                        onSelect={setSelected}
                        onCamera={setCamera}
                        restoreCamera={restoreCamera}
                      />
                      <div className="geometry-footer">
                        <span>
                          <span className="tiny-dot" />
                          Prepared, enclosed fluid volume
                        </span>
                        <code title={geometry.geometry_hash}>
                          REV {geometry.geometry_hash.slice(0, 10)}
                        </code>
                      </div>
                    </div>
                    <div className="boundary-panel">
                      <div className="panel-heading">
                        <h2>Boundary assignments</h2>
                        <span className="count-pill">
                          {geometry.faces.length}
                        </span>
                      </div>
                      <p className="panel-intro">
                        Select a face in the viewer or list, then assign its
                        role.
                      </p>
                      <div className="face-list">
                        {geometry.faces.map((f) => (
                          <button
                            key={f.id}
                            className={selected === f.id ? "selected" : ""}
                            onClick={() => setSelected(f.id)}
                          >
                            <i className={`role-dot ${assignments[f.id]}`} />
                            <div>
                              <strong>
                                {assignments[f.id] === "wall"
                                  ? geometry.imported
                                    ? "Wall"
                                    : "Pipe wall"
                                  : assignments[f.id] === "inlet"
                                    ? "Inlet port"
                                    : "Outlet port"}
                              </strong>
                              <small>
                                {(f.area_m2 * 1e6).toFixed(2)} mm² · Face{" "}
                                {f.index + 1}
                              </small>
                            </div>
                            <ChevronRight size={15} />
                          </button>
                        ))}
                      </div>
                      {face && (
                        <div className="face-detail">
                          <label htmlFor="face-role">SELECTED FACE ROLE</label>
                          <select
                            id="face-role"
                            value={assignments[face.id]}
                            onChange={(e) => {
                              setAssignments((a) => ({
                                ...a,
                                [face.id]: e.target.value as Role,
                              }));
                              setDirty(true);
                            }}
                          >
                            <option value="inlet">Inlet</option>
                            <option value="outlet">Outlet</option>
                            <option value="wall">Wall</option>
                          </select>
                          <code>{face.id}</code>
                          <p>
                            Saved references belong to this exact geometry
                            revision.
                          </p>
                        </div>
                      )}
                      <button
                        className="secondary save-button"
                        disabled={busy || !dirty}
                        onClick={() => action(save)}
                      >
                        <Check size={15} />
                        {dirty ? "Save assignments" : "Assignments saved"}
                      </button>
                    </div>
                  </section>
                  <div className="geometry-dimensions">
                    Source units: {geometry.source_units.join(", ")} · Converted
                    to metres · Bounding dimensions:{" "}
                    {[0, 1, 2]
                      .map((i) =>
                        (
                          (geometry.bounds_m[i + 3] - geometry.bounds_m[i]) *
                          1000
                        ).toFixed(3),
                      )
                      .join(" × ")}{" "}
                    mm · Fluid volume{" "}
                    {(geometry.volume_m3 * 1e9).toPrecision(6)} mm³
                  </div>
                  <Annotations
                    key={geometry.geometry_hash}
                    geometry={geometry}
                    selected={selected}
                    camera={camera}
                    api={api}
                    onSelect={setSelected}
                    onRestore={setRestoreCamera}
                  />
                  {geometry.imported && (
                    <button
                      className="secondary new-study"
                      disabled={busy || hasActive}
                      onClick={() => {
                        setRecord(null);
                        setDraft(null);
                        localStorage.removeItem(
                          `venturi-alpha-draft-${geometry.geometry_hash}-new`,
                        );
                        setOpenGeneration((n) => n + 1);
                      }}
                    >
                      New study on this geometry
                    </button>
                  )}
                  {geometry.imported && (
                    <AssistantPanel
                      api={api}
                      geometry={geometry}
                      assignments={assignments}
                      record={record}
                      disabled={busy || hasActive || offline || dirty}
                      onApplied={(saved) => {
                        setRecord(saved);
                        setDraft(saved.study);
                        setStudies((old) => [
                          saved,
                          ...old.filter((s) => s.id !== saved.id),
                        ]);
                        localStorage.removeItem(
                          `venturi-alpha-draft-${geometry.geometry_hash}-${saved.id}`,
                        );
                        setOpenGeneration((n) => n + 1);
                      }}
                    />
                  )}
                  {geometry.imported && (
                    <FlowSetup
                      key={`${geometry.geometry_hash}-${record?.id || "new"}-${openGeneration}`}
                      geometry={geometry}
                      assignments={assignments}
                      disabled={
                        busy ||
                        hasActive ||
                        offline ||
                        !diagnostics.solver_ready
                      }
                      record={record}
                      api={api}
                      onSave={saveStudy}
                      onDraft={setDraft}
                      onBuild={buildInternalMesh}
                    />
                  )}
                  <div className="test-cards">
                    {!geometry.imported && (
                      <section>
                        <span className="step-number">01 / GEOMETRY</span>
                        <h2>Does CAD become the right mesh?</h2>
                        <p>
                          Export the selected faces, generate a real mesh, then
                          compare boundary names, areas, and locations.
                        </p>
                        <button
                          className="secondary"
                          disabled={
                            busy || hasActive || !diagnostics.solver_ready
                          }
                          onClick={() => start("cad_mesh")}
                        >
                          Run boundary check
                          <ArrowRight size={16} />
                        </button>
                      </section>
                    )}
                    <section>
                      <span className="step-number">02 / NUMERICS</span>
                      <h2>Does the solver recover a known answer?</h2>
                      <p>
                        Run the fixed laminar pipe benchmark and compare
                        pressure drop, flow balance, and convergence.
                      </p>
                      <button
                        className="primary"
                        disabled={
                          busy || hasActive || !diagnostics.solver_ready
                        }
                        onClick={() => start("reference")}
                      >
                        Run pipe reference
                        <ArrowRight size={16} />
                      </button>
                    </section>
                  </div>
                </>
              )}
              {tab === "evidence" && (
                <section className="evidence-panel">
                  <div className="panel-heading">
                    <h2>{run ? runLabel(run.kind) : "No runs yet"}</h2>
                    {run && (
                      <span className={`status-pill ${run.status}`}>
                        {active(run) && (
                          <LoaderCircle size={13} className="spin" />
                        )}
                        {run.status}
                      </span>
                    )}
                  </div>
                  {run ? (
                    <>
                      <div className="run-heading">
                        <div>
                          <code>RUN {run.id.slice(0, 12)}</code>
                          <p>{run.stage}</p>
                        </div>
                        {active(run) ? (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() =>
                              action(async () => {
                                await api(`/runs/${run.id}/cancel`, {
                                  method: "POST",
                                });
                              })
                            }
                          >
                            <Square size={13} />
                            Cancel run
                          </button>
                        ) : (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => download()}
                          >
                            <ArrowDownToLine size={16} />
                            Export complete run
                          </button>
                        )}
                      </div>
                      {active(run) && (
                        <div className="running-indicator">
                          <div />
                          <p>
                            The worker is running locally. This page can be
                            reloaded without launching another job.
                          </p>
                        </div>
                      )}
                      {run.kind.startsWith("internal_") && (
                        <div
                          className={`study-currency ${currentStudy ? "current" : "historical"}`}
                          role="status"
                        >
                          {currentStudy
                            ? "Current study inputs"
                            : "Historical inputs — the current draft or geometry differs. Return to the study and build a new mesh for changes."}
                        </div>
                      )}
                      {run.error && (
                        <div className="recovery">
                          <pre className="run-error">{run.error}</pre>
                          <p>
                            This attempt stopped. Inspect its checks and logs.
                            For resource limits, revise the study budget; for
                            mesh or port failures, review geometry and cell
                            size. A retry preserves the failed attempt’s
                            original settings.
                          </p>
                        </div>
                      )}
                      {!active(run) &&
                        run.status !== "passed" &&
                        run.request && (
                          <button
                            className="secondary"
                            disabled={busy || hasActive}
                            onClick={retry}
                          >
                            Retry as a new attempt
                          </button>
                        )}
                      {run.retry_of && (
                        <p>
                          Retry of <code>{run.retry_of.slice(0, 12)}</code>. The
                          original evidence is retained.
                        </p>
                      )}
                      {run.study_hash && (
                        <details className="study-record">
                          <summary>Saved study and provenance</summary>
                          <p>
                            {run.result?.inputs?.name ||
                              run.request?.study?.name ||
                              "Saved study"}{" "}
                            ·{" "}
                            {run.result?.recipe ||
                              (run.kind.startsWith("internal_")
                                ? "laminar-internal/1"
                                : "laminar-pipe/1")}
                          </p>
                          <p>
                            Study <code>{run.study_hash}</code>
                          </p>
                          <p>
                            Recipe <code>{run.recipe_hash}</code>
                          </p>
                          <p>
                            The export includes frozen inputs, runtime versions,
                            command records, logs, and an artifact manifest.
                          </p>
                        </details>
                      )}
                      {run.desktop_brief && (
                        <div className="execution-budget">
                          <h3>{run.desktop_brief.question}</h3>
                          <p>
                            Fluid properties:{" "}
                            {run.desktop_brief.material_source} · Saved study
                            revision {run.desktop_brief.revision}.
                          </p>
                        </div>
                      )}
                      {run.kind.startsWith("internal_") &&
                        shownStudy?.resources && (
                          <div className="execution-budget">
                            Saved execution limits:{" "}
                            {shownStudy.resources.wall_time_seconds} s ·{" "}
                            {shownStudy.resources.memory_mb} MiB memory ·{" "}
                            {shownStudy.resources.disk_mb} MiB disk · one serial
                            CPU.
                          </div>
                        )}
                      <details
                        className="case-disclosure"
                        open={caseOpen}
                        onToggle={(e) => setCaseOpen(e.currentTarget.open)}
                      >
                        <summary>Inspect native case, logs & downloads</summary>
                        {caseOpen && (
                          <CaseViewer
                            key={run.id}
                            run={run}
                            runs={runs}
                            api={api}
                            onDownload={download}
                          />
                        )}
                      </details>
                      {run.result && (
                        <>
                          {run.kind.startsWith("internal_") &&
                            !["queued", "running"].includes(run.status) && (
                              <RecoveryPanel
                                key={`recovery-${run.id}`}
                                api={api}
                                run={run}
                                disabled={busy || offline || !currentStudy}
                                onRun={(id) => setRunId(id)}
                              />
                            )}
                          {run.kind === "internal_flow" &&
                            run.result.pressure_drop_pa !== undefined && (
                              <VisualReview
                                key={`visual-${run.id}`}
                                api={api}
                                run={run}
                                onExport={() => download(undefined, true)}
                              />
                            )}
                          {run.kind === "internal_mesh" &&
                            run.status === "passed" && (
                              <MeshReview
                                key={run.id}
                                run={run}
                                api={api}
                                disabled={
                                  busy || hasActive || offline || !currentStudy
                                }
                                onApprove={approveMesh}
                              />
                            )}
                          {run.result.pressure_drop_pa !== undefined && (
                            <div className="result-metrics">
                              <div>
                                <span>SIMULATED PRESSURE DROP</span>
                                <strong>
                                  {run.result.pressure_drop_pa.toFixed(5)}{" "}
                                  <small>Pa</small>
                                </strong>
                              </div>
                              {run.result.expected_pressure_drop_pa !==
                              undefined ? (
                                <>
                                  <div>
                                    <span>ANALYTICAL REFERENCE</span>
                                    <strong>
                                      {run.result.expected_pressure_drop_pa?.toFixed(
                                        5,
                                      )}{" "}
                                      <small>Pa</small>
                                    </strong>
                                  </div>
                                  <div>
                                    <span>RELATIVE DIFFERENCE</span>
                                    <strong>
                                      {(
                                        (run.result.relative_error || 0) * 100
                                      ).toFixed(2)}
                                      <small>%</small>
                                    </strong>
                                  </div>
                                </>
                              ) : (
                                <>
                                  <div>
                                    <span>FLOW IMBALANCE</span>
                                    <strong>
                                      {(
                                        (run.result.mass_imbalance || 0) * 100
                                      ).toExponential(2)}
                                      <small>%</small>
                                    </strong>
                                  </div>
                                  <div>
                                    <span>INLET FLOW</span>
                                    <strong>
                                      {run.result.volume_flow_in_m3_s?.toExponential(
                                        3,
                                      )}
                                      <small>m³/s</small>
                                    </strong>
                                  </div>
                                </>
                              )}
                            </div>
                          )}
                          <Plot run={run} />
                          {run.result.outlets && (
                            <div className="outlet-results">
                              <h3>Outlet flow distribution</h3>
                              <p>
                                Static pressure differences use area means at
                                each port. The headline drop weights outlet
                                pressure by its outward flow.
                              </p>
                              <table>
                                <thead>
                                  <tr>
                                    <th>Outlet</th>
                                    <th>Outward flow (m³/s)</th>
                                    <th>Share</th>
                                    <th>Pressure drop (Pa)</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {Object.entries(run.result.outlets).map(
                                    ([name, value]) => (
                                      <tr key={name}>
                                        <td>{name.replace("_", " ")}</td>
                                        <td>
                                          {value.volume_flow_m3_s.toExponential(
                                            5,
                                          )}
                                        </td>
                                        <td>
                                          {(value.flow_fraction * 100).toFixed(
                                            2,
                                          )}
                                          %
                                        </td>
                                        <td>
                                          {value.pressure_drop_pa.toPrecision(
                                            6,
                                          )}
                                        </td>
                                      </tr>
                                    ),
                                  )}
                                </tbody>
                              </table>
                            </div>
                          )}
                          <div className="evidence-checks">
                            {run.result.checks.map((c, i) => (
                              <CheckRow key={i} item={c} />
                            ))}
                          </div>
                          <div className="limitations">
                            <FileCheck2 size={20} />
                            <div>
                              <h3>What this result establishes</h3>
                              {run.result.limitations.map((l, i) => (
                                <p key={i}>{l}</p>
                              ))}
                            </div>
                          </div>
                        </>
                      )}
                    </>
                  ) : (
                    <div className="empty-state">
                      <Activity size={32} />
                      <p>
                        Run a boundary check or pipe reference to create your
                        first evidence record.
                      </p>
                      <button
                        className="primary"
                        onClick={() => setTab("geometry")}
                      >
                        Go to geometry
                        <ArrowRight size={16} />
                      </button>
                    </div>
                  )}
                </section>
              )}
              <section className="runtime-checks">
                <div className="section-label">WORKER READINESS</div>
                {diagnostics.checks.map((c, i) => (
                  <CheckRow key={i} item={c} />
                ))}
              </section>
            </>
          )}
          <footer>
            <span>
              Reference results use provisional criteria. Independent CFD review
              and platform qualification remain pending.
            </span>
            <span>Local by design. Inspectable by default.</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
