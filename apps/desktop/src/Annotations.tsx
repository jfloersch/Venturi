import { useEffect, useState } from "react";
import type { Api, Camera, Geometry, Notes } from "./types";

export function Annotations({
  geometry,
  selected,
  camera,
  api,
  onSelect,
  onRestore,
}: {
  geometry: Geometry;
  selected: string | null;
  camera: Camera | null;
  api: Api;
  onSelect: (id: string) => void;
  onRestore: (camera: Camera) => void;
}) {
  const [notes, setNotes] = useState<Notes | null>(null);
  const [label, setLabel] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const entity = [...geometry.faces, ...(geometry.edges || [])].find(
    (e) => e.id === selected,
  );
  useEffect(() => {
    let disposed = false;
    api(`/geometry/${geometry.geometry_hash}/annotations`)
      .then((n) => {
        if (!disposed) setNotes(n);
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, [geometry.geometry_hash, api]);
  async function save(annotations: Notes["annotations"]) {
    if (!notes) return;
    setBusy(true);
    setError("");
    try {
      const value = await api("/annotations", {
        method: "POST",
        body: JSON.stringify({
          geometry_hash: geometry.geometry_hash,
          expected_revision: notes.revision,
          annotations: annotations.map(
            ({ id, entity_id, label, text, position_m, camera }) => ({
              id,
              entity_id,
              label,
              text,
              position_m,
              camera,
            }),
          ),
        }),
      });
      setNotes(value);
      setLabel("");
      setText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="annotations">
      <h3>References & annotations</h3>
      <p>
        Notes belong to this exact STEP revision. Re-imported changes require
        new selections; annotations are never mapped silently.
      </p>
      <details>
        <summary>CAD edges ({geometry.edges?.length || 0})</summary>
        <div className="edge-list">
          {geometry.edges?.map((e) => (
            <button
              className={selected === e.id ? "selected" : ""}
              key={e.id}
              onClick={() => onSelect(e.id)}
            >
              Edge {e.index + 1} · {(e.length_m * 1000).toFixed(3)} mm
            </button>
          ))}
        </div>
      </details>
      {entity && (
        <p className="reference-detail">
          <code>{entity.id}</code> · centroid{" "}
          {entity.centroid_m.map((n) => (n * 1000).toFixed(3)).join(", ")} mm{" "}
          {"length_m" in entity
            ? `· ${entity.adjacent_faces.length} adjacent faces`
            : `· ${entity.surface_type}`}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (notes && entity && camera)
            void save([
              ...notes.annotations,
              {
                id: crypto.randomUUID(),
                entity_id: entity.id,
                label,
                text,
                position_m:
                  "points" in entity
                    ? entity.points.slice(0, 3)
                    : geometry.points.slice(
                        entity.cells[1] * 3,
                        entity.cells[1] * 3 + 3,
                      ),
                camera,
              },
            ]);
        }}
      >
        <div className="flow-fields">
          <label>
            Reference label
            <input
              required
              maxLength={120}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Pump supply"
            />
          </label>
          <label>
            Annotation
            <textarea
              maxLength={2000}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
        </div>
        <button
          className="secondary"
          disabled={busy || !notes || !entity || !camera}
        >
          Save annotation
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      <ul className="note-list">
        {notes?.annotations.map((n) => (
          <li key={n.id}>
            <button
              className="secondary"
              onClick={() => {
                onSelect(n.entity_id);
                onRestore(n.camera);
              }}
            >
              {n.label}
            </button>
            <p>{n.text}</p>
            <code>{n.entity_id}</code>
            <button
              className="text-button"
              aria-label={`Remove annotation ${n.label}`}
              disabled={busy}
              onClick={() =>
                save(notes.annotations.filter((a) => a.id !== n.id))
              }
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
