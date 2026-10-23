/**
 * src/components/legal/ReacceptanceGate.jsx — LEGAL-01
 *
 * Asks a signed-in user to accept the documents when their recorded acceptance
 * is not the current version.
 *
 * WHY THIS IS NEEDED FOR THE MECHANISM TO BE REAL
 * ===============================================
 * Signup now records an acceptance. That covers new accounts and nothing else.
 * 182 accounts already exist, NONE of them has a record — because the old
 * checkbox was React state that was never persisted — and the documents have
 * just changed materially. Without this component the acceptance table would be
 * accurate about new users and permanently silent about every existing one,
 * which is a mechanism that exists rather than one that works.
 *
 * The Terms also promise it: "For changes that materially affect your rights or
 * obligations, we will ask you to accept the new version before you continue
 * using the Platform, and we will record that acceptance." This is that.
 *
 * WHAT IT DOES NOT DO
 * ===================
 * NOT A HARD BLOCK ON THE WHOLE APP. It is a modal the user must act on, and
 * declining signs them out rather than trapping them in a loop or silently
 * letting them continue. Someone who will not accept the Terms should be able to
 * leave, read them, and come back — not be locked out of their own data with no
 * route forward.
 *
 * IT FAILS OPEN ON A READ ERROR, DELIBERATELY AND NARROWLY. If
 * /api/legal/acceptance cannot be read, the modal does not appear. The
 * alternative is showing every user a consent wall during a database outage,
 * which converts a backend problem into a product-wide outage. The endpoint
 * itself fails CLOSED in the other direction — it returns 503 rather than
 * reporting "already accepted" — so a failed read is never recorded as consent.
 * The two choices are deliberate and opposite: never fabricate a record, never
 * block the product on an audit read.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../../api'
import { supabase } from '../../supabase'
import {
  DOCUMENTS,
  DOCUMENT_ORDER,
  LEGAL_EFFECTIVE_DATE,
  LEGAL_VERSION,
  SIGNUP_REQUIRED_DOCUMENTS,
} from '../../legal/documents'

export default function ReacceptanceGate() {
  const [needed, setNeeded] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    api.legal
      .getMyAcceptance()
      .then((result) => {
        if (cancelled) return
        setNeeded(Boolean(result?.needs_acceptance))
      })
      .catch(() => {
        // See the header: fail open here, because the endpoint fails closed.
        if (!cancelled) setNeeded(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const accept = useCallback(async () => {
    setSubmitting(true)
    setError('')
    try {
      await api.legal.accept({
        version: LEGAL_VERSION,
        documents: SIGNUP_REQUIRED_DOCUMENTS,
        context: 're_acceptance',
      })
      setNeeded(false)
    } catch (err) {
      // A 409 means THIS BUNDLE IS STALE — the server publishes a version this
      // build does not have — so retrying the same request cannot work and the
      // honest instruction is to reload.
      const code = err?.response?.data?.error
      if (code === 'LEGAL_VERSION_UNKNOWN') {
        setError(
          'This page is out of date and is not showing the current documents. '
          + 'Reload and read them again before accepting.',
        )
      } else {
        setError(
          err?.response?.data?.message
          || 'We could not record your acceptance. Nothing was saved. Please try again.',
        )
      }
    } finally {
      setSubmitting(false)
    }
  }, [])

  const declineAndSignOut = useCallback(async () => {
    try {
      await supabase.auth.signOut()
    } finally {
      sessionStorage.removeItem('token')
      window.location.assign('/')
    }
  }, [])

  if (!needed) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Updated legal documents"
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-xl border border-line-default bg-surface-raised p-6">
        <h2 className="mb-2 text-lg font-bold text-content-primary">
          Please review our updated terms
        </h2>
        <p className="mb-4 text-xs font-mono text-content-secondary">
          Version {LEGAL_VERSION} &middot; effective {LEGAL_EFFECTIVE_DATE}
        </p>

        <p className="mb-4 text-sm leading-relaxed text-content-secondary">
          We have rewritten our legal documents so they accurately describe what
          the platform does, including how automated trading works and what we do
          and do not hold. Please read them and confirm you accept.
        </p>

        <ul className="mb-5 space-y-1.5">
          {DOCUMENT_ORDER.map((id) => (
            <li key={id}>
              <Link
                to={`/legal/${id}`}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-brand underline"
              >
                {DOCUMENTS[id].title}
              </Link>
            </li>
          ))}
        </ul>

        {error && (
          <p className="mb-4 rounded-lg border border-red-500/40 bg-red-500/5 p-3 text-xs text-red-300">
            {error}
          </p>
        )}

        {/* Not pre-ticked. The user must act. */}
        <label className="mb-5 flex cursor-pointer items-start gap-2 text-sm text-content-secondary">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-1"
          />
          <span>
            I have read and accept the Terms of Service, Privacy Policy, Risk
            Disclosure and Refund Policy.
          </span>
        </label>

        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={declineAndSignOut}
            disabled={submitting}
            className="rounded-lg border border-line-default px-4 py-2 text-sm text-content-secondary transition-colors hover:text-content-primary disabled:opacity-50"
          >
            Decline and sign out
          </button>
          <button
            type="button"
            onClick={accept}
            disabled={!agreed || submitting}
            className="rounded-lg border border-brand/40 bg-brand/10 px-4 py-2 text-sm text-content-primary transition-colors hover:bg-brand/20 disabled:opacity-50"
          >
            {submitting ? 'Recording…' : 'Accept and continue'}
          </button>
        </div>
      </div>
    </div>
  )
}
