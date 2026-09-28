/**
 * Hero — the landing page's first screen.
 *
 * WHAT THIS REPLACED, AND WHY
 * ---------------------------
 * The previous hero carried SIX competing calls to action (a primary signup, an anchor, and
 * three platform cards) and a floating metric cluster reading `Sharpe: 2.1`, `Win Rate: 67.4%`,
 * `Max DD: -8.2%`.
 *
 * Both are gone.
 *
 * 1. THE FIGURES WERE INVENTED. Nothing in this repository produced them. They sat under a
 *    9px "Illustrative demo values" caption, which is not a defence: a performance figure on a
 *    trading product's first screen is read as a claim about the product, and `Footer.jsx`
 *    states in the same page that VyomQuant is not a registered investment adviser. The
 *    product's real differentiator is that it REFUSES to flatter a strategy — VectorBT plus
 *    the Monte Carlo / walk-forward / sensitivity passes in
 *    `backend_app/backend/optimization_engine.py` — so advertising a fabricated Sharpe
 *    undercut the exact thing worth selling.
 *
 * 2. THREE OF THE SIX CTAs LED NOWHERE. The Windows and macOS cards pointed at
 *    `/download#windows` and `/download#macos`, where `PlatformArtifact` renders an
 *    `unavailable` panel — the installers are not published. `DownloadSection.jsx` says so
 *    in plain language further down the same page, so the hero was contradicting its own page.
 *    Desktop is a real roadmap item and is announced in `DownloadSection`, which is the one
 *    place on this page that can state its status accurately.
 *
 * WHAT THE FRAME SHOWS INSTEAD
 * ----------------------------
 * The strategy pipeline's STRUCTURE — feed, indicator, logic, risk gate, order — which is a
 * true statement about `backend_app/backend/strategy_dag/schema.py`'s node categories and
 * typed ports. Structure can be shown honestly without a backtest result; a P&L curve cannot.
 * No figure in this file is a performance number.
 *
 * TOKENS, NOT LITERALS
 * --------------------
 * Canonical `styles/tokens.css` names throughout (`surface-*`, `content-*`, `brand`,
 * `status-*`), not the deprecated compat aliases the old landing surface used. That block is
 * scheduled for removal once these components stop reading it, so a rewrite is the moment to
 * stop. This file declares zero colour literals and zero absolute font sizes; its entries in
 * `no-colour-literals.budget.js` (3) and `absolute-font-sizes.budget.js` (2) go to zero.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, ShieldCheck, Store } from 'lucide-react'

/** One node in the illustrative pipeline frame. Structure only — never a result. */
const PIPELINE = [
  {
    kind: 'Market feed',
    name: 'BTC/USDT · 15m',
    detail: 'CCXT.pro websocket',
    accent: 'border-l-brand',
  },
  {
    kind: 'Indicator',
    name: 'RSI(14) · EMA(200)',
    detail: 'Typed scalar series',
    accent: 'border-l-status-warning',
  },
  {
    kind: 'Logic gate',
    name: 'Crossover AND threshold',
    detail: 'Boolean series out',
    accent: 'border-l-brand',
  },
  {
    kind: 'Risk gate',
    name: 'Position cap · drawdown stop',
    detail: 'Blocks before sizing',
    accent: 'border-l-status-warning',
  },
  {
    kind: 'Order intent',
    name: 'Limit buy · trailing stop',
    detail: 'Paper by default',
    accent: 'border-l-env-paper',
  },
]

