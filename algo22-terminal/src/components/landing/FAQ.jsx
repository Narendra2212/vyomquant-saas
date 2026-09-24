import React from 'react'
import { Accordion } from '../ui/Accordion'

const platformFaqs = [
  { question: 'Do I need coding experience to use VyomQuant?', answer: 'No. VyomQuant is built as a visual node interface. All strategy logic is constructed via drag-and-drop DAG. Programming knowledge is not required.' },
  { question: 'What is the difference between Paper Trading and Live Trading?', answer: 'Paper trading executes against live market data with simulated capital. Live trading routes orders to connected exchanges via your API credentials. Paper is the default deployment mode.' },
  { question: 'Which exchanges are supported?', answer: '50+ exchanges via CCXT.pro integration, including Binance, Bybit, OKX, Kraken, and Coinbase. Exchange connectivity can be configured and tested directly in the Exchange Manager.' },
  { question: 'Can I use machine learning models without knowing Python?', answer: 'Yes. The XGBoost node accepts parameter configuration through the visual interface. Model training runs on managed infrastructure. No local Python environment required.' },
]

const billingFaqs = [
  { question: 'Is my exchange API key information secure?', answer: 'Authenticated encryption at rest. Keys never traverse the frontend. Row-level security isolation ensures your credentials are logically separated from all other users.' },
  { question: 'Can I cancel or change my subscription tier at any time?', answer: 'Yes. Modify or cancel your subscription at any time from the account panel. Changes take effect at the next billing cycle.' },
  { question: 'What happens to my strategies if I downgrade to Free?', answer: 'Your strategies remain in read-only state. Live bots are paused. You retain access to paper mode and community features.' },
  { question: 'Is there a free trial for Pro or Elite features?', answer: 'The Free tier provides full platform access with capacity limits. Upgrade to Pro or Elite when you require additional bots, backtests, or ML slots.' },
]

export default function FAQ() {
  return (
    <section id="faq" className="py-24 lg:py-32 border-t border-border-default/80 bg-bg-surface/30" aria-label="Frequently Asked Questions">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-widest mb-3 font-semibold">FAQ</div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">Common Questions</h2>
            <p className="text-text-secondary max-w-xl mx-auto text-base sm:text-lg">Everything you need to know about VyomQuant platform and billing.</p>
          </div>
          
          <div className="max-w-3xl mx-auto space-y-12">
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4 font-semibold">// PLATFORM & TRADING</div>
              <Accordion items={platformFaqs} defaultOpen={[0]} />
            </div>
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4 font-semibold">// SECURITY & BILLING</div>
              <Accordion items={billingFaqs} defaultOpen={[0]} />
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
