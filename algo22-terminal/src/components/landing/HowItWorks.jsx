import React from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, Activity, ShieldCheck, Rocket, ArrowRight } from 'lucide-react'

const steps = [
  { 
    icon: GitBranch, 
    title: 'Construct DAG Rules', 
    description: 'Map data feeds, technical indicators, logic gates, and execution parameters visually without coding.' 
  },
  { 
    icon: Activity, 
    title: 'VectorBT Simulation', 
    description: 'Execute tick-level backtests across multi-year historical datasets in seconds with Monte Carlo analysis.' 
  },
  { 
    icon: ShieldCheck, 
    title: 'Configure Risk Guards', 
    description: 'Enforce account drawdown ceilings, margin limits, and volatility circuit breakers before execution.' 
  },
  { 
    icon: Rocket, 
    title: 'Forward Test & Deploy', 
    description: 'Validate live market behavior in paper mode with simulated capital, then route orders to connected exchanges.' 
  },
]

export default function HowItWorks() {
  return (
    <section id="workflow" className="py-24 lg:py-32 border-t border-border-default bg-bg-surface/30" aria-label="Systematic Workflow">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3 font-semibold">Workflow</div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">
              The Systematic Pipeline
            </h2>
            <p className="text-text-secondary max-w-xl mx-auto text-base sm:text-lg">
              From raw market data to disciplined execution in four rigorous steps.
            </p>
          </div>
          
          <div className="max-w-5xl mx-auto">
            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6 relative">
              {steps.map((step, i) => {
                const Icon = step.icon
                return (
                  <div key={i} className="card-surface p-6 flex flex-col items-center text-center relative group hover:border-accent-cyan/40 transition-all duration-300">
                    <div className="w-16 h-16 rounded-2xl bg-bg-elevated border border-border-default flex items-center justify-center mb-5 group-hover:bg-accent-cyan/10 group-hover:border-accent-cyan/30 transition-colors">
                      <Icon className="w-7 h-7 text-accent-cyan" />
                    </div>
                    <div className="text-[11px] font-mono text-accent-cyan font-bold tracking-wider mb-2">STEP 0{i + 1}</div>
                    <h3 className="text-base font-bold text-text-primary mb-2">{step.title}</h3>
                    <p className="text-xs text-text-secondary leading-relaxed">{step.description}</p>
                  </div>
                )
              })}
            </div>

            <div className="text-center mt-12">
              <Link 
                to="/signup" 
                className="inline-flex items-center gap-2.5 px-8 py-4 rounded-xl font-bold text-sm bg-accent-cyan text-text-inverse hover:bg-accent-cyan/90 transition-all shadow-[0_0_30px_rgba(0,212,255,0.3)] hover:-translate-y-0.5"
              >
                Get Started Free
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

