/**
 * src/bootstrap.js — FE-02
 *
 * Local-dev detection, lightweight error telemetry (Sentry) and analytics (GA/Clarity)
 * wiring, extracted from three inline `<script>` blocks that used to live directly in
 * `index.html`.
 *
 * WHY THIS IS A SEPARATE MODULE AND NOT INLINE HTML
 * --------------------------------------------------
 * index.html is served as a static file from S3 behind CloudFront (see
 * .github/workflows/06-frontend-deploy.yml) — there is no per-request server that could hand
 * out a CSP nonce the way `backend_app/main.py`'s `SecurityHeadersMiddleware` does for the
 * API. A `<meta http-equiv="Content-Security-Policy">` tag baked into the static HTML is the
 * only CSP this SPA gets, and a meta-tag CSP cannot carry a nonce either. The one way to keep
 * script-src free of `'unsafe-inline'` for a build with no per-request origin is for every
 * script to be a same-origin file: `'self'` already has to be in script-src for the app's own
 * bundle, so a loader shipped as `/src/bootstrap.js` needs no additional CSP grant at all.
 *
 * `%VITE_X%` token substitution (what the old inline scripts read) is an index.html-only Vite
 * feature; a real module reads `import.meta.env.VITE_X` instead, which Vite replaces at build
 * time the same way. The old "is this literally the unsubstituted `%VITE_X%` placeholder"
 * checks are gone because `import.meta.env` never leaves a token unexpanded — an unset env var
 * reads as `undefined`, already handled by `isDummyValue`'s falsy check.
 *
 * Loaded as `<script type="module" src="/src/bootstrap.js">` ahead of
 * `<script type="module" src="/src/main.jsx">` in index.html's `<body>`, so `window.gtag` /
 * `window.clarity` / `window.__VQ_LOCAL__` are defined before the app bundle (module scripts
 * without `async` run in document order, after parsing, before `DOMContentLoaded`).
 */

function isDummyValue(value) {
  if (!value) return true;
  const lower = String(value).toLowerCase();
  return lower.indexOf('dummy') !== -1 || lower.indexOf('placeholder') !== -1 || lower.indexOf('test') !== -1;
}

function detectLocal() {
  const h = window.location.hostname;
  return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h.slice(-6) === '.local';
}

function initSentry(sentryDsn, isLocal) {
  if (!isDummyValue(sentryDsn)) {
    const script = document.createElement('script');
    script.src = 'https://js.sentry-cdn.com/browser.min.js';
    script.crossOrigin = 'anonymous';
    script.onload = function onSentryLoad() {
      if (window.Sentry) {
        window.Sentry.init({
          dsn: sentryDsn,
          tracesSampleRate: 1.0,
          profilesSampleRate: 1.0,
        });
        // eslint-disable-next-line no-console
        console.log('Telemetry: Sentry initialized in browser.');
      }
    };
    document.head.appendChild(script);
  } else if (isLocal) {
    window.onerror = function onLocalError(msg, url, lineNo, columnNo, error) {
      // eslint-disable-next-line no-console
      console.error('[DEV Telemetry] ', msg, url, lineNo, columnNo, error);
      return false;
    };
  }
}

function initAnalytics(gaId, clarityId, isLocal) {
  if (!isDummyValue(gaId)) {
    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://www.googletagmanager.com/gtag/js?id=' + gaId;
    document.head.appendChild(script);

    window.dataLayer = window.dataLayer || [];
    window.gtag = function gtag() { window.dataLayer.push(arguments); };
    window.gtag('js', new Date());
    window.gtag('config', gaId);
  } else {
    window.gtag = function gtagStub() {
      if (isLocal) {
        // eslint-disable-next-line no-console
        console.log('[DEV] GA Event:', arguments);
      }
    };
  }

  if (!isDummyValue(clarityId)) {
    (function loadClarity(c, l, a, r, i) {
      c[a] = c[a] || function clarityQueue() { (c[a].q = c[a].q || []).push(arguments); };
      const t = l.createElement(r);
      t.async = 1;
      t.src = 'https://www.clarity.ms/tag/' + i;
      const y = l.getElementsByTagName(r)[0];
      y.parentNode.insertBefore(t, y);
    }(window, document, 'clarity', 'script', clarityId));
  } else {
    window.clarity = function clarityStub() {
      if (isLocal) {
        // eslint-disable-next-line no-console
        console.log('[DEV] Clarity Event:', arguments);
      }
    };
  }
}

export function runBootstrap(env) {
  const vars = env || import.meta.env;
  const isLocal = detectLocal();
  window.__VQ_LOCAL__ = isLocal;
  initSentry(vars.VITE_SENTRY_DSN, isLocal);
  initAnalytics(vars.VITE_GA_TRACKING_ID, vars.VITE_CLARITY_PROJECT_ID, isLocal);
  return isLocal;
}

export { isDummyValue, detectLocal };

runBootstrap();
