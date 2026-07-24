import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Globe, ArrowDown, Sparkles } from 'lucide-react'

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
  const [userOS, setUserOS] = useState('windows')

  useEffect(() => {
    const ua = navigator.userAgent.toLowerCase()
    if (ua.includes('win')) setUserOS('windows')
    else if (ua.includes('mac')) setUserOS('macos')
    else if (ua.includes('linux')) setUserOS('linux')
  }, [])

  return (
    <section id="download" className="py-24 lg:py-32 border-t border-border-default relative bg-bg-surface/20">
      <div className="section-container">
        <div className="text-center mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-cyan-dim border border-accent-cyan/30 text-xs font-mono text-accent-cyan mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            v0.1.0 Production Desktop Release
          </div>
          <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
            Choose Your Platform
          </h2>
          <p className="text-text-secondary max-w-xl mx-auto text-base">
            Trade anywhere with our high-performance web platform or install native desktop terminals for Windows, macOS, and Linux.
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

          {/* Windows */}
          <div className={`card-surface p-8 flex flex-col items-center text-center border transition-all duration-300 ${userOS === 'windows' ? 'border-accent-cyan/60 bg-accent-cyan/5 shadow-[0_0_15px_rgba(0,212,255,0.15)]' : 'border-border-default hover:border-border-active'}`}>
            {userOS === 'windows' && <span className="mb-3 text-[10px] bg-accent-cyan text-text-inverse px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Recommended for You</span>}
            <div className="w-14 h-14 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-5">
              <WindowsIcon className="w-7 h-7 text-text-primary" />
            </div>
            <h3 className="text-lg font-bold text-text-primary mb-1">Windows</h3>
            <p className="text-xs text-text-secondary mb-4">
              Native .exe installer for 64-bit Windows 10/11.
            </p>
            <div className="text-[11px] font-mono text-text-muted mb-6">v0.1.0 · 84.2 MB</div>
            <a
              href="/releases/windows/VyomQuant-Setup-0.1.0.exe"
              download
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-semibold hover:border-accent-cyan/40 transition-colors text-sm"
            >
              <ArrowDown className="w-4 h-4" />
              Download for Windows
            </a>
          </div>

          {/* macOS */}
          <div className={`card-surface p-8 flex flex-col items-center text-center border transition-all duration-300 ${userOS === 'macos' ? 'border-accent-cyan/60 bg-accent-cyan/5 shadow-[0_0_15px_rgba(0,212,255,0.15)]' : 'border-border-default hover:border-border-active'}`}>
            {userOS === 'macos' && <span className="mb-3 text-[10px] bg-accent-cyan text-text-inverse px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Recommended for You</span>}
            <div className="w-14 h-14 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-5">
              <AppleIcon className="w-7 h-7 text-text-primary" />
            </div>
            <h3 className="text-lg font-bold text-text-primary mb-1">macOS</h3>
            <p className="text-xs text-text-secondary mb-4">
              Universal DMG for Apple Silicon & Intel Macs.
            </p>
            <div className="text-[11px] font-mono text-text-muted mb-6">v0.1.0 · 78.5 MB</div>
            <a
              href="/releases/mac/VyomQuant-0.1.0-universal.dmg"
              download
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-semibold hover:border-accent-cyan/40 transition-colors text-sm"
            >
              <ArrowDown className="w-4 h-4" />
              Download for macOS
            </a>
          </div>

          {/* Linux */}
          <div className={`card-surface p-8 flex flex-col items-center text-center border transition-all duration-300 ${userOS === 'linux' ? 'border-accent-cyan/60 bg-accent-cyan/5 shadow-[0_0_15px_rgba(0,212,255,0.15)]' : 'border-border-default hover:border-border-active'}`}>
            {userOS === 'linux' && <span className="mb-3 text-[10px] bg-accent-cyan text-text-inverse px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Recommended for You</span>}
            <div className="w-14 h-14 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-5">
              <LinuxIcon className="w-7 h-7 text-text-primary" />
            </div>
            <h3 className="text-lg font-bold text-text-primary mb-1">Linux</h3>
            <p className="text-xs text-text-secondary mb-4">
              Standalone AppImage and Debian DEB packages.
            </p>
            <div className="text-[11px] font-mono text-text-muted mb-4">v0.1.0 · AppImage & DEB</div>
            <div className="mt-auto w-full flex flex-col gap-2">
              <a
                href="/releases/linux/VyomQuant-0.1.0.AppImage"
                download
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-medium hover:border-accent-cyan/40 transition-colors text-xs"
              >
                <ArrowDown className="w-3.5 h-3.5" />
                Download AppImage
              </a>
              <a
                href="/releases/linux/vyomquant_0.1.0_amd64.deb"
                download
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-medium hover:border-accent-cyan/40 transition-colors text-xs"
              >
                <ArrowDown className="w-3.5 h-3.5" />
                Download DEB
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
