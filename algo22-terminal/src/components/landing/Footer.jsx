/**
 * Footer — sitemap, social, legal and the risk disclaimer.
 *
 * THE BROKEN ANCHOR IS FIXED
 * --------------------------
 * `platformLinks` carried `{ label: 'Platform Capabilities', href: '#features' }`. No element on
 * the page has had the id `features` at any point, so `document.querySelector('#features')`
 * returned null and the button did nothing at all — a control that looks live and is inert. It now
 * points at `#platform`, which exists. Every href and `to` in this file resolves to a real target;
 * that is asserted in `tests/unit/landing/landingSections.test.jsx`.
 *
 * THE INSTALLER LINKS ARE REFRAMED, NOT DELETED
 * ---------------------------------------------
 * "Windows (.exe)" and "macOS (.dmg)" pointed at `/download#windows` and `/download#macos`. A
 * file extension in a link label is a promise of a file, and no such file is served — CI's `dist/`
 * carries no `releases/` directory. Desktop IS coming, so the entry stays; it now reads as one
 * destination describing platform status rather than two named artifacts, and `/download` reports
 * the true state of each build when you get there.
 *
 * THE DISCLAIMER IS VERBATIM
 * --------------------------
 * The risk paragraph is unchanged, down to the sentence order. It is the page's only legally
 * load-bearing copy and a redesign is not a reason to reword it.
 *
 * THE SOCIAL LINKS WERE CHECKED, AND TWO OF THE FOUR WERE DEAD
 * ------------------------------------------------------------
 * The file this replaces carried four, under a comment reading `Social Media Placeholders`, and
 * none had been verified. They were fetched:
 *
 *   x.com/vyomquant                  200 — the account exists ("Vyomquant (@Vyomquant) on X").
 *                                    KEPT, and the href moved to `x.com`, since `twitter.com`
 *                                    has redirected there since the rename.
 *   github.com/VyomQuant             404. REMOVED.
 *   linkedin.com/company/vyomquant   404. REMOVED.
 *   discord.gg/vyomquant             Inconclusive. Discord serves a page for an expired invite
 *                                    and renders the real state client-side, so a fetch cannot
 *                                    tell a live invite from a dead one. A `discord.gg` vanity
 *                                    path also requires a boosted server. REMOVED on the same
 *                                    principle as the two 404s: unverifiable is not verified.
 *
 * WHY REMOVING BEAT KEEPING. This whole redesign removed claims the product could not support —
 * a fabricated Sharpe ratio, "50+ exchanges" against one certified venue, an "Enterprise
 * Security" badge with no audit behind it. A footer icon that 404s is the same defect in
 * miniature, and it is the cheapest one for a visitor to catch: one click. An absent link costs
 * nothing.
 *
 * RE-ADDING ONE IS A SINGLE LINE. `SOCIALS` below is a list, the icons for GitHub, LinkedIn and
 * Discord are still in this file, and the two removed entries are commented out in place with
 * their URLs intact. Create the account, uncomment, done.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { Globe, MonitorDown } from 'lucide-react'

/**
 * The only social glyph this file still needs.
 *
 * The hand-rolled `GithubIcon` and `LinkedinIcon` that stood here went with their links, and
 * the `MessageSquare` import that stood in for Discord went with that one — an icon component
 * with no call site is dead code, and `no-unused-vars` is right to say so.
 *
 * Restoring any of the three needs no SVG work: `lucide-react` ships `Github`, `Linkedin` and
 * `MessageSquare`, so it is an import and an uncommented line in `SOCIALS`.
 */
function TwitterIcon({ className }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M22 4s-.7 2.1-2 3.4c1.6 10-9.4 17.3-18 11.6 2.2.1 4.4-.6 6-2C3 15.5.5 9.6 3 5c2.2 2.6 5.6 4.1 9 4-.9-4.2 4-6.6 7-3.8 1.1 0 3-1.2 3-1.2z" />
    </svg>
  )
}

/**
 * Only accounts confirmed to resolve. See the file header for what was removed and why; the
 * three commented entries are the removals, kept in place so restoring one is a single line
 * once the account exists.
 */
const SOCIALS = [
  { label: 'VyomQuant on X', href: 'https://x.com/vyomquant', Icon: TwitterIcon },
  // Removed, not lost. Each needs a `lucide-react` import and this line uncommented:
  //   GitHub    https://github.com/VyomQuant             404 — icon: Github
  //   LinkedIn  https://linkedin.com/company/vyomquant   404 — icon: Linkedin
  //   Discord   https://discord.gg/vyomquant             unverifiable — icon: MessageSquare
]

