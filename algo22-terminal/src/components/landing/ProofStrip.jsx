/**
 * ProofStrip — the credibility band directly beneath the hero.
 *
 * WHY IT EXISTS
 * -------------
 * A crypto trading product is judged on legitimacy before it is judged on features, and the
 * slot immediately under the hero is where that doubt peaks. The conventional fill is customer
 * logos, a user count, or an audit badge.
 *
 * VyomQuant has NONE of those. There is no customer count, no testimonial, no SOC 2, no
 * ISO 27001, no pen-test report and no funding claim anywhere in this repository. Inventing one
 * would be the single most damaging thing this page could do, because it is also the easiest
 * claim for a prospect to check.
 *
 * So this band is filled with CAPABILITY PROOF instead: six statements, each of which is
 * verifiable in the codebase, each naming the concrete mechanism rather than an adjective. A
 * reader who knows the domain will recognise `CCXT.pro`, `VectorBT` and `walk-forward` as
 * specific, and specificity is what carries credibility when there is no third party to borrow
 * it from.
 *
 * EVERY ITEM'S SOURCE
 * -------------------
 *   Binance certified    `backend_app/core/exchange_certification.py` — the ONLY venue
 *                        registered at LEVEL_5_PRODUCTION_READY. Stated as one certified
 *                        exchange rather than the "50+ exchanges" the old page claimed three
 *                        times over: CCXT.pro can reach many, one is production-certified, and
 *                        those are different sentences.
 *   CCXT.pro             `backend_app/backend/connection_engine.py` — `import ccxt.pro`, one
 *                        pooled authenticated instance per (user, exchange).
 *   VectorBT             `backend_app/backend/backtesting_engine.py` —
 *                        `vbt.Portfolio.from_signals`, 50-bar minimum, pandas fallback.
 *   Monte Carlo          `backend_app/backend/optimization_engine.py` —
 *                        `run_monte_carlo_simulation`, 100–1000 runs, randomised slippage and
 *                        latency, confidence intervals. Plus walk-forward and sensitivity.
 *   Encrypted keys       `backend_app/routers/security.py`, `core/tenant.py` and the RLS
 *                        policies — authenticated encryption at rest, per-tenant isolation.
 *   Audit trail          `backend_app/core/audit_trail.py` — `OrderAuditLogger`,
 *                        `StrategyAuditLogger`.
 *
 * No number on this strip is a performance figure.
 */

import React from 'react'
import { Activity, Fingerprint, KeyRound, Plug, ScrollText, Sigma } from 'lucide-react'

const PROOF = [
  {
    icon: Fingerprint,
    label: 'Binance',
    detail: 'Production-certified venue',
  },
  {
    icon: Plug,
    label: 'CCXT.pro',
    detail: 'Unified exchange connectivity',
  },
  {
    icon: Activity,
    label: 'VectorBT',
    detail: 'Vectorised backtest engine',
  },
  {
    icon: Sigma,
    label: 'Monte Carlo',
    detail: 'Walk-forward & sensitivity',
  },
  {
    icon: KeyRound,
    label: 'Encrypted vault',
    detail: 'API keys never in plaintext',
  },
  {
    icon: ScrollText,
    label: 'Audit trail',
    detail: 'Every order and edit logged',
  },
]

export default function ProofStrip() {
  return (
    <section
      className="border-y border-line-default bg-surface-panel/60"
      aria-label="What the platform is built on"
    >
      <div className="section-container py-10 lg:py-12">
        <div className="section-inner">
          <h2 className="mb-8 text-center font-mono text-micro font-semibold uppercase tracking-[0.2em] text-content-secondary">
            Built on named, inspectable infrastructure
          </h2>

          <ul className="grid grid-cols-2 gap-x-6 gap-y-7 sm:grid-cols-3 lg:grid-cols-6">
            {PROOF.map(({ icon: Icon, label, detail }) => (
              <li key={label} className="flex flex-col items-center gap-2 text-center">
                <Icon className="h-5 w-5 text-brand" aria-hidden="true" />
                <span className="text-title font-bold text-content-primary">{label}</span>
                <span className="font-mono text-micro leading-snug text-content-secondary">
                  {detail}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
