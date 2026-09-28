/**
 * ProductTour — what a user actually works in. Anchor `#tour`.
 *
 * RENAMED FROM `ScreenshotsSection.jsx`
 * ------------------------------------
 * The old name described an intention that was never met and has now been dropped on purpose:
 * there are no product screenshots on this page and there will not be any. The file it replaced
 * rendered hand-built HTML mockups, and the unreferenced `ScreenshotComingSoon.jsx` next to it
 * exists because captures were once planned. Keeping a filename that promises screenshots on a
 * page that has decided against them is a note that misleads the next reader, so the rename came
 * with the decision.
 *
 * This is also no longer a second feature list. `TrustSection` states the four pillars once;
 * this section shows what each one looks like in use. The overlap that had both sections pitching
 * the same four capabilities is gone.
 *
 * THE INVENTED NUMBERS ARE GONE
 * -----------------------------
 * It previously rendered `+28.4% Net Return`, `Sharpe Ratio 2.14`, `Sortino Ratio 3.08`,
 * `Max Drawdown -8.4%`, `Profit Factor 1.82`, a twenty-bar equity curve that only rose, and
 * `Simulated Exchange Match @ 89,420.50 USDT`. None of it came from a backtest. A
 * `[Illustrative Interactive Preview]` caption in 10px does not neutralise a Sharpe ratio
 * printed in brand colour, and the page's own footer states VyomQuant is not a registered
 * investment adviser.
 *
 * WHAT REPLACED THEM
 * ------------------
 * The SHAPE of each surface — which node categories exist, which passes the research engine
 * runs, which lifecycle states an order moves through, which limits can be armed. Shape is
 * verifiable and it is what a systematic trader is actually evaluating. Not one figure below is
 * a return, a ratio or a price.
 *
 * Sources: node categories and port types from `backend_app/backend/strategy_dag/schema.py`;
 * research passes from `backend_app/backend/optimization_engine.py`; order states from
 * `.kiro/specs/trading-lifecycle-integration/` Requirement 16; paper accounting from
 * `backend_app/backend/paper_trading_service.py`.
 *
 * ACCESSIBILITY
 * -------------
 * A real tab widget this time. The previous version put `role="tab"` on buttons with no
 * `tablist` parent, no `aria-controls`, no `id` on the panel and no keyboard handling, so a
 * screen reader announced four tabs belonging to nothing and arrow keys did nothing. Now:
 * `tablist` → `tab` → `tabpanel` are wired with matching ids, the inactive tabs are removed
 * from the tab sequence, and Left/Right/Home/End move between them per the ARIA tabs pattern.
 *
 * Zero colour literals and zero absolute font sizes: both budget entries (3 and 12) go to zero.
 */

import React, { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ChevronRight,
  GitBranch,
  Radar,
  ShieldAlert,
  SlidersHorizontal,
} from 'lucide-react'

/** Node categories the DAG schema actually defines, with a representative block each. */
const NODE_CATEGORIES = [
  { group: 'Data', example: 'OHLCV feed · order book' },
  { group: 'Indicator', example: 'RSI · EMA · Supertrend' },
  { group: 'Math', example: 'Z-score · rolling ops' },
  { group: 'Logic', example: 'Crossover · AND / OR' },
  { group: 'Feature engineering', example: 'Lag · normalise' },
  { group: 'ML / DL', example: 'XGBoost predictor' },
  { group: 'Action', example: 'Order intent · sizing' },
]

/** Port types, which is what makes an invalid connection refusable. */
const PORT_TYPES = [
  'OHLCV frame',
  'Price series',
  'Scalar series',
  'Boolean series',
  'Feature matrix',
  'Prediction',
  'Signal',
  'Trade intent',
]

