import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Clock, Zap } from 'lucide-react';

const MarketRibbon = () => {
  const [data, setData] = useState([
    { symbol: 'BTC', price: 68420.50, change: 2.4 },
    { symbol: 'ETH', price: 3450.20, change: -0.8 },
    { symbol: 'SOL', price: 145.60, change: 5.2 },
    { symbol: 'BNB', price: 590.10, change: 1.1 },
    { symbol: 'XRP', price: 0.58, change: -2.3 },
    { symbol: 'DOGE', price: 0.14, change: 8.5 },
    { symbol: 'FUNDING', rate: '0.01%', label: 'Avg Rate' },
    { symbol: 'FEAR/GREED', index: 72, label: 'Greed' },
  ]);

  // Simulate real-time price jitters
  useEffect(() => {
    const interval = setInterval(() => {
      setData(prev => prev.map(item => {
        if (item.price) {
          const jitter = item.price * (Math.random() * 0.002 - 0.001);
          return { ...item, price: item.price + jitter };
        }
        return item;
      }));
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="w-full bg-[#080A0D] border-b border-[#202938] h-8 overflow-hidden relative flex items-center">
      <style>{`
        @keyframes marquee {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .animate-marquee {
          display: flex;
          width: fit-content;
          animation: marquee 40s linear infinite;
        }
        .animate-marquee:hover {
          animation-play-state: paused;
        }
      `}</style>
      
      <div className="absolute left-0 z-10 w-12 h-full bg-gradient-to-r from-[#080A0D] to-transparent pointer-events-none"></div>
      
      <div className="animate-marquee flex items-center whitespace-nowrap cursor-default">
        {/* Render twice for seamless loop */}
        {[...data, ...data].map((item, idx) => (
          <div key={idx} className="flex items-center gap-2 px-6 border-r border-[#202938] h-4">
            <span className="font-bold text-[#8B949E] text-[10px] font-mono">{item.symbol}</span>
            {item.price !== undefined ? (
              <>
                <span className="text-[#E6EDF3] text-xs font-mono font-bold">${item.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                <span className={`text-[10px] font-mono font-bold flex items-center ${item.change >= 0 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>
                  {item.change >= 0 ? <TrendingUp size={10} className="mr-0.5" /> : <TrendingDown size={10} className="mr-0.5" />}
                  {Math.abs(item.change)}%
                </span>
              </>
            ) : item.rate !== undefined ? (
              <>
                <span className="text-[#2962FF] text-[10px] font-mono font-bold">{item.rate}</span>
                <span className="text-[#8B949E] text-[10px]">{item.label}</span>
              </>
            ) : (
              <>
                <span className={`text-[10px] font-mono font-bold ${item.index > 50 ? 'text-[#26A69A]' : 'text-[#EF5350]'}`}>{item.index}</span>
                <span className="text-[#8B949E] text-[10px] font-mono tracking-widest uppercase">{item.label}</span>
              </>
            )}
          </div>
        ))}
      </div>
      
      <div className="absolute right-0 z-10 w-12 h-full bg-gradient-to-l from-[#080A0D] to-transparent pointer-events-none"></div>
    </div>
  );
};

export default React.memo(MarketRibbon);
