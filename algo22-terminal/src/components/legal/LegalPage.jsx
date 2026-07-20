import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

const legalContent = {
  privacy: {
    title: 'Privacy Policy',
    updated: '2026-06-01',
    sections: [
      {
        heading: '1. Information We Collect',
        body: `We collect information you provide directly to us: name, email address, trading experience level, and expected volume when you join our waitlist or create an account. We also collect usage data automatically including IP address, browser type, pages visited, and platform interactions.`,
      },
      {
        heading: '2. How We Use Your Information',
        body: `We use collected information to provide, maintain, and improve the VyomQuant platform; send you waitlist notifications and product updates; monitor for fraudulent activity; and comply with legal obligations. We do not sell your personal data to third parties.`,
      },
      {
        heading: '3. Exchange API Credentials',
        body: `Exchange API keys you provide are encrypted at rest using AES-256. We never store your API secret in plaintext. Read-only API permissions are enforced by default. You may delete your API keys at any time from the platform settings.`,
      },
      {
        heading: '4. Data Retention',
        body: `We retain your account data for the duration of your account. Waitlist data is retained for 24 months. You may request deletion of your data by contacting us at privacy@vyomquant.com.`,
      },
      {
        heading: '5. Cookies',
        body: `We use strictly necessary cookies for authentication session management. We do not use third-party tracking or advertising cookies.`,
      },
      {
        heading: '6. Contact',
        body: `For privacy inquiries, contact: privacy@vyomquant.com.`,
      },
    ],
  },
  terms: {
    title: 'Terms of Service',
    updated: '2026-06-01',
    sections: [
      {
        heading: '1. Acceptance',
        body: `By accessing or using VyomQuant, you agree to be bound by these Terms of Service and all applicable laws. If you do not agree, do not use the platform.`,
      },
      {
        heading: '2. Platform Use',
        body: `VyomQuant provides systematic trading infrastructure software. You are solely responsible for all trading decisions, strategy configurations, and orders placed through the platform. Paper trading mode is enabled by default; live trading requires explicit activation per strategy.`,
      },
      {
        heading: '3. Account Responsibility',
        body: `You are responsible for maintaining the confidentiality of your account credentials. You must not share accounts or allow unauthorized access. Notify us immediately of any unauthorized use.`,
      },
      {
        heading: '4. Prohibited Uses',
        body: `You may not use VyomQuant for market manipulation, wash trading, front-running, or any activity that violates applicable securities laws or exchange terms of service. Violation results in immediate account termination.`,
      },
      {
        heading: '5. Intellectual Property',
        body: `VyomQuant software, design, and content is owned by VyomQuant. Community strategies published on the marketplace remain the intellectual property of their creators.`,
      },
      {
        heading: '6. Limitation of Liability',
        body: `VyomQuant shall not be liable for any trading losses, missed opportunities, or financial damage arising from use of the platform. Software is provided "as is" without warranty of any kind.`,
      },
    ],
  },
  risk: {
    title: 'Risk Disclosure',
    updated: '2026-06-01',
    sections: [
      {
        heading: 'Important Risk Warning',
        body: `Algorithmic trading and cryptocurrency trading involve substantial risk of loss. You could lose some or all of your invested capital. You should not trade with money you cannot afford to lose.`,
      },
      {
        heading: 'No Financial Advice',
        body: `VyomQuant is not a registered investment adviser. Nothing on this platform constitutes financial advice, investment advice, or a recommendation to buy or sell any asset. All content is for educational and infrastructure purposes only.`,
      },
      {
        heading: 'Backtest Limitations',
        body: `Historical backtesting results do not guarantee future performance. Backtests are subject to survivorship bias, look-ahead bias, and market impact that is not modeled. Live results may differ materially from backtest results.`,
      },
      {
        heading: 'Paper Trading',
        body: `Paper trading simulates orders but does not account for slippage, partial fills, exchange downtime, or liquidity constraints. Paper trading performance may differ from live trading performance.`,
      },
      {
        heading: 'Exchange Risk',
        body: `Cryptocurrency exchanges may freeze funds, halt withdrawals, or close without notice. VyomQuant has no control over connected exchanges and is not responsible for exchange failures.`,
      },
      {
        heading: 'Technology Risk',
        body: `Software bugs, network failures, API outages, or latency spikes may cause unintended orders or missed executions. Always monitor your running strategies.`,
      },
    ],
  },
  refund: {
    title: 'Refund Policy',
    updated: '2026-06-01',
    sections: [
      {
        heading: '1. Subscription Fees',
        body: `Paid subscription fees are non-refundable except where required by applicable law. If you cancel your subscription, you retain access to paid features until the end of your current billing period.`,
      },
      {
        heading: '2. Free Beta Period',
        body: `During the closed beta period, VyomQuant is provided at no charge. No refunds are applicable to beta access.`,
      },
      {
        heading: '3. Exceptional Circumstances',
        body: `In cases of platform unavailability exceeding 72 continuous hours, we may, at our discretion, issue pro-rated service credits. Service credits are not redeemable for cash.`,
      },
      {
        heading: '4. Contact for Billing Issues',
        body: `For billing inquiries or refund requests under applicable law, contact: billing@vyomquant.com within 14 days of the charge.`,
      },
    ],
  },
}

export default function LegalPage({ type = 'privacy' }) {
  const content = legalContent[type] || legalContent.privacy

  return (
    <div className="relative min-h-screen bg-bg-primary">
      {/* Background */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-accent-cyan/5 rounded-full blur-[120px]" />
      </div>

      <div className="relative z-10 max-w-3xl mx-auto px-6 py-24">
        {/* Back link */}
        <Link
          to="/"
          className="inline-flex items-center gap-2 text-sm text-text-muted hover:text-text-primary transition-colors mb-10"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Home
        </Link>

        {/* Header */}
        <div className="mb-10">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-lg bg-accent-cyan flex items-center justify-center shadow-[0_0_15px_rgba(0,212,255,0.2)]">
              <span className="text-text-inverse font-bold text-xs font-mono">VQ</span>
            </div>
            <span className="text-sm font-mono text-text-muted">VyomQuant Legal</span>
          </div>
          <h1 className="text-3xl sm:text-4xl font-black text-text-primary mt-4 mb-2">{content.title}</h1>
          <p className="text-xs font-mono text-text-muted">Last updated: {content.updated}</p>
        </div>

        {/* Legal content */}
        <div className="space-y-8">
          {content.sections.map((section, i) => (
            <div key={i} className="card-surface p-6">
              <h2 className="text-base font-bold text-text-primary mb-3">{section.heading}</h2>
              <p className="text-sm text-text-secondary leading-relaxed">{section.body}</p>
            </div>
          ))}
        </div>

        {/* Footer nav */}
        <div className="mt-12 pt-8 border-t border-border-default">
          <div className="flex flex-wrap gap-4 text-xs font-mono text-text-muted">
            <Link to="/legal/privacy" className="hover:text-text-primary transition-colors">Privacy Policy</Link>
            <Link to="/legal/terms" className="hover:text-text-primary transition-colors">Terms of Service</Link>
            <Link to="/legal/risk" className="hover:text-text-primary transition-colors">Risk Disclosure</Link>
            <Link to="/legal/refund" className="hover:text-text-primary transition-colors">Refund Policy</Link>
          </div>
          <p className="text-xs text-text-muted mt-4">© {new Date().getFullYear()} VyomQuant. Not financial advice.</p>
        </div>
      </div>
    </div>
  )
}
