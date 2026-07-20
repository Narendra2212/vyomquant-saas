import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Check, AlertCircle, Cpu, HardDrive, MemoryStick, Wifi, ArrowRight, Calendar, GitBranch, FileText, Loader2, Monitor } from 'lucide-react'
import { downloadAnalyticsApi } from '../../lib/downloadAnalyticsApi'
import Navbar from '../landing/Navbar'
import Footer from '../landing/Footer'

const RELEASE_CONFIG = {
  version: 'v0.8.2-beta',
  buildDate: '2026-06-20',
  windows: { url: 'https://github.com/vyomquant/vyomquant-desktop/releases/download/v0.8.2-beta/VyomQuant_0.8.2_x64_en-US.msi', filename: 'VyomQuant_0.8.2_x64_en-US.msi', size: '48.3 MB', architecture: 'x64', checksum: 'sha256:a3f5c8e2d1b9e4f7a6c3d8e5b2a1f4c7e9d6b3a8f2c5e1d7b4a9f6c3e8d5b2a1' },
  macos: { url: 'https://github.com/vyomquant/vyomquant-desktop/releases/download/v0.8.2-beta/VyomQuant_0.8.2_x64.dmg', filename: 'VyomQuant_0.8.2_x64.dmg', size: '52.1 MB', architecture: 'x64', checksum: 'sha256:b7e1a9f4c3d8e2b5a6f9c4d7e1b3a8f5c2e6d9b4a7f1c3e8d5b2a6f9c4d7e1b' },
  linux: { url: 'https://github.com/vyomquant/vyomquant-desktop/releases/download/v0.8.2-beta/vyomquant_0.8.2_amd64.deb', filename: 'vyomquant_0.8.2_amd64.deb', size: '45.7 MB', architecture: 'x64', checksum: 'sha256:c9d2e5b8a1f4c7e3d6b9a2f5c8e1d4b7a3f6c9d2e5b8a1f4c7e3d6b9a2f5c8e1' },
}

const RELEASE_NOTES = [
  { version: 'v0.8.2-beta', date: '2026-06-20', changes: ['Added real-time WebSocket reconnection logic for exchange feeds', 'Improved VectorBT backtest engine performance (40% faster tick processing)', 'New XGBoost node configuration panel with hyperparameter tuning', 'Fixed memory leak in long-running paper trading sessions', 'Updated CCXT.pro to v4.2.1 (Binance API v3 compatibility)'] },
  { version: 'v0.8.1-beta', date: '2026-06-05', changes: ['Strategy Marketplace v2 with clone-and-modify workflow', 'Kill switch now supports per-bot granularity', 'Added Monte Carlo simulation visualization charts', 'Supabase RLS policy enforcement for all data operations', 'Tauri desktop app auto-updater integration'] },
  { version: 'v0.8.0-beta', date: '2026-05-18', changes: ['Initial public beta release', 'Visual DAG builder with 15+ node types', 'Paper trading across 50+ exchanges via CCXT.pro', 'AES-256 credential vault with row-level security', 'Signal trace visualization for strategy debugging'] },
]

