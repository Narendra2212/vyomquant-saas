/**
 * SecuritySection — the controls that protect keys and capital. Anchor `#security`.
 *
 * THE BADGE IS GONE
 * -----------------
 * This section used to open with a pill reading "Enterprise Security". There is no SOC 2, no
 * ISO 27001 and no penetration-test report anywhere in this repository, and "enterprise
 * security" as a standalone badge is read as an attestation. The six controls it listed are
 * real — that was never the problem. The problem was framing implemented behaviour as if a
 * third party had signed off on it.
 *
 * So the heading now says what these are: controls that are implemented, described by their
 * mechanism, with nothing implying external certification.
 *
 * SIX CARDS BECAME FOUR
 * ---------------------
 * "Authenticated Encryption at Rest" and "Secure API Key Storage" were the same control
 * described twice, and "Secure Authentication" (password hashing, cookie policy) is table
 * stakes that earns no space on a landing page. Merged and dropped respectively.
 *
 * "Protected Trading Infrastructure — execution engines run in isolated, protected VPC
 * environments" is NOT claimed here, because it is not a universal property of the platform. The
 * closest real line item is the dedicated execution environment on the Business plan
 * (`Feature.DEDICATED_EXECUTION` in `backend_app/core/subscription_engine.py`); stating isolation
 * as something every account gets would contradict the plan that sells it.
 *
 * (The tier that carried this before the pricing rework was called "Institutional". The plan it
 * became is "Business" — same ₹2,499 position, same stored identifier `enterprise`. The name is
 * recorded here only so a reader of the old copy can follow the change.)
 *
 * Sources: `backend_app/routers/security.py`, `core/tenant.py`, `core/tenant_middleware.py`,
 * `backend/tenant_rls_validator.py`, the RLS policies in
 * `migrations/006_reconcile_production_database.sql`, and `core/audit_trail.py`.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { FileSearch, KeyRound, Layers, Users } from 'lucide-react'

const CONTROLS = [
  {
    icon: KeyRound,
    title: 'Exchange keys are encrypted, never plaintext',
    body:
      'Credentials are encrypted before storage with an integrity check on every record, so tampering is detectable. They are injected into the execution environment at runtime and never travel to the browser.',
  },
  {
    icon: Layers,
    title: 'Per-tenant isolation at the database',
    body:
      'Row-level security policies scope your strategies, orders and credentials to your account in the database itself, rather than relying on application code to remember to filter.',
  },
  {
    icon: Users,
    title: 'Role-based access control',
    body:
      'Roles determine what a caller can reach. Administrative review and moderation actions require an explicitly granted role, checked server-side on every request.',
  },
  {
    icon: FileSearch,
    title: 'Audit trail on orders and strategy edits',
    body:
      'Order lifecycle events and strategy modifications are written to an audit log, so a fill or a configuration change can be reconstructed after the fact.',
  },
]

export default function SecuritySection() {
  return (
    <section
      id="security"
      className="border-t border-line-default bg-surface-canvas py-20 lg:py-28"
      aria-label="Security controls"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              Security
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Controls that are implemented, described plainly
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Four mechanisms, named so you can ask us about them. We hold no third-party security
              certification yet, and we would rather say that than imply one.
            </p>
          </div>

          <div className="mt-14 grid gap-5 sm:grid-cols-2">
            {CONTROLS.map(({ icon: Icon, title, body }) => (
              <article
                key={title}
                className="rounded-2xl border border-line-default bg-surface-raised p-7 sm:p-8 transition-colors duration-150 hover:border-brand/40"
              >
                <span className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line-strong bg-surface-raised">
                  <Icon className="h-4.5 w-4.5 text-brand" aria-hidden="true" />
                </span>
                <h3 className="text-title font-bold text-content-primary">{title}</h3>
                <p className="mt-2 text-body leading-relaxed text-content-secondary">{body}</p>
              </article>
            ))}
          </div>

          <p className="mt-8 text-center text-body text-content-secondary">
            Read the full{' '}
            <Link
              to="/legal/risk"
              className="font-semibold text-brand underline underline-offset-2 hover:text-brand-hover"
            >
              risk disclosure
            </Link>{' '}
            before connecting an exchange.
          </p>
        </div>
      </div>
    </section>
  )
}
