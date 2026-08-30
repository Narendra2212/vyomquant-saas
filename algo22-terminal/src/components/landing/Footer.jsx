import React from 'react'
import { Link } from 'react-router-dom'
import { Globe, MessageSquare } from 'lucide-react'

function WindowsIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801" />
    </svg>
  )
}

function AppleIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 814 1000" fill="currentColor" aria-hidden="true">
      <path d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9-42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.5-164-39.5c-76 0-103.7 40.8-165.9 40.8s-105-36.8-162.8-108.8L274 737.7c-55.9-37.8-116.1-85.2-116.1-170.9 0-10.3.7-21.2 2-32.2 14.1-97.3 76.2-148.6 149-148.6 52.8 0 84.7 35.9 135.2 35.9 48.4 0 88.4-38.5 143.2-38.5 36 0 92.3 14.9 134.4 55.4zM535.2 10.1c25.1 27.6 44.4 67.6 44.4 107.9 0 7.1-.6 14.3-1.7 21.4-30.6 4.5-68.7 27.2-93.2 55.4-23.2 26.1-41.9 63.5-41.9 99.3 0 6.4.7 12.7 1.7 18.9 37.9 6.3 77.2-15.3 103.7-43.9 26.5-28.7 47.8-72.4 47.8-116.2z" />
    </svg>
  )
}

function GithubIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  )
}

function LinkedinIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z" />
      <rect width="4" height="12" x="2" y="9" />
      <circle cx="4" cy="4" r="2" />
    </svg>
  )
}

function TwitterIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M22 4s-.7 2.1-2 3.4c1.6 10-9.4 17.3-18 11.6 2.2.1 4.4-.6 6-2C3 15.5.5 9.6 3 5c2.2 2.6 5.6 4.1 9 4-.9-4.2 4-6.6 7-3.8 1.1 0 3-1.2 3-1.2z" />
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
  { label: 'Downloads', to: '/download' },
]

const companyLinks = [
  { label: 'About the Founder', href: '#founder' },
  { label: 'Frequently Asked Questions', href: '#faq' },
]

