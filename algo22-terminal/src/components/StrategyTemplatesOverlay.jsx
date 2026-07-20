import React from 'react';
import { X, TrendingUp, Activity, BarChart2, Zap } from 'lucide-react';

const templates = [
  {
    id: 'rsi_reversal',
    title: 'RSI Mean Reversion',
    description: 'Buys when RSI < 30 (Oversold) and sells when RSI > 70 (Overbought). A classic oscillating strategy.',
    icon: <Activity size={24} className="text-[#2962FF]" />,
    color: '#2962FF',
    difficulty: 'Beginner',
    nodes: [
      { id: 't1-source', type: 'source', position: { x: 50, y: 150 }, data: { label: 'CCXT Asset Feed', params: { symbol: 'BTC/USDT', timeframe: '15m' } } },
      { id: 't1-rsi', type: 'indicator', position: { x: 300, y: 150 }, data: { label: 'RSI', params: { period: 14 } } },
      { id: 't1-logic', type: 'logic', position: { x: 550, y: 150 }, data: { label: 'Cross Under', params: { threshold: 30 } } },
      { id: 't1-action', type: 'action', position: { x: 800, y: 150 }, data: { label: 'Buy Market', params: { amount: 100 } } }
    ],
    edges: [
      { id: 'e1', source: 't1-source', target: 't1-rsi' },
      { id: 'e2', source: 't1-rsi', target: 't1-logic' },
      { id: 'e3', source: 't1-logic', target: 't1-action' }
    ]
  },
  {
    id: 'ema_cross',
    title: 'EMA Golden Cross',
    description: 'Trend following algorithm that executes when a fast 50 EMA crosses above a slow 200 EMA.',
    icon: <TrendingUp size={24} className="text-[#26A69A]" />,
    color: '#26A69A',
    difficulty: 'Intermediate',
    nodes: [
      { id: 't2-source', type: 'source', position: { x: 50, y: 150 }, data: { label: 'CCXT Asset Feed', params: { symbol: 'ETH/USDT', timeframe: '1h' } } },
      { id: 't2-ema50', type: 'indicator', position: { x: 300, y: 100 }, data: { label: 'EMA', params: { period: 50 } } },
      { id: 't2-ema200', type: 'indicator', position: { x: 300, y: 200 }, data: { label: 'EMA', params: { period: 200 } } },
      { id: 't2-logic', type: 'logic', position: { x: 550, y: 150 }, data: { label: 'Cross Over', params: {} } },
      { id: 't2-action', type: 'action', position: { x: 800, y: 150 }, data: { label: 'Buy Market', params: { amount: 500 } } }
    ],
    edges: [
      { id: 'e1', source: 't2-source', target: 't2-ema50' },
      { id: 'e2', source: 't2-source', target: 't2-ema200' },
      { id: 'e3', source: 't2-ema50', target: 't2-logic' },
      { id: 'e4', source: 't2-ema200', target: 't2-logic' },
      { id: 'e5', source: 't2-logic', target: 't2-action' }
    ]
  },
  {
    id: 'grid_bot',
    title: 'High-Frequency Grid',
    description: 'Places multiple continuous limit orders within a defined price range to capture sideways volatility.',
    icon: <BarChart2 size={24} className="text-[#FFB74D]" />,
    color: '#FFB74D',
    difficulty: 'Advanced',
    nodes: [
      { id: 't3-source', type: 'source', position: { x: 50, y: 150 }, data: { label: 'Live Ticker', params: { symbol: 'SOL/USDT' } } },
      { id: 't3-grid', type: 'logic', position: { x: 300, y: 150 }, data: { label: 'Grid Logic', params: { grids: 10, range: 5 } } },
      { id: 't3-action', type: 'action', position: { x: 550, y: 150 }, data: { label: 'Place Limit', params: { amount: 10 } } }
    ],
    edges: [
      { id: 'e1', source: 't3-source', target: 't3-grid' },
      { id: 'e2', source: 't3-grid', target: 't3-action' }
    ]
  }
];

const StrategyTemplatesOverlay = ({ onClose, onSelect }) => {
  return (
    <div className="absolute inset-0 bg-[#080A0D]/80 backdrop-blur-md z-50 flex items-center justify-center p-6 animate-in fade-in duration-300">
      <div className="bg-[#131722] border border-[#202938] rounded-xl w-full max-w-4xl shadow-2xl overflow-hidden flex flex-col">
        <div className="p-6 border-b border-[#202938] flex justify-between items-center bg-[#080A0D]">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-[#2962FF]10 text-[#2962FF] rounded-lg border border-[#2962FF]30">
              <Zap size={20} />
            </div>
            <div>
              <h2 className="text-xl font-bold text-[#E6EDF3] font-['Inter']">Strategy Templates</h2>
              <p className="text-[#8B949E] text-xs font-mono mt-1">Jumpstart your algorithmic pipeline with institutional presets.</p>
            </div>
          </div>
          <button onClick={onClose} className="text-[#8B949E] hover:text-[#E6EDF3] transition-colors bg-[#131722] p-2 rounded-lg border border-[#202938] hover:bg-[#1A222C]">
            <X size={20} />
          </button>
        </div>
        
        <div className="p-8 grid grid-cols-1 md:grid-cols-3 gap-6 bg-[#080A0D]">
          {templates.map(t => (
            <div 
              key={t.id}
              onClick={() => onSelect(t)}
              className="bg-[#131722] border border-[#202938] rounded-xl p-6 cursor-pointer hover:border-[color:var(--t-color)] hover:shadow-[0_0_30px_rgba(41,98,255,0.1)] transition-all group flex flex-col h-full relative overflow-hidden"
              style={{ '--t-color': t.color }}
            >
              <div className="absolute -right-8 -top-8 opacity-5 group-hover:opacity-10 transition-opacity transform group-hover:scale-110 group-hover:rotate-12 duration-500">
                {React.cloneElement(t.icon, { size: 120 })}
              </div>
              <div className="w-12 h-12 rounded-lg bg-[#080A0D] border border-[#202938] flex items-center justify-center mb-5 group-hover:border-[color:var(--t-color)] transition-colors shadow-lg">
                {t.icon}
              </div>
              <h3 className="text-[#E6EDF3] font-bold text-lg mb-2 font-['Inter'] z-10">{t.title}</h3>
              <p className="text-[#8B949E] text-sm mb-8 flex-1 leading-relaxed z-10">{t.description}</p>
              
              <div className="flex items-center justify-between mt-auto z-10 pt-4 border-t border-[#202938] group-hover:border-[color:var(--t-color)] group-hover:border-opacity-30 transition-colors">
                <span className="text-[10px] font-mono font-bold tracking-widest uppercase border border-[#202938] px-2 py-1 rounded bg-[#080A0D] text-[#8B949E]">
                  {t.difficulty}
                </span>
                <span className="text-xs font-bold text-[color:var(--t-color)] opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 font-mono uppercase tracking-wider">
                  Deploy <TrendingUp size={12} />
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default React.memo(StrategyTemplatesOverlay);
