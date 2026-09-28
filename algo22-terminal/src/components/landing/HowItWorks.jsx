/**
 * HowItWorks — the four-step path from idea to live execution.
 *
 * Retained from the previous page, which got this section right: it was the one place that
 * described a sequence rather than listing adjectives. Three changes.
 *
 * 1. The order now matches what the product actually enforces. `Deployment_Gate` in
 *    `.kiro/specs/trading-lifecycle-integration/` makes paper the default mode and live an
 *    explicit promotion, so paper trading is its own step rather than a clause tacked onto
 *    "Forward Test & Deploy".
 * 2. "Execute tick-level backtests across multi-year historical datasets in seconds" lost the
 *    speed claim. Nothing in this repository measures backtest throughput, so "in seconds" was
 *    an unmeasured number. What replaces it is the thing that IS implemented and is rarer:
 *    the Monte Carlo, walk-forward and sensitivity passes in
 *    `backend_app/backend/optimization_engine.py`.
 * 3. Its one `text-[11px]` is now the `text-small` token, taking this file's
 *    `absolute-font-sizes.budget.js` entry from 1 to zero.
 *
 * The connector rules are `aria-hidden`: the ordered list already carries the sequence for a
 * screen reader, and a decorative line announcing itself would be noise.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, GitBranch, LineChart, Rocket, ShieldCheck } from 'lucide-react'

const STEPS = [
  {
    icon: GitBranch,
    title: 'Build the logic',
    description:
      'Drag market feeds, indicators, conditions and order blocks onto a canvas. Ports are typed, so an invalid connection is refused as you draw it.',
  },
  {
    icon: LineChart,
    title: 'Backtest and stress it',
    description:
      'Run the strategy over real historical data with VectorBT, then put it through Monte Carlo, walk-forward and sensitivity passes to see where it breaks.',
  },
  {
    icon: ShieldCheck,
    title: 'Forward-test on paper',
    description:
      'Deploy against live market data with simulated capital. Real prices, real order flow, no money at risk — and this is the default, not an opt-in.',
  },
  {
    icon: Rocket,
    title: 'Go live when ready',
    description:
      'Connect your own exchange keys and promote the strategy. Position caps and drawdown stops stay armed the whole time.',
  },
]

export default function HowItWorks() {
  return (
    <section
      id="workflow"
      className="border-t border-line-default bg-surface-canvas py-20 lg:py-28"
      aria-label="How VyomQuant works"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-3 font-mono text-micro font-semibold uppercase tracking-[0.2em] text-brand">
              Workflow
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
              Four steps, and you cannot skip the third
            </h2>
            <p className="mt-4 text-lg text-content-secondary">
              Most losses in automated trading come from deploying an untested idea. The platform
              is ordered so that paper trading sits between your backtest and your capital.
            </p>
          </div>

          <ol className="relative mx-auto mt-14 grid max-w-6xl gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map(({ icon: Icon, title, description }, index) => (
              <li
                key={title}
                className="group relative rounded-xl border border-line-default bg-surface-panel p-6 transition-colors duration-150 hover:border-brand/40"
              >
                {/* Connector between cards on the widest layout only. */}
                {index < STEPS.length - 1 && (
                  <span
                    className="absolute right-0 top-11 hidden h-px w-5 translate-x-full bg-line-strong lg:block"
                    aria-hidden="true"
                  />
                )}

                <div className="mb-5 flex items-center justify-between">
                  <span className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-line-strong bg-surface-raised transition-colors duration-150 group-hover:border-brand/40">
                    <Icon className="h-5 w-5 text-brand" aria-hidden="true" />
                  </span>
                  <span className="font-mono text-small font-bold tracking-wider text-content-muted">
                    STEP {index + 1}
                  </span>
                </div>

                <h3 className="text-section font-bold text-content-primary">{title}</h3>
                <p className="mt-2 text-body leading-relaxed text-content-secondary">
                  {description}
                </p>
              </li>
            ))}
          </ol>

          <div className="mt-12 text-center">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand px-8 py-4 text-sm font-bold text-content-inverse shadow-raised transition-colors duration-150 hover:bg-brand-hover"
            >
              Start at step one
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
