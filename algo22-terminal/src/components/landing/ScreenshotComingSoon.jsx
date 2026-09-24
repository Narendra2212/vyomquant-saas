/**
 * RETAINED ON PURPOSE — UNRENDERED, BUT LOAD-BEARING AS A TEST FIXTURE.
 *
 * Nothing imports this component. That is not an oversight and it is not an invitation to
 * delete it. Seven sibling files in this directory (`AICopilot`, `BacktestingDemo`,
 * `Features`, `MetricsBar`, `PaperTradingDemo`, `PortfolioAnalytics`,
 * `StrategyBuilderDemo`) were equally unimported and were deleted. This one was kept,
 * because `tests/unit/guards/no-placeholders.test.js` measures itself against it:
 *
 *     it('finds the placeholder that is really in the tree')
 *
 * That test is the guard's non-vacuity proof — its own comment calls it "THE FIXED POINT".
 * It asserts this file exists on disk, that the directory scan reached it, that the scan
 * read exactly `['Coming Soon']` out of it, and that its budget entry is above zero. The
 * guard walks `src/{pages,components,lib}` counting placeholder copy and holds every file
 * at a budgeted number. If path resolution broke, or its copy rule stopped matching, every
 * count would read 0 and only the under-budget direction would object — the guard would
 * pass vacuously forever. A real file on disk that MUST measure non-zero is the only thing
 * standing between that guard and silent uselessness, and this is that file.
 *
 * So: do not delete it, do not zero its budget entries, and do not edit the
 * `Screenshot Coming Soon` string below. It is the measured value. Its entries are
 * `'components/landing/ScreenshotComingSoon.jsx': 1` in `no-placeholders.budget.js` and
 * `: 2` in `no-colour-literals.budget.js`, both of which stay because the file stays.
 *
 * Nothing here reaches a visitor. The live landing surface is
 * `components/landing/LandingPage.jsx` and the 14 sections it renders; this is not one of
 * them. If you need this frame on a real page, import it there and then move its entry
 * into the in-scope half of the placeholder budget, because at that point the copy below
 * becomes a placeholder a customer can read.
 *
 * (Requirement 14.1, second arm — Decision D3 of the `retail-ui-simplification` spec.)
 *
 * ScreenshotComingSoon — placeholder frame standing in for app screenshots
 * Props:
 *   title: string — section name shown in the frame
 *   height: string — optional tailwind height class, default 'h-64'
 */
import React from 'react'
import { Camera } from 'lucide-react'

export default function ScreenshotComingSoon({ title = 'Screenshot', height = 'h-64' }) {
  return (
    <div className={`relative rounded-xl border border-border-default bg-bg-surface/60 overflow-hidden ${height}`}>
      {/* Terminal chrome */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border-default bg-bg-elevated/60">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-accent-loss/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-accent-gold/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-accent-profit/60" />
        </div>
        <span className="text-xs font-mono text-text-muted ml-2">vyomquant — {title.toLowerCase().replace(/ /g, '-')}</span>
      </div>

      {/* Placeholder content */}
      <div className="absolute inset-0 top-10 flex flex-col items-center justify-center gap-3">
        {/* Subtle grid */}
        <div
          className="absolute inset-0 opacity-30"
          style={{
            backgroundImage: 'linear-gradient(rgba(30,37,48,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(30,37,48,0.15) 1px, transparent 1px)',
            backgroundSize: '32px 32px'
          }}
        />
        <div className="relative z-10 flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-bg-elevated border border-border-default flex items-center justify-center">
            <Camera className="w-6 h-6 text-text-muted" />
          </div>
          <div className="px-3 py-1.5 rounded-full border border-accent-cyan/30 bg-accent-cyan-dim">
            <span className="text-xs font-mono text-accent-cyan">Screenshot Coming Soon</span>
          </div>
          <p className="text-xs text-text-muted font-mono text-center max-w-xs">
            {title} · Live preview available in the web app
          </p>
        </div>
      </div>
    </div>
  )
}
