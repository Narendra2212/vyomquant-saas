/**
 * tests/unit/apiBaseOrigin.test.js
 *
 * THE DEFECT, AND WHY IT WAS INVISIBLE FROM THE SERVER
 * ---------------------------------------------------
 * `CONFIG.apiBaseUrl` and `CONFIG.wsBaseUrl` were env-FIRST. `VITE_API_URL` / `VITE_WS_URL`
 * held the raw CloudFront distribution domain, and because Vite inlines env vars at build
 * time the shipped bundle carried them as unconditional constants -- the origin fallback was
 * dead-code-eliminated:
 *
 *     Af=()=>"https://d7d88qs4jmch.cloudfront.net"
 *     Of=()=>"wss://d7d88qs4jmch.cloudfront.net"
 *
 * Meanwhile `index.html`'s CSP permits `connect-src 'self' https://*.vyomquant.in
 * wss://*.vyomquant.in https://*.supabase.co ...` and does NOT list that distribution host,
 * with no `https:` wildcard. So the browser refused every API call and the websocket before
 * it left the page. axios surfaces that as "Network Error" with no response, which the UI
 * renders as "No connection to VyomQuant" on every panel.
 *
 * Measured over five days of production logs: same-origin relative calls (`/api/config`,
 * `/api/release.json`) arrived, while NOT ONE cross-origin API call or websocket handshake
 * appeared at all. Sign-in kept working throughout, because `https://*.supabase.co` IS in
 * the policy -- the app was authenticated and totally disconnected at the same time.
 *
 * WHAT IS PINNED HERE
 *   1. an https page resolves BOTH bases to its OWN origin, for every host the
 *      distribution serves -- so `'self'` covers them and no CSP entry is needed;
 *   2. a non-https context (the Tauri desktop build, `npm run dev`) still falls through to
 *      the env var, which is the only reason that var exists;
 *   3. `index.html`'s CSP really does allow `'self'` for connect-src, which is the property
 *      the whole fix leans on -- and still does NOT name a raw cloudfront host, so a future
 *      edit cannot quietly reintroduce the cross-origin arrangement.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Replace `window.location` before importing config.js, which reads it at module load. */
const withLocation = async (href) => {
  vi.resetModules();
  const url = new URL(href);
  Object.defineProperty(window, 'location', {
    value: {
      href: url.href,
      origin: url.origin,
      protocol: url.protocol,
      host: url.host,
      hostname: url.hostname,
      port: url.port,
      pathname: url.pathname,
    },
    writable: true,
    configurable: true,
  });
  return import('../../src/config.js');
};

let originalLocation;

beforeEach(() => {
  originalLocation = window.location;
});

afterEach(() => {
  Object.defineProperty(window, 'location', {
    value: originalLocation,
    writable: true,
    configurable: true,
  });
  vi.resetModules();
});

// ---------------------------------------------------------------------------
// 1. An https page addresses itself
// ---------------------------------------------------------------------------

