import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { 
  GitBranch, Activity, Shield, PieChart, 
  ArrowRight, Check, Play, Terminal, Database, 
  Lock, AlertTriangle, ChevronRight, BarChart3, 
  Layers, Sliders, Cpu, Gauge
} from 'lucide-react'

export default function ScreenshotsSection() {
  const [activeTab, setActiveTab] = useState('builder')

  const tabs = [
    { id: 'builder', label: 'Visual DAG Builder', icon: GitBranch, badge: 'Strategy Constructor' },
    { id: 'backtest', label: 'VectorBT Simulation', icon: Activity, badge: 'Vectorized Engine' },
    { id: 'execution', label: 'Paper Trading Terminal', icon: Terminal, badge: 'Live Telemetry' },
    { id: 'risk', label: 'Risk Control Center', icon: Shield, badge: 'Safety Guards' },
  ]

  return (
    <section id="architecture" className="py-24 lg:py-32 border-t border-border-default relative overflow-hidden bg-bg-surface/20" aria-label="Platform Architecture">
      <div className="section-container">
        <div className="section-inner max-w-6xl mx-auto">
          
          {/* Section Header */}
          <div className="text-center mb-14">
            <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full border border-accent-cyan/30 bg-accent-cyan/10 mb-4">
              <Cpu className="w-3.5 h-3.5 text-accent-cyan" />
              <span className="text-xs font-mono text-accent-cyan uppercase tracking-wider font-semibold">Platform Architecture</span>
            </div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">
              Engineered for Systematic Precision
            </h2>
            <p className="text-text-secondary max-w-2xl mx-auto text-base sm:text-lg">
              Explore the four core engines powering the VyomQuant quantitative pipeline.
            </p>
          </div>

          {/* Tab Selector Buttons */}
          <div className="flex flex-wrap items-center justify-center gap-2.5 mb-10 p-1.5 rounded-2xl bg-bg-surface border border-border-default/80 max-w-3xl mx-auto shadow-lg">
            {tabs.map(tab => {
              const Icon = tab.icon
              const isActive = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs sm:text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${
                    isActive 
                      ? 'bg-accent-cyan text-text-inverse shadow-[0_0_20px_rgba(0,212,255,0.3)] scale-[1.02]' 
                      : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated/60'
                  }`}
                  role="tab"
                  aria-selected={isActive}
                >
                  <Icon className="w-4 h-4" />
                  <span>{tab.label}</span>
                </button>
              )
            })}
          </div>

          {/* Interactive Platform Terminal Frame */}
          <div className="rounded-2xl border border-border-default bg-[#0d121d] overflow-hidden shadow-[0_25px_70px_rgba(0,0,0,0.7)]">
            
            {/* Terminal Title Bar */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-default/80 bg-bg-elevated/90">
              <div className="flex items-center gap-3">
                <div className="flex gap-1.5">
                  <div className="w-3 h-3 rounded-full bg-accent-loss/80" />
                  <div className="w-3 h-3 rounded-full bg-accent-gold/80" />
                  <div className="w-3 h-3 rounded-full bg-accent-profit/80" />
                </div>
                <span className="text-xs font-mono text-text-muted hidden sm:inline">
                  vyomquant-terminal://{activeTab}
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-accent-cyan/10 border border-accent-cyan/30 text-accent-cyan">
                  {tabs.find(t => t.id === activeTab)?.badge}
                </span>
                <span className="text-[10px] font-mono text-text-muted opacity-75">
                  [Illustrative Interactive Preview]
                </span>
              </div>
            </div>

            {/* Tab 1: Visual DAG Builder Canvas */}
            {activeTab === 'builder' && (
              <div className="p-6 sm:p-10 grid lg:grid-cols-3 gap-8 items-center min-h-[460px]">
                <div className="lg:col-span-2 space-y-4">
                  <div className="p-6 rounded-xl border border-border-default bg-bg-primary/90 relative overflow-hidden">
                    <div 
                      className="absolute inset-0 opacity-20 pointer-events-none" 
                      style={{
                        backgroundImage: 'linear-gradient(rgba(0, 212, 255, 0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 212, 255, 0.1) 1px, transparent 1px)',
                        backgroundSize: '24px 24px'
                      }} 
                    />
                    <div className="relative z-10 grid sm:grid-cols-3 gap-4">
                      {/* Node 1 */}
                      <div className="card-surface p-4 border-l-4 border-l-accent-cyan rounded-lg shadow-md">
                        <div className="flex items-center justify-between text-[11px] font-mono text-text-muted mb-1">
                          <span>FEED NODE</span>
                          <Database className="w-3 h-3 text-accent-cyan" />
                        </div>
                        <div className="text-sm font-bold text-text-primary">BTC/USDT 1m</div>
                        <div className="text-[11px] font-mono text-accent-profit mt-1">● CCXT WebSocket</div>
                      </div>

                      {/* Node 2 */}
                      <div className="card-surface p-4 border-l-4 border-l-accent-gold rounded-lg shadow-md">
                        <div className="flex items-center justify-between text-[11px] font-mono text-text-muted mb-1">
                          <span>INDICATOR</span>
                          <Sliders className="w-3 h-3 text-accent-gold" />
                        </div>
                        <div className="text-sm font-bold text-text-primary">RSI(14) + EMA(200)</div>
                        <div className="text-[11px] font-mono text-text-secondary mt-1">Cross-Validation</div>
                      </div>

                      {/* Node 3 */}
                      <div className="card-surface p-4 border-l-4 border-l-accent-profit rounded-lg shadow-md">
                        <div className="flex items-center justify-between text-[11px] font-mono text-text-muted mb-1">
                          <span>EXECUTION ROUTE</span>
                          <Play className="w-3 h-3 text-accent-profit" />
                        </div>
                        <div className="text-sm font-bold text-text-primary">Limit Order + Trailing</div>
                        <div className="text-[11px] font-mono text-accent-profit mt-1">Risk Check: PASS</div>
                      </div>
                    </div>

                    {/* Logic Connectors */}
                    <div className="mt-6 pt-4 border-t border-border-default/60 flex flex-wrap items-center justify-between gap-3 text-xs font-mono text-text-secondary">
                      <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" /> Zero Python code required</span>
                      <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" /> Real-time DAG validation</span>
                      <span className="flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-accent-profit" /> 1-Click backtest deploy</span>
                    </div>
                  </div>
                </div>

                {/* Tab 1 Sidebar Explainer */}
                <div className="space-y-4">
                  <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider font-semibold">Visual DAG Architecture</div>
                  <h3 className="text-2xl font-bold text-text-primary">Design Complex Systematic Rules Visually</h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    Map indicators, custom logic filters, position sizing formulas, and multi-exchange routing into an intuitive directed graph. Every node executes with strict type validation.
                  </p>
                  <Link to="/signup" className="inline-flex items-center gap-2 text-xs font-mono font-bold text-accent-cyan hover:underline mt-2">
                    Open DAG Builder in Sandbox <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            )}

            {/* Tab 2: VectorBT Backtesting Engine */}
            {activeTab === 'backtest' && (
              <div className="p-6 sm:p-10 grid lg:grid-cols-3 gap-8 items-center min-h-[460px]">
                <div className="lg:col-span-2 space-y-5">
                  {/* Simulated Equity Curve Visualization */}
                  <div className="p-5 rounded-xl border border-border-default bg-bg-primary/90">
                    <div className="flex items-center justify-between mb-4">
                      <div className="text-xs font-mono text-text-muted">Simulated Backtest Equity Curve (BTC/USDT 2024–2026)</div>
                      <div className="text-xs font-mono text-accent-profit font-bold">+28.4% Net Return</div>
                    </div>
                    <div className="flex items-end gap-1 h-32 mb-4">
                      {[35, 42, 38, 48, 52, 49, 58, 65, 62, 70, 75, 72, 80, 84, 82, 89, 93, 91, 98, 100].map((val, idx) => (
                        <div key={idx} className="flex-1 bg-accent-cyan/15 rounded-t-sm relative group">
                          <div 
                            className="absolute bottom-0 left-0 right-0 bg-accent-cyan/70 rounded-t-sm transition-all duration-300 group-hover:bg-accent-cyan" 
                            style={{ height: `${val}%` }} 
                          />
                        </div>
                      ))}
                    </div>
                    {/* Metrics Grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-border-default/60">
                      <div className="p-2.5 rounded-lg bg-bg-elevated/70 border border-border-default text-center">
                        <div className="text-[10px] font-mono text-text-muted">Sharpe Ratio</div>
                        <div className="text-base font-bold font-mono text-accent-cyan">2.14</div>
                      </div>
                      <div className="p-2.5 rounded-lg bg-bg-elevated/70 border border-border-default text-center">
                        <div className="text-[10px] font-mono text-text-muted">Sortino Ratio</div>
                        <div className="text-base font-bold font-mono text-accent-profit">3.08</div>
                      </div>
                      <div className="p-2.5 rounded-lg bg-bg-elevated/70 border border-border-default text-center">
                        <div className="text-[10px] font-mono text-text-muted">Max Drawdown</div>
                        <div className="text-base font-bold font-mono text-accent-loss">-8.4%</div>
                      </div>
                      <div className="p-2.5 rounded-lg bg-bg-elevated/70 border border-border-default text-center">
                        <div className="text-[10px] font-mono text-text-muted">Profit Factor</div>
                        <div className="text-base font-bold font-mono text-text-primary">1.82</div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Tab 2 Sidebar Explainer */}
                <div className="space-y-4">
                  <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider font-semibold">VectorBT Simulation</div>
                  <h3 className="text-2xl font-bold text-text-primary">Vectorized Multi-Year Tick Simulation</h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    Test your logic across millions of historical ticks in seconds. Evaluate full trade distributions, slippage models, fee structures, and Monte Carlo randomized scenario stress-testing.
                  </p>
                  <Link to="/signup" className="inline-flex items-center gap-2 text-xs font-mono font-bold text-accent-cyan hover:underline mt-2">
                    Run Vectorized Backtests <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            )}

            {/* Tab 3: Paper Trading Terminal */}
            {activeTab === 'execution' && (
              <div className="p-6 sm:p-10 grid lg:grid-cols-3 gap-8 items-center min-h-[460px]">
                <div className="lg:col-span-2 space-y-4">
                  <div className="p-5 rounded-xl border border-border-default bg-bg-primary/90 font-mono text-xs">
                    <div className="flex items-center justify-between pb-3 mb-3 border-b border-border-default/80">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-accent-profit animate-pulse" />
                        <span className="font-bold text-text-primary">PAPER EXECUTION ENGINE — ACTIVE</span>
                      </div>
                      <span className="text-text-muted">Balance: $10,000.00 USDT</span>
                    </div>
                    {/* Telemetry rows */}
                    <div className="space-y-2.5">
                      <div className="flex items-center justify-between p-2.5 rounded bg-bg-elevated/70 border border-border-default">
                        <span className="text-accent-cyan">[SIGNAL DETECTED]</span>
                        <span className="text-text-primary">BTC/USDT RSI(14) Oversold &lt; 28.5</span>
                        <span className="text-accent-profit font-semibold">ACTION: BUY</span>
                      </div>
                      <div className="flex items-center justify-between p-2.5 rounded bg-bg-elevated/70 border border-border-default">
                        <span className="text-accent-gold">[RISK PREFLIGHT]</span>
                        <span className="text-text-primary">Position Size: 0.15 BTC (2.5% Account Margin)</span>
                        <span className="text-accent-profit font-semibold">GUARD: APPROVED</span>
                      </div>
                      <div className="flex items-center justify-between p-2.5 rounded bg-bg-elevated/70 border border-border-default">
                        <span className="text-accent-profit">[ORDER ROUTED]</span>
                        <span className="text-text-primary">Simulated Exchange Match @ $89,420.50</span>
                        <span className="text-accent-cyan font-semibold">STATUS: FILLED</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Tab 3 Sidebar Explainer */}
                <div className="space-y-4">
                  <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider font-semibold">Forward Testing</div>
                  <h3 className="text-2xl font-bold text-text-primary">Zero-Capital Forward Validation</h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    Validate your strategies in real-time market conditions without financial risk. Paper trading uses real exchange order books and tick streams with realistic latency simulations.
                  </p>
                  <Link to="/signup" className="inline-flex items-center gap-2 text-xs font-mono font-bold text-accent-cyan hover:underline mt-2">
                    Launch Paper Trading Bot <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            )}

            {/* Tab 4: Risk Control Center */}
            {activeTab === 'risk' && (
              <div className="p-6 sm:p-10 grid lg:grid-cols-3 gap-8 items-center min-h-[460px]">
                <div className="lg:col-span-2 space-y-4">
                  <div className="p-5 rounded-xl border border-border-default bg-bg-primary/90">
                    <div className="grid sm:grid-cols-2 gap-4 mb-4">
                      <div className="p-4 rounded-lg bg-bg-elevated border border-border-default">
                        <div className="flex items-center justify-between text-xs font-mono text-text-muted mb-2">
                          <span>CIRCUIT BREAKER</span>
                          <Shield className="w-4 h-4 text-accent-cyan" />
                        </div>
                        <div className="text-lg font-bold text-text-primary">Max Drawdown Killswitch</div>
                        <div className="text-xs font-mono text-accent-profit mt-1">ARMED (-5.0% threshold)</div>
                      </div>
                      <div className="p-4 rounded-lg bg-bg-elevated border border-border-default">
                        <div className="flex items-center justify-between text-xs font-mono text-text-muted mb-2">
                          <span>LEVERAGE CEILING</span>
                          <Gauge className="w-4 h-4 text-accent-gold" />
                        </div>
                        <div className="text-lg font-bold text-text-primary">Portfolio Margin Cap</div>
                        <div className="text-xs font-mono text-accent-gold mt-1">LOCKED (Max 3.0x Notional)</div>
                      </div>
                    </div>
                    <div className="p-3.5 rounded-lg bg-accent-cyan/5 border border-accent-cyan/20 text-xs font-mono text-text-secondary flex items-center gap-3">
                      <Lock className="w-4 h-4 text-accent-cyan flex-shrink-0" />
                      <span>Account-level circuit breakers halt active orders instantly upon anomalous volatility.</span>
                    </div>
                  </div>
                </div>

                {/* Tab 4 Sidebar Explainer */}
                <div className="space-y-4">
                  <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider font-semibold">Capital Protection</div>
                  <h3 className="text-2xl font-bold text-text-primary">Institutional Risk Architecture</h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    Protect your portfolio with account-wide drawdown stops, per-trade maximum allocation boundaries, slippage tolerance ceilings, and automatic liquidation safety guards.
                  </p>
                  <Link to="/signup" className="inline-flex items-center gap-2 text-xs font-mono font-bold text-accent-cyan hover:underline mt-2">
                    Configure Safety Limits <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            )}

          </div>

        </div>
      </div>
    </section>
  )
}

