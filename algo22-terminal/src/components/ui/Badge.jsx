import React from 'react'

const variants = {
  cyan: 'bg-accent-cyan-dim text-accent-cyan',
  gold: 'bg-accent-gold-dim text-accent-gold',
  profit: 'bg-accent-profit-dim text-accent-profit',
  loss: 'bg-accent-loss-dim text-accent-loss',
  muted: 'bg-bg-elevated text-text-muted',
}

export function Badge({ children, variant = 'cyan', className = '' }) {
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded text-xs font-mono font-medium uppercase tracking-wider ${variants[variant] || variants.cyan} ${className}`}>
      {children}
    </span>
  )
}
