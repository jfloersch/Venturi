import { useEffect, useState } from "react";
import type {
  Api,
  Geometry,
  InternalStudy,
  Role,
  StudyRecord,
  StudySave,
} from "./types";

type Values = {
  name: string;
  flow: string;
  density: string;
  viscosity: string;
  cell: string;
  question: string;
  source: string;
  wall: string;
  memory: string;
  disk: string;
  cells: string;
  iterations: string;
  meshTime: string;
  solveTime: string;
  profile: StudyRecord["profile"];
  flowUnit: "m3s" | "lmin";
};
type Plan = {
  study: InternalStudy;
  study_hash: string;
  estimated_cells: number[];
  estimate_note: string;
  port_reynolds: Record<string, number>;
  execution: string;
  inputKey: string;
};
export function loadStudyValues(
  geometry: Geometry,
  record: StudyRecord | null,
): Values {
  const key = `venturi-alpha-draft-${geometry.geometry_hash}-${record?.id || "new"}`;
  const widths = [0, 1, 2].map(
    (i) => geometry.bounds_m[i + 3] - geometry.bounds_m[i],
  );
  const s = record?.study;
  const defaults: Values = {
    name: s?.name || geometry.source_name || "Prepared STEP flow",
    flow: s ? String(s.flow_rate_m3_s) : "",
    density: s ? String(s.density_kg_m3) : "1000",
    viscosity: s ? String(s.dynamic_viscosity_pa_s) : "0.001",
    cell: s
      ? String(s.mesh.cell_size_m * 1000)
      : ((Math.min(...widths) / 12) * 1000).toPrecision(4),
    question: record?.question || "",
    source: record?.material_source || "",
    wall: String(s?.resources?.wall_time_seconds ?? 2400),
    memory: String(s?.resources?.memory_mb ?? 4096),
    disk: String(s?.resources?.disk_mb ?? 2048),
    cells: String(s?.mesh.maximum_cells ?? 250000),
    iterations: String(s?.max_iterations ?? 600),
    meshTime: String(s?.mesh.timeout_seconds ?? 600),
    solveTime: String(s?.timeout_seconds ?? 900),
    profile: record?.profile || "guided",
    flowUnit: "m3s",
  };
  try {
    const draft = JSON.parse(localStorage.getItem(key) || "null");
    if (draft?.baseRevision === (record?.revision ?? 0))
      return { ...defaults, ...draft.values };
  } catch {
    /* Invalid browser drafts do not override a saved study. */
  }
  return defaults;
}

export function studyFromValues(
  values: Values,
  geometry: Geometry,
  assignments: Record<string, Role>,
): InternalStudy {
  return {
    schema_version: "venturi.internal-study.v1",
    recipe: "laminar-internal/1",
    name: values.name,
    selection: { geometry_hash: geometry.geometry_hash, assignments },
    flow_rate_m3_s:
      Number(values.flow) / (values.flowUnit === "lmin" ? 60000 : 1),
    density_kg_m3: Number(values.density),
    dynamic_viscosity_pa_s: Number(values.viscosity),
    mesh: {
      cell_size_m: Number(values.cell) / 1000,
      maximum_cells: Number(values.cells),
      timeout_seconds: Number(values.meshTime),
    },
    resources: {
      wall_time_seconds: Number(values.wall),
      memory_mb: Number(values.memory),
      disk_mb: Number(values.disk),
    },
    max_iterations: Number(values.iterations),
    timeout_seconds: Number(values.solveTime),
  };
}

