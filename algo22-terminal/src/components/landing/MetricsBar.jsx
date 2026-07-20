import React from 'react'
import { Check } from 'lucide-react'

const platformFeatures = [
  { label: 'Visual DAG Builder', desc: 'No-code strategy construction' },
  { label: 'VectorBT Backtesting', desc: 'Multi-year tick-data simulation' },
  { label: 'Paper Trading First', desc: 'Zero capital risk by default' },
  { label: '50+ Exchange Integrations', desc: 'Powered by CCXT' },
]

export default function MetricsBar() {
  return (
    <section className="relative border-y border-border-default bg-bg-surface/40">
      <div className="section-container">
        <div className="section-inner">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-border-default/40">
            {platformFeatures.map((feat) => (
              <div key={feat.label} className="flex items-center gap-3 px-6 py-5">
                <div className="flex-shrink-0 w-7 h-7 rounded-lg bg-accent-profit-dim flex items-center justify-center">
                  <Check className="w-4 h-4 text-accent-profit" />
                </div>
                <div>
                  <div className="text-sm font-semibold text-text-primary leading-tight">{feat.label}</div>
                  <div className="text-xs text-text-muted font-mono mt-0.5">{feat.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
