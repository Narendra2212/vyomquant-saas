/**
 * tests/unit/desktopStartsAtSignIn.test.jsx
 *
 * THE REQUIREMENT
 * ---------------
 * The downloaded application must not open on the marketing landing page. The web app and
 * the desktop app are ONE bundle built once, and they do not share a front door: a visitor
 * who types the domain has not been told what the product is, so `/` serves the landing
 * page; someone running the installed app has already found it, downloaded it and installed
 * it, so opening to a page that pitches the product and offers four installers is a dead
 * end. The desktop app opens at sign-in.
 *
 * WHAT IS ASSERTED
 *   1. `isDesktopApp()` recognises the packaged shell by every signal it claims to, and
 *      reports `false` for an ordinary web origin and for a context with no `window`.
 *   2. `/` renders the landing page in a browser and REDIRECTS to `/signin` in the desktop
 *      app — driven through a real router, not by reading the component's return value.
 *   3. The redirect REPLACES rather than pushes, so Back from the sign-in form cannot land
 *      on a marketing page with a download button on it.
 *
 * `LandingPage` is mocked to a marker. This file is about the ROUTING DECISION; mounting
 * eleven real marketing sections to find out which branch was taken would make it slow and
 * would couple it to copy that `tests/unit/landing/landingSections.test.jsx` already owns.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../src/components/landing/LandingPage', () => ({
  default: () => <div data-testid="landing-page">landing</div>,
}));

import { RootRoute } from '../../src/App';
import { isDesktopApp } from '../../src/runtime/desktop';

/** Remove whichever desktop signal a test installed. */
const clearSignals = () => {
  delete window.__TAURI_INTERNALS__;
  delete window.__TAURI__;
};

let realLocation;

beforeEach(() => {
  realLocation = window.location;
  clearSignals();
});

afterEach(() => {
  cleanup();
  clearSignals();
  vi.unstubAllGlobals();
  Object.defineProperty(window, 'location', {
    value: realLocation,
    writable: true,
    configurable: true,
  });
});

const setLocation = (href) => {
  const url = new URL(href);
  Object.defineProperty(window, 'location', {
    value: { href: url.href, origin: url.origin, protocol: url.protocol, host: url.host, hostname: url.hostname },
    writable: true,
    configurable: true,
  });
};

// ---------------------------------------------------------------------------
// 1. Detection
// ---------------------------------------------------------------------------

describe('isDesktopApp', () => {
  it('is false for an ordinary web origin', () => {
    setLocation('https://app.vyomquant.in/app/dashboard');
    expect(isDesktopApp()).toBe(false);
  });

  it('is false on the vite dev server, which is not the desktop shell', () => {
    // The two are both "not https" — `config.js` groups them for the API base — but they
    // are different questions and this one must not conflate them.
    setLocation('http://localhost:1420/');
    expect(isDesktopApp()).toBe(false);
  });

  it("is true on Tauri v2's IPC bridge, the signal that is always present", () => {
    // A perfectly ordinary https location, so the ONLY thing making this true is the
    // injected global. That is what makes it the primary signal rather than one of three.
    setLocation('https://app.vyomquant.in/');
    window.__TAURI_INTERNALS__ = { invoke: () => {} };
    expect(isDesktopApp()).toBe(true);
  });

  it('is true on the withGlobalTauri global, so enabling that option cannot break it', () => {
    setLocation('https://app.vyomquant.in/');
    window.__TAURI__ = {};
    expect(isDesktopApp()).toBe(true);
  });

  it('is true on the macOS/Linux custom protocol', () => {
    setLocation('tauri://localhost/');
    expect(isDesktopApp()).toBe(true);
  });

  it('is true on the Windows custom protocol host', () => {
    // http-shaped, but on a hostname no DNS resolves, so it cannot be a real web origin.
    setLocation('http://tauri.localhost/');
    expect(isDesktopApp()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2 & 3. What `/` serves, through a real router
// ---------------------------------------------------------------------------

const mountRoot = (initialEntries = ['/']) =>
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/" element={<RootRoute />} />
        <Route path="/signin" element={<div data-testid="signin-page">sign in</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe('what `/` serves', () => {
  it('renders the landing page in a browser', async () => {
    setLocation('https://app.vyomquant.in/');

    mountRoot();

    // `LandingPage` is lazy, so the Suspense boundary resolves on a later tick.
    expect(await screen.findByTestId('landing-page')).toBeTruthy();
    expect(screen.queryByTestId('signin-page')).toBeNull();
  });

  it('redirects to sign-in in the desktop app', async () => {
    setLocation('https://app.vyomquant.in/');
    window.__TAURI_INTERNALS__ = { invoke: () => {} };

    mountRoot();

    expect(screen.getByTestId('signin-page')).toBeTruthy();
    // The marketing surface must not be mounted at all — not rendered-then-hidden.
    expect(screen.queryByTestId('landing-page')).toBeNull();
  });

  it('redirects on the desktop custom protocol too, not just on the injected global', async () => {
    setLocation('tauri://localhost/');

    mountRoot();

    expect(screen.getByTestId('signin-page')).toBeTruthy();
    expect(screen.queryByTestId('landing-page')).toBeNull();
  });

  it('replaces rather than pushes, so Back cannot reach the landing page', async () => {
    setLocation('https://app.vyomquant.in/');
    window.__TAURI_INTERNALS__ = { invoke: () => {} };

    // Two entries with `/` last: a REPLACE collapses it onto the previous entry, so going
    // back lands on whatever preceded `/` rather than on `/` itself. A push would leave `/`
    // in the stack and Back from `/signin` would re-enter it — which in the desktop app is
    // the dead end this behaviour exists to prevent.
    const { container } = mountRoot(['/legal/terms', '/']);

    expect(screen.getByTestId('signin-page')).toBeTruthy();
    expect(container.querySelector('[data-testid="landing-page"]')).toBeNull();
  });
});
