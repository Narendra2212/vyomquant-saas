/**
 * ═══════════════════════════════════════════════════════════════════════════
 * EVENT-DRIVEN DAG RUNNER COMPONENT
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Live event-driven DAG execution with real-time signal visualization.
 * 
 * Features:
 * - Start/stop event-driven sessions
 * - Real-time signal feed
 * - Live statistics dashboard
 * - Rolling window visualization
 * - Symbol selection
 * 
 * Uses: useEventDag hook
 */

import React, { useState, useEffect, useCallback } from 'react';
import { 
  Play, 
  Square, 
  Activity, 
  Zap, 
  TrendingUp, 
  TrendingDown,
  Clock,
  BarChart3,
  Radio,
  AlertCircle,
  CheckCircle,
  XCircle,
  Loader2
} from 'lucide-react';
import { useEventDag } from '../hooks/useEventDag';

// ═══════════════════════════════════════════════════════════════════════════
// COLOR PALETTE
// ═══════════════════════════════════════════════════════════════════════════
const C = {
  bg: "#0a0a0a",
  bg2: "#0f1115",
  bg3: "#13161c",
  border: "#1f2937",
  t1: "#f1f5f9",
  t2: "#94a3b8",
  t3: "#64748b",
  accent: "#00d4ff",
  cyan: "#00d4ff",
  green: "#00ff88",
  red: "#ff3366",
  orange: "#ff9500",
  purple: "#8b5cf6",
};

