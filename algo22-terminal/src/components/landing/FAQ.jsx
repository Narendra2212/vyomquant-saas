/**
 * FAQ — the objections a retail algo-trading visitor actually arrives with. Anchor `#faq`.
 *
 * THREE ANSWERS WERE WRONG AND ARE CORRECTED
 * ------------------------------------------
 * 1. "Which exchanges are supported?" previously answered "50+ exchanges via CCXT.pro
 *    integration, including Binance, Bybit, OKX, Kraken, and Coinbase."
 *    `backend_app/core/exchange_certification.py` grades venues from LEVEL 0 (CCXT metadata
 *    only) to LEVEL 5 (production certified) and registers exactly ONE at
 *    LEVEL_5_PRODUCTION_READY: Binance. Named rate-limit configurations exist for Coinbase,
 *    Kraken and OKX; Kraken has no public CCXT sandbox at all. Listing five venues as
 *    "supported" flattened a real distinction that matters to anyone about to route an order.
 *
 * 2. "Is there a free trial for Pro or Elite features?" named tiers that do not exist. The four
 *    published tiers in `Pricing.jsx` are Free / Sandbox, Trader, Pro Quant and Institutional.
 *    "Elite" is from an older price list.
 *
 * 3. The desktop question was absent entirely, which is how the old page ended up advertising
 *    installers in eleven places while `DownloadSection` said they were not published. It is
 *    answered here directly.
 *
 * TWO QUESTIONS ADDED
 * -------------------
 * "Can I lose money while paper trading?" and "What happens when a strategy breaches its risk
 * limit?" — the two things a cautious first-time visitor most wants settled, and both have
 * concrete answers in the codebase.
 *
 * The accordion is `components/ui/Accordion`, unchanged: it already handles the disclosure
 * semantics and keyboard behaviour, and this section has no reason to own a second copy.
 */

import React from 'react'
import { Link } from 'react-router-dom'

import { Accordion } from '../ui/Accordion'

const gettingStarted = [
  {
    question: 'Do I need to know how to code?',
    answer:
      'No. Strategies are built by connecting blocks on a canvas — market feeds, indicators, logic gates, risk rules and order actions. Ports are typed, so the builder refuses an invalid connection as you draw it. No Python environment is involved.',
  },
  {
    question: 'Can I lose money while paper trading?',
    answer:
      'No. Paper trading uses real market prices with simulated capital and never touches an exchange balance. It is the default deployment mode — a strategy has to be explicitly promoted before it can place a real order, and that requires you to have connected your own exchange keys.',
  },
  {
    question: 'Is the desktop app available?',
    answer:
      'Not yet. The web platform is complete and is what you should start on today. Native terminals for Windows, macOS and Linux are built on Tauri and in final testing; the platforms section on this page reports the current status of each one. Your account and strategies carry across, so nothing you build now is wasted.',
  },
  {
    question: 'How much does it cost to start?',
    answer:
      'Nothing. The Free / Sandbox tier includes the visual builder, VectorBT backtesting, paper trading and one active strategy, with no card required. Paid tiers start at ₹499 per month when you need more concurrent strategies or machine learning nodes.',
  },
]

const platformAndRisk = [
  {
    question: 'Which exchanges can I connect?',
    answer:
      'Connectivity runs through CCXT.pro, which reaches a large set of venues through one interface. We certify venues individually rather than claiming them all: Binance is currently the only exchange certified production-ready end to end. Others can be connected and tested in the Exchange Manager, and the certification level is shown before you route anything.',
  },
  {
    question: 'How is a backtest different from what most bots show me?',
    answer:
      'A single backtest is easy to overfit. Alongside the VectorBT simulation, the research engine runs Monte Carlo — 100 to 1,000 re-runs with randomised slippage and latency — plus walk-forward splits and a parameter sensitivity sweep. Results are stored with the graph hash, dataset checksum and engine version so a run can be reproduced.',
  },
  {
    question: 'What happens when a strategy breaches its risk limit?',
    answer:
      'It is stopped, not logged. Account drawdown ceilings, per-trade position caps and leverage limits are evaluated in the execution path before an order is sized, so a breach prevents the order rather than appearing in a report afterwards.',
  },
  {
    question: 'Are my exchange API keys safe?',
    answer:
      'Keys are encrypted before storage with an integrity check on every record, are never sent to the browser, and are injected into the execution environment only at runtime. Row-level security in the database scopes them to your account. We hold no third-party security certification yet, and we will say so plainly until we do.',
  },
  {
    question: 'Can I change or cancel my plan?',
    answer:
      'Yes, from the billing panel at any time, with changes taking effect at the next cycle. If you move down to Free, your strategies stay intact and readable and live bots pause — you keep paper mode and the strategy library.',
  },
]

export default function FAQ() {
  return (
    <section
      id="faq"
      className="border-t border-line-default bg-surface-canvas py-20 lg:py-28"
      aria-label="Frequently asked questions"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-3 font-mono text-micro font-semibold uppercase tracking-[0.2em] text-brand">
              FAQ
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
              Questions worth asking first
            </h2>
            <p className="mt-4 text-lg text-content-secondary">
              Including the ones where the answer is no.
            </p>
          </div>

          <div className="mx-auto mt-14 max-w-3xl space-y-10">
            <div>
              <h3 className="mb-4 font-mono text-micro font-semibold uppercase tracking-[0.2em] text-content-secondary">
                Getting started
              </h3>
              <Accordion items={gettingStarted} defaultOpen={[0]} />
            </div>
            <div>
              <h3 className="mb-4 font-mono text-micro font-semibold uppercase tracking-[0.2em] text-content-secondary">
                Platform, risk and billing
              </h3>
              <Accordion items={platformAndRisk} defaultOpen={[0]} />
            </div>
          </div>

          <p className="mt-10 text-center text-body text-content-secondary">
            Still deciding? The{' '}
            <Link
              to="/legal/risk"
              className="font-semibold text-brand underline underline-offset-2 hover:text-brand-hover"
            >
              risk disclosure
            </Link>{' '}
            is worth five minutes before you automate anything.
          </p>
        </div>
      </div>
    </section>
  )
}
