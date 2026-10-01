/**
 * MarketplaceSection — discover and subscribe. Anchor `#marketplace`.
 *
 * WHY THE CAUTION IN THE PREVIOUS VERSION OF THIS FILE NO LONGER APPLIES
 * ---------------------------------------------------------------------
 * This section used to end with "Paid strategy subscriptions and creator payouts are being
 * completed ahead of general availability", and it was right to: at the time,
 * `.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md` classified
 * `create_marketplace_checkout` as EXISTS+BROKEN, `renew_subscription` reactivated access with no
 * payment, and the 90/10 arithmetic queried two columns that did not exist and returned a
 * fabricated zero through a bare `except`. Advertising a revenue stream with no settlement ledger
 * behind it would have been the most expensive sentence on the page.
 *
 * All three are now implemented, and the claim is made because the code is there to point at:
 *
 *   * **Payment.** `POST /api/library/{id}/checkout` delegates to
 *     `backend/marketplace/checkout_service.create_checkout`, which prices from the Listing's
 *     integer `price_minor`, writes a `pending` row and hands back a real provider session.
 *   * **Settlement.** `backend/marketplace/settlement_service.settle` is the single writer of
 *     `marketplace_settlements` (migration `008_marketplace_settlement.sql`) and the only path
 *     permitted to set `library_subscriptions.status = 'active'` — enforced by
 *     `trg_subscription_transition_guard` in the database rather than by convention.
 *   * **The split.** `backend/marketplace/money.split_ninety_ten` computes the creator's 90% with
 *     integer arithmetic and takes the platform fee as the RESIDUAL, so the two always sum to the
 *     amount charged. `chk_settlement_conserved` makes a row that does not sum unrepresentable.
 *
 * WHAT IS STILL NOT CLAIMED
 * -------------------------
 * No subscriber counts, no revenue figures, no "top creators earn ₹X". Nothing in this repository
 * produces those numbers, and a fabricated one on a trading product's marketing page is the
 * easiest lie for a prospect to check — the same reasoning that removed the invented `Sharpe: 2.1`
 * cluster from `Hero.jsx`.
 *
 * THE PLAN BOUNDARY IS STATED HERE, NOT ONLY IN PRICING
 * ----------------------------------------------------
 * Subscribing starts with Trader and publishing starts with Pro Quant. Both are said on this
 * section rather than left to the pricing table, because a visitor who reads "subscribe to
 * strategies" and signs up on Free would otherwise discover the boundary from a 403.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Compass, Rocket, ShoppingCart } from 'lucide-react'

/**
 * Discover → Subscribe → Deploy.
 *
 * The numbering is rendered from the index rather than written into the copy, so a step cannot be
 * mislabelled when one is inserted.
 */
const STEPS = [
  {
    icon: Compass,
    title: 'Discover',
    body:
      'Browse the public catalogue without an account. Filter by category, sort by trending, and open a listing to see its backtest evidence, risk metrics and the conditions it trades.',
    glyph: 'text-brand',
    wash: 'bg-brand-wash',
  },
  {
    icon: ShoppingCart,
    title: 'Subscribe',
    body:
      'Subscribe to the strategies that fit your approach. Pricing is set by the creator and charged through the same gateway as your plan. Marketplace subscriptions start with Trader.',
    glyph: 'text-status-warning',
    wash: 'bg-status-warning-wash',
  },
  {
    icon: Rocket,
    title: 'Deploy',
    body:
      'Run a subscribed strategy in your own trading environment, paper first. You monitor it and your own risk controls apply — the creator never touches your exchange keys.',
    glyph: 'text-env-paper',
    wash: 'bg-env-paper-wash',
  },
]

export default function MarketplaceSection() {
  return (
    <section
      id="marketplace"
      className="border-t border-line-default bg-surface-panel/40 py-20 lg:py-28"
      aria-label="VyomQuant Strategy Marketplace"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              VyomQuant Strategy Marketplace
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Don&rsquo;t Build Everything From Scratch.
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Discover systematic strategies created by other VyomQuant users, subscribe to the ones
              that fit your approach, and deploy them through your connected trading environment.
            </p>
          </div>

          <ol className="mt-14 grid gap-5 md:grid-cols-3">
            {STEPS.map(({ icon: Icon, title, body, glyph, wash }, index) => (
              <li
                key={title}
                className="rounded-2xl border border-line-default bg-surface-raised p-7"
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${wash}`}
                  >
                    <Icon className={`h-4 w-4 ${glyph}`} aria-hidden="true" />
                  </span>
                  <span className="font-mono text-small font-bold tabular-nums text-content-muted">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                </div>
                <h3 className="mt-4 text-section font-bold text-content-primary">{title}</h3>
                <p className="mt-2 text-body leading-relaxed text-content-secondary">{body}</p>
              </li>
            ))}
          </ol>

          <div className="mt-10 flex justify-center">
            <Link
              to="/marketplace"
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-brand/40 bg-brand-wash px-7 py-3.5 text-sm font-bold text-brand transition-colors duration-150 hover:bg-brand/15"
            >
              Explore Marketplace
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
