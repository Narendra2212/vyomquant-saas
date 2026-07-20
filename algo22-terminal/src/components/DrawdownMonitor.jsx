import React, { useState, useEffect, useCallback } from 'react';

/**
 * 🔴 STEP 4 — DRAWDOWN WARNING SYSTEM
 * 
 * CRITICAL: Prevents account destruction by monitoring equity drawdown
 * 
 * Drawdown Rules:
 * - >10%: Warning (yellow)
 * - >20%: Strong Warning (orange)
 * - >30%: BLOCK TRADING (red + disable execution)
 */

const DRAWDOWN_LEVELS = {
  WARNING: 0.10,      // 10%
  STRONG_WARNING: 0.20, // 20%
  CRITICAL: 0.30        // 30% - BLOCK TRADING
};

const DrawdownMonitor = ({ wsClient, accountId, onDrawdownBlock }) => {
  // Track peak and current equity
  const [peakEquity, setPeakEquity] = useState(0);
  const [currentEquity, setCurrentEquity] = useState(0);
  const [drawdownPercent, setDrawdownPercent] = useState(0);
  const [isBlocked, setIsBlocked] = useState(false);

  // Calculate drawdown
  const calculateDrawdown = useCallback((current, peak) => {
    if (peak <= 0) return 0;
    return (peak - current) / peak;
  }, []);

  // 🔴 STEP 4: Subscribe to equity updates via WebSocket
  useEffect(() => {
    if (!wsClient || !accountId) return;

    wsClient.send(JSON.stringify({
      type: "subscribe_equity",
      account_id: accountId
    }));

    console.log(`[STEP 4] Subscribed to equity updates for drawdown monitoring`);

    return () => {
      wsClient.send(JSON.stringify({
        type: "unsubscribe_equity",
        account_id: accountId
      }));
    };
  }, [wsClient, accountId]);

  // 🔴 STEP 4: Handle equity updates and calculate drawdown
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "equity_update") {
          const current = data.equity || 0;
          
          // Update peak equity if current is higher
          setPeakEquity(prev => {
            const newPeak = Math.max(prev, current);
            
            // Calculate drawdown
            const drawdown = calculateDrawdown(current, newPeak);
            setDrawdownPercent(drawdown);
            setCurrentEquity(current);

            // 🔴 STEP 4: Check drawdown levels and take action
            if (drawdown >= DRAWDOWN_LEVELS.CRITICAL) {
              setIsBlocked(true);
              // Notify parent component to block trading
              if (onDrawdownBlock) {
                onDrawdownBlock(true, drawdown);
              }
              console.error(`[STEP 4] 🔴 CRITICAL DRAWDOWN: ${(drawdown * 100).toFixed(1)}% - TRADING BLOCKED`);
            } else if (drawdown >= DRAWDOWN_LEVELS.STRONG_WARNING) {
              setIsBlocked(false);
              if (onDrawdownBlock) {
                onDrawdownBlock(false, drawdown);
              }
              console.warn(`[STEP 4] ⚠️ STRONG WARNING: ${(drawdown * 100).toFixed(1)}% drawdown`);
            } else if (drawdown >= DRAWDOWN_LEVELS.WARNING) {
              setIsBlocked(false);
              if (onDrawdownBlock) {
                onDrawdownBlock(false, drawdown);
              }
              console.warn(`[STEP 4] ⚠️ WARNING: ${(drawdown * 100).toFixed(1)}% drawdown`);
            } else {
              setIsBlocked(false);
              if (onDrawdownBlock) {
                onDrawdownBlock(false, drawdown);
              }
            }

            return newPeak;
          });
        }
      } catch (err) {
        console.error("[STEP 4] Failed to parse equity update:", err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribePortfolio = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribePortfolio = wsClient.subscribe('portfolio', handleMessage);
    } else {
      console.warn('[DrawdownMonitor] wsClient.subscribe is not available');
    }

    return () => {
      if (unsubscribePortfolio) unsubscribePortfolio();
    };
  }, [wsClient, calculateDrawdown, onDrawdownBlock]);

  // Get status color based on drawdown level
  const getStatusColor = () => {
    if (drawdownPercent >= DRAWDOWN_LEVELS.CRITICAL) return 'red';
    if (drawdownPercent >= DRAWDOWN_LEVELS.STRONG_WARNING) return 'orange';
    if (drawdownPercent >= DRAWDOWN_LEVELS.WARNING) return 'yellow';
    return 'green';
  };

  // Get status text
  const getStatusText = () => {
    if (drawdownPercent >= DRAWDOWN_LEVELS.CRITICAL) return 'TRADING BLOCKED';
    if (drawdownPercent >= DRAWDOWN_LEVELS.STRONG_WARNING) return 'STRONG WARNING';
    if (drawdownPercent >= DRAWDOWN_LEVELS.WARNING) return 'WARNING';
    return 'NORMAL';
  };

  // Format currency
  const formatCurrency = (value) => {
    return `$${Math.abs(value).toFixed(2)}`;
  };

  // Format percentage
  const formatPercent = (value) => {
    return `${(value * 100).toFixed(1)}%`;
  };

  const statusColor = getStatusColor();
  const statusText = getStatusText();

  return (
    <div className="drawdown-monitor">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-text-3 text-[10px] font-mono font-bold tracking-widest uppercase">
          DRAWDOWN MONITOR
        </span>
        <div className={`px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-${statusColor}/20 text-${statusColor}`}>
          {statusText}
        </div>
      </div>

      {/* Drawdown Bar */}
      <div className="mb-4">
        <div className="flex justify-between text-[9px] font-mono mb-1">
          <span className="text-text-3">Current Drawdown</span>
          <span className={`text-${statusColor} font-bold`}>
            {formatPercent(drawdownPercent)}
          </span>
        </div>
        
        {/* Progress bar showing drawdown levels */}
        <div className="h-3 bg-bg-2 rounded overflow-hidden relative">
          {/* Warning zones */}
          <div className="absolute left-0 top-0 bottom-0 w-[10%] bg-yellow/30" />
          <div className="absolute left-[10%] top-0 bottom-0 w-[10%] bg-orange/30" />
          <div className="absolute left-[20%] top-0 bottom-0 w-[10%] bg-red/30" />
          <div className="absolute left-[30%] top-0 bottom-0 right-0 bg-red/50" />
          
          {/* Current drawdown indicator */}
          <div 
            className={`absolute top-0 bottom-0 bg-${statusColor} transition-all duration-300`}
            style={{ width: `${Math.min(drawdownPercent * 100, 100)}%` }}
          />
        </div>
        
        {/* Legend */}
        <div className="flex justify-between text-[8px] font-mono mt-1 text-text-3">
          <span>0%</span>
          <span className="text-yellow">10%</span>
          <span className="text-orange">20%</span>
          <span className="text-red">30%</span>
        </div>
      </div>

      {/* Equity Display */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="p-2 bg-bg-3 rounded border border-border">
          <div className="text-text-3 text-[9px] font-mono mb-0.5">PEAK EQUITY</div>
          <div className="text-text-1 text-sm font-mono font-bold">
            {formatCurrency(peakEquity)}
          </div>
        </div>
        <div className="p-2 bg-bg-3 rounded border border-border">
          <div className="text-text-3 text-[9px] font-mono mb-0.5">CURRENT</div>
          <div className={`text-sm font-mono font-bold ${currentEquity < peakEquity ? 'text-red' : 'text-green'}`}>
            {formatCurrency(currentEquity)}
          </div>
        </div>
      </div>

      {/* 🔴 STEP 4: Critical Drawdown Alert */}
      {drawdownPercent >= DRAWDOWN_LEVELS.CRITICAL && (
        <div className="p-3 bg-red/10 border border-red rounded animate-pulse">
          <div className="flex items-center gap-2 text-red">
            <span className="text-lg">🚫</span>
            <div>
              <div className="text-[10px] font-mono font-bold">TRADING DISABLED</div>
              <div className="text-[9px] font-mono">
                Drawdown exceeded 30%. All trading blocked.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Strong Warning */}
      {drawdownPercent >= DRAWDOWN_LEVELS.STRONG_WARNING && drawdownPercent < DRAWDOWN_LEVELS.CRITICAL && (
        <div className="p-3 bg-orange/10 border border-orange rounded">
          <div className="flex items-center gap-2 text-orange">
            <span className="text-lg">⚠️</span>
            <div>
              <div className="text-[10px] font-mono font-bold">STRONG WARNING</div>
              <div className="text-[9px] font-mono">
                Drawdown &gt; 20%. Consider reducing position size.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Warning */}
      {drawdownPercent >= DRAWDOWN_LEVELS.WARNING && drawdownPercent < DRAWDOWN_LEVELS.STRONG_WARNING && (
        <div className="p-2 bg-yellow/10 border border-yellow rounded">
          <div className="flex items-center gap-2 text-yellow">
            <span>⚠️</span>
            <span className="text-[9px] font-mono">
              Drawdown &gt; 10%. Monitor positions closely.
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default DrawdownMonitor;
export { DRAWDOWN_LEVELS };
