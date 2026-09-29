import { useEffect, useState } from "react";
import type { Api } from "./types";

export function SetupPanel({
  onConnected,
}: {
  onConnected: (token: string) => void;
}) {
  const native = "__TAURI_INTERNALS__" in window;
  const [status, setStatus] = useState<{
    status: string;
    log: string;
    platform: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const invoke = async <T,>(command: string) =>
    (await import("@tauri-apps/api/core")).invoke<T>(command);
  useEffect(() => {
    if (!native) return;
    const refresh = () =>
      void invoke<typeof status>("setup_status")
        .then(setStatus)
        .catch((e) => setError(String(e)));
    refresh();
    const id = window.setInterval(refresh, 1500);
    return () => window.clearInterval(id);
  }, [native]);
  return (
    <section className="connection-card setup-panel">
      <h2>Guided local setup</h2>
      <p>
        Venturi runs simulations on this computer. The initial supported
        environment is Ubuntu 22.04 x86_64, including Windows WSL2. Setup
        downloads a private Python environment, CAD libraries and the pinned
        solver. Allow several gigabytes of disk space and an internet
        connection.
      </p>
      {native ? (
        <>
          {status?.platform === "windows" && (
            <p>
              <button
                onClick={() =>
                  void invoke("install_wsl").catch((e) => setError(String(e)))
                }
              >
                Install Ubuntu 22.04 with WSL
              </button>
              Windows may request administrator approval and a restart. Open
              Ubuntu once to create your Linux account, then return here.
            </p>
          )}
          <p>
            Ubuntu needs the CAD graphics libraries. If setup reports missing
            libraries, install the prerequisites and retry.
          </p>
          <button
            disabled={status?.status === "running"}
            onClick={() =>
              void invoke("install_prerequisites").catch((e) =>
                setError(String(e)),
              )
            }
          >
            Install Ubuntu prerequisites
          </button>
          <button
            disabled={status?.status === "running"}
            onClick={() => {
              setError("");
              void invoke("setup_worker").catch((e) => setError(String(e)));
            }}
          >
            Install or repair local worker
          </button>
          <button
            disabled={connecting || status?.status === "running"}
            onClick={async () => {
              setError("");
              setConnecting(true);
              try {
                const result = await invoke<{ token: string }>(
                  "worker_connection",
                );
                onConnected(result.token);
              } catch (e) {
                setError(String(e));
              } finally {
                setConnecting(false);
              }
            }}
          >
            {connecting ? "Connecting…" : "Start installed worker"}
          </button>
          {status && <p role="status">Setup: {status.status}</p>}
          {status?.log && (
            <details>
              <summary>Local setup details</summary>
              <pre className="assistant-context">{status.log}</pre>
            </details>
          )}
        </>
      ) : (
        <p>
          Use the native desktop installer for guided setup. The browser
          workbench connects to an existing local worker using the connection
          form below.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function SupportPanel({ api }: { api: Api }) {
  const [preview, setPreview] = useState<{
    data: object;
    preview_hash: string;
  } | null>(null);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
      <summary>Privacy and support report</summary>
      <p>
        Project files stay on your computer. AI sharing requires a separate
        approval for each request. No diagnostic reports are uploaded
        automatically.
      </p>
      <p>
        The support report contains application/platform versions, check
        outcomes and run counts. It excludes geometry, inputs, prompts, account
        details, credentials and logs.
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void perform(async () => {
            setPreview(await api("/support/preview"));
            setApproved(false);
          })
        }
      >
        Preview support report
      </button>
      {preview && (
        <>
          <pre className="assistant-context">
            {JSON.stringify(preview.data, null, 2)}
          </pre>
          <label className="confirmation">
            <input
              type="checkbox"
              checked={approved}
              onChange={(e) => setApproved(e.target.checked)}
            />
            I reviewed these diagnostics and want to save a support report
          </label>
          <button
            disabled={busy || !approved}
            onClick={() =>
              void perform(async () => {
                const data = await api("/support/export", {
                  method: "POST",
                  body: JSON.stringify({ preview_hash: preview.preview_hash }),
                });
                const text = JSON.stringify(data, null, 2);
                if ("__TAURI_INTERNALS__" in window) {
                  const { save } = await import("@tauri-apps/plugin-dialog");
                  const { writeFile } = await import("@tauri-apps/plugin-fs");
                  const path = await save({
                    defaultPath: "venturi-support.json",
                    filters: [{ name: "JSON", extensions: ["json"] }],
                  });
                  if (path)
                    await writeFile(path, new TextEncoder().encode(text));
                } else {
                  const url = URL.createObjectURL(
                    new Blob([text], { type: "application/json" }),
                  );
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = "venturi-support.json";
                  link.click();
                  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                }
              })
            }
          >
            Save reviewed support report
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