export default function Hero() {
  return (
    <section
      className="relative isolate overflow-hidden bg-surface-canvas pt-32 pb-20 sm:pt-40 sm:pb-24 lg:pt-44 lg:pb-28"
      aria-label="VyomQuant overview"
    >
      {/* Ambient depth. Two washes built from the brand token's own opacity modifiers, so
          there is no hand-mixed colour anywhere in this file. */}
      <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
        <div className="absolute -top-40 left-1/2 h-[42rem] w-[72rem] -translate-x-1/2 rounded-full bg-brand/8 blur-3xl" />
        <div className="absolute bottom-0 right-0 h-[28rem] w-[36rem] rounded-full bg-status-profit/5 blur-3xl" />
        <div className="absolute inset-x-0 bottom-0 h-px bg-line-default" />
      </div>

      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto flex max-w-3xl flex-col items-center text-center">
            {/* Stage badge. `Early access` is a fact — there is a `/admin/waitlist` surface
                and version 0.1.1 — not a growth claim. */}
            <span className="mb-8 inline-flex items-center gap-2 rounded-full border border-brand/30 bg-brand-wash px-4 py-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-brand" />
              <span className="font-mono text-micro font-semibold uppercase tracking-[0.18em] text-brand">
                Early access · Paper trading by default
              </span>
            </span>

            <h1 className="text-4xl font-black leading-[1.05] tracking-tight text-content-primary sm:text-5xl lg:text-6xl">
              Build crypto trading bots
              <br className="hidden sm:block" />{' '}
              <span className="text-gradient-cyan">without writing code</span>
            </h1>

            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-content-secondary sm:text-xl">
              Design a strategy on a visual canvas, backtest it against real historical data,
              then stress-test it with Monte Carlo and walk-forward passes before a single rupee
              is at stake. Connect an exchange only once you are convinced.
            </p>

            {/* ONE primary action. The secondary is a read-only route that needs no account,
                which is the lowest-friction way to let a visitor judge the product. */}
            <div className="mt-10 flex w-full flex-col items-center gap-3 sm:w-auto sm:flex-row">
              <Link
                to="/signup"
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-8 py-4 text-sm font-bold text-content-inverse shadow-raised transition-colors duration-150 hover:bg-brand-hover sm:w-auto"
              >
                Start free — no card required
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link
                to="/marketplace"
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-line-strong bg-surface-panel px-6 py-4 text-sm font-semibold text-content-primary transition-colors duration-150 hover:border-brand/40 hover:bg-surface-raised sm:w-auto"
              >
                <Store className="h-4 w-4 text-brand" />
                Browse strategies
              </Link>
            </div>

            <ul className="mt-8 flex flex-wrap items-center justify-center gap-x-7 gap-y-2.5">
              {[
                'Paper trading is the default mode',
                'Your exchange keys, encrypted',
                'Cancel any time',
              ].map((item) => (
                <li key={item} className="flex items-center gap-2 text-body text-content-secondary">
                  <Check className="h-4 w-4 shrink-0 text-status-profit" />
                  {item}
                </li>
              ))}
            </ul>
          </div>

          {/* ── Pipeline frame ──────────────────────────────────────────────
              Deliberately NOT captioned as a screenshot. It is a diagram of the
              node graph a strategy compiles to, and it says so in its own chrome. */}
          <div className="mx-auto mt-16 max-w-5xl lg:mt-20">
            <div className="overflow-hidden rounded-2xl border border-line-default bg-surface-panel shadow-overlay">
              <div className="flex items-center gap-3 border-b border-line-default bg-surface-raised px-5 py-3">
                <div className="flex gap-1.5" aria-hidden="true">
                  <span className="h-2.5 w-2.5 rounded-full bg-status-loss/70" />
                  <span className="h-2.5 w-2.5 rounded-full bg-status-warning/70" />
                  <span className="h-2.5 w-2.5 rounded-full bg-status-profit/70" />
                </div>
                <span className="font-mono text-micro text-content-secondary">
                  vyomquant · strategy graph
                </span>
                <span className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-env-paper/30 bg-env-paper-wash px-2 py-0.5">
                  <ShieldCheck className="h-3 w-3 text-env-paper" />
                  <span className="font-mono text-micro font-semibold uppercase tracking-wider text-env-paper">
                    Paper
                  </span>
                </span>
              </div>

              <div className="p-5 sm:p-7">
                <p className="mb-5 font-mono text-micro uppercase tracking-[0.18em] text-content-secondary">
                  Every strategy compiles to a typed graph — each stage validated before it runs
                </p>

                <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {PIPELINE.map((node, index) => (
                    <li
                      key={node.kind}
                      className={`rounded-lg border border-line-default bg-surface-raised p-4 border-l-2 ${node.accent}`}
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="font-mono text-micro uppercase tracking-wider text-content-secondary">
                          {node.kind}
                        </span>
                        <span className="font-mono text-micro text-content-muted" aria-hidden="true">
                          0{index + 1}
                        </span>
                      </div>
                      <p className="mt-1.5 text-title font-bold text-content-primary">{node.name}</p>
                      <p className="mt-1 font-mono text-micro text-content-secondary">{node.detail}</p>
                    </li>
                  ))}
                </ol>

                <p className="mt-5 border-t border-line-subtle pt-4 font-mono text-micro text-content-secondary">
                  Illustrative graph · a diagram of the node stages, not a screenshot and not a
                  backtest result
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