/** The passes the research engine runs. Names of passes, not results. */
const RESEARCH_PASSES = [
  {
    name: 'VectorBT simulation',
    detail: 'Vectorised fills over historical bars, with fees and slippage applied.',
  },
  {
    name: 'Monte Carlo',
    detail: '100–1,000 re-runs with randomised slippage and latency. Returns confidence intervals per metric, not a single flattering number.',
  },
  {
    name: 'Walk-forward',
    detail: 'Splits history into sequential train and test folds, so a parameter tuned on one era is scored on the next.',
  },
  {
    name: 'Sensitivity sweep',
    detail: 'Varies each parameter independently to show which ones your result actually depends on.',
  },
  {
    name: 'Benchmark comparison',
    detail: 'Scores the strategy against holding the underlying over the same window.',
  },
]

/** Order lifecycle states, in the order the engine moves through them. */
const ORDER_STATES = [
  { state: 'Signal', note: 'Graph evaluated on bar close' },
  { state: 'Risk preflight', note: 'Caps and drawdown checked' },
  { state: 'Submitted', note: 'Routed to the venue' },
  { state: 'Partially filled', note: 'Cumulative average tracked' },
  { state: 'Filled', note: 'Position and equity updated' },
]

/** Limits that can be armed, with the state vocabulary the risk page uses. */
const RISK_CONTROLS = [
  { control: 'Account drawdown killswitch', effect: 'Halts every strategy on breach' },
  { control: 'Per-trade position cap', effect: 'Rejects oversized intents' },
  { control: 'Leverage ceiling', effect: 'Caps notional exposure' },
  { control: 'Slippage tolerance', effect: 'Abandons a fill that drifts too far' },
]

const TABS = [
  { id: 'builder', label: 'Strategy builder', icon: GitBranch },
  { id: 'research', label: 'Research', icon: Radar },
  { id: 'paper', label: 'Paper trading', icon: ShieldAlert },
  { id: 'risk', label: 'Risk controls', icon: SlidersHorizontal },
]

/** Shared chrome so each panel is framed identically. */
function Frame({ caption, badge, children }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line-default bg-surface-canvas">
      <div className="flex items-center gap-3 border-b border-line-default bg-surface-raised px-4 py-2.5">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="h-2.5 w-2.5 rounded-full bg-status-loss/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-status-warning/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-status-profit/70" />
        </div>
        <span className="font-mono text-micro text-content-secondary">{caption}</span>
        {badge && (
          <span className="ml-auto rounded-md border border-brand/30 bg-brand-wash px-2 py-0.5 text-micro font-semibold uppercase tracking-wider text-brand">
            {badge}
          </span>
        )}
      </div>
      <div className="p-5 sm:p-6">{children}</div>
    </div>
  )
}

/** The explanatory column that sits beside every frame. */
function Aside({ eyebrow, title, body, cta }) {
  return (
    <div>
      <p className="text-xs font-bold uppercase tracking-[0.16em] text-brand">
        {eyebrow}
      </p>
      <h3 className="mt-3 text-2xl font-bold tracking-tight text-content-primary">{title}</h3>
      <p className="mt-3 text-body leading-relaxed text-content-secondary">{body}</p>
      <Link
        to="/signup"
        className="mt-5 inline-flex items-center gap-1.5 text-sm font-bold text-brand hover:underline"
      >
        {cta}
        <ChevronRight className="h-3.5 w-3.5" />
      </Link>
    </div>
  )
}