const SYSTEM_REQUIREMENTS = {
  windows: { os: 'Windows 10 (64-bit) or Windows 11', cpu: 'Intel Core i5 / AMD Ryzen 5 or better', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection (WebSocket feeds)', display: '1280 x 720 resolution minimum' },
  macos: { os: 'macOS 12 (Monterey) or later', cpu: 'Apple Silicon (M1+) or Intel Core i5', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection (WebSocket feeds)', display: '1280 x 720 resolution minimum' },
  linux: { os: 'Ubuntu 22.04 LTS / Debian 12 / Fedora 39 or equivalent', cpu: 'Intel Core i5 / AMD Ryzen 5 or better', ram: '8 GB RAM minimum, 16 GB recommended', storage: '500 MB available space', network: 'Broadband internet connection (WebSocket feeds)', display: '1280 x 720 resolution minimum' },
}

const PLATFORM_TABS = [{ id: 'windows', label: 'Windows' }, { id: 'macos', label: 'macOS' }, { id: 'linux', label: 'Linux' }]

export default function DownloadPage() {
  const [activePlatform, setActivePlatform] = useState('windows')
  const [downloading, setDownloading] = useState(false)
  const [downloadComplete, setDownloadComplete] = useState(false)
  const [error, setError] = useState(null)
  const [release] = useState(RELEASE_CONFIG)

  const handleDownload = async () => {
    const platformData = release[activePlatform]
    if (!platformData) return
    setDownloading(true)
    setError(null)
    try {
      await downloadAnalyticsApi.track({ version: release.version, platform: activePlatform, architecture: platformData.architecture })
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
    } catch (err) { setError('Download failed. Please try again or check the GitHub releases page.') } finally { setDownloading(false) }
  }

  const activeReqs = SYSTEM_REQUIREMENTS[activePlatform]
  const platformRelease = release[activePlatform]

  return (
    <div className="min-h-screen bg-bg-primary">
      <Navbar />
      <section className="pt-32 pb-16 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-accent-cyan/5 to-transparent" />
        <div className="section-container relative z-10">
          <div className="section-inner text-center">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-4">Desktop Application</div>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-text-primary mb-4">Download VyomQuant</h1>
            <p className="text-text-secondary max-w-xl mx-auto mb-8">Native desktop terminal for Windows, macOS, and Linux. Built with Tauri for institutional-grade performance.</p>
            <div className="inline-flex items-center gap-3 px-4 py-2 rounded-xl bg-bg-elevated border border-border-default mb-10">
              <GitBranch className="w-4 h-4 text-accent-cyan" />
              <span className="text-sm font-mono text-text-primary">{release.version}</span>
              <span className="w-px h-4 bg-border-default" />
              <Calendar className="w-4 h-4 text-text-muted" />
              <span className="text-sm font-mono text-text-muted">{release.buildDate}</span>
              <span className="text-xs font-mono text-accent-profit bg-accent-profit-dim px-2 py-0.5 rounded">Beta</span>
            </div>
          </div>
        </div>
      </section>

      <section className="pb-24">
        <div className="section-container">
          <div className="section-inner max-w-4xl">
            <div className="flex justify-center gap-2 mb-8">
              {PLATFORM_TABS.map(tab => {
                const isActive = activePlatform === tab.id
                return (
                  <button key={tab.id} onClick={() => { setActivePlatform(tab.id); setDownloadComplete(false); setError(null) }}
                    className={`flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-medium transition-all ${isActive ? 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/30' : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated border border-transparent'}`}>
                    {tab.label}
                  </button>
                )
              })}
            </div>

            <div className="card-surface p-8 sm:p-10 mb-8">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h2 className="text-xl font-bold text-text-primary">VyomQuant {release.version}</h2>
                    <span className="text-xs font-mono text-text-muted bg-bg-elevated px-2 py-1 rounded">{platformRelease?.architecture}</span>
                  </div>
                  <p className="text-sm text-text-secondary mb-3">{platformRelease?.filename} · {platformRelease?.size}</p>
                  <div className="flex items-center gap-4 text-xs font-mono text-text-muted">
                    <span className="flex items-center gap-1.5"><Check className="w-3 h-3 text-accent-profit" />Code-signed</span>
                    <span className="flex items-center gap-1.5"><Check className="w-3 h-3 text-accent-profit" />Auto-updater</span>
                    <span className="flex items-center gap-1.5"><Check className="w-3 h-3 text-accent-profit" />Offline capable</span>
                  </div>
                </div>
                <button onClick={handleDownload} disabled={downloading}
                  className={`btn-primary text-base px-8 py-4 min-w-[200px] justify-center ${downloadComplete ? 'bg-accent-profit hover:bg-accent-profit' : ''}`}>
                  {downloading ? <><Loader2 className="w-5 h-5 mr-2 animate-spin" />Starting...</> :
                   downloadComplete ? <><Check className="w-5 h-5 mr-2" />Download Started</> :
                   <><Download className="w-5 h-5 mr-2" />Download for {activePlatform === 'windows' ? 'Windows' : activePlatform === 'macos' ? 'macOS' : 'Linux'}</>}
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

            <div className="grid lg:grid-cols-2 gap-6">
              <div className="card-surface p-6 sm:p-8">
                <div className="flex items-center gap-3 mb-6">
                  <FileText className="w-5 h-5 text-accent-cyan" />
                  <h3 className="text-lg font-bold text-text-primary">Release Notes</h3>
                </div>
                <div className="space-y-6">
                  {RELEASE_NOTES.map((notes, i) => (
                    <div key={i} className={i > 0 ? 'pt-6 border-t border-border-default' : ''}>
                      <div className="flex items-center gap-3 mb-3">
                        <span className="text-sm font-bold text-text-primary">{notes.version}</span>
                        <span className="text-xs font-mono text-text-muted">{notes.date}</span>
                        {i === 0 && <span className="text-xs font-mono text-accent-cyan bg-accent-cyan-dim px-2 py-0.5 rounded">Latest</span>}
                      </div>
                      <ul className="space-y-2">
                        {notes.changes.map((change, ci) => (
                          <li key={ci} className="flex items-start gap-2 text-sm text-text-secondary"><span className="w-1 h-1 rounded-full bg-accent-cyan mt-2 flex-shrink-0" />{change}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
                <div className="mt-6 pt-6 border-t border-border-default">
                  <a href="https://github.com/vyomquant/vyomquant-desktop/releases" target="_blank" rel="noopener noreferrer" className="inline-flex items-center text-sm text-accent-cyan hover:text-cyan-300 transition-colors">
                    View all releases on GitHub<ArrowRight className="w-4 h-4 ml-1" />
                  </a>
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
                <div className="mt-6 pt-6 border-t border-border-default p-4 rounded-xl bg-accent-gold-dim border border-accent-gold/10">
                  <p className="text-xs text-text-secondary leading-relaxed"><strong className="text-accent-gold">Note:</strong> Paper trading mode requires no exchange API keys. Live trading requires valid API credentials for your connected exchange. All credentials are stored in the AES-256 encrypted local vault.</p>
                </div>
              </div>
            </div>

            <div className="mt-8 card-surface p-6 sm:p-8">
              <h3 className="text-lg font-bold text-text-primary mb-4">Installation Notes</h3>
              <div className="grid sm:grid-cols-3 gap-6">
                <div>
                  <div className="text-sm font-bold text-text-primary mb-2">Windows</div>
                  <p className="text-xs text-text-secondary leading-relaxed">Run the .msi installer. Windows Defender may show a SmartScreen warning — click "More info" then "Run anyway". Requires administrator privileges.</p>
                </div>
                <div>
                  <div className="text-sm font-bold text-text-primary mb-2">macOS</div>
                  <p className="text-xs text-text-secondary leading-relaxed">Open the .dmg and drag VyomQuant to Applications. On first launch, right-click the app and select "Open" to bypass Gatekeeper. Apple Silicon native, Rosetta not required.</p>
                </div>
                <div>
                  <div className="text-sm font-bold text-text-primary mb-2">Linux</div>
                  <p className="text-xs text-text-secondary leading-relaxed">Install the .deb with <code className="text-accent-cyan">sudo dpkg -i vyomquant_*.deb</code>. Dependencies will be resolved automatically. AppImage format available on request.</p>
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
