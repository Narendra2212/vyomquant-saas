/**
 * /download — production-launch-hardening task 4.3.
 *
 * WHAT CHANGED, AND WHY THERE IS NO URL IN THIS FILE ANY MORE
 * ---------------------------------------------------------
 * This page advertised four installers — with four hardcoded sizes (84.2 / 78.5 / 75.4 /
 * 68.2 MB), four SHA-256 strings and a "Code Signed / Verified Publisher / Production Build"
 * row — and served none of them. All four URLs return 403: CI's `dist/` carries no
 * `releases/` directory and `aws s3 sync dist/ --delete` deletes that prefix from the bucket
 * on every deploy. A size or a checksum for a file that does not exist is a fabricated figure
 * on a rendered surface, so the whole advertisement is withdrawn rather than relabelled.
 *
 * The four cards now render through the SAME convention every absent figure in this app uses:
 * `design/pageFields` declares each platform `VERDICT.UNAVAILABLE` with a reason,
 * `usePanelState` short-circuits to `unavailable` on that reason — issuing no request at all
 * (Requirement 19.3) — and `ds/Panel` renders the not-available marker carrying it. No new
 * primitive, no new copy path, no new state.
 *
 * Restoring a platform is one edit in `pageFields.js` once its artifact is actually
 * published; nothing here needs to change for that, because nothing here holds a URL.
 */

import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, HardDrive, MemoryStick, Wifi, ArrowRight, Calendar, GitBranch, FileText, Monitor, Globe, Sparkles } from 'lucide-react'
import Navbar from '../landing/Navbar'
import Footer from '../landing/Footer'
import { PlatformArtifact } from './PlatformArtifact'

const RELEASE_CONFIG = {
  version: 'v0.1.0',
  buildDate: '2026-07-24',
  publisher: 'Aerora Dynamics Private Limited'
}

const RELEASE_NOTES = [
  // The first line used to read "Initial production desktop release for Windows, macOS, and
  // Linux". No installer is published for any of the three, so it claimed a release that did
  // not reach a user — the same fabrication as the sizes, in the next column.
  { version: 'v0.1.0', date: '2026-07-24', changes: ['Initial release, on the web platform. No desktop installer is published yet — see the platform card above', 'Integrated Tauri & Electron dual runtime for high-performance execution', 'Direct connection to AWS ECS production backend cluster', 'Supabase authentication & encrypted local security vault integration', 'Real-time WebSocket market feeds and interactive charts'] }
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

  // No `handleDownload`. There is nothing to hand a click to, and a control that starts a
  // request which can only 403 is exactly the non-functional control Requirement 19.4 forbids.
  // `downloadAnalyticsApi` is not imported here for the same reason: an artifact nobody can
  // fetch has no download to count.

  const activeReqs = SYSTEM_REQUIREMENTS[activePlatform.startsWith('linux') ? 'linux' : activePlatform] || SYSTEM_REQUIREMENTS.windows

  return (
    <div className="min-h-screen bg-bg-primary">
      <Navbar />
      <section className="pt-32 pb-16 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-accent-cyan/5 to-transparent" />
        <div className="section-container relative z-10">
          <div className="section-inner text-center">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-4">Official Release Downloads</div>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-text-primary mb-4">Download VyomQuant Terminal</h1>
            <p className="text-text-secondary max-w-xl mx-auto mb-8">
              Institutional-grade desktop application published by {RELEASE_CONFIG.publisher}.
            </p>
            <div className="inline-flex items-center gap-3 px-4 py-2 rounded-xl bg-bg-elevated border border-border-default mb-6">
              <GitBranch className="w-4 h-4 text-accent-cyan" />
              <span className="text-sm font-mono text-text-primary">{RELEASE_CONFIG.version}</span>
              <span className="w-px h-4 bg-border-default" />
              <Calendar className="w-4 h-4 text-text-muted" />
              <span className="text-sm font-mono text-text-muted">{RELEASE_CONFIG.buildDate}</span>
              <span className="text-xs font-mono text-accent-profit bg-accent-profit-dim px-2 py-0.5 rounded">Production</span>
            </div>

            {detectedOS && (
              <div className="mt-2 text-xs text-accent-cyan flex items-center justify-center gap-1.5 font-medium">
                <Sparkles className="w-3.5 h-3.5" />
                {/* Names what was detected and what was selected. It no longer says a download
                    was selected for you: none is offered. */}
                Detected your Operating System: <span className="capitalize font-bold text-text-primary">{detectedOS}</span> (your platform is selected below)
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="pb-24">
        <div className="section-container">
          <div className="section-inner max-w-4xl">
            {/* Quick Web Access Card */}
            <div className="mb-8 p-6 rounded-2xl bg-gradient-to-r from-accent-cyan/10 via-accent-cyan/5 to-transparent border border-accent-cyan/30 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-xl bg-accent-cyan/20 border border-accent-cyan/40 flex items-center justify-center flex-shrink-0">
                  <Globe className="w-6 h-6 text-accent-cyan" />
                </div>
                <div>
                  <div className="text-base font-bold text-text-primary">Instant Web Access</div>
                  <div className="text-xs text-text-secondary">No installation required. Run directly in your browser.</div>
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
                    className={`relative flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all ${isActive ? 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/30' : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated border border-transparent'}`}>
                    {tab.label}
                    {isRecommended && <span className="ml-1 text-[10px] bg-accent-cyan text-text-inverse px-1.5 py-0.5 rounded-full font-bold">Recommended</span>}
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
                  <FileText className="w-5 h-5 text-accent-cyan" />
                  <h3 className="text-lg font-bold text-text-primary">Release Notes</h3>
                </div>
                <div className="space-y-6">
                  {RELEASE_NOTES.map((notes, i) => (
                    <div key={i}>
                      <div className="flex items-center gap-3 mb-3">
                        <span className="text-sm font-bold text-text-primary">{notes.version}</span>
                        <span className="text-xs font-mono text-text-muted">{notes.date}</span>
                        <span className="text-xs font-mono text-accent-cyan bg-accent-cyan-dim px-2 py-0.5 rounded">Latest</span>
                      </div>
                      <ul className="space-y-2">
                        {notes.changes.map((change, ci) => (
                          <li key={ci} className="flex items-start gap-2 text-sm text-text-secondary"><span className="w-1.5 h-1.5 rounded-full bg-accent-cyan mt-2 flex-shrink-0" />{change}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card-surface p-6 sm:p-8">
                <div className="flex items-center gap-3 mb-6">
                  <Cpu className="w-5 h-5 text-accent-cyan" />
                  <h3 className="text-lg font-bold text-text-primary">System Requirements</h3>
                </div>
                <div className="space-y-4">
                  {[{icon:Monitor,label:'Operating System',value:activeReqs.os},{icon:Cpu,label:'Processor',value:activeReqs.cpu},{icon:MemoryStick,label:'Memory',value:activeReqs.ram},{icon:HardDrive,label:'Storage',value:activeReqs.storage},{icon:Wifi,label:'Network',value:activeReqs.network},{icon:Monitor,label:'Display',value:activeReqs.display}].map((req,i) => (
                    <div key={i} className="flex items-start gap-3">
                      <req.icon className="w-4 h-4 text-text-muted mt-1 flex-shrink-0" />
                      <div>
                        <div className="text-xs font-mono text-text-muted uppercase mb-1">{req.label}</div>
                        <div className="text-sm text-text-primary">{req.value}</div>
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
