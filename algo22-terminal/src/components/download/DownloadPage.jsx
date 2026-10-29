/**
 * /download — production-launch-hardening task 4.3.
 *
 * WHY THERE IS STILL NO URL IN THIS FILE
 * --------------------------------------
 * This page once advertised four installers — with four hardcoded sizes (84.2 / 78.5 / 75.4 /
 * 68.2 MB), four SHA-256 strings and a "Code Signed / Verified Publisher / Production Build"
 * row — and served none of them. All four URLs returned 403: CI's `dist/` carried no
 * `releases/` directory and `aws s3 sync dist/ --delete` deleted that prefix from the bucket
 * on every deploy. Task 4.3 withdrew the advertisement rather than relabel it.
 *
 * The artifacts are now genuinely published, as GitHub release `desktop-v0.1.0`. The cards
 * still render through a DECLARATION rather than from copy in this file:
 * `design/desktopReleases.js` carries each published artifact's url, exact byte count and
 * SHA-256; `design/pageFields.js` still carries the label and the withdrawal reason for any
 * platform that has no published build. `PlatformArtifact` prefers the first and falls back to
 * the second, so adding or retiring a platform is one edit in a declaration and none here.
 *
 * That is the point of the arrangement: this file cannot state a size, a checksum or a URL,
 * so it cannot state one that is wrong. Every figure it used to invent is now derived from a
 * byte count that was measured against the published file.
 *
 * The builds are UNSIGNED and the macOS DMG is UNNOTARIZED. Each card renders its own
 * first-run step from the declaration, because Gatekeeper refuses an unnotarized app outright
 * and a user given no instruction concludes the download is broken.
 */

import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, HardDrive, MemoryStick, Wifi, ArrowRight, Calendar, GitBranch, FileText, Monitor, Globe, Sparkles } from 'lucide-react'
import Navbar from '../landing/Navbar'
import Footer from '../landing/Footer'
import { DESKTOP_RELEASE } from '../../design/desktopReleases'
import { PlatformArtifact } from './PlatformArtifact'

// Read from the declaration rather than restated, so the version in the header and the
// version on the four cards cannot disagree.
const RELEASE_CONFIG = {
  version: `v${DESKTOP_RELEASE.version}`,
  buildDate: DESKTOP_RELEASE.publishedOn,
  publisher: DESKTOP_RELEASE.publisher
}

const RELEASE_NOTES = [
  // The first line used to read "Initial production desktop release for Windows, macOS, and
  // Linux" while no installer was published for any of the three — the same fabrication as
  // the sizes, in the next column. All three are now published and verified, so the line
  // states that, together with the one thing CI does NOT establish about them.
  { version: `v${DESKTOP_RELEASE.version}`, date: DESKTOP_RELEASE.publishedOn, changes: ['Desktop builds published for Windows (x64), macOS (universal) and Linux (AppImage, DEB, RPM)', 'Unsigned and unnotarized: CI compiles and bundles these but does not install or launch them, so treat this as a beta', 'Every published file was magic-byte identified and re-fetched anonymously to confirm it serves, before any URL was advertised', 'Built on Tauri 2 — the bundles use your OS webview rather than shipping a browser engine', 'Direct connection to the AWS ECS production backend cluster', 'Supabase authentication & encrypted local security vault integration', 'Real-time WebSocket market feeds and interactive charts'] }
]

