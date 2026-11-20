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
 *   source run  : `07 Desktop Build (native, unsigned)` run 41618342986
 *   built from  : 6817dd51 (main, the merge commit for #183)
 *   verified by : magic-byte identification of every file before publication -- PE `MZ`,
 *                 OLE compound file, UDIF `koly` trailer, ELF + AppImage type-2 magic,
 *                 `!<arch>`, RPM lead -- plus a 1 MiB floor, plus a check that no digest
 *                 equals the empty-string hash, plus an ANONYMOUS re-fetch of every
 *                 published URL confirming HTTP 200, a byte-exact `Content-Length`, AND
 *                 that the bytes actually served hash to the digest written down here.
 *
 * WHY THESE URLS ARE SAME-ORIGIN AND NOT GITHUB RELEASE URLS
 * ---------------------------------------------------------
 * Through 0.1.1 this module pointed at `github.com/Narendra2212/vyomquant-saas/releases/
 * download/...`. THAT REPOSITORY IS PRIVATE, so those URLs returned 404 TO EVERY VISITOR.
 * Measured: the deployed bundle's own asset base, fetched with no credentials, 404s, while
 * `api.github.com/repos/octocat/Hello-World` fetched the same way returns 200 -- so it was
 * the repository's visibility and not the network. The REST asset endpoint still serves the
 * bytes to a token holder, which is why the artifacts verified fine while being unreachable
 * for the people the page is for. Every desktop download on this site has been broken since
 * the first one was advertised.
 *
 * The installers are therefore served from the bucket that already serves this SPA, which
 * exposes only the installer bytes -- bytes that are meant to be public -- and needs no
 * change to the repository's visibility.
 *
 * THE SAME-ORIGIN PREFIX USED TO BE DELETED ON EVERY DEPLOY. THAT IS NOW FIXED.
 * ----------------------------------------------------------------------------
 * `landingSections.test.jsx` records why the page's original same-origin `/releases/...`
 * installers 403'd: "CI's `dist/` carries no `releases/` directory, so `aws s3 sync dist/
 * --delete` removes that prefix from the bucket on every deploy." That was worked around by
 * moving to GitHub rather than by fixing the sync, so the sync was still broken -- and a
 * `--dryrun` against the real bucket confirmed it would have deleted a probe object under
 * `downloads/` too. `06-frontend-deploy.yml`'s unhashed pass now carries
 * `--exclude "downloads/*"`, the other two passes are allow-listed and cannot reach the
 * prefix, and `guards/deployPrefixProtection.test.js` derives the protected prefix FROM THE
 * URLS BELOW and fails if any `--delete` pass could remove them.
 *
 * `notesUrl` is served from the same prefix for the same reason: the GitHub release page
 * is a 404 for anyone without repository access.
 *
 * WHY 0.1.1 REPLACED 0.1.0, which matters more than the version number
 * -------------------------------------------------------------------
 * The 0.1.0 bundles could not reach the API AT ALL. They were built before the packaged app
 * had a declared API base, and the desktop shell takes neither of `config.js`'s other two
 * branches -- it has no https origin to read, and `07-desktop-build.yml` passed
 * `VITE_API_BASE_URL`, a name nothing under `src/` reads, while never setting the
 * `VITE_API_URL` that `config.js` does read. So they fell through to
 * `http://localhost:8000` and dialled a dead port on every user's machine, with every panel
 * reading "No connection". Seventeen people downloaded them.
 *
 * It was not a CSP refusal -- `src-tauri/tauri.conf.json` permits `http://localhost:*` --
 * so the request left the webview and was refused by the network, which is why it LOOKED
 * like the web app's failure while having a different cause.
 *
 * A NEW VERSION rather than new bytes under the old tag, because `tauri build` names its
 * output from the configured version: a corrected build produces the same FILENAMES. Serving
 * those over `desktop-v0.1.0` would make one tag, one filename and one advertised digest
 * mean two different binaries depending on when you fetched them -- which is precisely the
 * promise the `sha256` field below exists to keep.
 *
 * The 0.1.0 release is deliberately left in place and still resolves, so nobody mid-download
 * is broken; it is simply no longer what this module points at.
 *
 * WHY 0.1.2 REPLACED 0.1.1: A VULNERABLE RUNTIME, NOT A FEATURE
 * ------------------------------------------------------------
 * The 0.1.0 and 0.1.1 bundles both embed Tauri 2.10.3, which is affected by
 * GHSA-7gmj-67g7-phm9 / CVE-2026-42184 -- an origin-confusion flaw that lets a REMOTE page
 * invoke IPC commands the app means to expose only to its own local content. Upstream fixed
 * it in 2.11.1; these bundles carry 2.12.5, so 0.1.2 is the first desktop release that is
 * not affected. Nothing user-visible changed, which is exactly why the version had to move:
 * there is otherwise no way for a user to tell the two apart.
 *
 * Verified by reading `src-tauri/Cargo.lock` at each tag rather than from the changelog:
 * `desktop-v0.1.0` 2.10.3, `desktop-v0.1.1` 2.10.3, `6817dd51` 2.12.5.
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
  version: '0.1.2',
  tag: 'desktop-v0.1.2',
  publishedOn: '2026-11-03',
  publisher: 'Aerora Dynamics Private Limited',
  // Plain text, same origin, same prefix as the installers. The GitHub release page for
  // this tag exists and is correct, but the repository is private, so linking it would put
  // a 404 behind a "release notes" control on a public page.
  notesUrl: 'https://www.vyomquant.in/downloads/desktop-v0.1.2/RELEASE-NOTES.txt',
  /** No certificate is configured on any platform. Read by the UI; never hardcoded there. */
  signed: false,
});

