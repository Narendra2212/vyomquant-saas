/**
 * ═══════════════════════════════════════════════════════════════════════════
 * PlatformArtifact — one desktop installer's card, driven by its declaration
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * production-launch-hardening task 4.3. Requirements 1.30, 2.30, 3.6, 3.7 (preservation).
 *
 * THIS IS NOT A NEW PRIMITIVE
 * --------------------------
 * It renders no markup of its own, chooses no colour, and authors no copy. It is the same
 * three lines every rebuilt page writes at its own call sites — look up the declared entry,
 * drive `usePanelState` with the declared reason, hand the state to `ds/Panel` — collected in
 * one module because TWO surfaces show these four artifacts (`/download` and the landing
 * page's download section) and two copies of the lookup would be two things to keep in step.
 *
 * WHY NO REQUEST IS ISSUED
 * -----------------------
 * `usePanelState(null, { unavailable: reason })`: a non-empty reason short-circuits to
 * `unavailable` before `enabled` is even consulted, and the reader is never called
 * (Requirement 19.3). The reader argument is `null` on purpose rather than a probe of the
 * artifact URL — a HEAD request whose only possible answer today is 403 would be a request
 * issued to learn something already declared, and holding the URL here is how a link creeps
 * back onto the surface.
 *
 * WHAT IT WILL NOT RENDER
 * ----------------------
 * No href, no `download` attribute, no size, no checksum, and — Requirement 3.7's
 * preservation — no `0`. `ds/Panel`'s `unavailable` state renders the marker and the reason
 * and nothing else; children are not evaluated into the tree in that state at all, so there is
 * no code path here that could put a figure or a link beside the marker.
 */

import React from 'react'

import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../design/pageFields'
import { usePanelState } from '../../hooks/usePanelState'
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
 *   glyph on the landing section. Not a control: `Panel` renders whatever it is given here,
 *   and nothing in this file puts an action in it.
 * @param {2|3} [props.level] Heading level for the panel title.
 * @param {string} [props.className]
 */
export function PlatformArtifact({ platform, actions, level = 2, className = '' }) {
  const field = artifactFieldFor(platform)
  // Called unconditionally, before the guard below, because it is a hook. With no declaration
  // there is no reason, `usePanelState` stays `idle` and issues nothing either way.
  const { state } = usePanelState(null, { unavailable: field ? field.reason : null })

  // A platform with no declaration renders nothing rather than an unexplained card: the
  // declaration is what carries the reason, and `Panel` may not be `unavailable` without one.
  if (!field) return null

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
