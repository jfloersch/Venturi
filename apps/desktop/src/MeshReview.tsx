import { useEffect, useState } from "react";
import type { Geometry, InternalStudy, Run } from "./types";
import { Viewer } from "./Viewer";
export function MeshReview({
  run,
  api,
  disabled,
  onApprove,
}: {
  run: Run;
  api: (path: string) => Promise<unknown>;
  disabled: boolean;
  onApprove: () => void;
}) {
  const [preview, setPreview] = useState<Geometry | null>(null);
  const [error, setError] = useState("");
  const [renderReady, setRenderReady] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const study = run.request?.study as InternalStudy | undefined;
  useEffect(() => {
    let disposed = false;
    setPreview(null);
    setError("");
    setRenderReady(false);
    api(`/runs/${run.id}/files/mesh-preview.json`)
      .then((value) => {
        const geometry = value as Geometry;
        if (geometry.geometry_hash !== run.result?.mesh_hash)
          throw new Error("Mesh preview does not match the recorded mesh.");
        if (!disposed) {
          setPreview(geometry);
          setSelected(geometry.faces[0]?.id || null);
        }
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, [run.id, run.result?.mesh_hash, api]);
  return (
    <section className="mesh-review">
      <h3>Review this mesh and saved study</h3>
      <p>
        {run.result?.mesh?.cells.toLocaleString()} cells ·{" "}
        {run.result?.mesh?.regions} connected fluid region
      </p>
      <p>
        Inlet flow {study?.flow_rate_m3_s.toExponential(4)} m³/s · density{" "}
        {study?.density_kg_m3} kg/m³ · viscosity {study?.dynamic_viscosity_pa_s}{" "}
        Pa·s.
      </p>
      <p>
        Uniform inlet flow · equal zero-gauge outlet pressure · stationary
        no-slip walls.
      </p>
      {error && <p role="alert">{error}</p>}
      {preview && (
        <Viewer
          geometry={preview}
          assignments={preview.selection.assignments}
          selected={selected}
          onSelect={setSelected}
          mesh
          onRenderReady={setRenderReady}
        />
      )}
      <p className="mesh-identity">
        Geometry <code>{study?.selection.geometry_hash}</code>
        <br />
        Mesh <code>{run.result?.mesh_hash}</code>
      </p>
      <p>
        Approval applies to these saved inputs and this exact mesh. Check that
        the visible fluid region and ports represent your intended study. A new
        study or changed mesh needs a new review.
      </p>
      <button
        className="primary"
        disabled={disabled || !preview || !renderReady || !!error}
        onClick={onApprove}
      >
        Approve saved study & solve
      </button>
    </section>
  );
}
