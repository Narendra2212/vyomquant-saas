import React from 'react'

function ScreenshotPlaceholder({ title, description, badge }) {
  return (
    <div className="w-full mb-16 last:mb-0">
      <div className="flex flex-col md:flex-row md:items-end justify-between mb-6 gap-4">
        <div>
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-accent-cyan-dim border border-accent-cyan/20 mb-3">
            <span className="text-[10px] font-mono text-accent-cyan uppercase">{badge}</span>
          </div>
          <h3 className="text-2xl font-bold text-text-primary mb-2">{title}</h3>
          <p className="text-text-secondary max-w-xl">{description}</p>
        </div>
      </div>

      <div className="w-full aspect-[16/9] md:aspect-[21/9] rounded-2xl border border-border-default bg-bg-elevated relative overflow-hidden flex items-center justify-center group shadow-2xl shadow-black/20">
        {/* Placeholder styling */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-bg-surface to-bg-primary" />
        <div className="absolute inset-0" style={{
          backgroundImage: 'linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)',
          backgroundSize: '40px 40px'
        }} />
        
        <div className="relative z-10 flex flex-col items-center">
          <div className="w-16 h-16 rounded-2xl bg-bg-surface border border-border-active flex items-center justify-center mb-4 shadow-lg group-hover:scale-105 transition-transform duration-500">
            <span className="text-text-muted font-mono text-xs">IMG</span>
          </div>
          <span className="font-mono text-sm text-text-muted">Screenshot Coming Soon</span>
        </div>
      </div>
    </div>
  )
}

export default function ScreenshotsSection() {
  return (
    <section id="screenshots" className="py-24 lg:py-32 border-t border-border-default relative">
      <div className="section-container">
        <div className="text-center mb-20">
          <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
            See VyomQuant In Action
          </h2>
          <p className="text-text-secondary max-w-2xl mx-auto text-base">
            Institutional-grade quantitative tools made accessible through a modern, responsive interface.
          </p>
        </div>

        <div className="max-w-6xl mx-auto">
          <ScreenshotPlaceholder 
            badge="Visual Constructor"
            title="Strategy Builder Screenshot" 
            description="Drag and drop nodes to construct trading logic, indicators, and execution parameters."
          />
          <ScreenshotPlaceholder 
            badge="Vectorized Engine"
            title="Backtesting Screenshot" 
            description="Analyze historical performance with deep metrics, drawdown charts, and trade logs."
          />
          <ScreenshotPlaceholder 
            badge="Performance Tracking"
            title="Portfolio Analytics Screenshot" 
            description="Monitor live strategies, paper trading positions, and overall portfolio health in real-time."
          />
          <ScreenshotPlaceholder 
            badge="Risk Controls"
            title="Risk Management Screenshot" 
            description="Configure automated circuit breakers, max drawdown limits, and real-time capital protection."
          />
        </div>
      </div>
    </section>
  )
}
