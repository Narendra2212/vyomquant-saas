/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Is this the packaged desktop application, or a browser?
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHY THE DISTINCTION IS NEEDED
 * ----------------------------
 * The web app and the desktop app are ONE bundle, built once. They should not have the
 * same front door. A visitor who types the domain has to be told what the product is, so
 * `/` serves the marketing landing page. Someone who has already found, downloaded and
 * installed the application has been told; opening it to a page that pitches the product
 * and offers to let them download it is a dead end with a "Download for Windows" button in
 * the middle of it. The desktop app opens at sign-in instead.
 *
 * HOW IT IS DETECTED, AND WHY THREE SIGNALS
 * ----------------------------------------
 * `__TAURI_INTERNALS__` is the primary and sufficient signal: Tauri v2 injects it into
 * every window because it is the IPC bridge the framework itself needs, so it is present
 * whether or not the app opts into any JS API. `__TAURI__` is checked too but is NOT
 * relied upon -- it only exists when `app.withGlobalTauri` is true, and
 * `src-tauri/tauri.conf.json` does not set it.
 *
 * The two location checks are a belt-and-braces fallback in case a future Tauri version
 * renames the internals global, which has happened once already between v1 and v2. A
 * packaged Tauri app is served from its own custom protocol rather than over the network:
 * `tauri://localhost` on macOS and Linux, and `http://tauri.localhost` on Windows. Neither
 * is reachable as a real web origin, so neither can produce a false positive in a browser.
 *
 * WHAT IT IS NOT FOR
 * -----------------
 * Not for choosing an API base. `config.js` decides that from whether the page was served
 * over https, which is a question about the TRANSPORT and has a different answer in
 * `npm run dev` (also not https, also not desktop). Two different questions, deliberately
 * not collapsed into one flag.
 */

/**
 * True when running inside the packaged desktop application.
 *
 * Safe to call before mount, in node, and under jsdom: every access is guarded, so a
 * context with no `window` reports `false` -- a browser, which is the right default for
 * anything that is not demonstrably the desktop shell.
 *
 * @returns {boolean}
 */
export const isDesktopApp = () => {
  if (typeof window === 'undefined') return false;

  // Tauri v2's IPC bridge. Present in every packaged window.
  if (window.__TAURI_INTERNALS__) return true;
  // Only set with `withGlobalTauri`; kept so enabling that option cannot break detection.
  if (window.__TAURI__) return true;

  const location = window.location;
  if (!location) return false;
  // macOS / Linux custom protocol.
  if (location.protocol === 'tauri:') return true;
  // Windows custom protocol, which is http-shaped but on a hostname no DNS resolves.
  if (location.hostname === 'tauri.localhost') return true;

  return false;
};

export default isDesktopApp;
