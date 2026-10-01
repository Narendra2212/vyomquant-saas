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
 * 2. "Is there a free trial for Pro or Elite features?" named tiers that do not exist. The five
 *    published plans in `Pricing.jsx` are Free, Trader, Pro Quant, Business and Enterprise.
 *    "Elite" is from an older price list, and "Institutional" was the previous name of the plan
 *    now published as Business.
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

/**
 * NO PRICE FIGURE APPEARS IN ANY ANSWER BELOW.
 *
 * "How much does it cost to start?" used to answer with the rupee list verbatim — "Trader (₹499
 * per month, or ₹4,990 per year)… Pro Quant (₹999 per month)". That was a second, hardcoded price
 * list on the same page as the first, and once `Pricing.jsx` began showing the visitor's own
 * currency it became a visible contradiction: the cards quoted `$5.20` and the FAQ two screens
 * below still said `₹499`. A visitor cannot tell which of the two is the price.
 *
 * The question is about cost TO START, and the answer to that is "nothing" — which needs no
 * figure. Everything that does need one is one scroll away in `#pricing`, where it is localised,
 * labelled with the currency it will be charged in, and read from the catalogue that takes the
 * payment. One surface quotes prices, and it is the one that cannot be wrong.
 *
 * `tests/unit/landing_page_pricing_crash_regression.test.jsx` asserts that no published rupee
 * figure appears anywhere on the landing page while another currency is on screen, so
 * re-introducing one here fails the build.
 */
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
    // NO FIGURES HERE, DELIBERATELY. See the note above `faqSections`.
    answer:
      'Nothing. The Free plan includes the visual builder, VectorBT backtesting, paper trading and one active strategy, with no card required. Paid plans start with Trader, which is where a strategy can be automated on a connected exchange; Pro Quant adds machine learning nodes, parameter optimization and the ability to publish to the marketplace. The pricing section on this page lists every plan with its monthly and annual price in your own currency.',
  },
  {
    question: 'Can I earn money from strategies I build?',
    answer:
      'Yes, on Pro Quant and Business. You can publish eligible strategies to the Strategy Marketplace — a completed backtest and a real order block are required, and submissions are moderated before they go live — and you keep 90% of the marketplace subscription revenue they generate, with VyomQuant taking 10%. The share is calculated after the marketplace\u2019s payment, refund and chargeback handling, so reversals net out of earnings. Free and Trader can browse the marketplace and Trader can subscribe to strategies, but neither can publish.',
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
      'Yes, from the billing panel at any time, with changes taking effect at the next cycle. Moving down a plan never deletes anything: strategies you built above your new capacity stay intact and readable, and the billing page tells you which allowances you are over. What a lower plan prevents is creating or activating MORE than it allows — not keeping what you already have.',
  },
  {
    question: 'What counts against my monthly limits?',
    answer:
      'Backtests, optimization runs and ML training runs are monthly allowances that reset at the start of each month, and a run counts whether it succeeds or fails — the compute is spent either way. Everything else is a live count of what exists right now: strategies, exchange connections, ML models, marketplace listings and marketplace subscriptions. Delete one and the capacity comes back immediately. Historical data, tick data, alerts and marketplace browsing carry no limit on any plan.',
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
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              FAQ
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Questions worth asking first
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Including the ones where the answer is no.
            </p>
          </div>

          <div className="mx-auto mt-14 max-w-3xl space-y-10">
            <div>
              <h3 className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-content-secondary">
                Getting started
              </h3>
              <Accordion items={gettingStarted} defaultOpen={[0]} />
            </div>
            <div>
              <h3 className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-content-secondary">
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
