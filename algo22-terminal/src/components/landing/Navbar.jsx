/**
 * Navbar — the landing page's fixed header.
 *
 * WHAT CAME OUT
 * -------------
 * Two "Windows" and "macOS" buttons sat beside the primary call to action, and two more inside
 * the mobile menu under a "Download Desktop App" heading. All four pointed at `/download#windows`
 * and `/download#macos`, where every artifact renders an `unavailable` panel — the installers are
 * not published. Four of the header's seven controls led to a dead end, in the most trusted
 * region of the page.
 *
 * Desktop is a real roadmap item and `DownloadSection` announces it properly, with each artifact
 * reporting its own status. A nav button is the wrong instrument for a nuanced status: it promises
 * a file.
 *
 * The section links now match the ids that exist. `Architecture` and `Security` were replaced by
 * `How it works` and `Marketplace` — the second because `/marketplace` is a public route that the
 * old page never linked to even once, and a visitor who can browse real strategies without an
 * account should be told so in the header.
 *
 * SCROLL BEHAVIOUR
 * ----------------
 * `scrollIntoView` relies on `html { scroll-behavior: smooth }` from `index.css`, which the global
 * `prefers-reduced-motion` block in `tokens.css` already collapses. Nothing extra is needed here.
 *
 * The mobile sheet closes on Escape and locks body scroll while open — it covers the viewport, so
 * a page scrolling behind it is a trap rather than an affordance.
 */

import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Menu, X } from 'lucide-react'

const SECTIONS = [
  { label: 'Platform', href: '#platform' },
  { label: 'How it works', href: '#workflow' },
  { label: 'Marketplace', href: '#marketplace' },
  { label: 'Pricing', href: '#pricing' },
  { label: 'FAQ', href: '#faq' },
]

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (!menuOpen) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menuOpen])

  const goToSection = (href) => {
    setMenuOpen(false)
    const target = document.querySelector(href)
    if (target) target.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <>
      <nav
        className={`fixed inset-x-0 top-0 z-50 transition-colors duration-200 ${
          scrolled
            ? 'border-b border-line-default bg-surface-overlay backdrop-blur-xl'
            : 'border-b border-transparent'
        }`}
        role="navigation"
        aria-label="Main Navigation"
      >
        <div className="section-container">
          <div className="section-inner flex h-16 items-center justify-between gap-6">
            <Link
              to="/"
              className="flex shrink-0 items-center gap-2.5"
              aria-label="VyomQuant home"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
                <span className="font-mono text-title font-extrabold text-content-inverse">VQ</span>
              </span>
              <span className="text-xl font-extrabold tracking-tight text-content-primary">
                VyomQuant
              </span>
            </Link>

            <div className="hidden items-center gap-7 md:flex">
              {SECTIONS.map(({ label, href }) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => goToSection(href)}
                  className="text-sm font-medium text-content-secondary transition-colors duration-150 hover:text-content-primary"
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="hidden shrink-0 items-center gap-3 md:flex">
              <Link
                to="/signin"
                className="px-2 py-1.5 text-sm font-semibold text-content-secondary transition-colors duration-150 hover:text-content-primary"
              >
                Sign In
              </Link>
              <Link
                to="/signup"
                className="inline-flex items-center gap-1.5 rounded-xl bg-brand px-4 py-2.5 text-sm font-bold text-content-inverse transition-colors duration-150 hover:bg-brand-hover"
              >
                Start free
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>

            <button
              type="button"
              className="rounded-lg p-2 text-content-primary md:hidden"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={menuOpen}
            >
              {menuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
            </button>
          </div>
        </div>
      </nav>

      {menuOpen && (
        <div className="fixed inset-0 z-40 overflow-y-auto bg-surface-canvas px-6 pb-10 pt-24 md:hidden">
          <div className="flex flex-col gap-1">
            {SECTIONS.map(({ label, href }) => (
              <button
                key={label}
                type="button"
                onClick={() => goToSection(href)}
                className="border-b border-line-subtle py-4 text-left text-xl font-bold text-content-primary"
              >
                {label}
              </button>
            ))}
          </div>

          <div className="mt-8 flex flex-col gap-3">
            <Link
              to="/signup"
              onClick={() => setMenuOpen(false)}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand py-4 text-sm font-bold text-content-inverse"
            >
              Start free — no card required
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/signin"
              onClick={() => setMenuOpen(false)}
              className="inline-flex items-center justify-center rounded-xl border border-line-strong py-4 text-sm font-semibold text-content-primary"
            >
              Sign In
            </Link>
          </div>
        </div>
      )}
    </>
  )
}
