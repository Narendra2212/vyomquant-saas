import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import * as Sentry from "@sentry/react";
import App from "./App";
import './App.css';

// Initialize Sentry dynamically for Aerora Observability Certification
const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
const isDummySentry = !sentryDsn ||
  sentryDsn.indexOf("dummy") !== -1 ||
  sentryDsn.indexOf("placeholder") !== -1 ||
  sentryDsn.indexOf("test") !== -1;

if (!isDummySentry) {
  Sentry.init({
    dsn: sentryDsn,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration(),
    ],
    tracesSampleRate: 1.0,
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
  });
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
