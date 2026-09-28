/**
 * DownloadSection — platform availability. Anchor `#download`.
 *
 * KEPT, DELIBERATELY. The redesign proposed removing this section outright, on the grounds that
 * a section whose job is to say "not available" four times is a poor use of the fold. That was
 * declined, and correctly: VyomQuant IS built for Windows, macOS and Linux — `src-tauri/` is a
 * real Tauri 2 project and `releases/windows/VyomQuant-Setup-0.1.0.exe` is a real 112 MB build —
 * and the desktop terminals are releasing shortly. A product with three native targets on the
 * way should say so on its landing page.
 *
 * SO THE FRAMING CHANGED, NOT THE FACTS
 * -------------------------------------
 * The section now announces desktop as a platform that is arriving, while each individual
 * artifact still reports its true state. Those are compatible statements and both belong here.
 * What it must not do is put a link on an artifact that is not served: CI's `dist/` carries no
 * `releases/` directory and `aws s3 sync dist/ --delete` removes that prefix on every deploy, so
 * all four `/releases/…` URLs return 403 and always have.
 *
 * WHY THE FOUR PANELS STAY EXACTLY AS THEY ARE
 * --------------------------------------------
 * `PlatformArtifact` reads each artifact's declaration from `design/pageFields.js` and renders
 * `ds/Panel`'s `unavailable` state with the declared reason. It issues no request — a non-empty
 * reason short-circuits `usePanelState` before the reader is consulted — and it cannot render an
 * href, a size or a checksum. `tests/unit/pages/downloadSurface.test.jsx` holds all four in
 * place, in this order, asserting the marker, the verbatim reason, the absence of any anchor
 * inside a card, and that nothing on this section points into `releases/`. Rewriting these into
 * hand-built "arriving" cards would have deleted that guarantee to gain a tone, and the tone is
 * available in the section header for free.
 *
 * The Web Platform card keeps its `/app` link. It is the one destination here that works, and
 * the same test file asserts it survives — non-vacuity for everything above it.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Globe, MonitorDown } from 'lucide-react'

import { PlatformArtifact } from '../download/PlatformArtifact'

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

function LinuxIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2C10.5 2 9.2 3.1 9 4.6C8.2 4.1 7.2 4 6.3 4.4C5.1 4.9 4.2 6 4 7.3C3.5 8.1 3.5 9.1 3.9 10C3.3 11 3.2 12.2 3.7 13.3C4.2 14.4 5.2 15.1 6.4 15.3C6.7 16.6 7.6 17.6 8.8 18.2C10 18.8 11.4 18.8 12.6 18.2C13.8 17.6 14.7 16.6 15 15.3C16.2 15.1 17.2 14.4 17.7 13.3C18.2 12.2 18.1 11 17.5 10C17.9 9.1 17.9 8.1 17.4 7.3C17.2 6 16.3 4.9 15.1 4.4C14.2 4 13.2 4.1 12.4 4.6C12.2 3.1 10.9 2 9.4 2H12Z" />
    </svg>
  )
}

export default function DownloadSection() {
  return (
    <section
      id="download"
      className="border-t border-line-default bg-surface-panel/40 py-20 lg:py-28"
      aria-label="Platform availability"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mx-auto max-w-2xl text-center">
            <p className="mb-4 text-xs font-bold uppercase tracking-[0.16em] text-brand">
              Platforms
            </p>
            <h2 className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Trade in the browser today, on your desktop shortly
            </h2>
            <p className="mt-5 text-lg leading-relaxed text-content-secondary">
              The web platform is live and complete — nothing to install, nothing to configure.
              Native terminals for Windows, macOS and Linux are built on Tauri and in final
              testing. Each one below reports its own status, so you always know what you can
              actually run.
            </p>
          </div>

          {/* The one card with a destination leads, at double width on the widest layout, so the
              working option is unambiguous rather than one of four equal-weight choices. */}
          <div className="mt-14 grid gap-5 lg:grid-cols-3">
            <article className="flex flex-col rounded-xl border border-brand/30 bg-brand-wash p-7 lg:col-span-1">
              <span className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-xl bg-brand shadow-raised">
                <Globe className="h-6 w-6 text-content-inverse" aria-hidden="true" />
              </span>
              <h3 className="text-section font-bold text-content-primary">Web platform</h3>
              <p className="mt-2 text-body leading-relaxed text-content-secondary">
                The full terminal in your browser. Build, backtest, paper trade and go live with no
                install step.
              </p>
              <p className="mt-4 font-mono text-micro uppercase tracking-wider text-status-profit">
                Available now · free tier included
              </p>
              <Link
                to="/app"
                className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-brand px-5 py-3 text-sm font-bold text-content-inverse transition-colors duration-150 hover:bg-brand-hover"
              >
                Launch in browser
                <ArrowRight className="h-4 w-4" />
              </Link>
            </article>

            {/* Desktop column. The heading states the roadmap once; the four panels below it
                each state their own true status, read from their declaration. */}
            <div className="lg:col-span-2">
              <div className="mb-4 flex items-center gap-2.5">
                <MonitorDown className="h-4 w-4 text-content-secondary" aria-hidden="true" />
                <h3 className="text-title font-bold text-content-primary">
                  Native desktop terminals
                </h3>
                <span className="rounded-md border border-status-warning/30 bg-status-warning-wash px-2 py-0.5 font-mono text-micro font-semibold uppercase tracking-wider text-status-warning">
                  In final testing
                </span>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <PlatformArtifact
                  platform="windows"
                  level={4}
                  actions={<WindowsIcon className="h-4 w-4 text-content-secondary" />}
                />
                <PlatformArtifact
                  platform="macos"
                  level={4}
                  actions={<AppleIcon className="h-4 w-4 text-content-secondary" />}
                />
                <PlatformArtifact
                  platform="linuxAppImage"
                  level={4}
                  actions={<LinuxIcon className="h-4 w-4 text-content-secondary" />}
                />
                <PlatformArtifact
                  platform="linuxDeb"
                  level={4}
                  actions={<LinuxIcon className="h-4 w-4 text-content-secondary" />}
                />
              </div>

              <p className="mt-4 font-mono text-micro leading-relaxed text-content-secondary">
                Your account, strategies and history are the same on every platform — the desktop
                terminals connect to the account you create today.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