export default function Footer() {
  const scrollTo = (href) => {
    if (href.startsWith('#')) {
      const el = document.querySelector(href)
      if (el) el.scrollIntoView({ behavior: 'smooth' })
    }
  }

  return (
    <footer className="border-t border-border-default/80 bg-bg-surface/40 backdrop-blur-sm" role="contentinfo" aria-label="Footer">
      <div className="section-container py-16 lg:py-20">
        <div className="section-inner">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-8 lg:gap-12 mb-14">
            
            {/* Brand & Socials */}
            <div className="col-span-2 md:col-span-1">
              <Link to="/" className="flex items-center gap-2.5 mb-4 group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-lg">
                <div className="w-8.5 h-8.5 rounded-xl bg-accent-cyan flex items-center justify-center shadow-[0_0_20px_rgba(0,212,255,0.3)] group-hover:scale-105 transition-transform duration-200">
                  <span className="text-text-inverse font-bold text-sm font-mono">VQ</span>
                </div>
                <span className="font-extrabold text-xl tracking-tight text-text-primary">VyomQuant</span>
              </Link>
              <p className="text-sm text-text-secondary mb-5 leading-relaxed font-normal">
                Institutional-grade systematic trading infrastructure.
              </p>

              {/* Social Media Placeholders */}
              <div className="flex items-center gap-3 text-text-muted">
                <a 
                  href="https://github.com/VyomQuant" 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  aria-label="VyomQuant GitHub"
                  className="w-8.5 h-8.5 rounded-lg border border-border-default/80 flex items-center justify-center text-text-secondary hover:text-accent-cyan hover:border-accent-cyan/40 hover:bg-bg-elevated transition-all duration-200"
                >
                  <GithubIcon className="w-4.5 h-4.5" />
                </a>
                <a 
                  href="https://discord.gg/vyomquant" 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  aria-label="VyomQuant Discord Community"
                  className="w-8.5 h-8.5 rounded-lg border border-border-default/80 flex items-center justify-center text-text-secondary hover:text-accent-cyan hover:border-accent-cyan/40 hover:bg-bg-elevated transition-all duration-200"
                >
                  <MessageSquare className="w-4.5 h-4.5" />
                </a>
                <a 
                  href="https://linkedin.com/company/vyomquant" 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  aria-label="VyomQuant LinkedIn"
                  className="w-8.5 h-8.5 rounded-lg border border-border-default/80 flex items-center justify-center text-text-secondary hover:text-accent-cyan hover:border-accent-cyan/40 hover:bg-bg-elevated transition-all duration-200"
                >
                  <LinkedinIcon className="w-4.5 h-4.5" />
                </a>
                <a 
                  href="https://twitter.com/vyomquant" 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  aria-label="VyomQuant Twitter / X"
                  className="w-8.5 h-8.5 rounded-lg border border-border-default/80 flex items-center justify-center text-text-secondary hover:text-accent-cyan hover:border-accent-cyan/40 hover:bg-bg-elevated transition-all duration-200"
                >
                  <TwitterIcon className="w-4.5 h-4.5" />
                </a>
              </div>
            </div>

            {/* Platform */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-widest mb-4 font-bold">// PLATFORM</div>
              <ul className="space-y-3">
                {platformLinks.map(item => (
                  <li key={item.label}>
                    {item.to ? (
                      <Link to={item.to} className="text-sm text-text-secondary hover:text-text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm">
                        {item.label}
                      </Link>
                    ) : (
                      <button
                        onClick={() => scrollTo(item.href)}
                        className="text-sm text-text-secondary hover:text-text-primary transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm"
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
              <div className="text-xs font-mono text-text-muted uppercase tracking-widest mb-4 font-bold">// DOWNLOADS</div>
              <ul className="space-y-3">
                <li>
                  <Link to="/app" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-2">
                    <Globe className="w-4 h-4 text-accent-cyan" />
                    Web Application
                  </Link>
                </li>
                <li>
                  <Link to="/download#windows" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-2">
                    <WindowsIcon className="w-4 h-4 text-text-secondary" />
                    Windows (.exe)
                  </Link>
                </li>
                <li>
                  <Link to="/download#macos" className="text-sm text-text-secondary hover:text-text-primary transition-colors flex items-center gap-2">
                    <AppleIcon className="w-4 h-4 text-text-secondary" />
                    macOS (.dmg)
                  </Link>
                </li>
              </ul>
            </div>

            {/* Company */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-widest mb-4 font-bold">// COMPANY</div>
              <ul className="space-y-3">
                {companyLinks.map(item => (
                  <li key={item.label}>
                    <button
                      onClick={() => scrollTo(item.href)}
                      className="text-sm text-text-secondary hover:text-text-primary transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm"
                    >
                      {item.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            {/* Legal */}
            <div>
              <div className="text-xs font-mono text-text-muted uppercase tracking-widest mb-4 font-bold">// LEGAL</div>
              <ul className="space-y-3">
                {legalLinks.map(item => (
                  <li key={item.label}>
                    <Link
                      to={item.to}
                      className="text-sm text-text-secondary hover:text-text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>

          </div>

          {/* Risk disclaimer & Copyright */}
          <div className="pt-8 border-t border-border-default/80 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
            <p className="text-xs text-text-muted font-mono leading-relaxed max-w-4xl">
              Algorithmic trading involves substantial risk of loss. Past performance of backtests does not guarantee future results.
              VyomQuant provides software infrastructure only; all execution decisions are made by the user. Paper trading mode is enabled by default.
              VyomQuant is not a registered investment adviser. Not financial advice.
            </p>
            <div className="text-xs text-text-muted font-mono whitespace-nowrap">
              © {new Date().getFullYear()} VyomQuant. All rights reserved.
            </div>
          </div>
        </div>
      </div>
    </footer>
  )
}
