import { useEffect, useState } from "react";
import type { Api, Run } from "./types";

type Artifact = { path: string; bytes: number; previewable: boolean };
function Highlight({ value }: { value: string }) {
  return (
    <>
      {value
        .split(
          /(\/\/[^\n]*|"[^"\n]*"|\b(?:dimensions|internalField|boundaryField|type|value|application|solver|nu|endTime)\b|\b\d+(?:\.\d+)?(?:e[-+]?\d+)?\b)/gi,
        )
        .map((part, i) => (
          <span
            key={i}
            className={
              part.startsWith("//")
                ? "syntax-comment"
                : part.startsWith('"')
                  ? "syntax-string"
                  : /^\d/.test(part)
                    ? "syntax-number"
                    : /^(dimensions|internalField|boundaryField|type|value|application|solver|nu|endTime)$/.test(
                          part,
                        )
                      ? "syntax-keyword"
                      : ""
            }
          >
            {part}
          </span>
        ))}
    </>
  );
}
export function CaseViewer({
  run,
  runs,
  api,
  onDownload,
}: {
  run: Run;
  runs: Run[];
  api: Api;
  onDownload: (path: string) => Promise<void>;
}) {
  const [files, setFiles] = useState<Artifact[]>([]);
  const [path, setPath] = useState("study.json");
  const [filter, setFilter] = useState("");
  const [compare, setCompare] = useState("");
  const [content, setContent] = useState<{
    text: string;
    sha256?: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let stopped = false;
    api(`/runs/${run.id}/artifacts`)
      .then((f) => {
        if (!stopped) setFiles(f);
      })
      .catch((e) => {
        if (!stopped) setError(String(e));
      });
    return () => {
      stopped = true;
    };
  }, [run.id, run.status, api, refresh]);
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    setContent(null);
    setError("");
    const route = compare
      ? `diff?other=${encodeURIComponent(compare)}&path=`
      : "text?path=";
    api(`/runs/${run.id}/${route}${encodeURIComponent(path)}`)
      .then((c) => {
        if (!stopped) setContent(c);
      })
      .catch((e) => {
        if (!stopped) setError(String(e));
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, [run.id, run.status, path, compare, api, refresh]);
  const downloads = [
    "report.html",
    "report.md",
    "metrics.csv",
    "outlet-flows.csv",
    "pressure-history.svg",
    "history.csv",
  ];
  return (
    <section className="case-viewer">
      <h3>
        Native case & artifacts <small>Read only</small>
      </h3>
      <p>
        Inspect the actual OpenFOAM case: initial fields in <code>0/</code>,
        fluid properties and mesh in <code>constant/</code>, and solver settings
        in <code>system/</code>. Pressure field p uses m²/s²; reported pressure
        differences use Pa and the saved density.
      </p>
      <div className="artifact-downloads">
        {files
          .filter(
            (f) =>
              downloads.includes(f.path) ||
              (f.path.endsWith(".csv") && !f.path.includes("/")),
          )
          .map((f) => (
            <button
              className="secondary"
              key={f.path}
              onClick={() => onDownload(f.path)}
            >
              Download {f.path}
            </button>
          ))}
      </div>
      <div className="action-row">
        <button
          className="secondary"
          onClick={() => {
            setPath("case/0/U");
            setCompare("");
          }}
        >
          Velocity boundary entries
        </button>
        <button
          className="secondary"
          onClick={() => {
            setPath("case/0/p");
            setCompare("");
          }}
        >
          Pressure boundary entries
        </button>
        <button
          className="secondary"
          onClick={() => {
            setPath("mesh-audit.json");
            setCompare("");
          }}
        >
          CAD to patch mapping
        </button>
        <button className="secondary" onClick={() => setRefresh((n) => n + 1)}>
          Refresh files & logs
        </button>
      </div>
      <div className="case-layout">
        <div className="file-tree">
          <label>
            Filter files
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="system/, logs/, .csv…"
            />
          </label>
          <div role="list" aria-label="Case files">
            {files
              .filter((f) =>
                f.path.toLowerCase().includes(filter.toLowerCase()),
              )
              .map((f) => (
                <button
                  role="listitem"
                  className={f.path === path ? "selected" : ""}
                  key={f.path}
                  onClick={() => {
                    setPath(f.path);
                    setCompare("");
                  }}
                >
                  <span>{f.path}</span>
                  <small>
                    {(f.bytes / 1024).toFixed(1)} KiB
                    {!f.previewable ? " · download" : ""}
                  </small>
                </button>
              ))}
          </div>
        </div>
        <div className="file-preview">
          <div className="preview-heading">
            <code>{path}</code>
            <button
              className="secondary"
              disabled={!files.some((f) => f.path === path)}
              onClick={() => onDownload(path)}
            >
              Download file
            </button>
          </div>
          <label>
            Compare same file with
            <select
              value={compare}
              onChange={(e) => setCompare(e.target.value)}
            >
              <option value="">No comparison</option>
              {runs
                .filter((r) => r.id !== run.id)
                .map((r) => (
                  <option value={r.id} key={r.id}>
                    {r.request?.study?.name || r.kind} · {r.id.slice(0, 8)}
                  </option>
                ))}
            </select>
          </label>
          {loading && <p role="status">Loading file…</p>}
          {error && <p role="alert">{error}</p>}
          {content && (
            <>
              <pre
                tabIndex={0}
                aria-label={compare ? "File difference" : "File content"}
              >
                <Highlight value={content.text} />
              </pre>
              {content.sha256 && (
                <small className="file-hash">SHA-256 {content.sha256}</small>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
