/**
 * ═══════════════════════════════════════════════════════════════════════════
 * PlatformArtifact — one desktop installer's card, driven by its declaration
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * production-launch-hardening task 4.3 withdrew four advertised installers from this card.
 * They are now PUBLISHED, so it renders a real download again — but it renders it from a
 * declaration, and only for a platform that genuinely has one.
 *
 * TWO DECLARATION SOURCES, DELIBERATELY, AND THE ORDER MATTERS
 * -----------------------------------------------------------
 *   1. `design/desktopReleases.js` — the PUBLISHED artifact: url, exact byte count, SHA-256,
 *      requirements, and the first-run step the operating system will demand. Present only
 *      for a platform whose installer actually exists at a URL that serves it.
 *   2. `design/pageFields.js` — the WITHDRAWAL: the label and the reason this app renders in
 *      place of an absent figure.
 *
 * (1) wins when it exists; (2) is the fallback. That ordering is the whole design: the next
 * platform that has no build yet keeps the not-available marker and its reason, with no edit
 * here, exactly as the four did before this release. The label comes from (2) in BOTH cases,
 * so a platform has one rendered name.
 *
 * WHY NO REQUEST IS ISSUED IN EITHER STATE
 * ---------------------------------------
 * Unavailable: `usePanelState(null, { unavailable: reason })` short-circuits before the reader
 * is consulted (Requirement 19.3). Published: the card renders an ANCHOR and nothing more. It
 * does not probe the URL to confirm it is there, does not HEAD it for a size — the size is
 * derived from the declared byte count — and does not click anything for the user. The
 * download begins when a human chooses it, which is the only thing that should start a
 * 78 MiB transfer.
 *
 * WHAT IT WILL NOT RENDER
 * ----------------------
 * In the unavailable state: no href, no `download` attribute, no size, no checksum, and
 * — Requirement 3.7's preservation — no `0`. `ds/Panel` does not evaluate children into the
 * tree in that state at all, so there is no path here that could put a figure beside the
 * marker.
 *
 * In the published state: no size or checksum that is not read from the declaration. There is
 * no literal figure in this file. That is what makes the rendered size and the file unable to
 * disagree, which is the specific defect that put "78.5 MB" beside a 69-byte text file here.
 */

import React from 'react'
import { Download, ShieldAlert } from 'lucide-react'

import {
  artifactForPlatform,
  formatArtifactSize,
  DESKTOP_RELEASE,
} from '../../design/desktopReleases'
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../design/pageFields'
import { PANEL_STATES, usePanelState } from '../../hooks/usePanelState'
// The module, not the `ds/` barrel: this renders on the landing page, and pulling the whole
// barrel into that chunk for one primitive is the bundle regression §13.3 forbids.
import { Panel } from '../ds/Panel'

/**
 * The declared entry for one platform key.
 *
 * `platform` is `DownloadPage.jsx`'s own tab id — `windows`, `macos`, `linuxAppImage`,
 * `linuxDeb` — which is also the `field` in the declaration, so there is one spelling.
 *
 * @param {string} platform
 * @returns {object|undefined}
 */
export const artifactFieldFor = (platform) =>
  PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DOWNLOAD, field: platform })]

/**
 * @param {Object} props
 * @param {string} props.platform One of the four `PAGES.DOWNLOAD` field keys.
 * @param {React.ReactNode} [props.actions] Passed to `Panel`'s header cluster — the platform
 *   glyph. Not a control: `Panel` renders whatever it is given here, and nothing in this file
 *   puts an action in it.
 * @param {2|3|4} [props.level] Heading level for the panel title.
 * @param {string} [props.className]
 */
export function PlatformArtifact({ platform, actions, level = 2, className = '' }) {
  const field = artifactFieldFor(platform)
  const published = artifactForPlatform(platform)

  // Called unconditionally, before any guard below, because it is a hook. A published
  // platform passes no reason, so the hook stays `idle` and still issues nothing — the
  // published branch does not read its state.
  const { state } = usePanelState(null, {
    unavailable: !published && field ? field.reason : null,
  })

  // A platform with no declaration of either kind renders nothing rather than an unexplained
  // card: the declaration is what carries the reason, and `Panel` may not be `unavailable`
  // without one.
  if (!field) return null

  if (published) {
    return (
      <Panel
        title={field.label}
        state={PANEL_STATES.READY}
        actions={actions}
        level={level}
        className={className}
        data-download-platform={platform}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <a
            href={published.url}
            download={published.filename}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-bold text-content-inverse transition-colors duration-150 hover:bg-brand-hover"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {/* The size is DERIVED from the declared byte count, never typed. */}
            Download · {formatArtifactSize(published.bytes)}
          </a>

          <p className="font-mono text-micro leading-relaxed text-content-secondary">
            {published.filename}
          </p>

          <p className="text-body leading-relaxed text-content-secondary">
            {published.requires}
          </p>

          {/* The operating system's own refusal, stated before the user meets it. Driven by
              `DESKTOP_RELEASE.signed` rather than by this file deciding the build is
              unsigned. */}
          {DESKTOP_RELEASE.signed ? null : (
            <p className="flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning-wash px-3 py-2 text-body leading-relaxed text-status-warning">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{published.firstRun}</span>
            </p>
          )}

          <details className="min-w-0">
            <summary className="cursor-pointer font-mono text-micro uppercase tracking-wider text-content-secondary">
              SHA-256
            </summary>
            <code className="mt-2 block break-all font-mono text-micro text-content-secondary">
              {published.sha256}
            </code>
          </details>
        </div>
      </Panel>
    )
  }

  return (
    <Panel
      title={field.label}
      state={state}
      unavailable={{ reason: field.reason }}
      actions={actions}
      level={level}
      className={className}
      data-download-platform={platform}
    />
  )
}

export default PlatformArtifact
