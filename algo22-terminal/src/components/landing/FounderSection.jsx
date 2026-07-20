import React from 'react'
import { GraduationCap, Target } from 'lucide-react'

export default function FounderSection() {
  return (
    <section id="founder" className="py-24 lg:py-32 border-t border-border-default relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute bottom-0 left-0 w-[500px] h-[400px] bg-accent-cyan/5 rounded-full blur-[100px]" />
      </div>

      <div className="section-container relative z-10 max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
            Founder
          </h2>
        </div>

        <div className="card-surface p-8 sm:p-12 border-2 border-border-default relative overflow-hidden">
          {/* Subtle decoration */}
          <div className="absolute top-0 right-0 w-64 h-64 bg-accent-cyan/5 rounded-full blur-[60px]" />
          
          <div className="relative z-10 flex flex-col md:flex-row gap-10 items-center md:items-start">
            
            {/* Avatar Placeholder / Initial */}
            <div className="w-32 h-32 rounded-2xl bg-bg-elevated border border-border-active flex items-center justify-center flex-shrink-0 shadow-xl">
              <span className="text-4xl font-black text-text-muted">NT</span>
            </div>

            {/* Content */}
            <div className="flex-1 text-center md:text-left">
              <h3 className="text-2xl font-bold text-text-primary mb-1">Narendra Tripathi</h3>
              <p className="text-text-secondary font-mono text-sm mb-6">Founder, VyomQuant</p>

              <div className="flex flex-col gap-4 mb-8">
                <div className="flex items-center justify-center md:justify-start gap-2.5">
                  <GraduationCap className="w-5 h-5 text-accent-cyan flex-shrink-0" />
                  <p className="text-sm text-text-primary font-medium">
                    NIT Andhra Pradesh Alumnus
                  </p>
                </div>
              </div>

              <div className="space-y-4">
                <p className="text-sm text-text-secondary leading-relaxed">
                  Focused on building accessible quantitative trading infrastructure for retail and professional systematic traders.
                </p>
              </div>

              <div className="mt-8 p-5 rounded-xl bg-accent-cyan/5 border border-accent-cyan/20">
                <div className="flex items-start gap-3">
                  <Target className="w-5 h-5 text-accent-cyan flex-shrink-0 mt-0.5" />
                  <div>
                    <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-1">Mission</div>
                    <p className="text-sm text-text-primary leading-relaxed font-medium">
                      Make institutional-grade systematic trading infrastructure accessible to every trader.
                    </p>
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
