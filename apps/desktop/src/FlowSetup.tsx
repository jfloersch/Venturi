import { useState } from "react";
import type { Geometry, InternalStudy, Role } from "./types";

type Values = {
  name: string;
  flow: string;
  density: string;
  viscosity: string;
  cell: string;
};
export function FlowSetup({
  geometry,
  assignments,
  disabled,
  onBuild,
}: {
  geometry: Geometry;
  assignments: Record<string, Role>;
  disabled: boolean;
  onBuild: (study: InternalStudy) => void;
}) {
  const key = `venturi-study-${geometry.geometry_hash}`;
  const [values, setValues] = useState<Values>(() => {
    const widths = [0, 1, 2].map(
      (i) => geometry.bounds_m[i + 3] - geometry.bounds_m[i],
    );
    const defaults = {
      name: geometry.source_name || "Prepared STEP flow",
      flow: "0.0000001",
      density: "1000",
      viscosity: "0.001",
      cell: ((Math.min(...widths) / 12) * 1000).toPrecision(4),
    };
    try {
      return { ...defaults, ...JSON.parse(localStorage.getItem(key) || "{}") };
    } catch {
      return defaults;
    }
  });
  function update(name: keyof Values, value: string) {
    setValues((old) => {
      const next = { ...old, [name]: value };
      localStorage.setItem(key, JSON.stringify(next));
      return next;
    });
  }
  const ports = Object.values(assignments);
  const validPorts =
    ports.filter((x) => x === "inlet").length === 1 &&
    ports.filter((x) => x === "outlet").length >= 1 &&
    ports.filter((x) => x === "outlet").length <= 4 &&
    ports.includes("wall");
  return (
    <section className="flow-setup">
      <div className="panel-heading">
        <h2>Set up internal flow</h2>
        <span>Steady · laminar · SI</span>
      </div>
      <p>
        Use a prepared fluid volume with one planar inlet and up to four planar
        outlets. The inlet has uniform normal flow, outlets share zero gauge
        pressure, and walls are stationary with no slip.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onBuild({
            schema_version: "venturi.internal-study.v1",
            recipe: "laminar-internal/1",
            name: values.name,
            selection: { geometry_hash: geometry.geometry_hash, assignments },
            flow_rate_m3_s: Number(values.flow),
            density_kg_m3: Number(values.density),
            dynamic_viscosity_pa_s: Number(values.viscosity),
            mesh: { cell_size_m: Number(values.cell) / 1000 },
          });
        }}
      >
        <div className="flow-fields">
          <label>
            Study name
            <input
              required
              maxLength={160}
              value={values.name}
              onChange={(e) => update("name", e.target.value)}
            />
          </label>
          <label>
            Inlet flow (m³/s)
            <input
              required
              type="number"
              min="1e-14"
              step="any"
              value={values.flow}
              onChange={(e) => update("flow", e.target.value)}
            />
          </label>
          <label>
            Density (kg/m³)
            <input
              required
              type="number"
              min="0.000001"
              step="any"
              value={values.density}
              onChange={(e) => update("density", e.target.value)}
            />
          </label>
          <label>
            Dynamic viscosity (Pa·s)
            <input
              required
              type="number"
              min="1e-14"
              step="any"
              value={values.viscosity}
              onChange={(e) => update("viscosity", e.target.value)}
            />
          </label>
          <label>
            Mesh cell size (mm)
            <input
              required
              type="number"
              min="0.000001"
              step="any"
              value={values.cell}
              onChange={(e) => update("cell", e.target.value)}
            />
          </label>
        </div>
        <p className="flow-note">
          Review the displayed fluid properties before continuing. The worker
          checks the flow regime and port resolution. You will review and
          approve the mesh before solving.
        </p>
        {!validPorts && (
          <p className="flow-note">
            Assign one inlet and one to four outlets, then save the assignments.
          </p>
        )}
        <button className="primary" disabled={disabled || !validPorts}>
          Build mesh for review
        </button>
      </form>
    </section>
  );
}
