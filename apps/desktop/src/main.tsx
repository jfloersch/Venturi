import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

if ("__TAURI_INTERNALS__" in window) {
  const originalError = console.error.bind(console);
  console.error = (...args: unknown[]) => {
    originalError(...args);
    import("@tauri-apps/api/core")
      .then(({ invoke }) =>
        invoke("client_diagnostic", { message: args.map(String).join(" ") }),
      )
      .catch(() => {});
  };
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
