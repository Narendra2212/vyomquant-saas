import React from 'react'
import { Link } from 'react-router-dom'
import { Check, Globe, ArrowDown } from 'lucide-react'

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

export default function Hero() {
  return (
    <section className="relative min-h-[92vh] flex items-center pt-28 pb-20 sm:pt-36 sm:pb-24 lg:pt-40 lg:pb-32 overflow-hidden" aria-label="Hero">
      {/* Layered Background Glows & Grid */}
      <div className="absolute inset-0 bg-[#0a0e17] pointer-events-none">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[1100px] h-[750px] bg-accent-cyan/8 rounded-full blur-[170px] opacity-70" />
        <div className="absolute bottom-0 right-0 w-[600px] h-[450px] bg-accent-profit/5 rounded-full blur-[140px] opacity-60" />
        <div 
          className="absolute inset-0 opacity-40" 
          style={{
            backgroundImage: 'linear-gradient(rgba(0, 212, 255, 0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 212, 255, 0.06) 1px, transparent 1px)',
            backgroundSize: '64px 64px'
          }} 
        />
      </div>

      <div className="section-container relative z-10">
        <div className="section-inner flex flex-col items-center text-center">

          {/* Status badge */}
          <div className="inline-flex items-center gap-2.5 px-4.5 py-1.5 rounded-full border border-accent-cyan/30 bg-accent-cyan/10 backdrop-blur-md mb-8 shadow-[0_0_20px_rgba(0,212,255,0.15)]">
            <span className="w-2 h-2 rounded-full bg-accent-cyan animate-pulse" />
            <span className="text-xs font-mono text-accent-cyan font-semibold uppercase tracking-wider">Closed Beta — Early Access Available</span>
          </div>

          {/* Headline */}
          <h1 className="text-4xl sm:text-5xl lg:text-[clamp(2.75rem,5.5vw,5.2rem)] font-black tracking-tight leading-[1.05] max-w-4xl mb-8 text-text-primary">
            Build, Backtest and Deploy{' '}
            <span className="text-gradient-cyan drop-shadow-[0_0_35px_rgba(0,212,255,0.3)]">Quantitative Trading Strategies</span>{' '}
            Without Writing Code
          </h1>

          {/* Subheadline */}
          <p className="text-text-secondary text-lg sm:text-xl max-w-2xl leading-relaxed mb-12 font-normal">
            Institutional-grade algorithmic trading infrastructure featuring visual strategy design,
            vectorized backtesting, paper trading, AI assistance, and live execution.
          </p>

          {/* ── Platform Cards — above the fold ── */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 w-full max-w-4xl mb-10">
            {/* Web App Card */}
            <Link
              to="/app"
              className="group relative flex flex-col items-center gap-3.5 p-7 rounded-2xl border-2 border-accent-cyan/50 bg-accent-cyan/8 hover:bg-accent-cyan/15 hover:border-accent-cyan transition-all duration-300 hover:-translate-y-1.5 shadow-[0_0_30px_rgba(0,212,255,0.2)] hover:shadow-[0_0_50px_rgba(0,212,255,0.35)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              aria-label="Launch Web Application"
            >
              <div className="w-13 h-13 rounded-xl bg-accent-cyan flex items-center justify-center shadow-[0_0_25px_rgba(0,212,255,0.4)] group-hover:scale-105 transition-transform duration-300">
                <Globe className="w-6.5 h-6.5 text-text-inverse" />
              </div>
              <div className="text-center">
                <div className="text-base font-bold text-text-primary mb-0.5">Web Application</div>
                <div className="text-xs text-text-secondary font-mono">Browser Access</div>
              </div>
              <div className="mt-auto inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent-cyan text-text-inverse text-xs font-bold group-hover:bg-accent-cyan/90 transition-colors shadow-md">
                Launch Web App
              </div>
            </Link>

            {/* Windows Card */}
            <Link
              to="/download#windows"
              className="group relative flex flex-col items-center gap-3.5 p-7 rounded-2xl border-2 border-border-default bg-bg-surface/70 hover:border-accent-cyan/50 hover:bg-bg-elevated/80 transition-all duration-300 hover:-translate-y-1.5 shadow-lg hover:shadow-2xl hover:shadow-accent-cyan/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              aria-label="Download Windows Desktop App"
            >
              <div className="w-13 h-13 rounded-xl bg-bg-elevated border border-border-default flex items-center justify-center group-hover:border-accent-cyan/40 transition-colors group-hover:scale-105 duration-300">
                <WindowsIcon className="w-6.5 h-6.5 text-text-primary" />
              </div>
              <div className="text-center">
                <div className="text-base font-bold text-text-primary mb-0.5">Windows Desktop App</div>
                <div className="text-xs text-text-secondary font-mono">64-bit Installer</div>
              </div>
              <div className="mt-auto inline-flex items-center gap-2 px-5 py-2.5 rounded-xl border border-border-default text-xs font-semibold text-text-secondary group-hover:text-text-primary group-hover:border-accent-cyan/50 transition-colors">
                <ArrowDown className="w-3.5 h-3.5" />
                Windows Download
              </div>
            </Link>

            {/* macOS Card */}
            <Link
              to="/download#macos"
              className="group relative flex flex-col items-center gap-3.5 p-7 rounded-2xl border-2 border-border-default bg-bg-surface/70 hover:border-accent-cyan/50 hover:bg-bg-elevated/80 transition-all duration-300 hover:-translate-y-1.5 shadow-lg hover:shadow-2xl hover:shadow-accent-cyan/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
              aria-label="Download macOS Desktop App"
            >
              <div className="w-13 h-13 rounded-xl bg-bg-elevated border border-border-default flex items-center justify-center group-hover:border-accent-cyan/40 transition-colors group-hover:scale-105 duration-300">
                <AppleIcon className="w-6.5 h-6.5 text-text-primary" />
              </div>
              <div className="text-center">
                <div className="text-base font-bold text-text-primary mb-0.5">macOS Desktop App</div>
                <div className="text-xs text-text-secondary font-mono">Apple Silicon & Intel</div>
              </div>
              <div className="mt-auto inline-flex items-center gap-2 px-5 py-2.5 rounded-xl border border-border-default text-xs font-semibold text-text-secondary group-hover:text-text-primary group-hover:border-accent-cyan/50 transition-colors">
                <ArrowDown className="w-3.5 h-3.5" />
                macOS Download
              </div>
            </Link>
          </div>

          {/* Platform availability checkmarks */}
          <div className="flex flex-wrap justify-center items-center gap-x-8 gap-y-3 text-text-muted text-sm font-medium mb-16">
            <span className="flex items-center gap-2.5"><Check className="w-4.5 h-4.5 text-accent-profit" /> Web Application</span>
            <span className="flex items-center gap-2.5"><Check className="w-4.5 h-4.5 text-accent-profit" /> Windows Desktop App</span>
            <span className="flex items-center gap-2.5"><Check className="w-4.5 h-4.5 text-accent-profit" /> macOS Desktop App</span>
          </div>

          {/* DAG terminal mockup */}
          <div className="w-full max-w-5xl">
            <div className="relative rounded-2xl border border-border-default/80 bg-bg-surface/90 backdrop-blur-md overflow-hidden shadow-[0_20px_60px_rgba(0,0,0,0.6)] group hover:border-accent-cyan/30 transition-all duration-300">
              {/* Title bar */}
              <div className="flex items-center gap-2 px-5 py-3.5 border-b border-border-default bg-bg-elevated/80">
                <div className="flex gap-2">
                  <div className="w-3 h-3 rounded-full bg-accent-loss/80" />
                  <div className="w-3 h-3 rounded-full bg-accent-gold/80" />
                  <div className="w-3 h-3 rounded-full bg-accent-profit/80" />
                </div>
                <div className="flex-1 text-center">
                  <span className="text-xs font-mono text-text-muted tracking-wide">vyomquant — strategy-builder-dag</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-accent-profit-dim border border-accent-profit/30">
                  <span className="w-1.5 h-1.5 rounded-full bg-accent-profit animate-pulse" />
                  <span className="text-[10px] font-mono font-bold text-accent-profit uppercase tracking-wider">LIVE EXECUTION</span>
                </div>
              </div>

              {/* DAG nodes */}
              <div className="p-6 sm:p-10 min-h-[280px] sm:min-h-[340px] relative">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-6">
                  <div className="card-surface p-5 border-l-4 border-l-accent-cyan shadow-md">
                    <div className="text-xs font-mono text-text-muted mb-1">Data Feed</div>
                    <div className="text-base font-bold text-text-primary">BTC/USDT Tick</div>
                    <div className="text-xs font-mono text-accent-profit mt-1.5 flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-accent-profit animate-ping" />
                      WebSocket: Connected
                    </div>
                  </div>
                  <div className="hidden sm:flex items-center justify-center">
                    <div className="w-full h-px bg-border-default relative">
                      <div className="absolute right-0 -top-1 w-2 h-2 border-t border-r border-text-muted rotate-45" />
                    </div>
                  </div>
                  <div className="card-surface p-5 border-l-4 border-l-accent-gold shadow-md">
                    <div className="text-xs font-mono text-text-muted mb-1">Indicator Node</div>
                    <div className="text-base font-bold text-text-primary">RSI(14) + EMA(50)</div>
                    <div className="text-xs font-mono text-text-secondary mt-1.5">Period: 1h</div>
                  </div>
                  <div className="card-surface p-5 border-l-4 border-l-accent-cyan sm:col-start-1 shadow-md">
                    <div className="text-xs font-mono text-text-muted mb-1">Logic Gate</div>
                    <div className="text-base font-bold text-text-primary">Crossover + Threshold</div>
                    <div className="text-xs font-mono text-text-secondary mt-1.5">AND condition</div>
                  </div>
                  <div className="hidden sm:flex items-center justify-center">
                    <div className="w-full h-px bg-border-default relative">
                      <div className="absolute right-0 -top-1 w-2 h-2 border-t border-r border-text-muted rotate-45" />
                    </div>
                  </div>
                  <div className="card-surface p-5 border-l-4 border-l-accent-profit shadow-md">
                    <div className="text-xs font-mono text-text-muted mb-1">Execution Node</div>
                    <div className="text-base font-bold text-text-primary">Limit Buy + Stop</div>
                    <div className="text-xs font-mono text-accent-profit mt-1.5">Order Routed to Binance</div>
                  </div>
                </div>

                {/* Floating backtest stats */}
                <div className="absolute top-4 right-4 flex flex-col gap-2">
                  <div className="bg-bg-elevated/95 border border-border-default rounded-lg px-3.5 py-1.5 text-xs font-mono shadow-md backdrop-blur-sm">
                    <span className="text-text-muted">Sharpe:</span>{' '}<span className="text-accent-cyan font-bold">2.1</span>
                  </div>
                  <div className="bg-bg-elevated/95 border border-border-default rounded-lg px-3.5 py-1.5 text-xs font-mono shadow-md backdrop-blur-sm">
                    <span className="text-text-muted">Win Rate:</span>{' '}<span className="text-accent-profit font-bold">67.4%</span>
                  </div>
                  <div className="bg-bg-elevated/95 border border-border-default rounded-lg px-3.5 py-1.5 text-xs font-mono shadow-md backdrop-blur-sm">
                    <span className="text-text-muted">Max DD:</span>{' '}<span className="text-accent-loss font-bold">-8.2%</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </section>
  )
}
