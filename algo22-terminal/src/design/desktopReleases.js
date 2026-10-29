/**
 * ═══════════════════════════════════════════════════════════════════════════
 * desktopReleases — the PUBLISHED desktop artifacts, declared once
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS IS A SEPARATE MODULE FROM `pageFields.js`
 * -------------------------------------------------
 * `pageFields` declares FIGURES READ FROM AN API RESPONSE. Its `entry()` builder documents
 * the invariant in its own comment: `path` is "non-null exactly when
 * `verdict === 'available'`", because an available figure is by definition at a dotted path
 * on a response body. A desktop installer is a static file at a URL; it has no response and
 * no path. Forcing these four artifacts into that table would mean either a null `path` on
 * an `available` entry — breaking the invariant a test enforces — or inventing a fake path.
 *
 * So `pageFields` keeps the four DOWNLOAD entries as the LABEL source and as the withdrawal
 * fallback for any platform that is NOT published, and this module is the source of truth for
 * the ones that are. `PlatformArtifact` prefers this module and falls back to that one, which
 * means the machinery that correctly said "not available" for two years still works for the
 * next platform that has no build yet.
 *
 * EVERY FIGURE BELOW WAS MEASURED, NOT COPIED FROM A RELEASE NOTE
 * --------------------------------------------------------------
 * This page previously advertised four installers at 84.2 / 78.5 / 75.4 / 68.2 MB with
 * SHA-256 strings, one of which was the hash of the EMPTY STRING, for four URLs that all
 * returned 403. Three of the four files it described were 64-69 byte text placeholders.
 * That is why the numbers here carry their provenance:
 *
 *   source run  : `07 Desktop Build (native, unsigned)` run 40833892696
 *   built from  : b6c1a01f (the PR merge commit for #146)
 *   verified by : magic-byte identification of every file before publication -- PE `MZ`,
 *                 OLE compound file, UDIF `koly` trailer, ELF + AppImage type-2 magic,
 *                 `!<arch>`, RPM lead -- plus a 1 MiB floor, plus a check that no digest
 *                 equals the empty-string hash, plus an ANONYMOUS re-fetch of every
 *                 published URL confirming HTTP 200 and a byte-exact `Content-Length`.
 *
 * `bytes` is the exact byte count, so the rendered size is derived rather than typed and
 * cannot drift from the file. `sha256` is what a user's `shasum -a 256` will print.
 *
 * THESE BUILDS ARE NOT SIGNED, AND THE UI SAYS SO
 * ----------------------------------------------
 * No Authenticode certificate and no Apple Developer ID is configured, so the bundles are
 * unsigned and the macOS DMG is unnotarized. `signed: false` is declared here rather than
 * written into copy, and `firstRun` carries the per-platform consequence, because a download
 * button that omits "your OS will refuse to open this" is the same class of defect as a
 * fabricated size: the surface telling the user something untrue about the artifact.
 */

/** The published release. One version is offered at a time. */
export const DESKTOP_RELEASE = Object.freeze({
  version: '0.1.0',
  tag: 'desktop-v0.1.0',
  publishedOn: '2026-10-29',
  publisher: 'Aerora Dynamics Private Limited',
  notesUrl: 'https://github.com/Narendra2212/vyomquant-saas/releases/tag/desktop-v0.1.0',
  /** No certificate is configured on any platform. Read by the UI; never hardcoded there. */
  signed: false,
});

const ASSET_BASE =
  'https://github.com/Narendra2212/vyomquant-saas/releases/download/desktop-v0.1.0';

/**
 * Keyed by the SAME platform ids `DownloadPage.jsx` uses for its tabs and `pageFields.js`
 * uses for its `field` — `windows`, `macos`, `linuxAppImage`, `linuxDeb` — so a platform has
 * one spelling across the declaration, the two surfaces and the tests.
 */
export const DESKTOP_ARTIFACTS = Object.freeze({
  windows: Object.freeze({
    platform: 'windows',
    filename: 'VyomQuant_0.1.0_x64-setup.exe',
    url: `${ASSET_BASE}/VyomQuant_0.1.0_x64-setup.exe`,
    bytes: 2563716,
    sha256: 'b238475123f07efb88296a42dd62512a5d2f65e35a7102fd2bc3284c4cb1870d',
    requires: 'Windows 10 or 11, 64-bit',
    firstRun:
      'Unsigned build: SmartScreen will say "Windows protected your PC". '
      + 'Choose More info, then Run anyway.',
  }),
  macos: Object.freeze({
    platform: 'macos',
    filename: 'VyomQuant_0.1.0_universal.dmg',
    url: `${ASSET_BASE}/VyomQuant_0.1.0_universal.dmg`,
    bytes: 6993988,
    sha256: '779ea5988720a6f600d22134d3c61a791c189eb9b647b19eca163bf7190a68b3',
    requires: 'macOS 12 or later — universal (Apple silicon and Intel)',
    // The strongest warning of the four, because macOS does not merely warn: Gatekeeper
    // refuses an unnotarized app outright, and a user with no instruction concludes the
    // download is broken.
    firstRun:
      'Not notarized: macOS will refuse the first launch with "cannot be opened because '
      + 'the developer cannot be verified". Right-click the app and choose Open, then Open '
      + 'again.',
  }),
  linuxAppImage: Object.freeze({
    platform: 'linuxAppImage',
    filename: 'VyomQuant_0.1.0_amd64.AppImage',
    url: `${ASSET_BASE}/VyomQuant_0.1.0_amd64.AppImage`,
    bytes: 82614776,
    sha256: '77646dd18855cdc78b56928af2991021544d2eeb256d4bab60077a13476e666e',
    requires: 'Any x86_64 desktop with glibc 2.35 or newer — built on Ubuntu 22.04',
    firstRun: 'Make it executable first: chmod +x VyomQuant_0.1.0_amd64.AppImage',
  }),
  linuxDeb: Object.freeze({
    platform: 'linuxDeb',
    filename: 'VyomQuant_0.1.0_amd64.deb',
    url: `${ASSET_BASE}/VyomQuant_0.1.0_amd64.deb`,
    bytes: 4521556,
    sha256: '04ac8c15f4183f57f339364580c82e0c74368489aa201ec2cef04271780fa054',
    requires: 'Debian 12, Ubuntu 22.04 or newer',
    firstRun: 'Install with: sudo apt install ./VyomQuant_0.1.0_amd64.deb',
  }),
});

/** Published artifacts, in the order the surfaces render their platforms. */
export const DESKTOP_PLATFORM_ORDER = Object.freeze([
  'windows',
  'macos',
  'linuxAppImage',
  'linuxDeb',
]);

/**
 * The published artifact for a platform id, or `null` when none is published.
 *
 * `null` is the signal `PlatformArtifact` uses to fall back to `pageFields`' withdrawal
 * declaration, so an unbuilt platform still renders the not-available marker with its reason
 * rather than a dead button.
 *
 * @param {string} platform
 * @returns {object|null}
 */
export const artifactForPlatform = (platform) =>
  (Object.prototype.hasOwnProperty.call(DESKTOP_ARTIFACTS, platform)
    ? DESKTOP_ARTIFACTS[platform]
    : null);

/**
 * Render an exact byte count as MiB.
 *
 * Derived from `bytes` on every render rather than stored as a string, which is what stops
 * the displayed size and the file from ever disagreeing — the specific failure that put
 * "78.5 MB" beside a 69-byte text file on this page.
 *
 * @param {number} bytes
 * @returns {string}
 */
export const formatArtifactSize = (bytes) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  const mib = bytes / (1024 * 1024);
  return `${mib.toFixed(1)} MiB`;
};