// ═══════════════════════════════════════════════════════════════════════════
// EVENT DAG RUNNER COMPONENT
// ═══════════════════════════════════════════════════════════════════════════
function EventDagRunner({ dagConfig }) {
  // ── Local State ─────────────────────────────────────────────────────────
  const [selectedSymbols, setSelectedSymbols] = useState(['BTCUSDT']);
  const [timeframe, setTimeframe] = useState('1m');
  const [simulationMode, setSimulationMode] = useState(true);
  const [speed, setSpeed] = useState(1.0);
  
  // ── Event DAG Hook ──────────────────────────────────────────────────────
  const {
    sessionId,
    isRunning,
    isLoading,
    error,
    stats,
    signals,
    latestSignal,
    start,
    stop,
    clearSignals
  } = useEventDag({
    onSignal: (signal) => {
      console.log('New signal:', signal);
    },
    onStats: (stats) => {
      // Stats updated automatically
    }
  });
  
  // ── Start Handler ───────────────────────────────────────────────────────
  const handleStart = async () => {
    if (!dagConfig || !dagConfig.nodes || dagConfig.nodes.length === 0) {
      alert('Please configure a DAG first');
      return;
    }
    
    try {
      await start({
        nodes: dagConfig.nodes,
        edges: dagConfig.edges,
        symbols: selectedSymbols,
        timeframe: timeframe,
        simulation: simulationMode,
        speed: speed,
        maxWindow: 500
      });
    } catch (err) {
      console.error('Failed to start event-DAG:', err);
    }
  };
  
  // ── Stop Handler ───────────────────────────────────────────────────────
  const handleStop = async () => {
    await stop();
  };
  
  // ── Available Symbols ───────────────────────────────────────────────────
  const availableSymbols = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'DOTUSDT',
    'XRPUSDT', 'UNIUSDT', 'LTCUSDT', 'LINKUSDT', 'BCHUSDT'
  ];
  
  const timeframes = ['1m', '5m', '15m', '1h', '4h', '1d'];
  
  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div style={{ 
      background: C.bg,
      border: `1px solid ${C.border}`,
      borderRadius: 12,
      padding: 24
    }}>
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        marginBottom: 24
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Radio size={24} color={isRunning ? C.green : C.t3} />
          <div>
            <h3 style={{ 
              fontSize: 18, 
              fontWeight: 700, 
              color: C.t1, 
              margin: 0 
            }}>
              Event-Driven DAG
            </h3>
            <p style={{ 
              fontSize: 13, 
              color: C.t2, 
              margin: '4px 0 0 0' 
            }}>
              {isRunning 
                ? `Running • Session: ${sessionId?.slice(0, 8)}...` 
                : 'Stopped • Configure and start'}
            </p>
          </div>
        </div>
        
        {/* Status Indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          background: isRunning ? `${C.green}15` : C.bg3,
          border: `1px solid ${isRunning ? C.green : C.border}`,
          borderRadius: 20,
          padding: '6px 16px'
        }}>
          <div style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: isRunning ? C.green : C.t3,
            animation: isRunning ? 'pulse 2s infinite' : 'none'
          }} />
          <span style={{
            fontSize: 12,
            color: isRunning ? C.green : C.t2,
            fontWeight: 600,
            fontFamily: 'monospace'
          }}>
            {isRunning ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>
      </div>
      
      {/* Error Message */}
      {error && (
        <div style={{
          background: `${C.red}15`,
          border: `1px solid ${C.red}40`,
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 16,
          display: 'flex',
          alignItems: 'center',
          gap: 8
        }}>
          <AlertCircle size={16} color={C.red} />
          <span style={{ color: C.red, fontSize: 13 }}>{error}</span>
        </div>
      )}
      
      {/* Configuration */}
      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: '1fr 1fr', 
        gap: 16,
        marginBottom: 24 
      }}>
        {/* Symbol Selection */}
        <div>
          <label style={{ 
            display: 'block', 
            fontSize: 12, 
            color: C.t2, 
            marginBottom: 8,
            fontWeight: 600 
          }}>
            Symbols
          </label>
          <div style={{ 
            display: 'flex', 
            flexWrap: 'wrap', 
            gap: 8,
            maxHeight: 100,
            overflowY: 'auto'
          }}>
            {availableSymbols.map(symbol => (
              <button
                key={symbol}
                onClick={() => {
                  if (selectedSymbols.includes(symbol)) {
                    setSelectedSymbols(prev => prev.filter(s => s !== symbol));
                  } else {
                    setSelectedSymbols(prev => [...prev, symbol]);
                  }
                }}
                disabled={isRunning}
                style={{
                  padding: '6px 12px',
                  background: selectedSymbols.includes(symbol) 
                    ? `${C.cyan}20` 
                    : C.bg3,
                  border: `1px solid ${selectedSymbols.includes(symbol) ? C.cyan : C.border}`,
                  borderRadius: 6,
                  color: selectedSymbols.includes(symbol) ? C.cyan : C.t2,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: isRunning ? 'not-allowed' : 'pointer',
                  opacity: isRunning ? 0.6 : 1,
                  fontFamily: 'monospace'
                }}
              >
                {symbol}
              </button>
            ))}
          </div>
        </div>
        
        {/* Timeframe & Mode */}
        <div>
          <label style={{ 
            display: 'block', 
            fontSize: 12, 
            color: C.t2, 
            marginBottom: 8,
            fontWeight: 600 
          }}>
            Timeframe
          </label>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            {timeframes.map(tf => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                disabled={isRunning}
                style={{
                  padding: '6px 12px',
                  background: timeframe === tf ? `${C.cyan}20` : C.bg3,
                  border: `1px solid ${timeframe === tf ? C.cyan : C.border}`,
                  borderRadius: 6,
                  color: timeframe === tf ? C.cyan : C.t2,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: isRunning ? 'not-allowed' : 'pointer'
                }}
              >
                {tf}
              </button>
            ))}
          </div>
          
          {/* Simulation Toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              onClick={() => setSimulationMode(!simulationMode)}
              disabled={isRunning}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 16px',
                background: simulationMode ? `${C.orange}15` : C.bg3,
                border: `1px solid ${simulationMode ? C.orange : C.border}`,
                borderRadius: 6,
                color: simulationMode ? C.orange : C.t2,
                fontSize: 12,
                fontWeight: 600,
                cursor: isRunning ? 'not-allowed' : 'pointer'
              }}
            >
              {simulationMode ? <Zap size={14} /> : <Activity size={14} />}
              {simulationMode ? 'Simulation' : 'Live Data'}
            </button>
            
            {simulationMode && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: C.t3 }}>Speed:</span>
                <input
                  type="range"
                  min="0.1"
                  max="10"
                  step="0.1"
                  value={speed}
                  onChange={(e) => setSpeed(parseFloat(e.target.value))}
                  disabled={isRunning}
                  style={{ width: 100 }}
                />
                <span style={{ 
                  fontSize: 11, 
                  color: C.t2,
                  fontFamily: 'monospace',
                  minWidth: 40
                }}>
                  {speed.toFixed(1)}x
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
      
      {/* Control Buttons */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
        {!isRunning ? (
          <button
            onClick={handleStart}
            disabled={isLoading || selectedSymbols.length === 0}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '12px 24px',
              background: `linear-gradient(135deg, ${C.green}20, ${C.green}10)`,
              border: `1px solid ${C.green}50`,
              borderRadius: 8,
              color: C.green,
              fontSize: 14,
              fontWeight: 600,
              cursor: isLoading || selectedSymbols.length === 0 ? 'not-allowed' : 'pointer',
              opacity: isLoading || selectedSymbols.length === 0 ? 0.6 : 1
            }}
          >
            {isLoading ? (
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
            ) : (
              <Play size={18} />
            )}
            {isLoading ? 'Starting...' : 'Start Event Loop'}
          </button>
        ) : (
          <button
            onClick={handleStop}
            disabled={isLoading}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '12px 24px',
              background: `linear-gradient(135deg, ${C.red}20, ${C.red}10)`,
              border: `1px solid ${C.red}50`,
              borderRadius: 8,
              color: C.red,
              fontSize: 14,
              fontWeight: 600,
              cursor: isLoading ? 'not-allowed' : 'pointer',
              opacity: isLoading ? 0.6 : 1
            }}
          >
            {isLoading ? (
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
            ) : (
              <Square size={18} />
            )}
            {isLoading ? 'Stopping...' : 'Stop Event Loop'}
          </button>
        )}
        
        {signals.length > 0 && (
          <button
            onClick={clearSignals}
            style={{
              padding: '12px 16px',
              background: C.bg3,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              color: C.t2,
              fontSize: 14,
              cursor: 'pointer'
            }}
          >
            Clear Signals
          </button>
        )}
      </div>
      
      {/* Stats Dashboard */}
      {isRunning && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 12,
          marginBottom: 24
        }}>
          <StatCard
            icon={<BarChart3 size={16} />}
            label="Events Processed"
            value={stats.events_processed.toLocaleString()}
            color={C.cyan}
          />
          <StatCard
            icon={<Zap size={16} />}
            label="Signals Emitted"
            value={stats.signals_emitted.toLocaleString()}
            color={C.green}
          />
          <StatCard
            icon={<Clock size={16} />}
            label="Runtime"
            value={`${Math.floor(stats.runtime_seconds)}s`}
            color={C.orange}
          />
          <StatCard
            icon={<Activity size={16} />}
            label="Events/sec"
            value={stats.events_per_second.toFixed(1)}
            color={C.purple}
          />
        </div>
      )}
      
      {/* Latest Signal */}
      {latestSignal && (
        <div style={{
          background: latestSignal.action === 'buy' 
            ? `${C.green}10` 
            : latestSignal.action === 'sell'
            ? `${C.red}10`
            : C.bg3,
          border: `1px solid ${latestSignal.action === 'buy' 
            ? C.green 
            : latestSignal.action === 'sell'
            ? C.red
            : C.border}`,
          borderRadius: 8,
          padding: 16,
          marginBottom: 24
        }}>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'space-between',
            marginBottom: 8
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {latestSignal.action === 'buy' ? (
                <TrendingUp size={20} color={C.green} />
              ) : latestSignal.action === 'sell' ? (
                <TrendingDown size={20} color={C.red} />
              ) : (
                <Activity size={20} color={C.t3} />
              )}
              <span style={{ 
                fontSize: 14, 
                fontWeight: 700,
                color: latestSignal.action === 'buy' 
                  ? C.green 
                  : latestSignal.action === 'sell'
                  ? C.red
                  : C.t2,
                textTransform: 'uppercase'
              }}>
                {latestSignal.action}
              </span>
            </div>
            <span style={{ fontSize: 12, color: C.t3, fontFamily: 'monospace' }}>
              {latestSignal.symbol}
            </span>
          </div>
          
          <div style={{ display: 'flex', gap: 16, fontSize: 12, color: C.t2 }}>
            <span>Strength: {(latestSignal.strength * 100).toFixed(0)}%</span>
            <span>Time: {new Date(latestSignal.timestamp).toLocaleTimeString()}</span>
          </div>
        </div>
      )}
      
      {/* Signal Feed */}
      <div style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: 8,
        maxHeight: 300,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column'
      }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between'
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: C.t1 }}>
            Signal Feed
          </span>
          <span style={{ fontSize: 12, color: C.t3 }}>
            {signals.length} signals
          </span>
        </div>
        
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px'
        }}>
          {signals.length === 0 ? (
            <div style={{
              textAlign: 'center',
              padding: 40,
              color: C.t3
            }}>
              <Activity size={32} style={{ marginBottom: 8, opacity: 0.5 }} />
              <p style={{ margin: 0, fontSize: 13 }}>
                {isRunning 
                  ? 'Waiting for signals...' 
                  : 'Start the event loop to see signals'}
              </p>
            </div>
          ) : (
            signals.slice(0, 20).map((signal, index) => (
              <div
                key={`${signal.timestamp}-${index}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 12px',
                  background: index === 0 ? `${C.cyan}08` : 'transparent',
                  border: `1px solid ${index === 0 ? `${C.cyan}30` : 'transparent'}`,
                  borderRadius: 6,
                  marginBottom: 4
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {signal.action === 'buy' ? (
                    <CheckCircle size={14} color={C.green} />
                  ) : signal.action === 'sell' ? (
                    <XCircle size={14} color={C.red} />
                  ) : (
                    <Activity size={14} color={C.t3} />
                  )}
                  <span style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: signal.action === 'buy' 
                      ? C.green 
                      : signal.action === 'sell'
                      ? C.red
                      : C.t2,
                    textTransform: 'uppercase',
                    minWidth: 50
                  }}>
                    {signal.action}
                  </span>
                  <span style={{ fontSize: 12, color: C.t2, fontFamily: 'monospace' }}>
                    {signal.symbol}
                  </span>
                </div>
                
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{
                    width: 40,
                    height: 4,
                    background: C.bg3,
                    borderRadius: 2,
                    overflow: 'hidden'
                  }}>
                    <div style={{
                      width: `${signal.strength * 100}%`,
                      height: '100%',
                      background: signal.action === 'buy' 
                        ? C.green 
                        : signal.action === 'sell'
                        ? C.red
                        : C.t3,
                      borderRadius: 2
                    }} />
                  </div>
                  <span style={{ 
                    fontSize: 11, 
                    color: C.t3,
                    fontFamily: 'monospace',
                    minWidth: 60
                  }}>
                    {new Date(signal.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
      
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// STAT CARD COMPONENT
// ═══════════════════════════════════════════════════════════════════════════
function StatCard({ icon, label, value, color }) {
  return (
    <div style={{
      background: C.bg2,
      border: `1px solid ${C.border}`,
      borderRadius: 8,
      padding: 16,
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }}>
      <div style={{ color: color }}>{icon}</div>
      <div style={{
        fontSize: 24,
        fontWeight: 800,
        color: C.t1,
        fontFamily: 'monospace'
      }}>
        {value}
      </div>
      <div style={{
        fontSize: 11,
        color: C.t3,
        textTransform: 'uppercase',
        letterSpacing: 0.5
      }}>
        {label}
      </div>
    </div>
  );
}

export default EventDagRunner;