const SYSTEM_REQUIREMENTS = {
  windows: { os: 'Windows 10 (64-bit) or Windows 11', cpu: 'Intel Core i5 / AMD Ryzen 5 or better', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection', display: '1280 x 720 resolution minimum' },
  macos: { os: 'macOS 12 (Monterey) or later', cpu: 'Apple Silicon (M1+) or Intel Core i5', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection', display: '1280 x 720 resolution minimum' },
  linux: { os: 'Ubuntu 22.04 LTS / Debian 12 / Fedora 39 or equivalent', cpu: 'Intel Core i5 / AMD Ryzen 5 or better', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection', display: '1280 x 720 resolution minimum' },
}

const PLATFORM_TABS = [
  { id: 'windows', label: 'Windows' },
  { id: 'macos', label: 'macOS' },
  { id: 'linuxAppImage', label: 'Linux (AppImage)' },
  { id: 'linuxDeb', label: 'Linux (DEB)' }
]

export default function DownloadPage() {
  const [activePlatform, setActivePlatform] = useState('windows')
  const [detectedOS, setDetectedOS] = useState('windows')

  useEffect(() => {
    const ua = navigator.userAgent.toLowerCase()
    if (ua.includes('win')) {
      setDetectedOS('windows')
      setActivePlatform('windows')
    } else if (ua.includes('mac')) {
      setDetectedOS('macos')
      setActivePlatform('macos')
    } else if (ua.includes('linux')) {
      setDetectedOS('linux')
      setActivePlatform('linuxAppImage')
    }
  }, [])

  // STILL no `handleDownload`, even now that the artifacts are published. Each card renders a
  // plain anchor carrying `href` and `download`, so the browser performs the transfer and the
  // user's own click starts it. A JS handler here would add a code path between the button and
  // the file for no gain, and it is what the withdrawn version used to click on the user's
  // behalf against a URL that could only 403.
  //
  // There is no download-tracking call either. DEPLOY-05 REMOVED the `downloadAnalyticsApi`
  // module this comment used to name - it wrote to a `download_analytics` table that was
  // declared in no migration and had no writer, and the admin dashboard's read of it threw a
  // missing-relation error at every admin who opened that tab. Counting downloads needs that
  // table to exist first; GitHub's own per-asset download counts are available in the
  // meantime.

  const activeReqs = SYSTEM_REQUIREMENTS[activePlatform.startsWith('linux') ? 'linux' : activePlatform] || SYSTEM_REQUIREMENTS.windows

  return (
    <div className="min-h-screen bg-surface-canvas">
      <Navbar />
      <section className="pt-32 pb-16 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-brand/5 to-transparent" />
        <div className="section-container relative z-10">
          <div className="section-inner text-center">
            <div className="text-xs font-mono text-brand uppercase tracking-wider mb-4">Official Release Downloads</div>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-content-primary mb-4">Download VyomQuant Terminal</h1>
            <p className="text-content-secondary max-w-xl mx-auto mb-8">
              Institutional-grade desktop application published by {RELEASE_CONFIG.publisher}.
            </p>
            <div className="inline-flex items-center gap-3 px-4 py-2 rounded-lg bg-surface-raised border border-line-default mb-6">
              <GitBranch className="w-4 h-4 text-brand" />
              <span className="text-sm font-mono text-content-primary">{RELEASE_CONFIG.version}</span>
              <span className="w-px h-4 bg-border-default" />
              <Calendar className="w-4 h-4 text-content-secondary" />
              <span className="text-sm font-mono text-content-secondary">{RELEASE_CONFIG.buildDate}</span>
              <span className="text-xs font-mono text-status-profit bg-status-profit-wash px-2 py-0.5 rounded-sm">Production</span>
            </div>

            {detectedOS && (
              <div className="mt-2 text-xs text-brand flex items-center justify-center gap-1.5 font-medium">
                <Sparkles className="w-3.5 h-3.5" />
                {/* Names what was detected and what was selected. It no longer says a download
                    was selected for you: none is offered. */}
                Detected your Operating System: <span className="capitalize font-bold text-content-primary">{detectedOS}</span> (your platform is selected below)
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="pb-24">
        <div className="section-container">
          <div className="section-inner max-w-4xl">
            {/* Quick Web Access Card */}
            <div className="mb-8 p-6 rounded-lg bg-gradient-to-r from-brand/10 via-brand/5 to-transparent border border-brand/30 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-lg bg-brand/20 border border-brand/40 flex items-center justify-center flex-shrink-0">
                  <Globe className="w-6 h-6 text-brand" />
                </div>
                <div>
                  <div className="text-base font-bold text-content-primary">Instant Web Access</div>
                  <div className="text-xs text-content-secondary">No installation required. Run directly in your browser.</div>
                </div>
              </div>
              <Link to="/app" className="btn-primary text-sm px-6 py-3 whitespace-nowrap">
                Launch Web App <ArrowRight className="w-4 h-4 ml-1" />
              </Link>
            </div>

            {/* Platform Selector Tabs */}
            <div className="flex flex-wrap justify-center gap-2 mb-8">
              {PLATFORM_TABS.map(tab => {
                const isActive = activePlatform === tab.id
                const isRecommended = (detectedOS === 'windows' && tab.id === 'windows') ||
                                      (detectedOS === 'macos' && tab.id === 'macos') ||
                                      (detectedOS === 'linux' && tab.id.startsWith('linux'))
                return (
                  <button key={tab.id} onClick={() => setActivePlatform(tab.id)}
                    className={`relative flex items-center gap-2 px-5 py-3 rounded-lg text-sm font-medium transition-all ${isActive ? 'bg-brand-wash text-brand border border-brand/30' : 'text-content-secondary hover:text-content-primary hover:bg-surface-raised border border-transparent'}`}>
                    {tab.label}
                    {isRecommended && <span className="ml-1 text-[10px] bg-brand text-content-inverse px-1.5 py-0.5 rounded-full font-bold">Recommended</span>}
                  </button>
                )
              })}
            </div>

            {/* The selected platform's artifact, as declared. In `unavailable` the panel
                renders the marker and its reason and nothing else — no filename, no size, no
                checksum, no control. The size strings and checksums that used to sit here
                described files this site does not serve. */}
            <div className="mb-8">
              <PlatformArtifact platform={activePlatform} />
            </div>

            {/* Grid for Release Notes & System Requirements */}
            <div className="grid lg:grid-cols-2 gap-6">
              <div className="card-surface p-6 sm:p-8">
                <div className="flex items-center gap-3 mb-6">
                  <FileText className="w-5 h-5 text-brand" />
                  <h3 className="text-lg font-bold text-content-primary">Release Notes</h3>
                </div>
                <div className="space-y-6">
                  {RELEASE_NOTES.map((notes, i) => (
                    <div key={i}>
                      <div className="flex items-center gap-3 mb-3">
                        <span className="text-sm font-bold text-content-primary">{notes.version}</span>
                        <span className="text-xs font-mono text-content-secondary">{notes.date}</span>
                        <span className="text-xs font-mono text-brand bg-brand-wash px-2 py-0.5 rounded-sm">Latest</span>
                      </div>
                      <ul className="space-y-2">
                        {notes.changes.map((change, ci) => (
                          <li key={ci} className="flex items-start gap-2 text-sm text-content-secondary"><span className="w-1.5 h-1.5 rounded-full bg-brand mt-2 flex-shrink-0" />{change}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card-surface p-6 sm:p-8">
                <div className="flex items-center gap-3 mb-6">
                  <Cpu className="w-5 h-5 text-brand" />
                  <h3 className="text-lg font-bold text-content-primary">System Requirements</h3>
                </div>
                <div className="space-y-4">
                  {[{icon:Monitor,label:'Operating System',value:activeReqs.os},{icon:Cpu,label:'Processor',value:activeReqs.cpu},{icon:MemoryStick,label:'Memory',value:activeReqs.ram},{icon:HardDrive,label:'Storage',value:activeReqs.storage},{icon:Wifi,label:'Network',value:activeReqs.network},{icon:Monitor,label:'Display',value:activeReqs.display}].map((req,i) => (
                    <div key={i} className="flex items-start gap-3">
                      <req.icon className="w-4 h-4 text-content-secondary mt-1 flex-shrink-0" />
                      <div>
                        <div className="text-xs font-mono text-content-secondary uppercase mb-1">{req.label}</div>
                        <div className="text-sm text-content-primary">{req.value}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
      <Footer />
    </div>
  )
}
