/**
 * TrustSection — the four capability pillars. Anchor `#platform`.
 *
 * WHAT THIS ABSORBED
 * ------------------
 * Three sections used to make overlapping feature claims: this one ("Built for Serious
 * Systematic Traders", six cards), `ModernTradingSection` ("Built for Modern Systematic
 * Trading", four cards — near-identical heading) and the four tabs of what is now `ProductTour`.
 * Twenty feature cards, pitching the DAG builder, VectorBT, paper trading and risk controls
 * three times each.
 *
 * `ModernTradingSection.jsx` is deleted. Two of its four claims were checked against the
 * backend and were false:
 *
 *   * "Natively support institutional execution algorithms including TWAP, VWAP, Iceberg" —
 *     there is no TWAP and no Iceberg anywhere in `backend_app/`. The only VWAP is
 *     `rolling_vwap` in `backend/indicators_backend.py`, which is an INDICATOR registered as
 *     an `IndicatorSpec` block, not an execution algorithm. Advertising it as an order type
 *     misrepresents an indicator as a routing capability.
 *   * "institutional FIX connections" — no FIX implementation exists.
 *
 * A third ("your strategy logic remains on your machine") was conditional on desktop builds
 * that are not published yet, so the benefit could not be delivered to anyone reading it. The
 * one durable claim it made — direct CCXT.pro routing with no third-party platform in the
 * path — survives, in the secondary row below.
 *
 * This section now states each pillar exactly once, and the deep-dive lives in `ProductTour`
 * rather than being a second copy of the same list.
 *
 * WHAT CAME OFF THE CLAIMS
 * ------------------------
 *   * "over 50+ global cryptocurrency exchanges" → CCXT.pro reaches many venues, but
 *     `backend_app/core/exchange_certification.py` registers exactly ONE at
 *     LEVEL_5_PRODUCTION_READY. The honest form names the library's reach and the certified
 *     venue separately.
 *   * "Run millions of backtest iterations in seconds" → nothing in the repository measures
 *     backtest throughput, so the figure and the timing were both unmeasured.
 *   * "institutional" as a bare adjective, four uses. Replaced by the mechanism in each case.
 */

import React from 'react'
import { Boxes, Brain, Radar, ShieldAlert, SlidersHorizontal, Waypoints } from 'lucide-react'

const PILLARS = [
  {
    icon: Boxes,
    title: 'A canvas, not a code editor',
    body:
      'Compose market feeds, indicators, maths, logic gates, feature engineering and order blocks as a graph. Ports are typed — an OHLCV frame will not connect to a boolean input — so structural mistakes surface while you are drawing, not mid-trade.',
    points: ['Drag-and-drop DAG builder', 'Validation as you build', 'No Python environment needed'],
  },
  {
    icon: Radar,
    title: 'Research that argues back',
    body:
      'VectorBT runs the simulation; then Monte Carlo re-runs it 100 to 1,000 times with randomised slippage and latency, walk-forward splits it across time, and sensitivity analysis sweeps your parameters. The point is to find the regime where your edge disappears.',
    points: ['VectorBT engine', 'Monte Carlo confidence bands', 'Walk-forward & sensitivity'],
  },
  {
    icon: ShieldAlert,
    title: 'Paper trading is the default',
    body:
      'Strategies deploy against live market data with simulated capital before they can touch real funds. Accounting is decimal, not floating point, and positions are persisted — so a restart does not quietly reset your forward test.',
    points: ['Real prices, simulated capital', 'Decimal position accounting', 'Separate from live state'],
  },
  {
    icon: SlidersHorizontal,
    title: 'Risk gates run before the order',
    body:
      'Account drawdown stops, per-trade position caps and leverage ceilings are evaluated in the execution path, not reported after the fact. A strategy that breaches its own limit is halted rather than logged.',
    points: ['Drawdown circuit breaker', 'Position size ceilings', 'Pre-trade risk preflight'],
  },
]

/** Capabilities that are real but do not need a full pillar each. */
const ALSO = [
  {
    icon: Waypoints,
    title: 'Direct exchange routing',
    body:
      'Orders go from your strategy to your exchange over CCXT.pro websockets using your own API keys. No third-party trading platform sits in the path.',
  },
  {
    icon: Brain,
    title: 'Machine learning as a node',
    body:
      'XGBoost training is configured through the visual interface and runs on managed infrastructure. Available from the Pro Quant tier.',
  },
  {
    icon: Radar,
    title: 'Signal tracing',
    body:
      'Every signal keeps the chain from the bar that triggered it to the order that resulted, so an unexpected fill can be explained rather than guessed at.',
  },
]

export default function TrustSection() {
  return (
    <section
      id="platform"
      className="border-t border-line-default bg-surface-panel/40 py-20 lg:py-28"
      aria-label="Platform capabilities"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-3 font-mono text-micro font-semibold uppercase tracking-[0.2em] text-brand">
              Platform
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
              Four things that decide whether a bot survives
            </h2>
            <p className="mt-4 text-lg text-content-secondary">
              Anyone can generate a strategy. Keeping it alive through a regime change takes
              honest research, a forward test, and risk limits that actually fire.
            </p>
          </div>

          <div className="mt-14 grid gap-5 lg:grid-cols-2">
            {PILLARS.map(({ icon: Icon, title, body, points }) => (
              <article
                key={title}
                className="group rounded-xl border border-line-default bg-surface-panel p-7 transition-colors duration-150 hover:border-brand/40"
              >
                <span className="mb-5 inline-flex h-11 w-11 items-center justify-center rounded-lg bg-brand-wash">
                  <Icon className="h-5 w-5 text-brand" aria-hidden="true" />
                </span>
                <h3 className="text-section font-bold text-content-primary">{title}</h3>
                <p className="mt-2.5 text-body leading-relaxed text-content-secondary">{body}</p>
                <ul className="mt-5 flex flex-wrap gap-2">
                  {points.map((point) => (
                    <li
                      key={point}
                      className="rounded-md border border-line-strong bg-surface-raised px-2.5 py-1 font-mono text-micro text-content-secondary"
                    >
                      {point}
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>

          <div className="mt-5 grid gap-5 sm:grid-cols-3">
            {ALSO.map(({ icon: Icon, title, body }) => (
              <article
                key={title}
                className="rounded-xl border border-line-default bg-surface-panel/70 p-6"
              >
                <div className="mb-3 flex items-center gap-2.5">
                  <Icon className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
                  <h3 className="text-title font-bold text-content-primary">{title}</h3>
                </div>
                <p className="text-body leading-relaxed text-content-secondary">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
