import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Menu, X, Globe } from 'lucide-react'

const navLinks = [
  { label: 'Features', href: '#features' },
  { label: 'Security', href: '#security' },
  { label: 'Pricing', href: '#pricing' },
  { label: 'About', href: '#about' },
]

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

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 80)
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
      <nav className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${scrolled ? 'glass-nav' : 'bg-transparent'}`}>
        <div className="section-container">
          <div className="section-inner flex items-center justify-between h-16">
            {/* Logo */}
            <Link to="/" className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-accent-cyan flex items-center justify-center">
                <span className="text-text-inverse font-bold text-sm font-mono">VQ</span>
              </div>
              <span className="font-bold text-lg tracking-tight text-text-primary">VyomQuant</span>
            </Link>

            {/* Desktop nav links */}
            <div className="hidden md:flex items-center gap-6">
              {navLinks.map((link) => (
                <button
                  key={link.label}
                  onClick={() => scrollToSection(link.href)}
                  className="text-sm text-text-secondary hover:text-text-primary transition-colors"
                >
                  {link.label}
                </button>
              ))}
            </div>

            {/* Desktop CTAs */}
            <div className="hidden md:flex items-center gap-2">
              {/* Downloads — compact */}
              <Link
                to="/download#windows"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default text-xs text-text-secondary hover:text-text-primary hover:border-accent-cyan/30 hover:bg-bg-elevated transition-all duration-150"
              >
                <WindowsIcon className="w-3 h-3" />
                Windows
              </Link>
              <Link
                to="/download#macos"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-default text-xs text-text-secondary hover:text-text-primary hover:border-accent-cyan/30 hover:bg-bg-elevated transition-all duration-150"
              >
                <AppleIcon className="w-3 h-3" />
                macOS
              </Link>

              <div className="w-px h-5 bg-border-default mx-1" />

              <Link to="/signin" className="text-sm text-text-secondary hover:text-text-primary transition-colors">
                Sign In
              </Link>
              <Link to="/app" className="inline-flex items-center gap-1.5 btn-primary text-xs px-4 py-2">
                <Globe className="w-3.5 h-3.5" />
                Launch App
              </Link>
            </div>

            {/* Mobile menu toggle */}
            <button className="md:hidden p-2 text-text-primary" onClick={() => setMobileOpen(!mobileOpen)}>
              {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </nav>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 bg-bg-primary/96 backdrop-blur-xl pt-20 px-6 md:hidden overflow-y-auto">
          <div className="flex flex-col gap-5">
            {navLinks.map((link) => (
              <button
                key={link.label}
                onClick={() => scrollToSection(link.href)}
                className="text-lg text-text-primary text-left"
              >
                {link.label}
              </button>
            ))}

            <div className="pt-4 border-t border-border-default flex flex-col gap-3">
              <p className="text-xs font-mono text-text-muted uppercase tracking-wider">Download Desktop App</p>
              <Link
                to="/download#windows"
                onClick={() => setMobileOpen(false)}
                className="inline-flex items-center gap-2.5 px-4 py-3 rounded-xl border border-border-default text-sm text-text-primary hover:bg-bg-elevated transition-colors"
              >
                <WindowsIcon className="w-4 h-4" />
                Download for Windows
              </Link>
              <Link
                to="/download#macos"
                onClick={() => setMobileOpen(false)}
                className="inline-flex items-center gap-2.5 px-4 py-3 rounded-xl border border-border-default text-sm text-text-primary hover:bg-bg-elevated transition-colors"
              >
                <AppleIcon className="w-4 h-4" />
                Download for macOS
              </Link>
            </div>

            <div className="pt-2 border-t border-border-default flex flex-col gap-3">
              <Link to="/signin" onClick={() => setMobileOpen(false)} className="text-text-secondary">
                Sign In
              </Link>
              <Link to="/app" onClick={() => setMobileOpen(false)} className="btn-primary justify-center">
                <Globe className="w-4 h-4 mr-2" />
                Launch Web App
              </Link>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
