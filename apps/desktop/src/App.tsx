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
import { FlowSetup } from "./FlowSetup";
import { MeshReview } from "./MeshReview";
import type {
  Check as EvidenceCheck,
  Diagnostics,
  Geometry,
  Role,
  Run,
  RunKind,
  InternalStudy,
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
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [assignments, setAssignments] = useState<Record<string, Role>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"geometry" | "evidence">("geometry");
  const [showConnection, setShowConnection] = useState(false);
  const run = runs.find((r) => r.id === runId) || runs[0];
  const hasActive = runs.some(active);
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
    Promise.all([api("/diagnostics"), api("/geometry"), api("/runs")])
      .then(([d, g, r]: [Diagnostics, Geometry, Run[]]) => {
        if (disposed) return;
        if (d.protocol_version !== protocol)
          throw new Error(
            "Worker protocol differs from this desktop. Install matching versions.",
          );
        setDiagnostics(d);
        setGeometry(g);
        setAssignments(g.selection.assignments);
        setSelected(g.faces[0]?.id || null);
        setRuns(r);
        setDirty(false);
        setShowConnection(false);
        sessionStorage.setItem("venturi-token", token);
      })
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
  }, [token, api]);

  useEffect(() => {
    if (!diagnostics) return;
    const id = window.setInterval(
      () =>
        api("/runs")
          .then(setRuns)
          .catch((e) => setError(`Worker connection lost: ${String(e)}`)),
      1500,
    );
    return () => window.clearInterval(id);
  }, [diagnostics, api]);

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
  async function download() {
    if (!run) return;
    await action(async () => {
      const response = await fetch(`${API}/v1/runs/${run.id}/export`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok)
        throw new Error(
          "Could not export this run. Wait for execution to finish.",
        );
      const filename = `venturi-${run.id.slice(0, 8)}.zip`;
      if ("__TAURI_INTERNALS__" in window) {
        const { save } = await import("@tauri-apps/plugin-dialog");
        const { writeFile } = await import("@tauri-apps/plugin-fs");
        const destination = await save({
          defaultPath: filename,
          filters: [{ name: "Venturi run archive", extensions: ["zip"] }],
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
  async function buildInternalMesh(study: InternalStudy) {
    await action(async () => {
      if (dirty) await save();
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          kind: "internal_mesh",
          request_id: crypto.randomUUID(),
          study,
        }),
      });
      setRuns((old) => [value, ...old.filter((r) => r.id !== value.id)]);
      setRunId(value.id);
      setTab("evidence");
    });
  }
  async function approveMesh() {
    if (!run || run.kind !== "internal_mesh") return;
    await action(async () => {
      const value: Run = await api("/runs", {
        method: "POST",
        body: JSON.stringify({
          kind: "internal_flow",
          request_id: crypto.randomUUID(),
          study: run.request?.study,
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
            <span>Milestone 1b · STEP to report</span>
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
            <span>Files stay on this computer</span>
          </div>
          <p>No AI account required.</p>
          <button onClick={() => setShowConnection(!showConnection)}>
            <PlugZap size={15} />
            Worker connection
          </button>
          <small>VENTURI / DEVELOPMENT BUILD 0.2.0</small>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <span>First principles</span>
          </div>
          <div
            className={`connection-status ${diagnostics ? "connected" : ""}`}
          >
            <span />
            {diagnostics
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
                    ? "Confirm the ports, review the mesh, and solve a bounded laminar study."
                    : "A simple pipe. Known physics. An inspectable path from geometry to evidence."
                  : "Review the checks, keep the artifacts, and see exactly what has been established."}
              </p>
            </div>
            <span className="milestone-badge">
              M1b <span>PREVIEW</span>
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
                  if (tokenInput.trim() === token) {
                    setToken("");
                    window.setTimeout(() => setToken(tokenInput.trim()), 10);
                  }
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
                        ? "Laminar · port Re ≤ 200"
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
                  {geometry.imported && (
                    <FlowSetup
                      key={geometry.geometry_hash}
                      geometry={geometry}
                      assignments={assignments}
                      disabled={busy || hasActive || !diagnostics.solver_ready}
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
                            onClick={download}
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
                      {run.error && (
                        <pre className="run-error">{run.error}</pre>
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
                      {run.result && (
                        <>
                          {run.kind === "internal_mesh" &&
                            run.status === "passed" && (
                              <MeshReview
                                key={run.id}
                                run={run}
                                api={api}
                                disabled={busy || hasActive}
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
