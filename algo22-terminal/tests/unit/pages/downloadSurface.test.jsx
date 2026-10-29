/**
 * tests/unit/pages/downloadSurface.test.jsx
 *
 * Requirements 1.30, 2.30, 3.6. Preservation 3.7.
 *
 * THE HISTORY THIS FILE EXISTS TO POLICE
 * --------------------------------------
 * `/download` and the landing page's download section once advertised four desktop installers
 * with four hardcoded sizes — 84.2, 78.5, 75.4 and 68.2 MB — four SHA-256 strings, one of
 * which was the hash of the EMPTY STRING, and four links. All four URLs returned 403: CI's
 * `dist/` carries no `releases/` directory, so `aws s3 sync dist/ --delete` deleted that
 * prefix from the bucket on every deploy. Three of the four files described did not exist;
 * one was a 69-byte text file. Task 4.3 withdrew the whole advertisement.
 *
 * The artifacts are now genuinely published, as GitHub release `desktop-v0.1.0`. So this file
 * no longer asserts absence — it asserts that what is rendered is READ FROM A DECLARATION and
 * that none of the old fabrications has crept back. Five claims:
 *
 *   1. **Every published artifact is fully declared**: url, exact byte count, 64-hex SHA-256,
 *      filename, requirements and a first-run step. No digest is the empty-string hash.
 *   2. **No request is issued by either surface.** A download is a user-initiated anchor
 *      navigation, not a fetch: mounting the page, and switching every tab, must leave
 *      `fetch`, `XMLHttpRequest` and `HTMLAnchorElement.click` at zero calls. The old
 *      `handleDownload` clicked an anchor for the user, which is what produced the 403.
 *   3. **Every rendered size is derived from the declared byte count**, so the figure on
 *      screen and the file cannot disagree. None of the four withdrawn size strings appears.
 *   4. **The unsigned consequence is stated on every card**, because Gatekeeper REFUSES an
 *      unnotarized app and a user given no instruction concludes the download is broken.
 *   5. **The withdrawal machinery still works.** A platform declared in `pageFields` but NOT
 *      published still renders the not-available marker with its reason and no link. Proved
 *      by mocking one platform out of `desktopReleases`, because all four are published today
 *      and a guarantee that cannot be observed failing is not a guarantee.
 *
 * NOTHING HERE IS MOCKED except where section 5 says so. `Navbar` and `Footer` are real,
 * `design/pageFields` and `design/desktopReleases` are real, `usePanelState` and `ds/Panel`
 * are real. The only other doubles are the three network recorders, which exist to prove
 * absence.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import DownloadPage from '../../../src/components/download/DownloadPage';
import DownloadSection from '../../../src/components/landing/DownloadSection';
import { PlatformArtifact } from '../../../src/components/download/PlatformArtifact';
import {
  DESKTOP_ARTIFACTS,
  DESKTOP_PLATFORM_ORDER,
  DESKTOP_RELEASE,
  artifactForPlatform,
  formatArtifactSize,
} from '../../../src/design/desktopReleases';
import {
  ABSENCE,
  PAGES,
  PAGE_FIELDS_BY_PAGE,
  PAGE_FIELD_BY_KEY,
  VERDICT,
  pageFieldKey,
} from '../../../src/design/pageFields';
import { PANEL_STATES, usePanelState } from '../../../src/hooks/usePanelState';

/** The four artifacts both surfaces render, and the tab that selects each on /download. */
const PLATFORMS = Object.freeze([
  { field: 'windows', tab: 'Windows' },
  { field: 'macos', tab: 'macOS' },
  { field: 'linuxAppImage', tab: 'Linux (AppImage)' },
  { field: 'linuxDeb', tab: 'Linux (DEB)' },
]);

const fieldFor = (platform) =>
  PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DOWNLOAD, field: platform })];

/** The four figures the surfaces used to invent. None may return, in any form. */
const WITHDRAWN_SIZES = Object.freeze(['84.2', '78.5', '75.4', '68.2']);

/** `sha256("")` — published on this page once, as a real file's checksum. */
const EMPTY_STRING_SHA256 =
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