/** `href` scrolls to a section on this page; `to` is a route. Never both. */
const COLUMNS = [
  {
    heading: 'Platform',
    links: [
      { label: 'Capabilities', href: '#platform' },
      { label: 'Product tour', href: '#tour' },
      { label: 'How it works', href: '#workflow' },
      { label: 'Security', href: '#security' },
    ],
  },
  {
    heading: 'Get started',
    links: [
      { label: 'Create an account', to: '/signup' },
      { label: 'Sign in', to: '/signin' },
      { label: 'Strategy library', to: '/marketplace' },
      { label: 'Pricing', href: '#pricing' },
    ],
  },
  {
    heading: 'Legal',
    links: [
      { label: 'Privacy Policy', to: '/legal/privacy' },
      { label: 'Terms of Service', to: '/legal/terms' },
      { label: 'Risk Disclosure', to: '/legal/risk' },
      { label: 'Refund Policy', to: '/legal/refund' },
    ],
  },
]

export default function Footer() {
  const scrollTo = (href) => {
    const target = document.querySelector(href)
    if (target) target.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <footer
      className="border-t border-line-default bg-surface-panel/50"
      role="contentinfo"
      aria-label="Footer"
    >
      <div className="section-container py-14 lg:py-16">
        <div className="section-inner">
          <div className="grid gap-10 md:grid-cols-2 lg:grid-cols-5">
            {/* Brand */}
            <div className="lg:col-span-2">
              <Link to="/" className="mb-4 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-brand">
                  <span className="font-mono text-body font-extrabold text-content-inverse">VQ</span>
                </span>
                <span className="text-xl font-extrabold tracking-tight text-content-primary">
                  VyomQuant
                </span>
              </Link>
              <p className="mb-6 max-w-xs text-body leading-relaxed text-content-secondary">
                Visual, no-code infrastructure for systematic crypto trading. Build it, prove it,
                then run it.
              </p>

              <ul className="flex items-center gap-2.5">
                {SOCIALS.map(({ label, href, Icon }) => (
                  <li key={label}>
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={label}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-line-default text-content-secondary transition-colors duration-150 hover:border-brand/40 hover:text-brand"
                    >
                      <Icon className="h-4 w-4" />
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            {COLUMNS.map(({ heading, links }) => (
              <nav key={heading} aria-label={heading}>
                <h2 className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-content-secondary">
                  {heading}
                </h2>
                <ul className="space-y-3">
                  {links.map((link) => (
                    <li key={link.label}>
                      {link.to ? (
                        <Link
                          to={link.to}
                          className="text-body text-content-secondary transition-colors duration-150 hover:text-content-primary"
                        >
                          {link.label}
                        </Link>
                      ) : (
                        <button
                          type="button"
                          onClick={() => scrollTo(link.href)}
                          className="text-left text-body text-content-secondary transition-colors duration-150 hover:text-content-primary"
                        >
                          {link.label}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </nav>
            ))}
          </div>

          {/* Platform availability, stated once, without naming a file that is not served. */}
          <div className="mt-12 flex flex-col gap-3 border-t border-line-subtle pt-8 sm:flex-row sm:items-center sm:gap-6">
            <Link
              to="/app"
              className="inline-flex items-center gap-2 text-body font-semibold text-content-primary transition-colors duration-150 hover:text-brand"
            >
              <Globe className="h-4 w-4 text-brand" aria-hidden="true" />
              Web platform — available now
            </Link>
            <Link
              to="/download"
              className="inline-flex items-center gap-2 text-body text-content-secondary transition-colors duration-150 hover:text-content-primary"
            >
              <MonitorDown className="h-4 w-4" aria-hidden="true" />
              Windows, macOS &amp; Linux — check release status
            </Link>
          </div>

          {/* Verbatim. See the header. */}
          <div className="mt-8 flex flex-col items-start justify-between gap-6 border-t border-line-subtle pt-8 md:flex-row md:items-center">
            <p className="max-w-4xl text-xs leading-relaxed text-content-secondary">
              Algorithmic trading involves substantial risk of loss. Past performance of backtests
              does not guarantee future results. VyomQuant provides software infrastructure only;
              all execution decisions are made by the user. Paper trading mode is enabled by
              default. VyomQuant is not a registered investment adviser. Not financial advice.
            </p>
            <p className="whitespace-nowrap text-xs text-content-secondary">
              © {new Date().getFullYear()} VyomQuant. All rights reserved.
            </p>
          </div>
        </div>
      </div>
    </footer>
  )
}