export default function ProductTour() {
  const [active, setActive] = useState('builder')
  const tabRefs = useRef([])

  /** ARIA tabs pattern: Left/Right wrap, Home/End jump to the ends. */
  const onKeyDown = (event, index) => {
    const keys = {
      ArrowRight: (index + 1) % TABS.length,
      ArrowLeft: (index - 1 + TABS.length) % TABS.length,
      Home: 0,
      End: TABS.length - 1,
    }
    const next = keys[event.key]
    if (next === undefined) return
    event.preventDefault()
    setActive(TABS[next].id)
    tabRefs.current[next]?.focus()
  }

  return (
    <section
      id="tour"
      className="border-t border-line-default bg-surface-canvas py-20 lg:py-28"
      aria-label="Product tour"
    >
      <div className="section-container">
        <div className="section-inner max-w-6xl">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              Product tour
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              What you actually work in
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              The blocks you get, the tests that run, the states an order passes through, and the
              limits you can arm.
            </p>
          </div>

          {/* Tab strip */}
          <div
            role="tablist"
            aria-label="Product areas"
            className="mx-auto mt-12 flex max-w-2xl flex-wrap items-center justify-center gap-1.5 rounded-xl border border-line-default bg-surface-panel p-1.5"
          >
            {TABS.map(({ id, label, icon: Icon }, index) => {
              const selected = active === id
              return (
                <button
                  key={id}
                  ref={(node) => {
                    tabRefs.current[index] = node
                  }}
                  role="tab"
                  id={`tour-tab-${id}`}
                  aria-selected={selected}
                  aria-controls={`tour-panel-${id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActive(id)}
                  onKeyDown={(event) => onKeyDown(event, index)}
                  className={`inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-bold transition-colors duration-150 ${
                    selected
                      ? 'bg-brand text-content-inverse'
                      : 'text-content-secondary hover:bg-surface-raised hover:text-content-primary'
                  }`}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {label}
                </button>
              )
            })}
          </div>

          {/* ── Builder ─────────────────────────────────────────────────── */}
          {active === 'builder' && (
            <div
              role="tabpanel"
              id="tour-panel-builder"
              aria-labelledby="tour-tab-builder"
              tabIndex={0}
              className="mt-8 grid items-center gap-8 lg:grid-cols-5"
            >
              <div className="lg:col-span-3">
                <Frame caption="vyomquant · builder" badge="Typed graph">
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-content-secondary">
                    Block palette
                  </p>
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {NODE_CATEGORIES.map(({ group, example }) => (
                      <li
                        key={group}
                        className="flex items-baseline justify-between gap-3 rounded-lg border border-line-default bg-surface-raised px-3 py-2"
                      >
                        <span className="text-body font-semibold text-content-primary">{group}</span>
                        <span className="font-mono text-micro text-content-secondary">{example}</span>
                      </li>
                    ))}
                  </ul>

                  <p className="mb-2.5 mt-5 text-xs font-semibold uppercase tracking-wider text-content-secondary">
                    Port types — connections are checked against these
                  </p>
                  <ul className="flex flex-wrap gap-1.5">
                    {PORT_TYPES.map((port) => (
                      <li
                        key={port}
                        className="rounded-md border border-brand/25 bg-brand-wash px-2 py-0.5 font-mono text-micro text-brand"
                      >
                        {port}
                      </li>
                    ))}
                  </ul>
                </Frame>
              </div>
              <div className="lg:col-span-2">
                <Aside
                  eyebrow="Strategy builder"
                  title="Seven block families, connected by type"
                  body="Because every port declares what it carries, the canvas can reject a wiring mistake at the moment you make it rather than failing at runtime. That is the difference between a diagram tool and a compiler."
                  cta="Open the builder"
                />
              </div>
            </div>
          )}

          {/* ── Research ────────────────────────────────────────────────── */}
          {active === 'research' && (
            <div
              role="tabpanel"
              id="tour-panel-research"
              aria-labelledby="tour-tab-research"
              tabIndex={0}
              className="mt-8 grid items-center gap-8 lg:grid-cols-5"
            >
              <div className="lg:col-span-3">
                <Frame caption="vyomquant · research" badge="Five passes">
                  <ol className="space-y-2">
                    {RESEARCH_PASSES.map(({ name, detail }, index) => (
                      <li
                        key={name}
                        className="rounded-lg border border-line-default bg-surface-raised p-3.5"
                      >
                        <div className="flex items-center gap-2.5">
                          <span className="font-mono text-micro text-content-muted">
                            0{index + 1}
                          </span>
                          <span className="text-body font-bold text-content-primary">{name}</span>
                        </div>
                        <p className="mt-1 pl-7 text-sm leading-relaxed text-content-secondary">
                          {detail}
                        </p>
                      </li>
                    ))}
                  </ol>
                  <p className="mt-4 rounded-lg border border-line-default bg-surface-panel p-3 text-sm leading-relaxed text-content-secondary">
                    Results are recorded with the graph hash, dataset checksum and engine version,
                    so a backtest can be reproduced rather than taken on trust.
                  </p>
                </Frame>
              </div>
              <div className="lg:col-span-2">
                <Aside
                  eyebrow="Research"
                  title="Designed to break your strategy, not flatter it"
                  body="A single backtest is the easiest number in finance to fool yourself with. Monte Carlo re-runs it under randomised execution conditions and walk-forward refuses to score a parameter on the data that chose it."
                  cta="Run a backtest"
                />
              </div>
            </div>
          )}

          {/* ── Paper trading ───────────────────────────────────────────── */}
          {active === 'paper' && (
            <div
              role="tabpanel"
              id="tour-panel-paper"
              aria-labelledby="tour-tab-paper"
              tabIndex={0}
              className="mt-8 grid items-center gap-8 lg:grid-cols-5"
            >
              <div className="lg:col-span-3">
                <Frame caption="vyomquant · paper" badge="Simulated capital">
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-content-secondary">
                    Order lifecycle
                  </p>
                  <ol className="space-y-1.5">
                    {ORDER_STATES.map(({ state, note }, index) => (
                      <li
                        key={state}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line-default bg-surface-raised px-3.5 py-2.5"
                      >
                        <span className="font-mono text-micro text-content-muted" aria-hidden="true">
                          {index + 1}
                        </span>
                        <span className="font-mono text-small font-bold uppercase tracking-wider text-brand">
                          {state}
                        </span>
                        <span className="text-body text-content-secondary">{note}</span>
                      </li>
                    ))}
                  </ol>
                  <p className="mt-4 rounded-lg border border-env-paper/25 bg-env-paper-wash p-3 text-sm leading-relaxed text-env-paper">
                    Paper balances, positions and trades are held separately from live state and
                    persisted, so a restart does not reset your forward test.
                  </p>
                </Frame>
              </div>
              <div className="lg:col-span-2">
                <Aside
                  eyebrow="Paper trading"
                  title="Real market data, simulated capital"
                  body="The strategy sees the same prices and the same order flow it would see live, and its accounting is kept in decimals so a long forward test does not accumulate float drift. Nothing about promotion to live is automatic."
                  cta="Start a paper run"
                />
              </div>
            </div>
          )}

          {/* ── Risk ────────────────────────────────────────────────────── */}
          {active === 'risk' && (
            <div
              role="tabpanel"
              id="tour-panel-risk"
              aria-labelledby="tour-tab-risk"
              tabIndex={0}
              className="mt-8 grid items-center gap-8 lg:grid-cols-5"
            >
              <div className="lg:col-span-3">
                <Frame caption="vyomquant · risk" badge="Pre-trade">
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {RISK_CONTROLS.map(({ control, effect }) => (
                      <li
                        key={control}
                        className="rounded-lg border border-line-default bg-surface-raised p-3.5"
                      >
                        <p className="text-body font-bold text-content-primary">{control}</p>
                        <p className="mt-1 text-sm leading-relaxed text-content-secondary">
                          {effect}
                        </p>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-4 rounded-lg border border-status-warning/25 bg-status-warning-wash p-3 text-sm leading-relaxed text-status-warning">
                    Limits are evaluated in the execution path. A breach stops the strategy rather
                    than appearing in a report afterwards.
                  </p>
                </Frame>
              </div>
              <div className="lg:col-span-2">
                <Aside
                  eyebrow="Risk controls"
                  title="Limits that act, not limits that log"
                  body="Set an account drawdown ceiling and a per-trade cap once, and they apply to every strategy you deploy. The check happens before the order is sized, which is the only point at which it can still prevent something."
                  cta="Configure limits"
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
