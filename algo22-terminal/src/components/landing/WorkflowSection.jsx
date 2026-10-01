/**
 * WorkflowSection — the whole path, end to end. Anchor `#workflow-full`.
 *
 * HOW THIS DIFFERS FROM `HowItWorks`
 * ---------------------------------
 * `HowItWorks` (anchor `#workflow`) explains the four steps of getting ONE strategy trading, in
 * prose, with a card per step. This section is the complete seven-stage arc including the two
 * stages that only exist because of the marketplace — PUBLISH and EARN — drawn as a single
 * progression so a reader can see where their plan stops.
 *
 * They are not redundant. One answers "what do I do first?"; this one answers "where does this
 * go?". Keeping them separate is what lets the second half of the arc carry its plan boundary
 * without turning `HowItWorks` into a pricing table.
 *
 * THE PLAN BOUNDARY IS PART OF THE DIAGRAM, NOT A FOOTNOTE
 * -------------------------------------------------------
 * Each stage carries the plan it becomes available on, read from the one place the ladder is
 * published. PUBLISH and EARN are marked Pro Quant, because that is what
 * `backend_app/core/subscription_engine.py` grants `marketplace_publish` and `creator_revenue` on
 * and what `core/subscription_dependencies.py` enforces. A visitor following the arc from BUILD to
 * EARN should reach the boundary in the diagram rather than in a 403.
 *
 * `LIVE` is where real money starts, so it takes `env-live` — the app's own red for live execution
 * (`styles/tokens.css`) — rather than the brand cyan. A visitor learns that hue here and meets it
 * again on their first live deployment badge.
 */

import React from 'react'
import { ArrowRight } from 'lucide-react'

/**
 * BUILD → BACKTEST → PAPER TRADE → AUTOMATE → OPTIMIZE → PUBLISH → EARN.
 *
 * `availableOn` is the plan name as published in the pricing catalogue. `null` means every plan,
 * including Free.
 *
 * Class strings are written out in full rather than composed, because Tailwind v4 scans source as
 * text and an interpolated `text-${hue}` emits nothing — the same silent failure `HowItWorks.jsx`
 * documents.
 */
const STAGES = [
  {
    label: 'Build',
    detail: 'Visual strategy canvas',
    availableOn: null,
    glyph: 'text-brand',
    wash: 'bg-brand-wash',
    ring: 'border-brand/30',
  },
  {
    label: 'Backtest',
    detail: 'VectorBT over real history',
    availableOn: null,
    glyph: 'text-brand',
    wash: 'bg-brand-wash',
    ring: 'border-brand/30',
  },
  {
    label: 'Paper trade',
    detail: 'Live prices, simulated capital',
    availableOn: null,
    glyph: 'text-env-paper',
    wash: 'bg-env-paper-wash',
    ring: 'border-env-paper/30',
  },
  {
    label: 'Automate',
    detail: 'Deploy to a connected exchange',
    availableOn: 'Trader',
    glyph: 'text-env-live',
    wash: 'bg-env-live-wash',
    ring: 'border-env-live/30',
  },
  {
    label: 'Optimize',
    detail: 'Parameter search & ML nodes',
    availableOn: 'Trader',
    glyph: 'text-status-warning',
    wash: 'bg-status-warning-wash',
    ring: 'border-status-warning/30',
  },
  {
    label: 'Publish',
    detail: 'List on the marketplace',
    availableOn: 'Pro Quant',
    glyph: 'text-status-profit',
    wash: 'bg-status-profit-wash',
    ring: 'border-status-profit/30',
  },
  {
    label: 'Earn',
    detail: '90% of subscription revenue',
    availableOn: 'Pro Quant',
    glyph: 'text-status-profit',
    wash: 'bg-status-profit-wash',
    ring: 'border-status-profit/30',
  },
]

export default function WorkflowSection() {
  return (
    <section
      id="workflow-full"
      className="border-t border-line-default py-20 lg:py-24"
      aria-label="From idea to trading system"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              The full arc
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
              From Idea to Trading System
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              VyomQuant combines strategy building, backtesting, automated trading and a strategy
              marketplace into one quantitative trading platform.
            </p>
          </div>

          {/* An ordered list, so the sequence is in the markup and not only in the arrows. The
              arrows are decorative and hidden from assistive technology. */}
          <ol className="mt-14 flex flex-col items-stretch gap-2 lg:flex-row lg:items-stretch">
            {STAGES.map((stage, index) => (
              <React.Fragment key={stage.label}>
                <li
                  className={`flex-1 rounded-xl border bg-surface-panel/60 p-4 ${stage.ring}`}
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${stage.wash}`}
                    >
                      <span className={`font-mono text-micro font-extrabold ${stage.glyph}`}>
                        {index + 1}
                      </span>
                    </span>
                    <p className="text-body font-bold uppercase tracking-wide text-content-primary">
                      {stage.label}
                    </p>
                  </div>
                  <p className="mt-2 text-small leading-relaxed text-content-secondary">
                    {stage.detail}
                  </p>
                  <p className="mt-2.5 font-mono text-micro uppercase tracking-wide text-content-muted">
                    {stage.availableOn === null ? 'All plans' : `${stage.availableOn}+`}
                  </p>
                </li>
                {index < STAGES.length - 1 ? (
                  <li
                    aria-hidden="true"
                    className="flex items-center justify-center lg:w-4"
                  >
                    <ArrowRight className="h-3.5 w-3.5 rotate-90 text-content-muted lg:rotate-0" />
                  </li>
                ) : null}
              </React.Fragment>
            ))}
          </ol>

          <p className="mt-8 text-center text-body text-content-secondary">
            Publishing to the marketplace and earning from subscribers is available to Pro Quant and
            Business.
          </p>
        </div>
      </div>
    </section>
  )
}
