/**
 * The landing page's "Choose Your Platform" section — production-launch-hardening task 4.3.
 *
 * WHAT CHANGED
 * ------------
 * Four `<a download href="/releases/…">` links and two hardcoded sizes (84.2 MB, 78.5 MB).
 * All four URLs return 403 and always have: CI's `dist/` carries no `releases/` directory and
 * `aws s3 sync dist/ --delete` deletes that prefix from the bucket on every deploy. The three
 * platform cards now render `ds/Panel`'s `unavailable` state, carrying the reason declared in
 * `design/pageFields` for that platform, reached by `usePanelState` without a request.
 *
 * The Web Platform card is untouched: `/app` exists, works, and is the only thing this section
 * could honestly offer.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { Globe, Sparkles } from 'lucide-react'

import { PlatformArtifact } from '../download/PlatformArtifact'

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

function LinuxIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2C10.5 2 9.2 3.1 9 4.6C8.2 4.1 7.2 4 6.3 4.4C5.1 4.9 4.2 6 4 7.3C3.5 8.1 3.5 9.1 3.9 10C3.3 11 3.2 12.2 3.7 13.3C4.2 14.4 5.2 15.1 6.4 15.3C6.7 16.6 7.6 17.6 8.8 18.2C10 18.8 11.4 18.8 12.6 18.2C13.8 17.6 14.7 16.6 15 15.3C16.2 15.1 17.2 14.4 17.7 13.3C18.2 12.2 18.1 11 17.5 10C17.9 9.1 17.9 8.1 17.4 7.3C17.2 6 16.3 4.9 15.1 4.4C14.2 4 13.2 4.1 12.4 4.6C12.2 3.1 10.9 2 9.4 2H12Z" />
    </svg>
  )
}

export default function DownloadSection() {
  // The user-agent sniff is gone with the links it served. It existed to put a "Recommended
  // for You" badge on one of the three installer cards; recommending an artifact that is not
  // published would be the advertisement this task withdrew, one layer down. `/download`
  // still detects the OS, because there it selects which platform's reason you read.

  return (
    <section id="download" className="py-24 lg:py-32 border-t border-border-default relative bg-bg-surface/20">
      <div className="section-container">
        <div className="text-center mb-16">
          {/* Both of these read as an advertisement for four installers that are not served.
              The eyebrow said "Production Desktop Release" and the paragraph offered native
              terminals for three platforms. They now say what is actually available. */}
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-cyan-dim border border-accent-cyan/30 text-xs font-mono text-accent-cyan mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            v0.1.0 Web Platform
          </div>
          <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
            Choose Your Platform
          </h2>
          <p className="text-text-secondary max-w-xl mx-auto text-base">
            Trade in your browser on our high-performance web platform. The native desktop terminals for Windows, macOS and Linux are not published yet.
          </p>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6 max-w-7xl mx-auto">
          {/* Web App */}
          <div className="card-surface p-8 flex flex-col items-center text-center border-2 border-accent-cyan/20 bg-accent-cyan/5 hover:border-accent-cyan/50 hover:bg-accent-cyan/10 transition-all duration-300">
            <div className="w-14 h-14 rounded-2xl bg-accent-cyan flex items-center justify-center mb-5 shadow-[0_0_20px_rgba(0,212,255,0.3)]">
              <Globe className="w-7 h-7 text-text-inverse" />
            </div>
            <h3 className="text-lg font-bold text-text-primary mb-1">Web Platform</h3>
            <p className="text-xs text-text-secondary mb-6">
              Instant browser access. No setup required.
            </p>
            <div className="text-[11px] font-mono text-text-muted mb-6">Version: Cloud Edge · Free</div>
            <Link
              to="/app"
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent-cyan text-text-inverse font-semibold hover:bg-accent-cyan/90 transition-colors text-sm"
            >
              Launch in Browser
            </Link>
          </div>

          {/* Windows, macOS and the two Linux artifacts. One panel per artifact the section
              used to link, so the mapping from withdrawn link to stated reason is one to one:
              the Linux column carried two links and carries two panels. The platform glyph
              travels in the panel header rather than becoming a second card around it. */}
          <PlatformArtifact
            platform="windows"
            level={3}
            actions={<WindowsIcon className="w-5 h-5 text-text-primary" />}
          />

          <PlatformArtifact
            platform="macos"
            level={3}
            actions={<AppleIcon className="w-5 h-5 text-text-primary" />}
          />

          <div className="flex flex-col gap-6">
            <PlatformArtifact
              platform="linuxAppImage"
              level={3}
              actions={<LinuxIcon className="w-5 h-5 text-text-primary" />}
            />
            <PlatformArtifact
              platform="linuxDeb"
              level={3}
              actions={<LinuxIcon className="w-5 h-5 text-text-primary" />}
            />
          </div>
        </div>
      </div>
    </section>
  )
}