describe('a deployed https page calls its own origin', () => {
  it.each([
    ['https://app.vyomquant.in/app/dashboard', 'https://app.vyomquant.in', 'wss://app.vyomquant.in'],
    ['https://www.vyomquant.in/', 'https://www.vyomquant.in', 'wss://www.vyomquant.in'],
    ['https://vyomquant.in/', 'https://vyomquant.in', 'wss://vyomquant.in'],
  ])('%s -> api %s, ws %s', async (href, expectedApi, expectedWs) => {
    const { CONFIG } = await withLocation(href);

    expect(CONFIG.apiBaseUrl).toBe(expectedApi);
    expect(CONFIG.wsBaseUrl).toBe(expectedWs);
  });

  it('never addresses a raw cloudfront distribution domain from an https page', async () => {
    // The specific host the CSP blocked. Pinned by substring rather than equality so any
    // `*.cloudfront.net` value reintroduced into the secret is caught too.
    const { CONFIG } = await withLocation('https://app.vyomquant.in/app/dashboard');

    expect(CONFIG.apiBaseUrl).not.toContain('cloudfront.net');
    expect(CONFIG.wsBaseUrl).not.toContain('cloudfront.net');
  });

  it('uses wss, never ws, on an https page', async () => {
    // A `ws://` socket from an https page is mixed content and is blocked outright.
    const { CONFIG } = await withLocation('https://app.vyomquant.in/');
    expect(CONFIG.wsBaseUrl.startsWith('wss://')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. The contexts that genuinely need the env var still reach it
// ---------------------------------------------------------------------------

describe('a context with no deployed https origin falls through to the env var', () => {
  it('the vite dev server does not address itself', async () => {
    // `npm run dev` serves on http://localhost:1420 while the API listens elsewhere, and
    // vite.config.js declares no proxy, so same-origin would be wrong here.
    const { CONFIG } = await withLocation('http://localhost:1420/app/dashboard');

    // The property that matters: the dev server must NOT become its own API base. The exact
    // value is deliberately not pinned — it is whatever `VITE_API_URL` holds in the running
    // environment, and asserting a literal here would make this test depend on whether CI
    // happens to export that variable for the test step.
    expect(CONFIG.apiBaseUrl).not.toBe('http://localhost:1420');
    expect(CONFIG.apiBaseUrl).not.toBe('http://localhost:1420/');
  });

  it('a plain http host does not silently become the api base', async () => {
    const { CONFIG } = await withLocation('http://10.0.0.5:8080/');
    expect(CONFIG.apiBaseUrl).not.toBe('http://10.0.0.5:8080');
  });
});

// ---------------------------------------------------------------------------
// 2b. The desktop app, which has no served origin to read
// ---------------------------------------------------------------------------

describe('the packaged desktop app', () => {
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
    delete window.__TAURI__;
  });

  it.each([
    ['http://tauri.localhost/', 'the Windows custom protocol host'],
    ['tauri://localhost/', 'the macOS/Linux custom protocol'],
  ])('addresses its declared origin on %s (%s)', async (href) => {
    const { CONFIG } = await withLocation(href);
    const { DESKTOP_API_ORIGIN, DESKTOP_WS_ORIGIN } = await import('../../src/runtime/desktop.js');

    expect(CONFIG.apiBaseUrl).toBe(DESKTOP_API_ORIGIN);
    expect(CONFIG.wsBaseUrl).toBe(DESKTOP_WS_ORIGIN);
  });

  it('is detected by the injected IPC global even on an http-looking origin', async () => {
    vi.resetModules();
    Object.defineProperty(window, 'location', {
      value: { href: 'http://localhost/', origin: 'http://localhost', protocol: 'http:', host: 'localhost', hostname: 'localhost' },
      writable: true,
      configurable: true,
    });
    window.__TAURI_INTERNALS__ = { invoke: () => {} };

    const { CONFIG } = await import('../../src/config.js');
    const { DESKTOP_API_ORIGIN } = await import('../../src/runtime/desktop.js');

    expect(CONFIG.apiBaseUrl).toBe(DESKTOP_API_ORIGIN);
  });

  it('never addresses the CDN distribution host', async () => {
    const { CONFIG } = await withLocation('tauri://localhost/');
    expect(CONFIG.apiBaseUrl).not.toContain('cloudfront.net');
    expect(CONFIG.wsBaseUrl).not.toContain('cloudfront.net');
  });
});

describe("src-tauri's own CSP permits the origin the desktop app is told to use", () => {
  // The whole reason the packaged app could not reach its API: the base it was given was
  // not in its own `connect-src`. This ties the two together so they cannot drift again.
  const connectSrc = (() => {
    const conf = JSON.parse(readFileSync(join(ROOT, 'src-tauri', 'tauri.conf.json'), 'utf8'));
    const csp = conf?.app?.security?.csp;
    if (!csp) throw new Error('tauri.conf.json declares no app.security.csp');
    const directive = csp
      .split(';')
      .map((d) => d.trim())
      .find((d) => d.toLowerCase().startsWith('connect-src'));
    if (!directive) throw new Error('the desktop CSP declares no connect-src');
    return directive.split(/\s+/).slice(1);
  })();

  /** Does any source in the policy admit this absolute origin? */
  const permits = (origin) => {
    const url = new URL(origin);
    return connectSrc.some((src) => {
      if (src === '*') return true;
      if (src === `${url.protocol}`) return true; // a bare scheme source, e.g. `wss:`
      if (src === origin) return true;
      const m = /^([a-z]+:)\/\/\*\.(.+)$/.exec(src);
      if (m) return m[1] === url.protocol && url.hostname.endsWith('.' + m[2]);
      return false;
    });
  };

  it('permits the declared desktop API origin', async () => {
    const { DESKTOP_API_ORIGIN } = await import('../../src/runtime/desktop.js');
    expect(
      permits(DESKTOP_API_ORIGIN),
      `${DESKTOP_API_ORIGIN} is not admitted by the desktop connect-src: ${connectSrc.join(' ')}`,
    ).toBe(true);
  });

  it('permits the declared desktop socket origin', async () => {
    const { DESKTOP_WS_ORIGIN } = await import('../../src/runtime/desktop.js');
    expect(
      permits(DESKTOP_WS_ORIGIN),
      `${DESKTOP_WS_ORIGIN} is not admitted by the desktop connect-src: ${connectSrc.join(' ')}`,
    ).toBe(true);
  });

  it('does NOT permit the CDN distribution host, which is why the old base failed', () => {
    // Non-vacuity: proves `permits` can say no, and records the actual defect.
    expect(permits('https://d7d88qs4jmch.cloudfront.net')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3. The CSP property the fix depends on
// ---------------------------------------------------------------------------

describe("index.html's CSP", () => {
  // Plain functions that THROW, not `expect` at collection time: Vitest does not allow an
  // assertion outside a test body, and a describe block runs during collection.
  const readConnectSrc = () => {
    const html = readFileSync(join(ROOT, 'index.html'), 'utf8');
    const tag = /<meta[^>]+Content-Security-Policy[^>]*>/i.exec(html);
    if (!tag) throw new Error('index.html has no Content-Security-Policy meta tag');
    const content = /content\s*=\s*"([^"]+)"/i.exec(tag[0])
      || /content\s*=\s*'([^']+)'/i.exec(tag[0]);
    if (!content) throw new Error('CSP meta tag has no content attribute');
    const directive = content[1]
      .split(';')
      .map((d) => d.trim())
      .find((d) => d.toLowerCase().startsWith('connect-src'));
    if (!directive) throw new Error('CSP declares no connect-src directive');
    return directive.split(/\s+/).slice(1);
  };

  const connectSrc = readConnectSrc();

  it("allows 'self', which is what makes a same-origin api call legal", () => {
    expect(connectSrc).toContain("'self'");
  });

  it('does not name a raw cloudfront host', () => {
    // If someone widens the policy instead of fixing the base URL, this fails. The point of
    // the fix is that no distribution domain needs to be blessed at all.
    for (const src of connectSrc) {
      expect(src).not.toContain('cloudfront.net');
    }
  });

  it('still allows supabase, which is how sign-in kept working while everything else failed', () => {
    expect(connectSrc.some((s) => s.includes('supabase.co'))).toBe(true);
  });

  it('has no blanket https: or wildcard source', () => {
    // Non-vacuity for the two assertions above: a policy containing `https:` or `*` would
    // make them pass while permitting everything.
    expect(connectSrc).not.toContain('https:');
    expect(connectSrc).not.toContain('*');
  });
});
