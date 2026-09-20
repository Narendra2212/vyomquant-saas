/**
 * tests/unit/pages/downloadSurface.test.jsx — production-launch-hardening task 4.3.
 *
 * Requirements 1.30, 2.30, 3.6. Preservation 3.7.
 *
 * WHAT IS BEING ASSERTED
 * ----------------------
 * `/download` and the landing page's download section advertised four desktop installers
 * with four hardcoded sizes — 84.2, 78.5, 75.4 and 68.2 MB — four SHA-256 strings, and four
 * links. **All four URLs return 403**, verified against production during design: CI's
 * `dist/` carries no `releases/` directory, so `aws s3 sync dist/ --delete` deletes that
 * prefix from the bucket on every deploy. The advertisement was withdrawn rather than
 * relabelled, through the convention this app already uses for an absent figure.
 *
 * Three claims, in the order they matter:
 *
 *   1. **Each card states not-available and carries its declared reason.** Not a blank card,
 *      not an empty state ("you have none of these"), not an error. `ds/Panel`'s
 *      `unavailable` state with the reason `design/pageFields` declares.
 *   2. **No request is issued.** `usePanelState` short-circuits on the reason before it
 *      consults `enabled`, and never calls the reader (Requirement 19.3). Asserted twice:
 *      at the hook, with a reader that records every call, and at the two surfaces, with
 *      `fetch`, `XMLHttpRequest` and `HTMLAnchorElement.click` all recording.
 *   3. **No download link is rendered for a platform with no artifact**, and no size,
 *      checksum or figure travels with the marker (3.7: the marker never renders `0`).
 *
 * WHY THE HOOK IS TESTED DIRECTLY AS WELL AS THROUGH THE PAGE
 * ----------------------------------------------------------
 * `PlatformArtifact` passes `null` as its reader, so "the surface issued no request" would be
 * true of it even if the short-circuit were broken. Section 2 closes that: it drives
 * `usePanelState` with a reader that WOULD issue one and asserts the declared reason stops it,
 * and — in the same test — that the identical reader IS called once the reason is removed. A
 * guard that cannot be observed failing is not a guard.
 *
 * NOTHING HERE IS MOCKED. `Navbar` and `Footer` are real (they import a router `Link` and two
 * icons, nothing else), `design/pageFields` is real, `usePanelState` and `ds/Panel` are real.
 * The only doubles are the three network recorders, and they exist to prove absence.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import DownloadPage from '../../../src/components/download/DownloadPage';
import DownloadSection from '../../../src/components/landing/DownloadSection';
import { PlatformArtifact } from '../../../src/components/download/PlatformArtifact';
import {
  ABSENCE,
  PAGES,
  PAGE_FIELDS_BY_PAGE,
  PAGE_FIELD_BY_KEY,
  VERDICT,
  pageFieldKey,
} from '../../../src/design/pageFields';
import { PANEL_STATES, usePanelState } from '../../../src/hooks/usePanelState';

/** The four artifacts the two surfaces used to link, and the tab that selects each on /download. */
const PLATFORMS = Object.freeze([
  { field: 'windows', tab: 'Windows' },
  { field: 'macos', tab: 'macOS' },
  { field: 'linuxAppImage', tab: 'Linux (AppImage)' },
  { field: 'linuxDeb', tab: 'Linux (DEB)' },
]);

const fieldFor = (platform) =>
  PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DOWNLOAD, field: platform })];

/** The four figures that used to be rendered beside the four links. */
const WITHDRAWN_SIZES = Object.freeze(['84.2', '78.5', '75.4', '68.2']);

/** A number with a unit — a size, wherever it appears. */
const SIZE_SHAPED = /\d[\d.]*\s*(?:B|KB|MB|GB|MiB|GiB)\b/i;

