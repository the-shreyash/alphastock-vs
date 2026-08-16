import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { installTelemetry } from "@/services/telemetry";

/*
 * PH3.7. Installed before the first render, on purpose: an exception thrown
 * while the tree is mounting is the one most worth hearing about, and handlers
 * registered afterwards would miss it. `installTelemetry` covers what React
 * cannot see — unhandled promise rejections and genuinely uncaught errors
 * (event handlers, timers). Render errors are caught by the ErrorBoundary in
 * App.js. See services/telemetry.js for what is and is not collected.
 */
installTelemetry();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
