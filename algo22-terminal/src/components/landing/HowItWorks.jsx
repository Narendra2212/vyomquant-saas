/**
 * HowItWorks — the four-step path from idea to live execution. Anchor `#workflow`.
 *
 * Retained from the previous page, which got this section right: it was the one place that
 * described a sequence rather than listing adjectives. Three content changes on the rewrite.
 *
 * 1. The order now matches what the product enforces. `Deployment_Gate` in
 *    `.kiro/specs/trading-lifecycle-integration/` makes paper the default mode and live an
 *    explicit promotion, so paper trading is its own step rather than a clause tacked onto
 *    "Forward Test & Deploy".
 * 2. "Execute tick-level backtests across multi-year historical datasets in seconds" lost the
 *    speed claim — nothing in this repository measures backtest throughput. What replaces it is
 *    the thing that IS implemented and is rarer: the Monte Carlo, walk-forward and sensitivity
 *    passes in `backend_app/backend/optimization_engine.py`.
 * 3. Its one `text-[11px]` became the `text-small` token, clearing this file's entry in
 *    `absolute-font-sizes.budget.js`.
 *
 * ═══ THE FOUR-COLOUR STAGE LANGUAGE ═══
 *
 * Each step owns a hue, and the SAME four hues repeat on `TrustSection`'s four pillars in the
 * same order. That is the point: a reader who sees amber on "Backtest and stress it" here meets
 * amber again on "Research that argues back" below, and the two are the same idea seen twice.
 *
 *   cyan   `brand`          build — the canvas, the thing you touch first
 *   amber  `status-warning` research — the stage whose job is to find what is wrong
 *   indigo `env-paper`      paper — and this is not decorative: `tokens.css` declares
 *                           `--color-env-paper` as THE environment colour for simulated
 *                           execution, used by the app's own environment badges. The landing
 *                           page borrowing it means the indigo a visitor learns here is the
 *                           indigo they will see on their first paper deployment.
 *   teal   `status-profit`  live — the same hue the app uses for a connected, running state.
 *
 * The page previously rendered every icon, eyebrow and chip in one cyan, which is why four
 * distinct sections read as one undifferentiated wall.
 *
 * Class strings are written out in full in `STEPS` rather than composed — Tailwind v4 scans
 * source as text, so an interpolated `text-${hue}` would emit nothing and the icon would
 * inherit. That is the same silent failure `tokens.css`'s compatibility-alias block exists to
 * prevent, arriving by a different route.
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
    icon_: 'text-brand',
    wash: 'bg-brand-wash',
    ring: 'border-brand/30',
    hover: 'hover:border-brand/40',
  },
  {
    icon: LineChart,
    title: 'Backtest and stress it',
    description:
      'Run the strategy over real historical data with VectorBT, then put it through Monte Carlo, walk-forward and sensitivity passes to see where it breaks.',
    icon_: 'text-status-warning',
    wash: 'bg-status-warning-wash',
    ring: 'border-status-warning/30',
    hover: 'hover:border-status-warning/40',
  },
  {
    icon: ShieldCheck,
    title: 'Forward-test on paper',
    description:
      'Deploy against live market data with simulated capital. Real prices, real order flow, no money at risk — and this is the default, not an opt-in.',
    icon_: 'text-env-paper',
    wash: 'bg-env-paper-wash',
    ring: 'border-env-paper/30',
    hover: 'hover:border-env-paper/40',
  },
  {
    icon: Rocket,
    title: 'Go live when ready',
    description:
      'Connect your own exchange keys and promote the strategy. Position caps and drawdown stops stay armed the whole time.',
    icon_: 'text-status-profit',
    wash: 'bg-status-profit-wash',
    ring: 'border-status-profit/30',
    hover: 'hover:border-status-profit/40',
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
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              Workflow
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Four steps, and you cannot skip the third
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Most losses in automated trading come from deploying an untested idea. The platform
              is ordered so that paper trading sits between your backtest and your capital.
            </p>
          </div>

          <ol className="mx-auto mt-16 grid max-w-6xl gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map(({ icon: Icon, title, description, icon_, wash, ring, hover }, index) => (
              <li
                key={title}
                className={`group relative rounded-2xl border border-line-default bg-surface-raised p-6 transition-colors duration-150 ${hover}`}
              >
                {/* Connector between cards, widest layout only. Decorative: the ordered list
                    already carries the sequence for a screen reader. */}
                {index < STEPS.length - 1 && (
                  <span
                    className="absolute right-0 top-12 hidden h-px w-5 translate-x-full bg-line-strong lg:block"
                    aria-hidden="true"
                  />
                )}

                <div className="mb-6 flex items-center justify-between">
                  <span
                    className={`inline-flex h-12 w-12 items-center justify-center rounded-xl border ${ring} ${wash}`}
                  >
                    <Icon className={`h-5 w-5 ${icon_}`} aria-hidden="true" />
                  </span>
                  <span className="font-mono text-small font-bold tracking-wider text-content-muted">
                    0{index + 1}
                  </span>
                </div>

                <h3 className="text-lg font-bold leading-snug text-content-primary">{title}</h3>
                <p className="mt-2.5 text-sm leading-relaxed text-content-secondary">
                  {description}
                </p>
              </li>
            ))}
          </ol>

          <div className="mt-14 text-center">
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
