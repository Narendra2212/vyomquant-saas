/**
 * src/components/legal/CookieConsent.jsx — LEGAL-01
 *
 * Asks before loading Google Analytics and Microsoft Clarity.
 *
 * WHY THIS IS NEEDED RATHER THAN NICE TO HAVE
 * ===========================================
 * Those two scripts used to load on first paint, before any interaction, while
 * the Privacy Policy claimed "We do not use third-party tracking or advertising
 * cookies". Clarity records pointer movement, clicks and scrolling — session
 * replay, not page counting.
 *
 * Signups are open globally and the Privacy Policy now carries GDPR/UK-GDPR
 * rights, so non-essential analytics need consent BEFORE they load. Disclosure
 * is not consent, and a banner that loads the scripts while it asks is not
 * consent either.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO
 * ==================================
 * NO PRE-TICKED BOX, and no styling that makes "Accept" the only obvious
 * action. Reject is a real button of equal prominence, because a choice
 * architecture that buries the refusal is not a free choice and regulators have
 * said so repeatedly.
 *
 * NO COOKIE WALL. Dismissing without choosing leaves analytics denied and the
 * product fully usable. Nothing here blocks access.
 *
 * Sentry is NOT gated by this banner. It is error diagnostics relied on under
 * legitimate interest, declared as such in the Privacy Policy, and configured
 * not to capture credentials. Gating crash reporting behind a banner would lose
 * exactly the crashes of visitors who never answer it.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  getConsentState,
  needsConsentDecision,
  setAnalyticsConsent,
} from '../../legal/consent'

export default function CookieConsent() {
  // Read on mount rather than at module scope: localStorage is unavailable
  // during SSR and in some sandboxed contexts, and consent.js already answers
  // "unknown" there. Reading in an effect also keeps the first render identical
  // for every visitor, so the banner cannot flash for someone who already chose.
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    setVisible(needsConsentDecision())
  }, [])

  if (!visible) return null

  const decide = (granted) => {
    setAnalyticsConsent(granted)
    setVisible(false)
  }

  return (
    <div
      role="dialog"
      aria-live="polite"
      aria-label="Cookie and analytics choice"
      className="fixed inset-x-0 bottom-0 z-[100] border-t border-line-default bg-surface-raised/95 p-4 backdrop-blur"
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <p className="text-xs leading-relaxed text-content-secondary">
          We use cookies that are necessary to keep you signed in. With your
          agreement we also use{' '}
          <span className="text-content-primary">Google Analytics</span> and{' '}
          <span className="text-content-primary">Microsoft Clarity</span>, which
          measure how the product is used and record page interactions such as
          clicks and scrolling. They are not loaded unless you agree, and you can
          change your mind at any time.{' '}
          <Link to="/legal/privacy" className="text-brand underline">
            Privacy Policy
          </Link>
        </p>

        {/* Equal prominence, deliberately. See the header. */}
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => decide(false)}
            className="rounded-lg border border-line-default px-4 py-2 text-xs font-mono text-content-secondary transition-colors hover:text-content-primary"
          >
            Reject analytics
          </button>
          <button
            type="button"
            onClick={() => decide(true)}
            className="rounded-lg border border-brand/40 bg-brand/10 px-4 py-2 text-xs font-mono text-content-primary transition-colors hover:bg-brand/20"
          >
            Accept analytics
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * A footer control for changing the decision later.
 *
 * The Privacy Policy promises consent can be withdrawn at any time. A banner
 * that only ever appears once cannot deliver that, so the promise needs a
 * permanent control to be true.
 */
export function CookieSettingsLink({ className = '' }) {
  const [state, setState] = useState('unknown')

  useEffect(() => {
    setState(getConsentState())
  }, [])

  const label =
    state === 'granted'
      ? 'Analytics: on'
      : state === 'denied'
        ? 'Analytics: off'
        : 'Cookie settings'

  return (
    <button
      type="button"
      onClick={() => {
        const next = state !== 'granted'
        setAnalyticsConsent(next)
        setState(next ? 'granted' : 'denied')
        if (!next) {
          // Withdrawal cannot unload a tag the browser already executed; only a
          // reload does. Say so rather than implying a clean revocation — the
          // Privacy Policy carries the same caveat.
           
          window.alert(
            'Analytics turned off. No further data will be sent. Reload the page '
              + 'to fully unload scripts that were already running.',
          )
        }
      }}
      className={className || 'hover:text-content-primary transition-colors'}
    >
      {label}
    </button>
  )
}
