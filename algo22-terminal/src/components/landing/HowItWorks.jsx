import React from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, BarChart3, Rocket, ArrowRight } from 'lucide-react'

const steps = [
  { icon: GitBranch, title: 'Build', description: 'Construct systematic rules via drag-and-drop node interface. No programming required.' },
  { icon: BarChart3, title: 'Validate', description: 'Run VectorBT backtests across historical tick data. Monte Carlo stress testing included.' },
  { icon: Rocket, title: 'Deploy', description: 'Deploy to paper or live environment via single action. Monitor in real time.' },
]

export default function HowItWorks() {
  return (
    <section className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Workflow</div>
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary">Three-Step Deployment</h2>
          </div>
          <div className="max-w-4xl mx-auto">
            <div className="grid md:grid-cols-3 gap-8 relative">
              <div className="hidden md:block absolute top-12 left-0 right-0 h-px bg-border-default">
                <div className="absolute inset-0 bg-gradient-to-r from-accent-cyan via-accent-cyan to-transparent opacity-30" />
              </div>
              {steps.map((step, i) => {
                const Icon = step.icon
                return (
                  <div key={i} className="relative text-center">
                    <div className="w-24 h-24 rounded-full bg-bg-elevated border border-border-default flex items-center justify-center mx-auto mb-6 relative z-10">
                      <div className="w-16 h-16 rounded-full bg-accent-cyan-dim flex items-center justify-center">
                        <Icon className="w-7 h-7 text-accent-cyan" />
                      </div>
                    </div>
                    <div className="text-xs font-mono text-accent-cyan mb-2">STEP {i + 1}</div>
                    <h3 className="text-lg font-bold text-text-primary mb-2">{step.title}</h3>
                    <p className="text-sm text-text-secondary leading-relaxed">{step.description}</p>
                  </div>
                )
              })}
            </div>
            <div className="text-center mt-12">
              <Link to="/signup" className="btn-primary">
                Build System
                <ArrowRight className="w-4 h-4 ml-2" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
