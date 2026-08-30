import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Menu, X, Globe } from 'lucide-react'

const navLinks = [
  { label: 'Platform', href: '#platform' },
  { label: 'Architecture', href: '#architecture' },
  { label: 'Security', href: '#security' },
  { label: 'Pricing', href: '#pricing' },
  { label: 'FAQ', href: '#faq' },
]

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

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 40)
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const scrollToSection = (href) => {
    setMobileOpen(false)
    if (href.startsWith('#')) {
      const el = document.querySelector(href)
      if (el) el.scrollIntoView({ behavior: 'smooth' })
    }
  }

  return (
    <>
      <nav 
        className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
          scrolled 
            ? 'bg-bg-primary/85 backdrop-blur-xl border-b border-border-default/80 shadow-lg shadow-black/20' 
            : 'bg-transparent border-b border-transparent'
        }`}
        role="navigation"
        aria-label="Main Navigation"
      >
        <div className="section-container">
          <div className="section-inner flex items-center justify-between h-18 py-3">
            {/* Logo */}
            <Link 
              to="/" 
              className="flex items-center gap-2.5 group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-lg"
              aria-label="VyomQuant Home"
            >
              <div className="w-9 h-9 rounded-xl bg-accent-cyan flex items-center justify-center shadow-[0_0_20px_rgba(0,212,255,0.35)] group-hover:scale-105 transition-transform duration-200">
                <span className="text-text-inverse font-extrabold text-sm font-mono">VQ</span>
              </div>
              <span className="font-extrabold text-xl tracking-tight text-text-primary group-hover:text-accent-cyan transition-colors">VyomQuant</span>
            </Link>

            {/* Desktop nav links */}
            <div className="hidden md:flex items-center gap-8">
              {navLinks.map((link) => (
                <button
                  key={link.label}
                  onClick={() => scrollToSection(link.href)}
                  className="text-sm font-medium text-text-secondary hover:text-text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm"
                >
                  {link.label}
                </button>
              ))}
            </div>

            {/* Desktop CTAs */}
            <div className="hidden md:flex items-center gap-3">
              {/* Downloads — compact */}
              <Link
                to="/download#windows"
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-border-default/80 text-xs font-semibold text-text-secondary hover:text-text-primary hover:border-accent-cyan/40 hover:bg-bg-elevated/80 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              >
                <WindowsIcon className="w-3.5 h-3.5" />
                Windows
              </Link>
              <Link
                to="/download#macos"
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-border-default/80 text-xs font-semibold text-text-secondary hover:text-text-primary hover:border-accent-cyan/40 hover:bg-bg-elevated/80 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              >
                <AppleIcon className="w-3.5 h-3.5" />
                macOS
              </Link>

              <div className="w-px h-5 bg-border-default mx-1" />

              <Link 
                to="/signin" 
                className="text-sm font-semibold text-text-secondary hover:text-text-primary transition-colors px-2 py-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan rounded-sm"
              >
                Sign In
              </Link>
              <Link 
                to="/signup" 
                className="inline-flex items-center gap-2 bg-accent-cyan text-text-inverse font-bold text-xs px-4.5 py-2.5 rounded-xl hover:bg-accent-cyan/90 transition-all shadow-[0_0_20px_rgba(0,212,255,0.25)] hover:shadow-[0_0_30px_rgba(0,212,255,0.4)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              >
                <Globe className="w-3.5 h-3.5" />
                Get Started
              </Link>
            </div>

            {/* Mobile menu toggle */}
            <button 
              className="md:hidden p-2.5 text-text-primary rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan" 
              onClick={() => setMobileOpen(!mobileOpen)}
              aria-label="Toggle Navigation Menu"
              aria-expanded={mobileOpen}
            >
              {mobileOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
            </button>
          </div>
        </div>
      </nav>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-bg-primary/98 backdrop-blur-2xl pt-24 px-6 md:hidden overflow-y-auto">
          <div className="flex flex-col gap-6">
            {navLinks.map((link) => (
              <button
                key={link.label}
                onClick={() => scrollToSection(link.href)}
                className="text-xl font-bold text-text-primary text-left py-1"
              >
                {link.label}
              </button>
            ))}

            <div className="pt-6 border-t border-border-default/80 flex flex-col gap-3">
              <p className="text-xs font-mono text-text-muted uppercase tracking-wider font-semibold">Download Desktop App</p>
              <Link
                to="/download#windows"
                onClick={() => setMobileOpen(false)}
                className="inline-flex items-center gap-3 px-4.5 py-3.5 rounded-xl border border-border-default text-sm font-semibold text-text-primary hover:bg-bg-elevated transition-colors"
              >
                <WindowsIcon className="w-4.5 h-4.5 text-accent-cyan" />
                Download for Windows
              </Link>
              <Link
                to="/download#macos"
                onClick={() => setMobileOpen(false)}
                className="inline-flex items-center gap-3 px-4.5 py-3.5 rounded-xl border border-border-default text-sm font-semibold text-text-primary hover:bg-bg-elevated transition-colors"
              >
                <AppleIcon className="w-4.5 h-4.5 text-accent-cyan" />
                Download for macOS
              </Link>
            </div>

            <div className="pt-4 border-t border-border-default/80 flex flex-col gap-3">
              <Link to="/signin" onClick={() => setMobileOpen(false)} className="text-center py-3 text-text-secondary font-semibold">
                Sign In
              </Link>
              <Link to="/signup" onClick={() => setMobileOpen(false)} className="inline-flex items-center justify-center gap-2 bg-accent-cyan text-text-inverse font-bold py-3.5 rounded-xl shadow-lg shadow-accent-cyan/20">
                <Globe className="w-4.5 h-4.5" />
                Get Started
              </Link>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
