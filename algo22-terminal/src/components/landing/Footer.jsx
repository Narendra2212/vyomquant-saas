import React from 'react'
import { Link } from 'react-router-dom'
import { Globe } from 'lucide-react'

function WindowsIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801" />
    </svg>
  )
}

function AppleIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 814 1000" fill="currentColor">
      <path d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9-42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.5-164-39.5c-76 0-103.7 40.8-165.9 40.8s-105-36.8-162.8-108.8L274 737.7c-55.9-37.8-116.1-85.2-116.1-170.9 0-10.3.7-21.2 2-32.2 14.1-97.3 76.2-148.6 149-148.6 52.8 0 84.7 35.9 135.2 35.9 48.4 0 88.4-38.5 143.2-38.5 36 0 92.3 14.9 134.4 55.4zM535.2 10.1c25.1 27.6 44.4 67.6 44.4 107.9 0 7.1-.6 14.3-1.7 21.4-30.6 4.5-68.7 27.2-93.2 55.4-23.2 26.1-41.9 63.5-41.9 99.3 0 6.4.7 12.7 1.7 18.9 37.9 6.3 77.2-15.3 103.7-43.9 26.5-28.7 47.8-72.4 47.8-116.2z" />
    </svg>
  )
}

const legalLinks = [
  { label: 'Privacy Policy', to: '/legal/privacy' },
  { label: 'Terms of Service', to: '/legal/terms' },
  { label: 'Risk Disclosure', to: '/legal/risk' },
  { label: 'Refund Policy', to: '/legal/refund' },
]

const platformLinks = [
  { label: 'Platform Capabilities', href: '#features' },
  { label: 'Security & Infrastructure', href: '#security' },
  { label: 'Pricing Options', href: '#pricing' },
  { label: 'Strategy Marketplace', href: '#marketplace' },
  { label: 'Downloads', to: '/download' },
  { label: 'Documentation', to: '/docs' },
]

const companyLinks = [
  { label: 'About the Founder', href: '#about' },
  { label: 'Careers', href: '/' },
  { label: 'Contact Us', href: '/' },
]

export default function Footer() {
  const scrollTo = (href) => {
    if (href.startsWith('#')) {
      const el = document.querySelector(href)
      if (el) el.scrollIntoView({ behavior: 'smooth' })
    }
  }

  return (
    <footer className="border-t border-border-default bg-bg-surface/30">
      <div className="section-container py-16">
        <div className="section-inner">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-8 mb-12">
            {/* Brand */}
            <div className="col-span-2 md:col-span-1">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-accent-cyan flex items-center justify-center shadow-[0_0_15px_rgba(0,212,255,0.2)]">
                  <span className="text-text-inverse font-bold text-sm font-mono">VQ</span>
                </div>
                <span className="font-bold text-lg tracking-tight text-text-primary">VyomQuant</span>
              </div>
              <p className="text-sm text-text-secondary mb-2">Institutional-grade systematic trading infrastructure.</p>
              <p className="text-xs text-text-muted font-mono mt-4">© {new Date().getFullYear()} VyomQuant</p>
            </div>

            {/* Platform */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4">Platform</div>
              <ul className="space-y-2.5">
                {platformLinks.map(item => (
                  <li key={item.label}>
                    {item.to ? (
                      <Link to={item.to} className="text-sm text-text-secondary hover:text-text-primary transition-colors">
                        {item.label}
                      </Link>
                    ) : (
                      <button
                        onClick={() => scrollTo(item.href)}
                        className="text-sm text-text-secondary hover:text-text-primary transition-colors text-left"
                      >
                        {item.label}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            {/* Download */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4">Download</div>
              <ul className="space-y-2.5">
                <li>
                  <Link to="/app" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5">
                    <Globe className="w-3.5 h-3.5" />
                    Web App
                  </Link>
                </li>
                <li>
                  <Link to="/download#windows" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5">
                    <WindowsIcon className="w-3.5 h-3.5" />
                    Windows (.exe)
                  </Link>
                </li>
                <li>
                  <Link to="/download#macos" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5">
                    <AppleIcon className="w-3.5 h-3.5" />
                    macOS (.dmg)
                  </Link>
                </li>
              </ul>
            </div>

            {/* Company */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4">Company</div>
              <ul className="space-y-2.5">
                {companyLinks.map(item => (
                  <li key={item.label}>
                    <button
                      onClick={() => scrollTo(item.href)}
                      className="text-sm text-text-secondary hover:text-text-primary transition-colors text-left"
                    >
                      {item.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {/* Legal */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-wider mb-4">Legal</div>
              <ul className="space-y-2.5">
                {legalLinks.map(item => (
                  <li key={item.label}>
                    <Link
                      to={item.to}
                      className="text-sm text-text-secondary hover:text-text-primary transition-colors"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Risk disclaimer */}
          <div className="pt-8 border-t border-border-default">
            <p className="text-xs text-text-muted font-mono leading-relaxed max-w-4xl">
              Algorithmic trading involves substantial risk of loss. Past performance of backtests does not guarantee future results.
              VyomQuant provides software infrastructure only; all execution decisions are made by the user. Paper trading mode is enabled by default.
              VyomQuant is not a registered investment adviser. Not financial advice.
            </p>
          </div>
        </div>
      </div>
    </footer>
  )
}