export function FlowSetup({
  geometry,
  assignments,
  disabled,
  record,
  api,
  onSave,
  onBuild,
  onDraft,
}: {
  geometry: Geometry;
  assignments: Record<string, Role>;
  disabled: boolean;
  record: StudyRecord | null;
  api: Api;
  onSave: (value: StudySave) => Promise<StudyRecord>;
  onBuild: (value: StudySave) => Promise<void>;
  onDraft: (study: InternalStudy | null) => void;
}) {
  const key = `venturi-alpha-draft-${geometry.geometry_hash}-${record?.id || "new"}`;
  const [values, setValues] = useState<Values>(() =>
    loadStudyValues(geometry, record),
  );
  const [plan, setPlan] = useState<Plan | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(
    () => !!record && !localStorage.getItem(key),
  );
  const roles = Object.values(assignments);
  const validPorts =
    roles.filter((x) => x === "inlet").length === 1 &&
    roles.filter((x) => x === "outlet").length >= 1 &&
    roles.filter((x) => x === "outlet").length <= 4 &&
    roles.includes("wall");
  const study = studyFromValues(values, geometry, assignments);
  const serialized = JSON.stringify(study);
  const inputKey = JSON.stringify([
    study,
    values.question,
    values.source,
    values.profile,
  ]);
  useEffect(() => {
    onDraft(JSON.parse(serialized));
    setPlan(null);
    setConfirmed(false);
  }, [serialized, onDraft]);
  function update(name: keyof Values, value: string) {
    const next = { ...values, [name]: value };
    setValues(next);
    setPlan(null);
    setConfirmed(false);
    setSaved(false);
    setError("");
    localStorage.setItem(
      key,
      JSON.stringify({ baseRevision: record?.revision ?? 0, values: next }),
    );
  }
  const payload = (): StudySave => ({
    study,
    question: values.question,
    material_source: values.source,
    profile: values.profile,
  });
  async function perform(task: () => Promise<void>, lockInputs = false) {
    setWorking(true);
    setWriting(lockInputs);
    setError("");
    try {
      await task();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
      setWriting(false);
    }
  }
  const blocked = disabled || working || !validPorts;
  function numeric(
    label: string,
    name: keyof Values,
    min: number,
    max: number,
    step = "any",
  ) {
    return (
      <label>
        {label}
        <input
          disabled={writing || disabled}
          required
          type="number"
          min={min}
          max={max}
          step={step}
          value={values[name]}
          onChange={(e) => update(name, e.target.value)}
        />
      </label>
    );
  }
  return (
    <section className="flow-setup">
      <div className="panel-heading">
        <h2>Set up internal flow</h2>
        <span>
          {saved
            ? `Saved revision ${record?.revision || ""}`
            : "Unsaved draft · stored in this browser"}
        </span>
      </div>
      <p>
        Find pressure loss and outlet flow split in a prepared fluid volume.
        This recipe uses steady laminar flow, a uniform normal inlet, equal
        zero-gauge outlet pressures and stationary no-slip walls.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const intent = (e.nativeEvent as SubmitEvent).submitter?.getAttribute(
            "value",
          );
          void perform(async () => {
            if (intent === "save") {
              await onSave(payload());
              setSaved(true);
              localStorage.removeItem(key);
            } else {
              const response = await api("/studies/plan", {
                method: "POST",
                body: serialized,
              });
              setPlan({ ...response, inputKey });
              setConfirmed(false);
            }
          }, intent === "save");
        }}
      >
        <div className="flow-fields">
          <label>
            Study name
            <input
              disabled={writing || disabled}
              required
              maxLength={160}
              value={values.name}
              onChange={(e) => update("name", e.target.value)}
            />
          </label>
          <label>
            Presentation
            <select
              disabled={writing || disabled}
              value={values.profile}
              onChange={(e) => update("profile", e.target.value)}
            >
              <option value="guided">Guided</option>
              <option value="collaborative">Collaborative</option>
              <option value="expert">Expert</option>
            </select>
          </label>
          <label className="wide-field">
            Engineering question
            <textarea
              disabled={writing || disabled}
              required
              maxLength={2000}
              placeholder="What pressure loss or flow distribution do you need to compare?"
              value={values.question}
              onChange={(e) => update("question", e.target.value)}
            />
          </label>
          {numeric(
            values.flowUnit === "m3s"
              ? "Inlet flow (m³/s)"
              : "Inlet flow (L/min)",
            "flow",
            1e-14,
            values.flowUnit === "m3s" ? 1 : 60000,
          )}
          <label>
            Flow units
            <select
              disabled={writing || disabled}
              value={values.flowUnit}
              onChange={(e) => {
                const unit = e.target.value as Values["flowUnit"];
                const flow = values.flow
                  ? String(
                      Number(values.flow) *
                        (unit === "lmin" ? 60000 : 1 / 60000),
                    )
                  : "";
                const next = { ...values, flow, flowUnit: unit };
                setValues(next);
                setPlan(null);
                setConfirmed(false);
                localStorage.setItem(
                  key,
                  JSON.stringify({
                    baseRevision: record?.revision ?? 0,
                    values: next,
                  }),
                );
              }}
            >
              <option value="m3s">m³/s</option>
              <option value="lmin">L/min</option>
            </select>
          </label>
          {numeric("Density (kg/m³)", "density", 0.000001, 20000)}
          {numeric("Dynamic viscosity (Pa·s)", "viscosity", 1e-14, 100)}
          <label className="wide-field">
            Fluid property source
            <input
              disabled={writing || disabled}
              required
              maxLength={500}
              placeholder="Fluid, temperature, and source of density and viscosity"
              value={values.source}
              onChange={(e) => update("source", e.target.value)}
            />
          </label>
          {numeric("Mesh cell size (mm)", "cell", 0.000001, 1000)}
        </div>
        <p>
          Density 1000 kg/m³ and viscosity 0.001 Pa·s are suggested starting
          values. Confirm or replace them using your material source. Flow has
          no default. Cell size is an initial geometric recommendation, subject
          to mesh checks.
        </p>
        <h3>Resource limits</h3>
        <p>
          One serial CPU and one attempt per action. No automatic retry and no
          AI spend. Wall time covers all tools; memory is a per-process
          virtual-memory ceiling, and disk usage is polled.
        </p>
        <div className="flow-fields">
          {numeric("Maximum elapsed time (s)", "wall", 1, 3600, "1")}
          {numeric("Memory ceiling (MiB)", "memory", 256, 16384, "1")}
          {numeric("Disk ceiling (MiB)", "disk", 1, 8192, "1")}
          {numeric("Maximum mesh cells", "cells", 1000, 400000, "1")}
        </div>
        {values.profile !== "guided" && (
          <div className="flow-fields">
            {numeric("Maximum solver iterations", "iterations", 100, 2000, "1")}
            {numeric("Mesh tool timeout (s)", "meshTime", 1, 1800, "1")}
            {numeric("Solver timeout (s)", "solveTime", 1, 1800, "1")}
          </div>
        )}
        {!validPorts && (
          <p>
            Assign one planar inlet and one to four planar outlets. Every other
            face is a wall.
          </p>
        )}
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        <div className="action-row">
          <button className="secondary" value="save" disabled={blocked}>
            Save study
          </button>
          <button className="primary" value="review" disabled={blocked}>
            Review study
          </button>
        </div>
      </form>
      {plan && plan.inputKey === inputKey && (
        <div className="study-plan">
          <h3>Review the study and execution plan</h3>
          <p>{values.question}</p>
          <p>
            {plan.study.flow_rate_m3_s.toExponential(5)} m³/s · {values.density}{" "}
            kg/m³ · {values.viscosity} Pa·s · cell size {values.cell} mm.
          </p>
          <p>
            Fluid source: {values.source}. Full-flow port Reynolds:{" "}
            {Object.values(plan.port_reynolds)
              .map((r) => r.toFixed(2))
              .join(", ")}{" "}
            (limit 200).
          </p>
          <p>
            Estimated cells:{" "}
            {plan.estimated_cells.map((n) => n.toLocaleString()).join("–")}.
            Maximum: {values.cells}.
          </p>
          <p>{plan.estimate_note}</p>
          <p>
            {plan.execution} Limits: {values.wall} s, {values.memory} MiB
            memory, {values.disk} MiB disk. Maximum solver iterations:{" "}
            {values.iterations}; mesh-tool timeout: {values.meshTime} s; solver
            timeout: {values.solveTime} s.
          </p>
          <label className="confirm">
            <input
              disabled={writing || disabled}
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            I confirm the units, fluid region, ports, material properties and
            resource limits for this study.
          </label>
          <button
            className="primary"
            disabled={blocked || !confirmed}
            onClick={() =>
              perform(async () => {
                await onBuild(payload());
                localStorage.removeItem(key);
              }, true)
            }
          >
            Build mesh for review
          </button>
        </div>
      )}
    </section>
  );
}
