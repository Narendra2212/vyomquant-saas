import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import * as Sentry from "@sentry/react";
import App from "./App";
import { ThemeProvider } from "./design/theme";
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

// THEME-01: `ThemeProvider` is OUTSIDE `BrowserRouter` on purpose. The theme is
// a device preference, not route state — nothing about it should re-resolve on
// navigation, and a provider inside the router would remount with it. It does
// not apply the theme at mount either: `public/theme-boot.js` has already done
// that synchronously before the first paint, and this provider initialises
// itself FROM the document so the two cannot disagree.
ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
);
