import React from 'react'
import { Cpu, Zap, Lock, Blocks } from 'lucide-react'

export default function ModernTradingSection() {
  const capabilities = [
    {
      icon: Zap,
      title: 'Native Execution Engine',
      desc: 'Direct market access to 50+ exchanges. Execute orders with sub-millisecond latency without routing through third-party platforms.'
    },
    {
      icon: Lock,
      title: 'Secure Local Execution',
      desc: 'With our desktop applications, your strategy logic remains on your machine. We never see your proprietary alpha.'
    },
    {
      icon: Blocks,
      title: 'Extensible Infrastructure',
      desc: 'Seamlessly integrate external alternative data feeds, custom Python models, and institutional FIX connections.'
    },
    {
      icon: Cpu,
      title: 'Advanced Order Types',
      desc: 'Natively support institutional execution algorithms including TWAP, VWAP, Iceberg, and dynamic trailing stops.'
    }
  ]

  return (
    <section id="capabilities" className="py-24 lg:py-32 border-t border-border-default bg-bg-surface/30 relative overflow-hidden">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 right-0 w-[600px] h-[600px] bg-accent-cyan/5 rounded-full blur-[150px]" />
      </div>

      <div className="section-container relative z-10">
        <div className="section-inner max-w-6xl mx-auto">
          {/* Header */}
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
              Built for Modern Systematic Trading
            </h2>
            <p className="text-text-secondary max-w-2xl mx-auto text-base">
              VyomQuant is a comprehensive, standalone quantitative research and execution environment engineered for scale, speed, and absolute reliability.
            </p>
          </div>

          {/* Capabilities Grid */}
          <div className="grid md:grid-cols-2 gap-6">
            {capabilities.map((item, i) => {
              const Icon = item.icon
              return (
                <div key={i} className="card-surface p-8 border border-border-default hover:border-accent-cyan/40 transition-all duration-300 group">
                  <div className="flex items-start gap-5">
                    <div className="w-12 h-12 rounded-xl bg-bg-elevated border border-border-active flex items-center justify-center flex-shrink-0 group-hover:bg-accent-cyan/10 group-hover:border-accent-cyan/30 transition-colors">
                      <Icon className="w-6 h-6 text-accent-cyan" />
                    </div>
                    <div>
                      <h3 className="text-xl font-bold text-text-primary mb-2">{item.title}</h3>
                      <p className="text-sm text-text-secondary leading-relaxed">
                        {item.desc}
                      </p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
