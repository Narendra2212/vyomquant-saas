import React from 'react'
import { Camera } from 'lucide-react'

/**
 * ScreenshotComingSoon — placeholder for actual app screenshots
 * Props:
 *   title: string — section name shown in the frame
 *   height: string — optional tailwind height class, default 'h-64'
 */
export default function ScreenshotComingSoon({ title = 'Screenshot', height = 'h-64' }) {
  return (
    <div className={`relative rounded-xl border border-border-default bg-bg-surface/60 overflow-hidden ${height}`}>
      {/* Terminal chrome */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border-default bg-bg-elevated/60">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-accent-loss/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-accent-gold/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-accent-profit/60" />
        </div>
        <span className="text-xs font-mono text-text-muted ml-2">vyomquant — {title.toLowerCase().replace(/ /g, '-')}</span>
      </div>

      {/* Placeholder content */}
      <div className="absolute inset-0 top-10 flex flex-col items-center justify-center gap-3">
        {/* Subtle grid */}
        <div
          className="absolute inset-0 opacity-30"
          style={{
            backgroundImage: 'linear-gradient(rgba(30,37,48,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(30,37,48,0.15) 1px, transparent 1px)',
            backgroundSize: '32px 32px'
          }}
        />
        <div className="relative z-10 flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-bg-elevated border border-border-default flex items-center justify-center">
            <Camera className="w-6 h-6 text-text-muted" />
          </div>
          <div className="px-3 py-1.5 rounded-full border border-accent-cyan/30 bg-accent-cyan-dim">
            <span className="text-xs font-mono text-accent-cyan">Screenshot Coming Soon</span>
          </div>
          <p className="text-xs text-text-muted font-mono text-center max-w-xs">
            {title} · Live preview available in the web app
          </p>
        </div>
      </div>
    </div>
  )
}
