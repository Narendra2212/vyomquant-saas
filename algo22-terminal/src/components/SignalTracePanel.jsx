import React, { useState, useEffect, useRef, useCallback } from 'react';
import WS_CHANNELS from '../constants/wsChannels';
import { normalizeTelemetryEvent } from '../websocketClient';

/**
 * 🔴 STEP 2 — SIGNAL TRACE PANEL (VERY IMPORTANT)
 * 
 * Shows complete signal flow from strategy to execution
 * 
 * Displays:
 * - Timestamp of signal generation
 * - Signal type (BUY/SELL/HOLD)
 * - Source node (RSI, ML, MACD, etc.)
 * - Signal value/strength
 * - Execution result
 */

const MAX_SIGNALS = 50; // Keep last 50 signals

// Signal type styling
const SIGNAL_CONFIG = {
  BUY: {
    color: '#00C853',
    bgColor: '#00C85315',
    borderColor: '#00C85340',
    icon: '▲',
    label: 'BUY'
  },
  SELL: {
    color: '#FF5252',
    bgColor: '#FF525215',
    borderColor: '#FF525240',
    icon: '▼',
    label: 'SELL'
  },
  HOLD: {
    color: '#9E9E9E',
    bgColor: '#9E9E9E15',
    borderColor: '#9E9E9E40',
    icon: '◆',
    label: 'HOLD'
  }
};

// Source node icons/colors
const SOURCE_CONFIG = {
  RSI: { color: '#FFAB00', icon: '📊' },
  ML: { color: '#9C27B0', icon: '🧠' },
  MACD: { color: '#2196F3', icon: '📈' },
  EMA: { color: '#00BCD4', icon: '〰️' },
  BB: { color: '#FF5722', icon: '⭕' },
  VOLUME: { color: '#4CAF50', icon: '🔊' },
  PATTERN: { color: '#E91E63', icon: '🔍' },
  DEFAULT: { color: '#607D8B', icon: '⚡' }
};

