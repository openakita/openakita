import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";
import { App } from "./App";

function hideBoot(remove = true) {
  const el = document.getElementById("boot");
  if (!el) return;
  if (remove) el.remove();
  else (el as HTMLElement).style.display = "none";
}

function wireBootButtons() {
  document.getElementById("bootClose")?.addEventListener("click", () => hideBoot(true));
  document.getElementById("bootReload")?.addEventListener("click", () => location.reload());
}

wireBootButtons();
window.addEventListener("openakita_app_ready", () => hideBoot(true));
// Failsafe: if something went wrong, don't leave it forever.
setTimeout(() => hideBoot(true), 20000);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// In case App mounts but doesn't emit.
requestAnimationFrame(() => hideBoot(true));

