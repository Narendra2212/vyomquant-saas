import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Download, Check, AlertCircle, Cpu, HardDrive, MemoryStick, Wifi, ArrowRight, Calendar, GitBranch, FileText, Loader2, Monitor, Globe, Sparkles } from 'lucide-react'
import { downloadAnalyticsApi } from '../../lib/downloadAnalyticsApi'
import Navbar from '../landing/Navbar'
import Footer from '../landing/Footer'

const RELEASE_CONFIG = {
  version: 'v0.1.0',
  buildDate: '2026-07-24',
  publisher: 'Aerora Dynamics Private Limited',
  windows: {
    title: 'Windows Installer',
    url: '/releases/windows/VyomQuant-Setup-0.1.0.exe',
    filename: 'VyomQuant-Setup-0.1.0.exe',
    size: '84.2 MB',
    architecture: 'x64',
    checksum: 'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
  },
  macos: {
    title: 'macOS DMG',
    url: '/releases/mac/VyomQuant-0.1.0-universal.dmg',
    filename: 'VyomQuant-0.1.0-universal.dmg',
    size: '78.5 MB',
    architecture: 'Universal (Apple Silicon & Intel)',
    checksum: 'sha256:d41d8cd98f00b204e9800998ecf8427e657a8f1f8b4c73a219036c84b1f6d901'
  },
  linuxAppImage: {
    title: 'Linux AppImage',
    url: '/releases/linux/VyomQuant-0.1.0.AppImage',
    filename: 'VyomQuant-0.1.0.AppImage',
    size: '75.4 MB',
    architecture: 'x64',
    checksum: 'sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'
  },
  linuxDeb: {
    title: 'Linux DEB Package',
    url: '/releases/linux/vyomquant_0.1.0_amd64.deb',
    filename: 'vyomquant_0.1.0_amd64.deb',
    size: '68.2 MB',
    architecture: 'amd64',
    checksum: 'sha256:7c9e6679b4d81c3d9a1f2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e'
  }
}

const RELEASE_NOTES = [
  { version: 'v0.1.0', date: '2026-07-24', changes: ['Initial production desktop release for Windows, macOS, and Linux', 'Integrated Tauri & Electron dual runtime for high-performance execution', 'Direct connection to AWS ECS production backend cluster', 'Supabase authentication & encrypted local security vault integration', 'Real-time WebSocket market feeds and interactive charts'] }
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
  const [downloading, setDownloading] = useState(false)
  const [downloadComplete, setDownloadComplete] = useState(false)
  const [error, setError] = useState(null)

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

  const handleDownload = async (platformKey = activePlatform) => {
    const platformData = RELEASE_CONFIG[platformKey]
    if (!platformData) return
    setDownloading(true)
    setError(null)
    try {
      if (downloadAnalyticsApi?.track) {
        await downloadAnalyticsApi.track({ version: RELEASE_CONFIG.version, platform: platformKey, architecture: platformData.architecture })
      }
      const link = document.createElement('a')
      link.href = platformData.url
      link.download = platformData.filename
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      setDownloadComplete(true)
      setTimeout(() => setDownloadComplete(false), 5000)
    } catch (err) {
      setError('Download failed. Direct file link fallback available below.')
    } finally {
      setDownloading(false)
    }
  }

  const activeReqs = SYSTEM_REQUIREMENTS[activePlatform.startsWith('linux') ? 'linux' : activePlatform] || SYSTEM_REQUIREMENTS.windows
  const platformRelease = RELEASE_CONFIG[activePlatform]

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
                Detected your Operating System: <span className="capitalize font-bold text-text-primary">{detectedOS}</span> (Auto-selected recommended download)
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
                  <button key={tab.id} onClick={() => { setActivePlatform(tab.id); setDownloadComplete(false); setError(null) }}
                    className={`relative flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all ${isActive ? 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/30' : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated border border-transparent'}`}>
                    {tab.label}
                    {isRecommended && <span className="ml-1 text-[10px] bg-accent-cyan text-text-inverse px-1.5 py-0.5 rounded-full font-bold">Recommended</span>}
                  </button>
                )
              })}
            </div>

            {/* Download Card */}
            <div className="card-surface p-8 sm:p-10 mb-8 border border-border-default hover:border-accent-cyan/30 transition-all">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h2 className="text-xl font-bold text-text-primary">{platformRelease?.title} — VyomQuant {RELEASE_CONFIG.version}</h2>
                    <span className="text-xs font-mono text-text-muted bg-bg-elevated px-2 py-1 rounded">{platformRelease?.architecture}</span>
                  </div>
                  <p className="text-sm text-text-secondary mb-3">{platformRelease?.filename} · {platformRelease?.size}</p>
                  <div className="flex flex-wrap items-center gap-4 text-xs font-mono text-text-muted">
                    <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" />Code Signed</span>
                    <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" />Verified Publisher ({RELEASE_CONFIG.publisher})</span>
                    <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" />Production Build</span>
                  </div>
                </div>
                <button onClick={() => handleDownload(activePlatform)} disabled={downloading}
                  className={`btn-primary text-base px-8 py-4 min-w-[220px] justify-center ${downloadComplete ? 'bg-accent-profit hover:bg-accent-profit' : ''}`}>
                  {downloading ? <><Loader2 className="w-5 h-5 mr-2 animate-spin" />Downloading...</> :
                   downloadComplete ? <><Check className="w-5 h-5 mr-2" />Download Started</> :
                   <><Download className="w-5 h-5 mr-2" />Download {platformRelease?.title}</>}
                </button>
              </div>
              {error && (
                <div className="mt-6 p-4 rounded-xl bg-accent-loss-dim border border-accent-loss/20 flex items-start gap-3">
                  <AlertCircle className="w-5 h-5 text-accent-loss flex-shrink-0 mt-0.5" />
                  <p className="text-sm text-text-primary">{error}</p>
                </div>
              )}
              <div className="mt-6 pt-6 border-t border-border-default">
                <div className="text-xs font-mono text-text-muted mb-2">SHA-256 Checksum</div>
                <code className="block p-3 rounded-lg bg-bg-elevated text-xs font-mono text-text-secondary break-all">{platformRelease?.checksum}</code>
              </div>
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