const SignalTracePanel = ({ 
  wsClient, 
  maxSignals = 20,
  onSignalClick,
  showFilters = true
}) => {
  const [signals, setSignals] = useState([]);
  const [filter, setFilter] = useState('ALL'); // ALL, BUY, SELL
  const [isPaused, setIsPaused] = useState(false);
  const [selectedSignal, setSelectedSignal] = useState(null);
  const signalBuffer = useRef([]);

  // 🔴 STEP 2: Format timestamp
  const formatTime = (timestamp) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', { 
      hour12: false, 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      fractionalSecondDigits: 3
    });
  };

  // 🔴 STEP 2: Add signal to trace
  const addSignal = useCallback((signal) => {
    const enrichedSignal = {
      ...signal,
      id: crypto.randomUUID(), // CSPRNG — Math.random() is not cryptographically secure
      receivedAt: Date.now()
    };

    signalBuffer.current.unshift(enrichedSignal);
    
    // Keep only max signals
    if (signalBuffer.current.length > MAX_SIGNALS) {
      signalBuffer.current = signalBuffer.current.slice(0, MAX_SIGNALS);
    }

    if (!isPaused) {
      setSignals([...signalBuffer.current]);
    }
  }, [isPaused]);

  // 🔴 STEP 2: Process WebSocket messages
  useEffect(() => {
    if (!wsClient) return;

    // Handle signal messages
    const handleSignal = (rawData) => {
      try {
        const data = normalizeTelemetryEvent(rawData);
        addSignal({
          timestamp: data.timestamp || Date.now(),
          type: data.signal_type || data.signal?.toUpperCase() || data.side?.toUpperCase() || 'HOLD',
          source: data.source || data.node_id || data.strategy_name || 'UNKNOWN',
          value: data.value || data.confidence || data.strength || 0,
          symbol: data.symbol || data.asset || '-',
          price: data.price || 0,
          strategyId: data.strategy_id,
          nodeType: data.node_type,
          metadata: data.metadata || {}
        });
      } catch (err) {
        console.error('[STEP 2] Failed to process signal:', err);
      }
    };

    // Handle order execution results
    const handleOrderFilled = (rawData) => {
      try {
        const data = normalizeTelemetryEvent(rawData);
        // Update existing signal with execution result
        // 🔴 STEP 7: Extract order ID from multiple possible locations
        const orderId = data?.execution_id || data?.result?.order_id || data?.order_id;
        signalBuffer.current = signalBuffer.current.map(s => 
          s.id === data.signal_id 
            ? { ...s, executed: true, executionPrice: data.price, orderId }
            : s
        );
        
        if (!isPaused) {
          setSignals([...signalBuffer.current]);
        }
      } catch (err) {
        console.error('[STEP 2] Failed to process order filled:', err);
      }
    };

    // Subscribe to events using custom wsClient interface
    let unsubscribeSignalTrace = null;
    let unsubscribeExecution = null;
    
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeSignalTrace = wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleSignal);
      unsubscribeExecution = wsClient.subscribe(WS_CHANNELS.EXECUTION_EVENTS, handleOrderFilled);
    } else {
      console.warn('[SignalTracePanel] wsClient.subscribe is not available');
    }
    
    return () => {
      if (unsubscribeSignalTrace) unsubscribeSignalTrace();
      if (unsubscribeExecution) unsubscribeExecution();
    };
  }, [wsClient, addSignal, isPaused]);

  // 🔴 STEP 2: Resume from pause
  useEffect(() => {
    if (!isPaused) {
      setSignals([...signalBuffer.current]);
    }
  }, [isPaused]);

  // 🔴 STEP 2: Filter signals
  const filteredSignals = signals
    .filter(s => filter === 'ALL' || s.type === filter)
    .slice(0, maxSignals);

  // 🔴 STEP 2: Handle signal click
  const handleSignalClick = (signal) => {
    setSelectedSignal(signal.id === selectedSignal ? null : signal.id);
    if (onSignalClick) {
      onSignalClick(signal);
    }
  };

  // 🔴 STEP 2: Get source config
  const getSourceConfig = (source) => {
    const upper = source?.toUpperCase();
    for (const [key, config] of Object.entries(SOURCE_CONFIG)) {
      if (upper?.includes(key)) return config;
    }
    return SOURCE_CONFIG.DEFAULT;
  };

  return (
    <div style={{
      backgroundColor: '#0d1117',
      border: '1px solid #30363d',
      borderRadius: '12px',
      padding: '16px',
      fontFamily: 'monospace',
      maxHeight: '600px',
      display: 'flex',
      flexDirection: 'column'
    }}>
      {/* 🔴 Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '12px',
        paddingBottom: '12px',
        borderBottom: '1px solid #30363d'
      }}>
        <div>
          <div style={{
            color: '#ffffff',
            fontSize: '14px',
            fontWeight: 900,
            letterSpacing: '0.5px'
          }}>
            SIGNAL TRACE PANEL
          </div>
          <div style={{
            color: '#8b949e',
            fontSize: '10px',
            marginTop: '4px'
          }}>
            {signals.length} signals | {filteredSignals.filter(s => s.type === 'BUY').length} buy | {filteredSignals.filter(s => s.type === 'SELL').length} sell
          </div>
        </div>

        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {/* Pause/Resume */}
          <button
            onClick={() => setIsPaused(!isPaused)}
            style={{
              backgroundColor: isPaused ? '#FFAB0020' : 'transparent',
              border: `1px solid ${isPaused ? '#FFAB00' : '#30363d'}`,
              borderRadius: '6px',
              padding: '6px 12px',
              color: isPaused ? '#FFAB00' : '#8b949e',
              fontSize: '10px',
              fontWeight: 700,
              cursor: 'pointer',
              fontFamily: 'monospace'
            }}
          >
            {isPaused ? '⏸ PAUSED' : '▶ LIVE'}
          </button>

          {/* Filters */}
          {showFilters && ['ALL', 'BUY', 'SELL'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                backgroundColor: filter === f ? '#58a6ff20' : 'transparent',
                border: `1px solid ${filter === f ? '#58a6ff' : '#30363d'}`,
                borderRadius: '6px',
                padding: '6px 12px',
                color: filter === f ? '#58a6ff' : '#8b949e',
                fontSize: '10px',
                fontWeight: 700,
                cursor: 'pointer',
                fontFamily: 'monospace'
              }}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* 🔴 Signal List */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px'
      }}>
        {filteredSignals.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '40px',
            color: '#8b949e',
            fontSize: '11px'
          }}>
            No signals received yet. Signals will appear here when strategies generate them.
          </div>
        ) : (
          filteredSignals.map((signal, index) => {
            const config = SIGNAL_CONFIG[signal.type] || SIGNAL_CONFIG.HOLD;
            const sourceConfig = getSourceConfig(signal.source);
            const isSelected = selectedSignal === signal.id;
            const isNew = index === 0;

            return (
              <div
                key={signal.id}
                onClick={() => handleSignalClick(signal)}
                style={{
                  backgroundColor: isSelected ? `${config.color}10` : '#161b22',
                  border: `1px solid ${isSelected ? config.color : '#30363d'}`,
                  borderLeft: `3px solid ${config.color}`,
                  borderRadius: '6px',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  animation: isNew && !isPaused ? 'slideIn 0.3s ease-out' : 'none'
                }}
              >
                {/* Signal Line */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  fontSize: '11px'
                }}>
                  {/* Timestamp */}
                  <div style={{
                    color: '#8b949e',
                    fontWeight: 700,
                    minWidth: '85px',
                    fontFamily: 'monospace'
                  }}>
                    {formatTime(signal.timestamp)}
                  </div>

                  {/* Arrow */}
                  <span style={{ 
                    color: config.color,
                    fontSize: '12px',
                    fontWeight: 900
                  }}>
                    →
                  </span>

                  {/* Source Node */}
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    minWidth: '120px'
                  }}>
                    <span>{sourceConfig.icon}</span>
                    <span style={{
                      color: sourceConfig.color,
                      fontWeight: 700
                    }}>
                      {signal.source}
                    </span>
                  </div>

                  {/* Value */}
                  <div style={{
                    color: '#ffffff',
                    fontWeight: 700,
                    minWidth: '80px'
                  }}>
                    {typeof signal.value === 'number' 
                      ? signal.value.toFixed(2) 
                      : signal.value}
                  </div>

                  {/* Signal Type Badge */}
                  <div style={{
                    backgroundColor: config.bgColor,
                    border: `1px solid ${config.borderColor}`,
                    borderRadius: '4px',
                    padding: '2px 8px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}>
                    <span style={{ color: config.color, fontSize: '10px' }}>
                      {config.icon}
                    </span>
                    <span style={{
                      color: config.color,
                      fontSize: '10px',
                      fontWeight: 900
                    }}>
                      {config.label}
                    </span>
                  </div>

                  {/* Symbol */}
                  <div style={{
                    marginLeft: 'auto',
                    color: '#58a6ff',
                    fontWeight: 700,
                    fontSize: '10px'
                  }}>
                    {signal.symbol}
                  </div>

                  {/* Executed Indicator */}
                  {signal.executed && (
                    <div style={{
                      backgroundColor: '#00C85320',
                      border: '1px solid #00C85340',
                      borderRadius: '4px',
                      padding: '2px 6px',
                      color: '#00C853',
                      fontSize: '9px',
                      fontWeight: 700
                    }}>
                      ✓ EXECUTED
                    </div>
                  )}
                </div>

                {/* Expanded Details */}
                {isSelected && (
                  <div style={{
                    marginTop: '10px',
                    padding: '10px',
                    backgroundColor: '#0d1117',
                    border: '1px solid #30363d',
                    borderRadius: '6px',
                    fontSize: '10px'
                  }}>
                    <div style={{ 
                      display: 'grid', 
                      gridTemplateColumns: 'repeat(2, 1fr)',
                      gap: '8px'
                    }}>
                      <div>
                        <span style={{ color: '#8b949e' }}>Strategy ID: </span>
                        <span style={{ color: '#ffffff' }}>{signal.strategyId || 'N/A'}</span>
                      </div>
                      <div>
                        <span style={{ color: '#8b949e' }}>Node Type: </span>
                        <span style={{ color: '#ffffff' }}>{signal.nodeType || 'N/A'}</span>
                      </div>
                      <div>
                        <span style={{ color: '#8b949e' }}>Price: </span>
                        <span style={{ color: '#ffffff' }}>${signal.price?.toFixed(2) || 'N/A'}</span>
                      </div>
                      <div>
                        <span style={{ color: '#8b949e' }}>Signal ID: </span>
                        <span style={{ color: '#ffffff' }}>{signal.id.slice(-8)}</span>
                      </div>
                      {signal.executionPrice && (
                        <div>
                          <span style={{ color: '#8b949e' }}>Exec Price: </span>
                          <span style={{ color: '#00C853' }}>${signal.executionPrice.toFixed(2)}</span>
                        </div>
                      )}
                      {signal.orderId && (
                        <div>
                          <span style={{ color: '#8b949e' }}>Order: </span>
                          <span style={{ color: '#58a6ff' }}>#{signal.orderId.slice(-6)}</span>
                        </div>
                      )}
                    </div>

                    {/* Metadata */}
                    {Object.keys(signal.metadata).length > 0 && (
                      <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #30363d' }}>
                        <div style={{ color: '#8b949e', marginBottom: '4px' }}>Additional Data:</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                          {Object.entries(signal.metadata).map(([key, value]) => (
                            <span key={key} style={{ 
                              backgroundColor: '#21262d', 
                              padding: '2px 6px', 
                              borderRadius: '4px',
                              color: '#c9d1d9'
                            }}>
                              {key}: {value}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <style>{`
        @keyframes slideIn {
          from {
            transform: translateX(-20px);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 2: Hook for signal tracking
export const useSignalTrace = () => {
  const [recentSignals, setRecentSignals] = useState([]);
  const [signalStats, setSignalStats] = useState({ buy: 0, sell: 0, hold: 0 });

  const addSignal = useCallback((signal) => {
    setRecentSignals(prev => {
      const updated = [signal, ...prev].slice(0, 50);
      
      // Update stats
      const type = signal.type?.toLowerCase();
      setSignalStats(stats => ({
        ...stats,
        [type]: (stats[type] || 0) + 1
      }));
      
      return updated;
    });
  }, []);

  const clearSignals = useCallback(() => {
    setRecentSignals([]);
    setSignalStats({ buy: 0, sell: 0, hold: 0 });
  }, []);

  return {
    signals: recentSignals,
    stats: signalStats,
    addSignal,
    clearSignals
  };
};

export default SignalTracePanel;
export { MAX_SIGNALS, SIGNAL_CONFIG };
