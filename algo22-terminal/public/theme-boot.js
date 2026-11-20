/**
 * public/theme-boot.js — THEME-01. Applies the stored theme BEFORE the first paint.
 *
 * WHY THIS FILE EXISTS AT ALL, AND WHY IT IS NOT IN src/
 * ---------------------------------------------------------------------------
 * Two constraints meet here and only this shape satisfies both.
 *
 * 1. NO INLINE SCRIPT. index.html's CSP carries no 'unsafe-inline' in
 *    script-src, deliberately: the app is static HTML on S3 behind CloudFront,
 *    so there is no per-request origin to mint a nonce, and a meta-tag CSP
 *    cannot carry one either. src/bootstrap.js's header records the resolution —
 *    every script ships as a same-origin FILE, which 'self' already covers. The
 *    usual one-line inline theme snippet is therefore not available.
 *
 * 2. IT MUST RUN BEFORE THE FIRST PAINT. src/bootstrap.js and src/main.jsx are
 *    `type="module"`, and module scripts are deferred — they execute after the
 *    document is parsed, by which point the browser may already have painted
 *    <body class="bg-surface-canvas">. For a user whose stored theme is light
 *    that paint is the DARK canvas, and the correction one frame later is the
 *    flash the brief asks to prevent. Putting this logic in bootstrap.js would
 *    be tidier and would not work.
 *
 * So: a CLASSIC, synchronous <script src> in <head>. Classic scripts without
 * `defer` block parsing at the point they appear, so this runs before any
 * element — including <body> — exists to be painted. It lives in public/ rather
 * than src/ because Vite copies public/ verbatim to the dist root; a file in
 * src/ would be bundled as a module and lose the synchronous property that is
 * the entire point.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * ---------------------------------------------------------------------------
 * No framework, no imports, no bundler syntax: it must run as-is in a browser
 * before anything else loads. It does not read cookies or call the API — the
 * theme is a device preference, and a network round trip before first paint
 * would reintroduce the flash it exists to remove.
 *
 * The storage key and the resolution order are duplicated in
 * src/design/theme.jsx, which owns the runtime. That duplication is real and is
 * the price of running before the bundle exists; it is pinned by
 * tests/unit/design/themeBoot.test.js so the two cannot drift silently.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'vq.theme';
  var VALID = { dark: 1, light: 1 };

  function stored() {
    try {
      // localStorage access throws in a sandboxed iframe and when a browser is
      // configured to block site data. A theme is not worth a blank page, so a
      // failure here falls through to the system preference.
      var v = window.localStorage.getItem(STORAGE_KEY);
      return VALID[v] ? v : null;
    } catch (e) {
      return null;
    }
  }

  function preferred() {
    try {
      // Consulted ONLY when nothing is stored. After a user has chosen, their
      // choice wins forever — see theme.jsx for why that is not a media query.
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        return 'light';
      }
    } catch (e) {
      /* matchMedia is absent in some embedded webviews; dark is the default. */
    }
    return 'dark';
  }

  var theme = stored() || preferred();

  // Dark is the default and is expressed by the ABSENCE of the attribute, so the
  // @theme block in tokens.css stays the baseline and no attribute is needed for
  // the overwhelmingly common case.
  if (theme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }

  // The browser chrome colour is a hex literal in a <meta> tag, which cannot
  // reference a CSS variable, so it is set here from the one place that knows
  // the theme. These two values are --color-surface-canvas in each theme; they
  // are asserted against tokens.css by the test named above.
  try {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme === 'light' ? '#F4F6F8' : '#080A0E');
    }
  } catch (e) {
    /* Cosmetic only. Never worth failing boot for. */
  }
})();
