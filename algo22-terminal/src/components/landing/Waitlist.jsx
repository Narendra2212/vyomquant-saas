import React from 'react'
import WaitlistForm from '../waitlist/WaitlistForm'

export default function Waitlist() {
  return (
    <section id="waitlist" className="relative py-24 lg:py-32 border-t border-border-default">
      <div className="absolute inset-0 bg-gradient-to-b from-accent-cyan/5 to-transparent" />
      <div className="section-container relative z-10">
        <div className="section-inner">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div>
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-4">Priority Access</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Stay in the Loop</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                VyomQuant is in early access. You can <a href="/signup" className="text-accent-cyan underline underline-offset-2 hover:text-accent-cyan/80 transition-colors">sign up immediately</a> to start building and backtesting strategies.
                Register below to receive priority onboarding, platform updates, and new feature announcements.
              </p>
              <div className="space-y-3">
                {[
                  { step: '1', title: 'Submit your details', desc: 'Name, email, and trading experience level' },
                  { step: '2', title: 'Receive updates', desc: 'Platform announcements and onboarding tips sent directly' },
                  { step: '3', title: 'Get priority support', desc: 'Early registrants receive dedicated onboarding assistance' },
                ].map(item => (
                  <div key={item.step} className="flex items-start gap-3">
                    <div className="w-5 h-5 rounded-full bg-accent-cyan-dim flex items-center justify-center flex-shrink-0 mt-0.5">
                      <span className="text-accent-cyan text-xs font-bold">{item.step}</span>
                    </div>
                    <div>
                      <div className="text-sm font-medium text-text-primary">{item.title}</div>
                      <div className="text-xs text-text-secondary">{item.desc}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div><WaitlistForm /></div>
          </div>
        </div>
      </div>
    </section>
  )
}