// Version-scoped, so a future release cannot overwrite these bytes and the objects can be
// cached immutably. Served by the same CloudFront distribution as the SPA.
const ASSET_BASE = 'https://www.vyomquant.in/downloads/desktop-v0.1.2';

/**
 * Keyed by the SAME platform ids `DownloadPage.jsx` uses for its tabs and `pageFields.js`
 * uses for its `field` — `windows`, `macos`, `linuxAppImage`, `linuxDeb` — so a platform has
 * one spelling across the declaration, the two surfaces and the tests.
 */
export const DESKTOP_ARTIFACTS = Object.freeze({
  windows: Object.freeze({
    platform: 'windows',
    filename: 'VyomQuant_0.1.2_x64-setup.exe',
    url: `${ASSET_BASE}/VyomQuant_0.1.2_x64-setup.exe`,
    bytes: 2657837,
    sha256: 'db25ae09a5d39398ef6b6ab9d15969a93828b78f2e7df7fdf245c6ed9bfcf2f4',
    requires: 'Windows 10 or 11, 64-bit',
    firstRun:
      'Unsigned build: SmartScreen will say "Windows protected your PC". '
      + 'Choose More info, then Run anyway.',
  }),
  macos: Object.freeze({
    platform: 'macos',
    filename: 'VyomQuant_0.1.2_universal.dmg',
    url: `${ASSET_BASE}/VyomQuant_0.1.2_universal.dmg`,
    bytes: 7264583,
    sha256: '5b811d4fd30c35c8a0411e2cffcf8f710a48515b99b75ddc63272fc945986814',
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
    filename: 'VyomQuant_0.1.2_amd64.AppImage',
    url: `${ASSET_BASE}/VyomQuant_0.1.2_amd64.AppImage`,
    bytes: 83007992,
    sha256: '0b3e2ca2b6d2fa4d3269d4e3d1cb6c7f490ed3893bc4c8128c1d2182c29dca43',
    requires: 'Any x86_64 desktop with glibc 2.35 or newer — built on Ubuntu 22.04',
    firstRun: 'Make it executable first: chmod +x VyomQuant_0.1.2_amd64.AppImage',
  }),
  linuxDeb: Object.freeze({
    platform: 'linuxDeb',
    filename: 'VyomQuant_0.1.2_amd64.deb',
    url: `${ASSET_BASE}/VyomQuant_0.1.2_amd64.deb`,
    bytes: 4735826,
    sha256: 'd43d73b0e02a1da93d110544c596139c4d91ffbb4a461e3824da9264b681cd99',
    requires: 'Debian 12, Ubuntu 22.04 or newer',
    firstRun: 'Install with: sudo apt install ./VyomQuant_0.1.2_amd64.deb',
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
