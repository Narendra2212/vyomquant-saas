/**
 * src/components/legal/LegalPage.jsx — LEGAL-01
 *
 * Renders a legal document. THE TEXT IS NOT HERE.
 *
 * It lives in `src/legal/documents.js`, which is the single source of truth, and
 * this file is presentation only. Before this change the prose was a
 * `legalContent` object literal in this file AND a second, different `DOCUMENTS`
 * object in `src/pages/LegalPage.jsx`, both routed and both served. They
 * contradicted each other on refunds: one promised pro-rated service credits,
 * the other said all purchases were final. Which page a customer happened to
 * open decided whether they were owed money.
 *
 * `src/pages/LegalPage.jsx` has been deleted and `/legal` now redirects to
 * `/legal/terms`, so there is one body of text reachable by one set of routes.
 */

import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

import {
  DOCUMENTS,
  DOCUMENT_ORDER,
  LEGAL_EFFECTIVE_DATE,
  LEGAL_VERSION,
  REVIEW_NOTES,
} from '../../legal/documents'

export default function LegalPage({ type = 'terms' }) {
  const content = DOCUMENTS[type] || DOCUMENTS.terms

  return (
    <div className="relative min-h-screen bg-surface-canvas">
      {/* Background */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-brand/5 rounded-full blur-[120px]" />
      </div>

      <div className="relative z-10 max-w-3xl mx-auto px-6 py-24">
        {/* Back link */}
        <Link
          to="/"
          className="inline-flex items-center gap-2 text-sm text-content-secondary hover:text-content-primary transition-colors mb-10"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Home
        </Link>

        {/* Header */}
        <div className="mb-10">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-lg bg-brand flex items-center justify-center shadow-[0_0_15px_rgba(0,212,255,0.2)]">
              <span className="text-content-inverse font-bold text-xs font-mono">VQ</span>
            </div>
            <span className="text-sm font-mono text-content-secondary">VyomQuant Legal</span>
          </div>
          <h1 className="text-3xl sm:text-4xl font-black text-content-primary mt-4 mb-2">
            {content.title}
          </h1>
          <p className="text-xs font-mono text-content-secondary">
            Version {LEGAL_VERSION} &middot; effective {LEGAL_EFFECTIVE_DATE}
          </p>
        </div>

        {/* Document navigation, so a reader can move between all four rather
            than returning to the footer. */}
        <nav className="mb-10 flex flex-wrap gap-2" aria-label="Legal documents">
          {DOCUMENT_ORDER.map((id) => (
            <Link
              key={id}
              to={`/legal/${id}`}
              aria-current={id === content.id ? 'page' : undefined}
              className={
                id === content.id
                  ? 'rounded-lg border border-brand/40 bg-brand/10 px-3 py-1.5 text-xs font-mono text-content-primary'
                  : 'rounded-lg border border-line-default px-3 py-1.5 text-xs font-mono text-content-secondary hover:text-content-primary transition-colors'
              }
            >
              {DOCUMENTS[id].title}
            </Link>
          ))}
        </nav>

        {/* Legal content. `white-space: pre-line` so the paragraph breaks and
            bullet lists authored in documents.js survive rendering — the text is
            written for a human to read, not reflowed into one block. */}
        <div className="space-y-8">
          {content.sections.map((section, i) => (
            <div key={section.heading || i} className="card-surface p-6">
              <h2 className="text-base font-bold text-content-primary mb-3">
                {section.heading}
              </h2>
              <p className="whitespace-pre-line text-sm text-content-secondary leading-relaxed">
                {section.body}
              </p>
            </div>
          ))}
        </div>

        {/* REVIEW NOTES, NON-PRODUCTION ONLY.
            These documents are accurate and complete but NOT counsel-reviewed.
            Showing the outstanding clauses in dev and preview builds keeps that
            visible to whoever is working on the product, without putting an
            internal to-do list in front of a customer. */}
        {import.meta.env.MODE !== 'production' && REVIEW_NOTES.length > 0 && (
          <div className="mt-12 rounded-lg border border-amber-500/40 bg-amber-500/5 p-6">
            <h2 className="mb-3 text-sm font-bold text-amber-400">
              Pending legal review ({REVIEW_NOTES.length}) &mdash; not shown in production
            </h2>
            <ul className="list-disc space-y-2 pl-5 text-xs text-content-secondary">
              {REVIEW_NOTES.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Footer nav */}
        <div className="mt-12 pt-8 border-t border-line-default">
          <div className="flex flex-wrap gap-4 text-xs font-mono text-content-secondary">
            {DOCUMENT_ORDER.map((id) => (
              <Link
                key={id}
                to={`/legal/${id}`}
                className="hover:text-content-primary transition-colors"
              >
                {DOCUMENTS[id].title}
              </Link>
            ))}
          </div>
          <p className="text-xs text-content-secondary mt-4">
            © {new Date().getFullYear()} VyomQuant. Not financial advice.
          </p>
        </div>
      </div>
    </div>
  )
}
