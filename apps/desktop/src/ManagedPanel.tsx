import { useEffect, useState } from "react";
import type { Api } from "./types";

type Catalog = {
  configured: boolean;
  connected: boolean;
  url?: string;
  tariff_hash?: string;
  tariff?: {
    model: string;
    version: string;
    input_usd_per_million: string;
    output_usd_per_million: string;
    markup: string;
  };
  top_up_cents?: number[];
};
type Wallet = {
  available_microusd: number;
  requests: {
    id: string;
    study: string;
    state: string;
    cost: number;
    reserved: number;
  }[];
  entries: {
    kind: string;
    amount: number;
    reference: string;
    created: number;
  }[];
  limits: { study: string; maximum: number }[];
};
const dollars = (value: number) => `$${(value / 1e6).toFixed(6)}`;

export function ManagedPanel({
  api,
  onChanged,
}: {
  api: Api;
  onChanged: () => Promise<void>;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [recovery, setRecovery] = useState("");
  const [newRecovery, setNewRecovery] = useState("");
  const [mode, setMode] = useState("login");
  const [budget, setBudget] = useState("5");
  const [studyBudget, setStudyBudget] = useState("1");
  const [remember, setRemember] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checkoutId, setCheckoutId] = useState(() => crypto.randomUUID());
  const [checkout, setCheckout] = useState<{
    url: string | null;
    status: string;
    order_id: string;
  } | null>(null);
  const post = (path: string, body: unknown = {}) =>
    api(path, { method: "POST", body: JSON.stringify(body) });
  const refresh = async () => {
    const next = await api("/managed");
    setCatalog(next);
    if (next.connected) setWallet(await api("/managed/wallet"));
    else setWallet(null);
  };
  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
    const changed = () => void refresh().catch((e) => setError(String(e)));
    window.addEventListener("venturi-ai-changed", changed);
    return () => window.removeEventListener("venturi-ai-changed", changed);
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
  async function openCheckout(url: string) {
    if ("__TAURI_INTERNALS__" in window) {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("open_checkout", { url });
    } else window.open(url, "_blank", "noopener,noreferrer");
  }
  return (
    <details className="assistance-panel managed-panel">
      <summary>Venturi Managed · prepaid AI</summary>
      <p>
        Simulation execution: this computer. Geometry and results: stored
        locally. When you approve sharing, selected context goes through Venturi
        Managed to OpenAI.
      </p>
      {catalog && !catalog.configured && (
        <p>
          Managed is not configured for this installation. You can use your own
          OpenAI account or work without AI.
        </p>
      )}
      {catalog?.configured && (
        <>
          <p>Service: {catalog.url}</p>
          {catalog.tariff && (
            <p>
              Model: <strong>{catalog.tariff.model}</strong>. Published tariff{" "}
              {catalog.tariff.version}: ${catalog.tariff.input_usd_per_million}{" "}
              input / ${catalog.tariff.output_usd_per_million} output per
              million tokens, multiplied by {catalog.tariff.markup}. All input
              tokens use the published input rate. No simulation compute charge.
            </p>
          )}
          <p>
            AI responses may fail and still incur provider usage. We reserve a
            maximum before each call and return the unused amount. Uncertain
            charges stay reserved until reconciled. Prepaid balance does not
            reset monthly; top-ups require your action.
          </p>
          <p>
            Prompts are forwarded for the approved request. Returned proposals
            may be cached by the gateway for transport recovery for up to 24
            hours before cleanup. Billing and security records are retained.
            Provider retention rules also apply. Diagnostic sharing is separate
            and optional.
          </p>
          <label>
            Account action{" "}
            <select
              value={mode}
              onChange={(e) => {
                setMode(e.target.value);
                setAccepted(false);
              }}
            >
              <option value="login">Sign in</option>
              <option value="register">Create account</option>
              <option value="recover">Recover account</option>
            </select>
          </label>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void perform(async () => {
                if (mode === "register") {
                  const result = await post("/managed/register", {
                    username,
                    password,
                    accept_terms: accepted,
                  });
                  setNewRecovery(result.recovery_code);
                  setMode("login");
                  setPassword("");
                } else if (mode === "recover") {
                  const result = await post("/managed/recover", {
                    username,
                    password,
                    recovery_code: recovery,
                  });
                  setNewRecovery(result.recovery_code);
                  setMode("login");
                  setRecovery("");
                  setPassword("");
                } else {
                  await post("/managed/connect", {
                    username,
                    password,
                    remember,
                    budget_usd: Number(budget),
                    study_budget_usd: Number(studyBudget),
                    tariff_hash: catalog.tariff_hash,
                  });
                  setPassword("");
                  await refresh();
                  await onChanged();
                }
              });
            }}
          >
            <div className="flow-fields">
              <label>
                Managed account name
                <input
                  required
                  minLength={3}
                  maxLength={60}
                  pattern="[a-zA-Z0-9_-]+"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                {mode === "recover"
                  ? "New Managed password"
                  : "Managed password"}
                <input
                  required
                  type="password"
                  minLength={12}
                  maxLength={256}
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              {mode === "recover" && (
                <label>
                  Recovery code
                  <input
                    required
                    type="password"
                    autoComplete="off"
                    value={recovery}
                    onChange={(e) => setRecovery(e.target.value)}
                  />
                </label>
              )}
              {mode === "login" && (
                <>
                  <label>
                    Workspace AI maximum (USD)
                    <input
                      type="number"
                      required
                      min="0.01"
                      max="1000"
                      step="0.01"
                      value={budget}
                      onChange={(e) => setBudget(e.target.value)}
                    />
                  </label>
                  <label>
                    Maximum per study (USD)
                    <input
                      type="number"
                      required
                      min="0.01"
                      max="1000"
                      step="0.01"
                      value={studyBudget}
                      onChange={(e) => setStudyBudget(e.target.value)}
                    />
                  </label>
                </>
              )}
            </div>
            {mode === "login" && (
              <label className="confirmation">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                Remember session in the OS credential store
              </label>
            )}
            {mode !== "recover" && (
              <label className="confirmation">
                <input
                  type="checkbox"
                  required
                  checked={accepted}
                  onChange={(e) => setAccepted(e.target.checked)}
                />
                {mode === "login"
                  ? "Use Managed billing at the displayed tariff and limits for future approved AI requests"
                  : "Accept the displayed prepaid usage and privacy terms; save my recovery code"}
              </label>
            )}
            <button disabled={busy || (mode !== "recover" && !accepted)}>
              {mode === "register"
                ? "Create Managed account"
                : mode === "recover"
                  ? "Reset password"
                  : "Use Venturi Managed"}
            </button>
          </form>
          {newRecovery && (
            <div className="notice">
              <p>
                Save this recovery code in your password manager. It is shown
                once and replaces any previous code.
              </p>
              <code>{newRecovery}</code>
              <button onClick={() => setNewRecovery("")}>
                I saved my recovery code
              </button>
            </div>
          )}
          {wallet && (
            <>
              <h3>
                Prepaid wallet · {dollars(wallet.available_microusd)} available
              </h3>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void perform(refresh)}
              >
                Refresh wallet
              </button>
              {catalog.top_up_cents?.map((cents) => (
                <button
                  key={cents}
                  disabled={busy || !!checkout}
                  onClick={() =>
                    void perform(async () => {
                      setCheckout(
                        await post("/managed/checkout", {
                          request_id: checkoutId,
                          amount_cents: cents,
                        }),
                      );
                    })
                  }
                >
                  Add ${(cents / 100).toFixed(2)}
                </button>
              ))}
              {checkout && (
                <p>
                  Order {checkout.order_id}: {checkout.status}.{" "}
                  {checkout.url ? (
                    <button
                      onClick={() =>
                        void perform(() => openCheckout(checkout.url!))
                      }
                    >
                      Open secure checkout
                    </button>
                  ) : (
                    "Checkout is unresolved. Contact support with this order ID before trying again."
                  )}
                </p>
              )}
              {checkout?.url && (
                <button
                  className="secondary"
                  onClick={() => {
                    setCheckout(null);
                    setCheckoutId(crypto.randomUUID());
                  }}
                >
                  Start a separate top-up
                </button>
              )}
              <details>
                <summary>Usage and reservations</summary>
                {wallet.requests.length === 0 && (
                  <p>No Managed requests yet.</p>
                )}
                {wallet.requests.map((r) => (
                  <p key={r.id}>
                    {r.id} · {r.state} · {dollars(r.cost)}{" "}
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await post(`/managed/requests/${r.id}/refresh`);
                          await refresh();
                          await onChanged();
                        })
                      }
                    >
                      Refresh local accounting
                    </button>
                  </p>
                ))}
              </details>
              <details>
                <summary>Study limits</summary>
                {wallet.limits.map((limit) => (
                  <form
                    key={limit.study}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const data = new FormData(e.currentTarget);
                      void perform(async () => {
                        await post("/managed/limits", {
                          study: limit.study,
                          maximum_microusd: Math.round(
                            Number(data.get("maximum")) * 1e6,
                          ),
                        });
                        await refresh();
                      });
                    }}
                  >
                    <label>
                      Study {limit.study.slice(0, 12)} maximum (USD)
                      <input
                        name="maximum"
                        type="number"
                        min="0"
                        max="1000"
                        step="0.01"
                        defaultValue={limit.maximum / 1e6}
                      />
                    </label>
                    <button disabled={busy}>Approve study limit</button>
                  </form>
                ))}
              </details>
              <details>
                <summary>Payment history</summary>
                {wallet.entries.map((entry, i) => (
                  <p key={i}>
                    {entry.kind.replaceAll("_", " ")} · {dollars(entry.amount)}{" "}
                    · {entry.reference}
                  </p>
                ))}
              </details>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await post("/managed/privacy/delete-outputs");
                    setError(
                      "Cached gateway outputs deleted. Accounting records and your local projects are retained.",
                    );
                  })
                }
              >
                Delete cached gateway outputs
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await post("/assistant/disconnect");
                    setWallet(null);
                    await refresh();
                    await onChanged();
                  })
                }
              >
                Disconnect Managed
              </button>
            </>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