/** An installer extension on an href, whatever the path in front of it. */
const INSTALLER_HREF = /\.(?:exe|dmg|AppImage|deb)(?:$|[?#])/i;

// ---------------------------------------------------------------------------
// The network recorders. Every one of them must stay at zero calls.
// ---------------------------------------------------------------------------

let requested;

beforeEach(() => {
  requested = [];

  // `fetch` exists in this environment (Node's), so this replaces it rather than defining
  // one; a surface that reached for it would be recorded and would not reach the network.
  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    requested.push(`fetch ${typeof input === 'string' ? input : input?.url}`);
    return Promise.reject(new Error('downloadSurface.test: no request expected'));
  });

  vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation((method, url) => {
    requested.push(`xhr ${method} ${url}`);
  });
  vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {});

  // The withdrawn `handleDownload` fetched nothing directly: it built an anchor and clicked
  // it, which is what made the browser issue the 403. A click on a detached anchor is
  // therefore a request for the purposes of this test.
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function record() {
    requested.push(`anchor click ${this.getAttribute('href')}`);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <DownloadPage />
    </MemoryRouter>,
  );

const renderSection = () =>
  render(
    <MemoryRouter>
      <DownloadSection />
    </MemoryRouter>,
  );

/** Click the tab whose label leads its accessible text. The badge may follow it. */
function selectPlatform(container, label) {
  const tab = [...container.querySelectorAll('button')].find((button) =>
    button.textContent.trim().startsWith(label),
  );
  expect(tab, `no tab labelled ${label}`).toBeDefined();
  fireEvent.click(tab);
  return tab;
}

const cardFor = (container, platform) =>
  container.querySelector(`[data-download-platform="${platform}"]`);

// ---------------------------------------------------------------------------
// 1. The declaration
// ---------------------------------------------------------------------------

describe('the four installers are declared unavailable, with reasons', () => {
  it('declares exactly the four artifacts the surfaces used to link', () => {
    expect(PAGE_FIELDS_BY_PAGE[PAGES.DOWNLOAD].map((f) => f.field)).toEqual(
      PLATFORMS.map((p) => p.field),
    );
  });

  it.each(PLATFORMS.map((p) => [p.field]))(
    '%s is UNAVAILABLE / UNREPORTED with a reason and no path',
    (platform) => {
      const field = fieldFor(platform);

      expect(field.verdict).toBe(VERDICT.UNAVAILABLE);
      expect(field.absence).toBe(ABSENCE.UNREPORTED);
      // Requirement 19.3: "not available" with no reason tells a trader what a blank cell does.
      expect(field.reason.trim().length).toBeGreaterThan(20);
      // Not a value on a response. A page that read one would be reading a URL to render.
      expect(field.path).toBeNull();
      // And the reason itself carries no figure — no size survives in the copy either.
      expect(field.reason).not.toMatch(SIZE_SHAPED);
    },
  );
});

// ---------------------------------------------------------------------------
// 2. The short-circuit, at the hook
// ---------------------------------------------------------------------------

describe('usePanelState reaches `unavailable` without calling the reader', () => {
  function Probe({ reason, reader }) {
    const { state } = usePanelState(reader, { unavailable: reason });
    return <output data-testid="state">{state}</output>;
  }

  it('never calls a reader that would issue a request, and calls it once the reason goes', () => {
    const reader = vi.fn(() => fetch('/releases/windows/VyomQuant-Setup-0.1.0.exe'));
    const { field, reason } = { field: 'windows', reason: fieldFor('windows').reason };
    expect(field).toBe('windows');

    const withReason = render(<Probe reason={reason} reader={reader} />);
    expect(withReason.getByTestId('state').textContent).toBe(PANEL_STATES.UNAVAILABLE);
    expect(reader).not.toHaveBeenCalled();
    expect(requested).toEqual([]);
    cleanup();

    // The same reader, the same hook, no declared reason: it runs. This is what makes the
    // assertion above a fact about the short-circuit rather than about a reader nobody wired.
    render(<Probe reason={null} reader={reader} />);
    expect(reader).toHaveBeenCalledTimes(1);
    expect(requested).toEqual(['fetch /releases/windows/VyomQuant-Setup-0.1.0.exe']);
  });
});

// ---------------------------------------------------------------------------
// 3. /download
// ---------------------------------------------------------------------------

describe('/download renders each platform as not-available, and offers nothing', () => {
  it.each(PLATFORMS.map((p) => [p.tab, p.field]))(
    'the %s tab renders the marker carrying the declared reason',
    (tab, platform) => {
      const { container } = renderPage();
      selectPlatform(container, tab);

      const card = cardFor(container, platform);
      expect(card, `no card for ${platform}`).not.toBeNull();
      // Not `empty` (which claims the trader has none), not `error` (which claims something
      // broke), not `idle` (which says nothing at all).
      expect(card.getAttribute('data-panel-state')).toBe(PANEL_STATES.UNAVAILABLE);
      expect(card.textContent).toContain('Not available');
      expect(card.textContent).toContain(fieldFor(platform).reason);
    },
  );

  it.each(PLATFORMS.map((p) => [p.tab, p.field]))(
    'the %s card carries no link, no control, no size and no figure',
    (tab, platform) => {
      const { container } = renderPage();
      selectPlatform(container, tab);
      const card = cardFor(container, platform);

      expect(card.querySelectorAll('a')).toHaveLength(0);
      expect(card.querySelectorAll('button')).toHaveLength(0);
      expect(card.querySelectorAll('[download]')).toHaveLength(0);
      expect(card.textContent).not.toMatch(SIZE_SHAPED);
      // 3.7: the not-available marker never renders `0`. Nor any other bare figure — a
      // withdrawn card has no reading to report.
      expect(card.textContent).not.toMatch(/\b0\b/);
      expect(card.textContent).not.toMatch(/sha-?256/i);
    },
  );

  it('renders no link to any installer anywhere on the page', () => {
    const { container } = renderPage();

    for (const anchor of container.querySelectorAll('a[href]')) {
      const href = anchor.getAttribute('href');
      expect(href, `${href} points into releases/`).not.toContain('releases');
      expect(href).not.toMatch(INSTALLER_HREF);
    }
    expect(container.querySelectorAll('[download]')).toHaveLength(0);
  });

  it('renders none of the four withdrawn size strings', () => {
    const { container } = renderPage();

    // Every tab, because each one used to carry its own figure.
    for (const { tab } of PLATFORMS) {
      selectPlatform(container, tab);
      for (const size of WITHDRAWN_SIZES) {
        expect(container.textContent, `${size} MB is still on the page`).not.toContain(
          `${size} MB`,
        );
      }
    }
  });

  it('issues no request while mounting or while switching platform', () => {
    const { container } = renderPage();
    for (const { tab } of PLATFORMS) selectPlatform(container, tab);

    expect(requested).toEqual([]);
  });

  it('still offers the web platform, which is the thing that does work', () => {
    // Non-vacuity for the assertions above: they would also pass on a page that rendered
    // nothing at all. The honest surface keeps its one real destination.
    const { container } = renderPage();
    const appLinks = [...container.querySelectorAll('a[href]')].filter(
      (a) => a.getAttribute('href') === '/app',
    );

    expect(appLinks.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// 4. The landing section
// ---------------------------------------------------------------------------

describe('the landing download section states the same four reasons', () => {
  it('renders one not-available panel per withdrawn artifact', () => {
    const { container } = renderSection();

    const cards = [...container.querySelectorAll('[data-download-platform]')];
    expect(cards.map((c) => c.getAttribute('data-download-platform'))).toEqual(
      PLATFORMS.map((p) => p.field),
    );

    for (const card of cards) {
      const platform = card.getAttribute('data-download-platform');
      expect(card.getAttribute('data-panel-state')).toBe(PANEL_STATES.UNAVAILABLE);
      expect(card.textContent).toContain('Not available');
      expect(card.textContent).toContain(fieldFor(platform).reason);
      expect(card.querySelectorAll('a')).toHaveLength(0);
      expect(card.textContent).not.toMatch(SIZE_SHAPED);
      expect(card.textContent).not.toMatch(/\b0\b/);
    }
  });

  it('renders no installer link and no download attribute, and issues no request', () => {
    const { container } = renderSection();

    for (const anchor of container.querySelectorAll('a[href]')) {
      const href = anchor.getAttribute('href');
      expect(href, `${href} points into releases/`).not.toContain('releases');
      expect(href).not.toMatch(INSTALLER_HREF);
    }
    expect(container.querySelectorAll('[download]')).toHaveLength(0);
    expect(requested).toEqual([]);

    // The web platform card is untouched.
    expect(
      [...container.querySelectorAll('a[href]')].some((a) => a.getAttribute('href') === '/app'),
    ).toBe(true);
  });

  it('renders neither of the two size strings it used to advertise', () => {
    const { container } = renderSection();

    expect(container.textContent).not.toContain('84.2 MB');
    expect(container.textContent).not.toContain('78.5 MB');
  });
});

// ---------------------------------------------------------------------------
// 5. The renderer reads the declaration rather than holding copy of its own
// ---------------------------------------------------------------------------

describe('PlatformArtifact', () => {
  it('renders the declared reason verbatim, for every platform', () => {
    for (const { field } of PLATFORMS) {
      const { container } = render(<PlatformArtifact platform={field} />);
      const card = cardFor(container, field);

      expect(card.textContent).toContain(fieldFor(field).reason);
      expect(card.textContent).toContain(fieldFor(field).label);
      cleanup();
    }
  });

  it('renders nothing for a platform that is not declared', () => {
    // Rather than an unexplained card: the reason lives in the declaration, and `ds/Panel`
    // may not be `unavailable` without one.
    const { container } = render(<PlatformArtifact platform="solaris" />);
    expect(container.innerHTML).toBe('');
    expect(requested).toEqual([]);
  });
});