const HEX64 = /^[0-9a-f]{64}$/;

// ---------------------------------------------------------------------------
// The network recorders. Every one of them must stay at zero calls.
// ---------------------------------------------------------------------------

let requested;

beforeEach(() => {
  requested = [];

  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    requested.push(`fetch ${typeof input === 'string' ? input : input?.url}`);
    return Promise.reject(new Error('downloadSurface.test: no request expected'));
  });

  vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation((method, url) => {
    requested.push(`xhr ${method} ${url}`);
  });
  vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {});

  // The withdrawn `handleDownload` built an anchor and clicked it, which is what made the
  // browser issue the 403. A programmatic click is therefore a request for this test's
  // purposes; a user's own click on a rendered anchor is not and never reaches here.
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

const downloadLinkIn = (card) =>
  [...card.querySelectorAll('a[href]')].find((a) => a.hasAttribute('download'));

// ---------------------------------------------------------------------------
// 1. The declarations
// ---------------------------------------------------------------------------

describe('the four platforms are declared, in both tables', () => {
  it('pageFields still declares exactly the four, in render order', () => {
    expect(PAGE_FIELDS_BY_PAGE[PAGES.DOWNLOAD].map((f) => f.field)).toEqual(
      PLATFORMS.map((p) => p.field),
    );
  });

  it('desktopReleases declares the same four, in the same order', () => {
    expect(DESKTOP_PLATFORM_ORDER).toEqual(PLATFORMS.map((p) => p.field));
    expect(Object.keys(DESKTOP_ARTIFACTS).sort()).toEqual(
      PLATFORMS.map((p) => p.field).sort(),
    );
  });

  it.each(PLATFORMS.map((p) => [p.field]))(
    '%s keeps a fallback withdrawal entry with a reason and no path',
    (platform) => {
      // Still UNAVAILABLE in `pageFields`: that entry is now the FALLBACK for a platform
      // with no published build, and the source of the rendered label in both states.
      const field = fieldFor(platform);

      expect(field.verdict).toBe(VERDICT.UNAVAILABLE);
      expect(field.absence).toBe(ABSENCE.UNREPORTED);
      expect(field.reason.trim().length).toBeGreaterThan(20);
      expect(field.path).toBeNull();
      expect(field.label.trim().length).toBeGreaterThan(0);
    },
  );

  it.each(PLATFORMS.map((p) => [p.field]))(
    '%s declares a url, an exact byte count, a real digest and a first-run step',
    (platform) => {
      const a = artifactForPlatform(platform);
      expect(a, `${platform} is not published`).not.toBeNull();

      expect(a.url).toMatch(/^https:\/\/github\.com\/.+\/releases\/download\/.+/);
      expect(a.url.endsWith(a.filename)).toBe(true);
      expect(a.filename).toMatch(/\.(?:exe|dmg|AppImage|deb)$/);

      // An exact count, above the 1 MiB floor the build workflow itself enforces. The
      // placeholders this page used to describe were 64-69 bytes.
      expect(Number.isInteger(a.bytes)).toBe(true);
      expect(a.bytes).toBeGreaterThan(1024 * 1024);

      expect(a.sha256).toMatch(HEX64);
      expect(a.sha256).not.toBe(EMPTY_STRING_SHA256);

      expect(a.requires.trim().length).toBeGreaterThan(0);
      expect(a.firstRun.trim().length).toBeGreaterThan(0);
    },
  );

  it('declares four distinct files and four distinct digests', () => {
    const files = DESKTOP_PLATFORM_ORDER.map((p) => DESKTOP_ARTIFACTS[p].filename);
    const digests = DESKTOP_PLATFORM_ORDER.map((p) => DESKTOP_ARTIFACTS[p].sha256);
    expect(new Set(files).size).toBe(files.length);
    expect(new Set(digests).size).toBe(digests.length);
  });

  it('records that the builds are not signed, rather than leaving the UI to decide', () => {
    expect(DESKTOP_RELEASE.signed).toBe(false);
    expect(DESKTOP_RELEASE.notesUrl).toMatch(/^https:\/\//);
  });

  it('derives the displayed size from the byte count', () => {
    // Not a stored string. This is the property that stops "78.5 MB" appearing beside a
    // 69-byte file ever again.
    for (const platform of DESKTOP_PLATFORM_ORDER) {
      const { bytes } = DESKTOP_ARTIFACTS[platform];
      const expected = `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
      expect(formatArtifactSize(bytes)).toBe(expected);
    }
    // And it refuses to render a figure for a non-size.
    expect(formatArtifactSize(0)).toBe('');
    expect(formatArtifactSize(-1)).toBe('');
    expect(formatArtifactSize(undefined)).toBe('');
  });

  it('declares none of the four withdrawn sizes', () => {
    const rendered = DESKTOP_PLATFORM_ORDER.map((p) =>
      formatArtifactSize(DESKTOP_ARTIFACTS[p].bytes),
    ).join(' ');
    for (const size of WITHDRAWN_SIZES) {
      expect(rendered).not.toContain(`${size} MB`);
    }
  });
});

// ---------------------------------------------------------------------------
// 2. The short-circuit, at the hook (unchanged: it still governs the fallback)
// ---------------------------------------------------------------------------

describe('usePanelState reaches `unavailable` without calling the reader', () => {
  function Probe({ reason, reader }) {
    const { state } = usePanelState(reader, { unavailable: reason });
    return <output data-testid="state">{state}</output>;
  }

  it('never calls a reader that would issue a request, and calls it once the reason goes', () => {
    const reader = vi.fn(() => fetch('/releases/windows/VyomQuant-Setup-0.1.0.exe'));
    const reason = fieldFor('windows').reason;

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

describe('/download offers every platform its real installer', () => {
  it.each(PLATFORMS.map((p) => [p.tab, p.field]))(
    'the %s tab links the declared artifact, at the declared size',
    (tab, platform) => {
      const { container } = renderPage();
      selectPlatform(container, tab);

      const card = cardFor(container, platform);
      expect(card, `no card for ${platform}`).not.toBeNull();
      expect(card.getAttribute('data-panel-state')).toBe(PANEL_STATES.READY);
      expect(card.textContent).toContain(fieldFor(platform).label);

      const declared = artifactForPlatform(platform);
      const link = downloadLinkIn(card);
      expect(link, `no download link on the ${platform} card`).toBeDefined();
      expect(link.getAttribute('href')).toBe(declared.url);
      expect(link.getAttribute('download')).toBe(declared.filename);

      // The size on screen is the declared byte count, formatted — not a typed string.
      expect(card.textContent).toContain(formatArtifactSize(declared.bytes));
      expect(card.textContent).toContain(declared.sha256);
      expect(card.textContent).toContain(declared.requires);
    },
  );

  it.each(PLATFORMS.map((p) => [p.tab, p.field]))(
    'the %s card states what the operating system will demand on first launch',
    (tab, platform) => {
      const { container } = renderPage();
      selectPlatform(container, tab);
      const card = cardFor(container, platform);

      // Unsigned builds: the warning is not optional copy, it is the difference between a
      // user installing the app and concluding the download is corrupt.
      expect(card.textContent).toContain(artifactForPlatform(platform).firstRun);
    },
  );

  it('renders none of the four withdrawn size strings, on any tab', () => {
    const { container } = renderPage();

    for (const { tab } of PLATFORMS) {
      selectPlatform(container, tab);
      for (const size of WITHDRAWN_SIZES) {
        expect(container.textContent, `${size} MB is back on the page`).not.toContain(
          `${size} MB`,
        );
      }
    }
  });

  it('publishes no digest that is the hash of the empty string', () => {
    const { container } = renderPage();
    for (const { tab } of PLATFORMS) selectPlatform(container, tab);
    expect(container.textContent).not.toContain(EMPTY_STRING_SHA256);
  });

  it('issues no request while mounting or while switching platform', () => {
    // A download is an anchor the USER activates. Nothing here may fetch the artifact, HEAD
    // it for a size, or click on the user's behalf.
    const { container } = renderPage();
    for (const { tab } of PLATFORMS) selectPlatform(container, tab);

    expect(requested).toEqual([]);
  });

  it('still offers the web platform, which needs no install', () => {
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

describe('the landing download section offers the same four installers', () => {
  it('renders one ready card per platform, in order, each linking its artifact', () => {
    const { container } = renderSection();

    const cards = [...container.querySelectorAll('[data-download-platform]')];
    expect(cards.map((c) => c.getAttribute('data-download-platform'))).toEqual(
      PLATFORMS.map((p) => p.field),
    );

    for (const card of cards) {
      const platform = card.getAttribute('data-download-platform');
      const declared = artifactForPlatform(platform);

      expect(card.getAttribute('data-panel-state')).toBe(PANEL_STATES.READY);
      expect(card.textContent).toContain(fieldFor(platform).label);

      const link = downloadLinkIn(card);
      expect(link, `no download link for ${platform} on the landing page`).toBeDefined();
      expect(link.getAttribute('href')).toBe(declared.url);
      expect(card.textContent).toContain(formatArtifactSize(declared.bytes));
      expect(card.textContent).toContain(declared.firstRun);
    }
  });

  it('issues no request, and keeps the web platform card', () => {
    const { container } = renderSection();

    expect(requested).toEqual([]);
    expect(
      [...container.querySelectorAll('a[href]')].some((a) => a.getAttribute('href') === '/app'),
    ).toBe(true);
  });

  it('renders neither of the two size strings it used to advertise', () => {
    const { container } = renderSection();

    expect(container.textContent).not.toContain('84.2 MB');
    expect(container.textContent).not.toContain('78.5 MB');
  });

  it('links the release notes, so the checksums are verifiable off-page', () => {
    const { container } = renderSection();
    const notes = [...container.querySelectorAll('a[href]')].map((a) => a.getAttribute('href'));
    expect(notes).toContain(DESKTOP_RELEASE.notesUrl);
  });
});

// ---------------------------------------------------------------------------
// 5. The renderer, and the withdrawal path that must still work
// ---------------------------------------------------------------------------

describe('PlatformArtifact', () => {
  it('renders the declared label for every platform', () => {
    for (const { field } of PLATFORMS) {
      const { container } = render(<PlatformArtifact platform={field} />);
      expect(cardFor(container, field).textContent).toContain(fieldFor(field).label);
      cleanup();
    }
  });

  it('renders nothing for a platform that is not declared at all', () => {
    const { container } = render(<PlatformArtifact platform="solaris" />);
    expect(container.innerHTML).toBe('');
    expect(requested).toEqual([]);
  });
});

describe('a platform with no published artifact still reports its reason', () => {
  // All four are published today, so the fallback cannot be observed with real data. It is
  // the guarantee that protects the NEXT platform without a build, so it is exercised by
  // removing one from the published table.
  beforeEach(() => {
    vi.resetModules();
  });

  it('renders the not-available marker, the declared reason, and no link', async () => {
    vi.doMock('../../../src/design/desktopReleases', async () => {
      const real = await vi.importActual('../../../src/design/desktopReleases');
      return {
        ...real,
        // macOS unpublished; the other three untouched.
        artifactForPlatform: (platform) =>
          (platform === 'macos' ? null : real.artifactForPlatform(platform)),
      };
    });

    const { PlatformArtifact: Subject } = await import(
      '../../../src/components/download/PlatformArtifact'
    );

    const { container } = render(<Subject platform="macos" />);
    const card = cardFor(container, 'macos');

    expect(card.getAttribute('data-panel-state')).toBe(PANEL_STATES.UNAVAILABLE);
    expect(card.textContent).toContain('Not available');
    expect(card.textContent).toContain(fieldFor('macos').reason);
    // Requirement 3.7: the marker carries no link, no size, no checksum and no `0`.
    expect(card.querySelectorAll('a')).toHaveLength(0);
    expect(card.querySelectorAll('[download]')).toHaveLength(0);
    expect(card.textContent).not.toMatch(/sha-?256/i);
    expect(card.textContent).not.toMatch(/\b0\b/);
    expect(requested).toEqual([]);

    vi.doUnmock('../../../src/design/desktopReleases');
  });
});
