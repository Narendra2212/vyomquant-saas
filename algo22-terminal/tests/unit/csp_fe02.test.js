/**
 * FE-02 -- the web app is served without a Content-Security-Policy.
 *
 * THE DEFECT
 *   index.html is served as a static file from S3 behind CloudFront (no origin that could
 *   set per-request response headers, unlike the API's SecurityHeadersMiddleware), and it
 *   carried no CSP of any kind -- not even a <meta> one. Its three inline <script> blocks
 *   (local-dev detection, Sentry, GA/Clarity) would also have forced 'unsafe-inline' into
 *   script-src the moment a CSP was added, defeating most of what a CSP is for.
 *
 * THE FIX
 *   1. index.html now carries a <meta http-equiv="Content-Security-Policy"> with a script-src
 *      that is 'self' plus only the CDN origins this build actually loads scripts from -- no
 *      'unsafe-inline', no 'unsafe-eval'.
 *   2. The three inline scripts moved into src/bootstrap.js, a same-origin module already
 *      covered by 'self', loaded via <script type="module" src="/src/bootstrap.js"> ahead of
 *      main.jsx.
 *
 * WHAT THIS FILE PROVES
 *   1. index.html's CSP meta tag exists, scopes script-src to 'self' + named origins only (no
 *      unsafe-inline/unsafe-eval), and every other directive it declares is non-empty.
 *   2. index.html contains no inline, executable <script> block any more -- every <script> tag
 *      either names a src (same-origin or an explicitly CSP-allowed origin) or is a
 *      non-executable type (application/ld+json), which browsers do not run as script and
 *      CSP's script-src does not gate.
 *   3. src/bootstrap.js's isDummyValue/detectLocal/runBootstrap reproduce the exact behaviour
 *      the old inline scripts had: a dummy/placeholder/unset id gets a harmless local-only
 *      stub, a real-looking id gets the real third-party script injected.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML_PATH = path.resolve(__dirname, '../../index.html');
const INDEX_HTML = fs.readFileSync(INDEX_HTML_PATH, 'utf-8');

function parseCspDirectives(cspContent) {
  const directives = {};
  for (const part of cspContent.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const [name, ...rest] = trimmed.split(/\s+/);
    directives[name] = rest;
  }
  return directives;
}

describe('FE-02: index.html declares a real CSP with no inline-script escape hatch', () => {
  it('carries a Content-Security-Policy meta tag', () => {
    expect(INDEX_HTML).toMatch(/<meta\s+http-equiv="Content-Security-Policy"/);
  });

  it('script-src is self + named origins only -- no unsafe-inline, no unsafe-eval', () => {
    const match = INDEX_HTML.match(
      /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"/s,
    );
    expect(match).not.toBeNull();
    const directives = parseCspDirectives(match[1]);

    expect(directives['script-src']).toBeDefined();
    expect(directives['script-src']).toContain("'self'");
    expect(directives['script-src']).not.toContain("'unsafe-inline'");
    expect(directives['script-src']).not.toContain("'unsafe-eval'");
    for (const token of directives['script-src']) {
      if (token === "'self'") continue;
      expect(token).toMatch(/^https:\/\//);
    }

    // every directive this policy declares actually restricts something (no empty directive
    // left over from an edit)
    for (const [name, values] of Object.entries(directives)) {
      expect(values.length, `${name} has no values`).toBeGreaterThan(0);
    }
  });

  it('has no inline, executable <script> block left (every tag has a src, or is non-executable)', () => {
    const scriptTagRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
    let match;
    let sawAny = false;
    while ((match = scriptTagRe.exec(INDEX_HTML)) !== null) {
      sawAny = true;
      const [, attrs, body] = match;
      const hasSrc = /\bsrc=/.test(attrs);
      const typeMatch = attrs.match(/\btype="([^"]*)"/);
      const type = typeMatch ? typeMatch[1] : null;
      const isNonExecutableType = type === 'application/ld+json';

      if (!hasSrc && !isNonExecutableType) {
        throw new Error(
          `inline executable <script> block found in index.html (type=${type}): ` +
          `${body.slice(0, 80)}...`,
        );
      }
      if (hasSrc) {
        // a src-based script must be same-origin (module loaders) -- nothing here should
        // point at a third-party host, which would need its own CSP grant and defeats the
        // point of keeping script-src narrow
        const srcMatch = attrs.match(/\bsrc="([^"]*)"/);
        expect(srcMatch[1].startsWith('/')).toBe(true);
      }
    }
    expect(sawAny).toBe(true);
  });

  it('loads src/bootstrap.js ahead of src/main.jsx in document order', () => {
    const bootstrapIdx = INDEX_HTML.indexOf('/src/bootstrap.js');
    const mainIdx = INDEX_HTML.indexOf('/src/main.jsx');
    expect(bootstrapIdx).toBeGreaterThan(-1);
    expect(mainIdx).toBeGreaterThan(-1);
    expect(bootstrapIdx).toBeLessThan(mainIdx);
  });
});

describe('FE-02: src/bootstrap.js reproduces the old inline scripts behaviour', () => {
  let isDummyValue;
  let detectLocal;
  let runBootstrap;

  beforeEach(async () => {
    vi.resetModules();
    document.head.innerHTML = '';
    // Clarity's loader snippet inserts its tag next to the page's own first <script> element
    // (document.getElementsByTagName('script')[0]) -- in the real page that is always this
    // very module script tag (or main.jsx's), never absent. Reproduced here so the snippet
    // has the same thing to insert next to that it always has in production.
    document.body.innerHTML = '<script id="host-script"></script>';
    delete window.gtag;
    delete window.clarity;
    delete window.dataLayer;
    delete window.onerror;
    delete window.__VQ_LOCAL__;
    const mod = await import('../../src/bootstrap.js');
    isDummyValue = mod.isDummyValue;
    detectLocal = mod.detectLocal;
    runBootstrap = mod.runBootstrap;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('treats unset, empty, and dummy/placeholder/test values as dummy', () => {
    expect(isDummyValue(undefined)).toBe(true);
    expect(isDummyValue('')).toBe(true);
    expect(isDummyValue('dummy')).toBe(true);
    expect(isDummyValue('DUMMY-sentry-dsn')).toBe(true);
    expect(isDummyValue('placeholder-id')).toBe(true);
    expect(isDummyValue('test-ga-id')).toBe(true);
  });

  it('treats a real-looking id as not dummy', () => {
    expect(isDummyValue('G-ABC123XYZ')).toBe(false);
    expect(isDummyValue('https://abc123@o0.ingest.sentry.io/1')).toBe(false);
  });

  it('detectLocal recognises localhost/127.0.0.1/.local, not a real host', () => {
    const hostnameSpy = vi.spyOn(window, 'location', 'get');
    hostnameSpy.mockReturnValue({ hostname: 'localhost' });
    expect(detectLocal()).toBe(true);
    hostnameSpy.mockReturnValue({ hostname: '127.0.0.1' });
    expect(detectLocal()).toBe(true);
    hostnameSpy.mockReturnValue({ hostname: 'dev-box.local' });
    expect(detectLocal()).toBe(true);
    hostnameSpy.mockReturnValue({ hostname: 'app.vyomquant.in' });
    expect(detectLocal()).toBe(false);
    hostnameSpy.mockRestore();
  });

  it('with dummy GA/Clarity ids: no third-party script injected, stubs installed instead', () => {
    runBootstrap({ VITE_SENTRY_DSN: 'dummy', VITE_GA_TRACKING_ID: 'dummy', VITE_CLARITY_PROJECT_ID: 'dummy' });

    expect(document.querySelector('script[src*="googletagmanager.com"]')).toBeNull();
    expect(document.querySelector('script[src*="clarity.ms"]')).toBeNull();
    expect(document.querySelector('script[src*="sentry-cdn.com"]')).toBeNull();
    expect(typeof window.gtag).toBe('function');
    expect(typeof window.clarity).toBe('function');
  });

  it('with a real-looking GA id: the gtag loader script is injected with that id', () => {
    runBootstrap({ VITE_SENTRY_DSN: 'dummy', VITE_GA_TRACKING_ID: 'G-REAL123', VITE_CLARITY_PROJECT_ID: 'dummy' });

    const gaScript = document.querySelector('script[src*="googletagmanager.com"]');
    expect(gaScript).not.toBeNull();
    expect(gaScript.src).toContain('G-REAL123');
    expect(Array.isArray(window.dataLayer)).toBe(true);
  });

  it('with a real-looking Clarity id: the clarity loader script is injected with that id', () => {
    runBootstrap({ VITE_SENTRY_DSN: 'dummy', VITE_GA_TRACKING_ID: 'dummy', VITE_CLARITY_PROJECT_ID: 'real-clarity-id' });

    const clarityScript = document.querySelector('script[src*="clarity.ms/tag/real-clarity-id"]');
    expect(clarityScript).not.toBeNull();
  });

  it('with a real-looking Sentry DSN: the Sentry loader script is injected', () => {
    runBootstrap({
      VITE_SENTRY_DSN: 'https://abc123@o0.ingest.sentry.io/1',
      VITE_GA_TRACKING_ID: 'dummy',
      VITE_CLARITY_PROJECT_ID: 'dummy',
    });

    expect(document.querySelector('script[src="https://js.sentry-cdn.com/browser.min.js"]')).not.toBeNull();
  });
});
