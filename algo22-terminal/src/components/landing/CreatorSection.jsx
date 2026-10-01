/**
 * CreatorSection — the other side of the marketplace. Anchor `#creators`.
 *
 * WHY THIS SECTION CAN EXIST NOW
 * ------------------------------
 * A creator-economy pitch needs three things to be true, and until recently only the first was:
 * somewhere to publish, a payment that actually settles, and a share that is computed rather than
 * promised. All three are implemented, and `MarketplaceSection.jsx`'s docblock names the files for
 * each. The 90/10 figure quoted below is `OWNER_SHARE_PERCENT = 90` in
 * `backend_app/backend/marketplace/money.py`, where the platform fee is taken as the integer
 * RESIDUAL of the creator's share so the two always sum to the amount charged — and
 * `chk_settlement_conserved` on `marketplace_settlements` makes a row that does not sum
 * unrepresentable.
 *
 * WHAT IS DELIBERATELY ABSENT
 * ---------------------------
 * No earnings figure, no subscriber count, no "creators make ₹X a month". Nothing in this
 * repository produces those numbers. The share is a published commercial term and can be stated;
 * an outcome cannot.
 *
 * The share is also stated as what it is — a share of marketplace subscription revenue, net of the
 * marketplace's own payment, refund and chargeback handling — rather than as "90% of every rupee",
 * which would be a claim about gross payment that `settlement_service` does not make. Reversals are
 * recorded as their own settlement rows and net out of a creator's earnings, which is why the copy
 * says "revenue" and not "sales".
 *
 * THE CTA ROUTES BY ENTITLEMENT, NOT BY HOPE
 * ------------------------------------------
 * "Become a Strategy Creator" goes to signup for a visitor and to the plan catalogue for a signed-in
 * account that cannot publish yet — rather than to a publishing surface that would refuse them. The
 * decision is made from the server's own entitlement answer via `useFeature`, so the link matches
 * what the backend would allow. On the public landing page a visitor is anonymous, the hook reports
 * not-permitted, and signup is the honest destination.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, BadgeCheck, Coins, ListChecks, Wrench } from 'lucide-react'

import { FEATURES } from '../../design/entitlements'
import { useFeature } from '../../components/gates'

/** BUILD → VALIDATE → LIST → EARN. The creator's own path through the product. */
const STAGES = [
  {
    icon: Wrench,
    title: 'Build',
    body: 'Design the strategy on the canvas with the same builder you trade with.',
  },
  {
    icon: BadgeCheck,
    title: 'Validate',
    body:
      'A completed backtest and a real order block are required before a strategy can be listed. Ownership is checked server-side.',
  },
  {
    icon: ListChecks,
    title: 'List',
    body:
      'Submit it for review and set your subscription price. Nothing reaches the public catalogue on submission alone.',
  },
  {
    icon: Coins,
    title: 'Earn',
    body: 'Subscribers pay through the platform. Your share settles to your account.',
  },
]

export default function CreatorSection() {
  // Anonymous on this page, so this reports not-permitted and the CTA goes to signup. The same
  // component rendered inside the app for a Free or Trader account routes to the plan catalogue.
  const { permitted, isReady } = useFeature(FEATURES.MARKETPLACE_PUBLISH)
  const destination = isReady && permitted ? '/app/marketplace' : '/signup'
  const ctaLabel = isReady && permitted ? 'Publish a strategy' : 'Become a Strategy Creator'

  return (
    <section
      id="creators"
      className="border-t border-line-default py-20 lg:py-28"
      aria-label="Strategy creators"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start lg:gap-16">
            <div>
              <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
                Creator economy
              </p>
              <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
                Turn Your Strategy Into Recurring Income
              </h2>
              <p className="mt-5 max-w-2xl text-lg leading-relaxed text-content-secondary">
                Build strategies on VyomQuant, validate them, publish eligible strategies to the
                marketplace and earn from subscribers.
              </p>

              <ol className="mt-10 grid gap-4 sm:grid-cols-2">
                {STAGES.map(({ icon: Icon, title, body }, index) => (
                  <li
                    key={title}
                    className="rounded-xl border border-line-default bg-surface-panel/60 p-5"
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-wash">
                        <Icon className="h-4 w-4 text-brand" aria-hidden="true" />
                      </span>
                      <span className="font-mono text-small font-bold tabular-nums text-content-muted">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                    </div>
                    <p className="mt-3 text-body font-bold text-content-primary">{title}</p>
                    <p className="mt-1 text-body leading-relaxed text-content-secondary">{body}</p>
                  </li>
                ))}
              </ol>
            </div>

            {/* The split. A published commercial term, so it is stated as a figure — unlike an
                earnings outcome, which is not stated at all. */}
            <aside className="rounded-2xl border border-brand/30 bg-brand-wash/60 p-7">
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-brand">
                Revenue share
              </p>

              <div className="mt-6 space-y-5">
                <div>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-body font-semibold text-content-primary">
                      Strategy creator
                    </span>
                    <span className="font-mono text-figure font-black tabular-nums text-content-primary">
                      90%
                    </span>
                  </div>
                  <div
                    className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-inset"
                    role="presentation"
                  >
                    <div className="h-full w-[90%] rounded-full bg-brand" />
                  </div>
                </div>

                <div>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-body font-semibold text-content-secondary">
                      VyomQuant
                    </span>
                    <span className="font-mono text-section font-bold tabular-nums text-content-secondary">
                      10%
                    </span>
                  </div>
                  <div
                    className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-inset"
                    role="presentation"
                  >
                    <div className="h-full w-[10%] rounded-full bg-content-muted" />
                  </div>
                </div>
              </div>

              <p className="mt-6 text-body leading-relaxed text-content-secondary">
                Calculated on marketplace subscription revenue, after the marketplace&rsquo;s
                payment, refund and chargeback handling. Reversals net out of earnings.
              </p>

              <p className="mt-5 rounded-lg border border-line-default bg-surface-panel px-4 py-3 text-body font-semibold text-content-primary">
                Marketplace publishing is available to Pro Quant and Business users.
              </p>

              <Link
                to={destination}
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-5 py-3 text-sm font-bold text-content-inverse transition-colors duration-150 hover:bg-brand-hover"
              >
                {ctaLabel}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </aside>
          </div>
        </div>
      </div>
    </section>
  )
}
