/**
 * MarketplaceSection — the strategy library. Anchor `#marketplace`.
 *
 * WHY THIS IS NEW
 * ---------------
 * `/marketplace` is a PUBLIC route in `src/App.jsx` — no auth guard, no token needed — and the
 * previous landing page did not mention it once. Grepping `components/landing/**` for
 * "marketplace" returned nothing. In retail crypto trading this is normally the primary
 * acquisition surface, because it is the one place a sceptical visitor can see real strategies
 * before creating an account. It was built and then hidden.
 *
 * WHAT IS CLAIMED, AND WHAT IS NOT
 * --------------------------------
 * `.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md` audits this surface
 * component by component. Its findings set the boundary for this copy:
 *
 *   WORKING, so stated plainly:
 *     * public browse, featured, trending, categories and detail (`routers/library.py`)
 *     * publish gated on ownership, a completed backtest and a present action node
 *     * admin moderation before a listing goes live (`admin_moderate_strategy`, and
 *       `get_admin_user` authorisation is classified EXISTS+WORKING)
 *     * ratings and favourites
 *
 *   NOT CLAIMED, because the audit classifies them BROKEN or MISSING:
 *     * paid subscriptions — `create_marketplace_checkout` is EXISTS+BROKEN, and
 *       `renew_subscription` reactivates access with no payment at all
 *     * the 90/10 creator split — the only arithmetic for it queries two columns that do not
 *       exist and returns a fabricated zero through a bare `except`
 *     * a creator payout ledger — MISSING entirely
 *
 * So there is no "earn 90% selling your strategy" anywhere below. Promising a revenue stream
 * whose settlement ledger does not exist would be the most expensive sentence on this page.
 * The one forward-looking line says paid subscriptions are being completed, and says it without
 * attaching a date.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { BadgeCheck, ArrowUpRight, Eye, Star, Upload } from 'lucide-react'

const FOR_BROWSERS = [
  {
    icon: Eye,
    title: 'Look before you sign up',
    body: 'The library is readable without an account. Filter by category, sort by trending, and open a listing to see what it claims.',
  },
  {
    icon: Star,
    title: 'Ratings from people running it',
    body: 'Listings carry ratings and favourites from other users rather than a vendor score.',
  },
]

const FOR_AUTHORS = [
  {
    icon: Upload,
    title: 'A backtest is required to publish',
    body: 'A strategy cannot be listed without a completed backtest and a real order block. Ownership is checked server-side.',
  },
  {
    icon: BadgeCheck,
    title: 'Reviewed before it goes live',
    body: 'Submissions are moderated. Nothing reaches the public catalogue on submission alone.',
  },
]

function Column({ eyebrow, heading, items }) {
  return (
    <div className="rounded-2xl border border-line-default bg-surface-raised p-7 sm:p-8">
      <p className="text-xs font-bold uppercase tracking-[0.16em] text-brand">
        {eyebrow}
      </p>
      <h3 className="mt-3 text-section font-bold text-content-primary">{heading}</h3>
      <ul className="mt-6 space-y-5">
        {items.map(({ icon: Icon, title, body }) => (
          <li key={title} className="flex gap-3.5">
            <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-wash">
              <Icon className="h-4 w-4 text-brand" aria-hidden="true" />
            </span>
            <div>
              <p className="text-body font-bold text-content-primary">{title}</p>
              <p className="mt-1 text-body leading-relaxed text-content-secondary">{body}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function MarketplaceSection() {
  return (
    <section
      id="marketplace"
      className="border-t border-line-default bg-surface-panel/40 py-20 lg:py-28"
      aria-label="Strategy library"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              Strategy library
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Start from someone else&rsquo;s work
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Published strategies are browsable without an account. Open one, read what it does,
              and clone it into your own workspace to study or adapt.
            </p>
          </div>

          <div className="mt-14 grid gap-5 lg:grid-cols-2">
            <Column
              eyebrow="If you are looking"
              heading="Browse the catalogue"
              items={FOR_BROWSERS}
            />
            <Column
              eyebrow="If you are publishing"
              heading="List your own strategy"
              items={FOR_AUTHORS}
            />
          </div>

          <div className="mt-10 flex flex-col items-center gap-4">
            <Link
              to="/marketplace"
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-brand/40 bg-brand-wash px-7 py-3.5 text-sm font-bold text-brand transition-colors duration-150 hover:bg-brand/15"
            >
              Open the strategy library
              <ArrowUpRight className="h-4 w-4" />
            </Link>
            {/* The one forward-looking sentence on this section, deliberately undated. */}
            <p className="max-w-xl text-center font-mono text-micro leading-relaxed text-content-secondary">
              Paid strategy subscriptions and creator payouts are being completed ahead of general
              availability. Browsing, cloning and publishing work today.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
