import React from 'react'
import { Link } from 'react-router-dom'
import { Globe, ArrowRight } from 'lucide-react'

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

export default function FinalCTA() {
  return (
    <section className="relative py-24 lg:py-32 border-t border-border-default overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-accent-cyan/5 via-transparent to-transparent" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-accent-cyan/10 rounded-full blur-[100px] opacity-40" />

      <div className="section-container relative z-10">
        <div className="section-inner text-center">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-accent-cyan/25 bg-accent-cyan-dim mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-accent-cyan animate-pulse" />
            <span className="text-xs font-mono text-accent-cyan uppercase tracking-wider">Early Access — Apply Now</span>
          </div>

          <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4">
            Deploy Your First System
          </h2>
          <p className="text-text-secondary max-w-xl mx-auto mb-10 text-sm leading-relaxed">
            VyomQuant is in early access. Sign up in your browser or download the native desktop terminal.
            Paper trading enabled by default — zero capital required to start.
          </p>

          {/* 3 CTAs matching hero */}
          <div className="flex flex-col items-center gap-4 max-w-sm mx-auto sm:max-w-none">
            <Link
              to="/signup"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-3 px-8 py-4 rounded-xl font-semibold text-sm bg-accent-cyan text-text-inverse hover:bg-accent-cyan/90 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_0_32px_rgba(0,212,255,0.3)] animate-pulse-glow"
            >
              <Globe className="w-4 h-4" />
              Get Started Free
              <ArrowRight className="w-4 h-4" />
            </Link>

            <div className="flex flex-col sm:flex-row items-center gap-3 w-full sm:w-auto">
              <Link
                to="/download#windows"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2.5 px-6 py-3.5 rounded-xl border border-border-default bg-bg-surface/80 text-sm font-medium text-text-primary transition-all duration-200 hover:border-accent-cyan/40 hover:bg-bg-elevated hover:-translate-y-0.5"
              >
                <WindowsIcon className="w-4 h-4" />
                Download Windows
              </Link>
              <Link
                to="/download#macos"
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2.5 px-6 py-3.5 rounded-xl border border-border-default bg-bg-surface/80 text-sm font-medium text-text-primary transition-all duration-200 hover:border-accent-cyan/40 hover:bg-bg-elevated hover:-translate-y-0.5"
              >
                <AppleIcon className="w-4 h-4" />
                Download macOS
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
