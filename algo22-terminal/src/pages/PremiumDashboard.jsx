import React, { useState, useEffect } from 'react';
import { 
  TrendingUp, TrendingDown, Activity, DollarSign, Target, Zap, 
  Shield, AlertTriangle, CheckCircle, Clock, BarChart3, PieChart, 
  ArrowUpRight, ArrowDownRight, X, Flame, Bot, Gauge, Bell, Server,
  MessageSquare, ChevronDown
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer
} from 'recharts';
import { endpoints } from '../api';
import FirstTradeWizard from '../components/FirstTradeWizard';
import { useAppState } from '../AppState';
import { LivePositions, PerformanceMetrics } from '../components/DashboardUpgrades';

const PremiumDashboard = ({ go }) => {
  const { demoMode, uiMode } = useAppState();
  
  const [stats, setStats] = useState(null);
  const [equityCurve, setEquityCurve] = useState([]);
  const [activeBots, setActiveBots] = useState([]);
  const [performanceMetrics, setPerformanceMetrics] = useState(null);
  const [recentTransactions, setRecentTransactions] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [onboardingComplete, setOnboardingComplete] = useState(false);

  useEffect(() => {
    let isMounted = true;
    const loadData = async () => {
      try {
        if (isMounted) setLoading(true);
        // Load performance metrics
        try {
          const perfData = await endpoints.user.getPerformance(30);
          if (isMounted && perfData) {
            setPerformanceMetrics(perfData);
            setStats({
              totalEquity: perfData.total_pnl,
              totalPnl: perfData.total_pnl,
              dailyPnl: perfData.avg_pnl,
              weeklyPnl: perfData.avg_pnl * 7,
              winRate: perfData.win_rate,
              sharpeRatio: perfData.sharpe_ratio,
              activeBots: perfData.total_trades,
              drawdown: 0
            });
          }
        } catch (e) {
          if (isMounted) console.error("Failed to load performance metrics", e);
        }

        // Load equity curve
        try {
          const curve = await endpoints.user.getEquityCurve(90);
          if (isMounted) {
            if (curve && curve.length > 0) {
              setEquityCurve(curve.map((d, i) => ({ d: i, v: d.value || d.equity || 0 })));
            } else {
              setEquityCurve([]);
            }
          }
        } catch (e) {
          if (isMounted) console.error("Failed to load equity curve", e);
        }

        // Load recent transactions
        try {
          const txs = await endpoints.user.getRecentTransactions(50, 30);
          if (isMounted && txs && txs.transactions) {
            setRecentTransactions(txs.transactions);
          }
        } catch (e) {
          if (isMounted) console.error("Failed to load transactions", e);
        }

        // Load heatmap
        try {
          const heatData = await endpoints.user.getHeatmap(3);
          if (isMounted && heatData) {
            setHeatmapData(heatData);
          }
        } catch (e) {
          if (isMounted) console.error("Failed to load heatmap", e);
        }

        // Load active bots
        if (isMounted) {
          if (demoMode) {
            setActiveBots([
              { name: "HFT Market Maker", pair: "BTC/USDT", status: "running", pnl: 14.5 },
              { name: "ETH Scalper Pro", pair: "ETH/USDT", status: "running", pnl: 8.2 },
              { name: "SOL Grid Array", pair: "SOL/USDT", status: "running", pnl: 22.1 },
              { name: "Arbitrage Triangle", pair: "XRP/USDT", status: "running", pnl: 4.5 },
              { name: "Mean Reversion Alpha", pair: "ADA/USDT", status: "paused", pnl: -1.2 },
            ]);
          } else {
            const botsPayload = await endpoints.strategies.list();
            if (isMounted) {
              const bots = Array.isArray(botsPayload) ? botsPayload : botsPayload?.data || [];
              setActiveBots(bots.slice(0, 5));
            }
          }
        }

      } catch (err) {
        if (isMounted) console.error("Failed to load dashboard data", err);
      } finally {
        if (isMounted) setLoading(false);
      }
    };
    loadData();
    return () => { isMounted = false; };
  }, [demoMode]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center h-full bg-[#080A0D]">
        <div className="flex flex-col items-center gap-4">
          <Activity className="animate-pulse text-[#00D4FF]" size={48} />
          <div className="text-[#8B949E] font-mono text-sm tracking-widest uppercase">Initializing Command Center...</div>
        </div>
      </div>
    );
  }

  // Beginner vs Pro specific language
  const labels = {
    equity: uiMode === 'beginner' ? 'Total Balance' : 'Total Equity',
    winRate: uiMode === 'beginner' ? 'Success Rate' : 'Win Rate',
    sharpe: uiMode === 'beginner' ? 'Risk-Adjusted Return' : 'Sharpe Ratio',
    bots: uiMode === 'beginner' ? 'Active Bots' : 'Active Algos'
  };

  return (
    <div className="flex-1 p-4 overflow-y-auto bg-[#080A0D] text-[#E6EDF3] font-['Inter'] relative">
      
      <div className="flex flex-col gap-4">
        
        {/* ROW 1: CRITICAL METRICS (4 Cols) */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard 
            title={labels.equity} 
            value={stats?.totalEquity !== undefined ? `$${stats.totalEquity.toLocaleString()}` : '—'} 
            trend="+2.4%" 
            positive 
          />
          <MetricCard 
            title="Daily P&L" 
            value={stats?.dailyPnl !== undefined ? `$${stats.dailyPnl.toFixed(2)}` : '—'} 
            trend="+1.2%" 
            positive 
          />
          <MetricCard 
            title={labels.bots} 
            value={stats?.activeBots !== undefined ? stats.activeBots : '—'} 
            icon={<Bot size={16}/>} 
          />
          <MetricCard 
            title="Risk Score" 
            value="SAFE" 
            trend="A-Grade"
            positive 
            icon={<Shield size={16}/>}
          />
        </div>

        {/* PERFORMANCE METRICS */}
        {performanceMetrics && (
          <PerformanceMetrics metrics={performanceMetrics} />
        )}

        {/* ROW 2: EXECUTION METRICS */}
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
          <div className="col-span-1 xl:col-span-8 flex flex-col gap-4">
            <div className="bg-[#131722] border border-[#202938] rounded-xl p-4 shadow-lg flex flex-col">
              <div className="flex justify-between items-center mb-4">
                <div className="flex items-center gap-3">
                  <h3 className="font-bold text-lg">Portfolio Performance</h3>
                  <span className="px-2 py-0.5 rounded bg-[#00D4FF]20 text-[#00D4FF] text-xs font-mono font-bold">LIVE</span>
                </div>
                <div className="flex bg-[#080A0D] rounded-lg p-1 border border-[#202938]">
                  {['1D', '1W', '1M', '3M', 'ALL'].map(tf => (
                    <button key={tf} className={`px-3 py-1 text-xs font-mono rounded ${tf === '1M' ? 'bg-[#00D4FF] text-white' : 'text-[#8B949E] hover:text-[#E6EDF3]'}`}>
                      {tf}
                    </button>
                  ))}
                </div>
              </div>
              <div className="h-[280px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve}>
                    <defs>
                      <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#00D4FF" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#00D4FF" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <Tooltip 
                      contentStyle={{ backgroundColor: '#131722', borderColor: '#202938', borderRadius: '8px', color: '#E6EDF3', fontFamily: 'monospace' }}
                      itemStyle={{ color: '#00D4FF' }}
                    />
                    <Area type="monotone" dataKey="v" stroke="#00D4FF" strokeWidth={3} fillOpacity={1} fill="url(#colorEquity)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="col-span-1 xl:col-span-4 flex flex-col gap-4">
            <LivePositions positions={recentTransactions} />
          </div>
        </div>

        {/* ROW 3: OPERATIONAL METRICS */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <ExchangeHealth demoMode={demoMode} />
          <MarketRegime uiMode={uiMode} demoMode={demoMode} />
          <PnlHeatmap data={heatmapData} />
        </div>

      </div> 
    </div>
  );
};

// -------------------------------------------------------------
// COMMAND CENTER SUB-COMPONENTS
// -------------------------------------------------------------

const MetricCard = ({ title, value, trend, icon, positive }) => (
  <div className="bg-[#131722] border border-[#202938] rounded-xl p-3 flex flex-col gap-2 shadow-lg relative overflow-hidden transition-colors">
    <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-[#00D4FF]20 to-transparent opacity-30 transition-opacity"></div>
    <div className="flex justify-between items-center z-10">
      <span className="text-[#8B949E] text-[10px] font-mono uppercase tracking-widest flex items-center gap-1.5">
        {title}
      </span>
      {icon && <div className="text-[#00D4FF]">{icon}</div>}
    </div>
    <div className="flex items-baseline gap-2 z-10 mt-1">
      <span className="text-xl lg:text-2xl font-black font-mono text-[#E6EDF3] tracking-tight">{value}</span>
      {trend && (
        <span className={`text-xs font-mono font-bold ${positive ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
          {trend}
        </span>
      )}
    </div>
  </div>
);

const PnlHeatmap = ({ data }) => {
  const days = data ? data.map(d => d.pnl_usd || 0) : [];

  if (days.length === 0) {
    return (
      <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg group flex flex-col h-full items-center justify-center">
         <Activity size={24} className="text-[#8B949E] mb-2" />
         <p className="text-[#8B949E] text-xs font-mono">No Heatmap Data Available</p>
      </div>
    );
  }

  return (
    <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg group flex flex-col h-full">
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-bold text-sm text-[#8B949E] uppercase tracking-wider flex items-center gap-2">
          <Activity size={16} className="text-[#00D4FF]" /> PnL Heatmap
        </h3>
      </div>
      <div className="flex-1 flex flex-col justify-center">
        <div className="grid grid-cols-7 gap-1.5 w-full">
          {days.map((val, i) => {
            let color = 'bg-[#1A222C]';
            if (val > 40) color = 'bg-[#26A69A]';
            else if (val > 0) color = 'bg-[#26A69A]80';
            else if (val < -40) color = 'bg-[#EF5350]';
            else if (val < 0) color = 'bg-[#EF5350]80';
            
            return (
              <div 
                key={i} 
                className={`w-full aspect-square rounded-sm ${color} transition-colors hover:brightness-125 cursor-pointer`}
                title={`Pnl: $${val.toFixed(2)}`}
              />
            );
          })}
        </div>
      </div>
      <div className="flex justify-between w-full mt-4 text-[10px] font-bold font-mono text-[#8B949E] tracking-widest px-1">
        <span className="text-[#EF5350]">LESS</span>
        <span className="text-[#26A69A]">MORE</span>
      </div>
    </div>
  );
};

const MarketRegime = ({ uiMode, demoMode }) => {
  return (
    <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg group">
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-bold text-sm text-[#8B949E] uppercase tracking-wider flex items-center gap-2">
          <Gauge size={16} className="text-[#00D4FF]" /> AI Briefing
        </h3>
      </div>
      
      <p className="text-[#8B949E] text-xs leading-relaxed mb-4 pb-4 border-b border-[#202938]">
        {demoMode 
          ? "Institutional volume spiked in the past 4 hours. AI recommends maintaining High-Frequency Grid arrays to capture sideways chop."
          : "AI recommends deploying trend-following momentum strategies on mid-cap assets to capture the current upward bias."}
      </p>

      <div className="flex flex-col gap-2">
        <div className="flex justify-between items-center bg-[#080A0D] p-2.5 rounded-lg border border-[#202938]">
          <span className="text-[#E6EDF3] text-xs font-bold">Regime</span>
          <span className="text-[#26A69A] font-mono text-[10px] font-bold uppercase tracking-wider flex items-center gap-1">
            <TrendingUp size={12} /> {uiMode === 'beginner' ? 'Uptrend' : 'Bullish Trend'}
          </span>
        </div>
        <div className="flex justify-between items-center bg-[#080A0D] p-2.5 rounded-lg border border-[#202938]">
          <span className="text-[#E6EDF3] text-xs font-bold">Volatility</span>
          <span className="text-[#FFB74D] font-mono text-[10px] font-bold uppercase tracking-wider">Medium</span>
        </div>
      </div>
    </div>
  );
}

const ExchangeHealth = ({ demoMode }) => {
  if (!demoMode) {
    return (
      <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg flex flex-col items-center justify-center text-center h-full">
        <Server size={24} className="text-[#8B949E] mb-2" />
        <p className="text-[#8B949E] text-xs font-mono">No API connected</p>
      </div>
    );
  }

  return (
  <div className="bg-[#131722] border border-[#202938] rounded-xl p-5 shadow-lg">
    <h3 className="font-bold text-sm text-[#8B949E] uppercase tracking-wider mb-4 flex items-center gap-2">
      <Server size={16} className="text-[#00D4FF]" /> API Connectivity
    </h3>
    <div className="flex flex-col gap-2 font-mono text-xs">
      <div className="flex justify-between items-center p-3 bg-[#080A0D] rounded-lg border border-[#202938] hover:border-[#26A69A]40 transition-colors">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-[#26A69A] animate-pulse shadow-[0_0_8px_#26A69A]"></div>
          <span className="font-bold text-[#E6EDF3]">Binance US</span>
        </div>
        <span className="text-[#26A69A] font-bold">42ms</span>
      </div>
      <div className="flex justify-between items-center p-3 bg-[#080A0D] rounded-lg border border-[#202938] hover:border-[#26A69A]40 transition-colors">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-[#26A69A] shadow-[0_0_8px_#26A69A]"></div>
          <span className="font-bold text-[#E6EDF3]">Bybit</span>
        </div>
        <span className="text-[#26A69A] font-bold">65ms</span>
      </div>
    </div>
  </div>
  );
};

export default PremiumDashboard;
