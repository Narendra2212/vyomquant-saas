import React from 'react'
import { Link } from 'react-router-dom'
import { Globe, ArrowDown } from 'lucide-react'

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

export default function DownloadSection() {
  return (
    <section id="download" className="py-24 lg:py-32 border-t border-border-default relative bg-bg-surface/20">
      <div className="section-container">
        <div className="text-center mb-16">
          <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
            Choose Your Platform
          </h2>
          <p className="text-text-secondary max-w-xl mx-auto text-base">
            Trade anywhere with our web application, or install the native desktop terminal for an optimized, low-latency execution environment.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
          {/* Web App */}
          <div className="card-surface p-8 flex flex-col items-center text-center border-2 border-accent-cyan/20 bg-accent-cyan/5 hover:border-accent-cyan/50 hover:bg-accent-cyan/10 transition-all duration-300">
            <div className="w-16 h-16 rounded-2xl bg-accent-cyan flex items-center justify-center mb-6 shadow-[0_0_20px_rgba(0,212,255,0.3)]">
              <Globe className="w-8 h-8 text-text-inverse" />
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">Web Application</h3>
            <p className="text-sm text-text-secondary mb-8">
              Instant browser access with no installation.
            </p>
            <Link
              to="/app"
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-accent-cyan text-text-inverse font-semibold hover:bg-accent-cyan/90 transition-colors"
            >
              Launch Web App
            </Link>
          </div>

          {/* Windows */}
          <div className="card-surface p-8 flex flex-col items-center text-center border border-border-default hover:border-border-active transition-all duration-300">
            <div className="w-16 h-16 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-6">
              <WindowsIcon className="w-8 h-8 text-text-primary" />
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">Windows Desktop App</h3>
            <p className="text-sm text-text-secondary mb-8">
              Optimized desktop experience for serious traders.
            </p>
            <a
              href="/download#windows"
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-semibold hover:border-accent-cyan/40 transition-colors"
            >
              <ArrowDown className="w-4 h-4" />
              Download for Windows
            </a>
          </div>

          {/* macOS */}
          <div className="card-surface p-8 flex flex-col items-center text-center border border-border-default hover:border-border-active transition-all duration-300">
            <div className="w-16 h-16 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-6">
              <AppleIcon className="w-8 h-8 text-text-primary" />
            </div>
            <h3 className="text-xl font-bold text-text-primary mb-2">macOS Desktop App</h3>
            <p className="text-sm text-text-secondary mb-8">
              Native experience for Apple Silicon and Intel Macs.
            </p>
            <a
              href="/download#macos"
              className="mt-auto w-full inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl border border-border-default bg-bg-elevated text-text-primary font-semibold hover:border-accent-cyan/40 transition-colors"
            >
              <ArrowDown className="w-4 h-4" />
              Download for macOS
            </a>
          </div>
        </div>
      </div>
    </section>
  )
}
