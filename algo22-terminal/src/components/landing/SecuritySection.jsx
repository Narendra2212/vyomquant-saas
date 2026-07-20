import React from 'react'
import { Check, ShieldCheck, Key, Users, FileSearch, Lock, Server } from 'lucide-react'

export default function SecuritySection() {
  const securityFeatures = [
    {
      icon: ShieldCheck,
      title: 'AES-256 Encryption',
      desc: 'All sensitive data and API keys are encrypted at rest using industry-standard AES-256 encryption.'
    },
    {
      icon: Key,
      title: 'Secure API Key Storage',
      desc: 'Exchange credentials are never stored in plaintext and are injected directly into the execution environment at runtime.'
    },
    {
      icon: Users,
      title: 'Role-Based Access Control',
      desc: 'Granular RBAC ensures users and organizations only have access to authorized strategies and sub-accounts.'
    },
    {
      icon: FileSearch,
      title: 'Audit Logging',
      desc: 'Comprehensive, immutable audit trails for all critical actions including logins, strategy modifications, and order routing.'
    },
    {
      icon: Lock,
      title: 'Encrypted Credentials',
      desc: 'Password hashing using Argon2id and strict secure cookie policies for all authentication tokens.'
    },
    {
      icon: Server,
      title: 'Protected Trading Infrastructure',
      desc: 'Execution engines run in isolated, protected VPC environments securely bridged to exchange networks.'
    }
  ]

  return (
    <section id="security" className="py-24 lg:py-32 border-t border-border-default relative overflow-hidden bg-bg-surface/30">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[500px] bg-accent-cyan/3 rounded-full blur-[120px]" />
      </div>

      <div className="section-container relative z-10">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-border-active bg-bg-elevated mb-6">
              <ShieldCheck className="w-3.5 h-3.5 text-accent-cyan" />
              <span className="text-xs font-mono text-text-secondary uppercase tracking-wider">Enterprise Security</span>
            </div>
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
              Security & Infrastructure
            </h2>
            <p className="text-text-secondary max-w-2xl mx-auto text-base">
              VyomQuant employs defense-in-depth architecture to ensure your proprietary trading strategies and exchange API keys remain fundamentally secure.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {securityFeatures.map((feat, i) => {
              const Icon = feat.icon
              return (
                <div key={i} className="card-surface p-6 group hover:border-accent-cyan/30 transition-colors duration-300">
                  <div className="w-10 h-10 rounded-lg bg-bg-elevated border border-border-default flex items-center justify-center mb-5 group-hover:border-accent-cyan/40 group-hover:bg-accent-cyan/10 transition-colors">
                    <Icon className="w-5 h-5 text-accent-cyan" />
                  </div>
                  <h3 className="text-base font-bold text-text-primary mb-2 flex items-center gap-2">
                    {feat.title}
                  </h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    {feat.desc}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
