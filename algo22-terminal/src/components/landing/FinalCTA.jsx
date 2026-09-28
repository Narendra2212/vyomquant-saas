/**
 * FinalCTA — the closing ask.
 *
 * THREE CTAs BECAME ONE
 * ---------------------
 * It previously offered "Get Started Free" plus "Download Windows" and "Download macOS", the
 * latter two pointing at `/download#windows` and `/download#macos` where every artifact renders
 * an `unavailable` panel. A closing section that splits attention three ways, and sends two of
 * those three to a dead end, is the worst possible last impression. `DownloadSection` handles
 * desktop properly, per-artifact, further up the page.
 *
 * `animate-pulse-glow` IS GONE
 * ----------------------------
 * The primary button carried it. The class has no definition anywhere — `tokens.css` records
 * that `--animate-pulse-glow` was deliberately not carried forward, because Requirement 1.5
 * retires coloured glows. It rendered nothing at all, and was the sole entry in
 * `dead-tailwind.test.js`'s `KNOWN_DEAD_CLASSES`. That entry is removed with it, which returns
 * the guard to zero known-dead classes.
 *
 * The badge no longer reads "Early Access — Apply Now". There is nothing to apply for: signup is
 * open and immediate. "Apply" invents a gate and suppresses the click it is asking for.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check } from 'lucide-react'

const REASSURANCES = [
  'No card required',
  'Paper trading by default',
  'Your keys stay encrypted',
]

export default function FinalCTA() {
  return (
    <section
      className="relative isolate overflow-hidden border-t border-line-default bg-surface-canvas py-20 lg:py-28"
      aria-label="Get started"
    >
      <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden="true">
        <div className="absolute left-1/2 top-0 h-[26rem] w-[46rem] -translate-x-1/2 rounded-full bg-brand/8 blur-3xl" />
      </div>

      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-brand/30 bg-brand-wash px-4 py-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-brand" />
              <span className="font-mono text-micro font-semibold uppercase tracking-[0.18em] text-brand">
                Early access is open
              </span>
            </span>

            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Build your first strategy today
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              Start on the free tier, build a strategy on the canvas, backtest it, and run it on
              paper against live market data. Connect an exchange whenever you are ready — or
              never.
            </p>

            <div className="mt-10">
              <Link
                to="/signup"
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-9 py-4 text-base font-bold text-content-inverse shadow-raised transition-colors duration-150 hover:bg-brand-hover sm:w-auto"
              >
                Start free
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>

            <ul className="mt-7 flex flex-wrap items-center justify-center gap-x-6 gap-y-2.5">
              {REASSURANCES.map((item) => (
                <li key={item} className="flex items-center gap-2 text-body text-content-secondary">
                  <Check className="h-4 w-4 shrink-0 text-status-profit" aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>

            <p className="mt-8 font-mono text-micro leading-relaxed text-content-secondary">
              Already have an account?{' '}
              <Link
                to="/signin"
                className="font-semibold text-brand underline underline-offset-2 hover:text-brand-hover"
              >
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
