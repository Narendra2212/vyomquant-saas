import React, { useState, useEffect, useCallback } from 'react';
import { normalizeTelemetryEvent } from '../websocketClient';

/**
 * 🔴 STEP 1 — STRATEGY EXECUTION DASHBOARD
 * 
 * Full visibility into automated strategy performance
 * 
 * Shows:
 * - Strategy health (RUNNING/STOPPED/ERROR)
 * - Signal generation rate
 * - PnL contribution per strategy
 * - Active positions by strategy
 * - Critical alerts on failures
 */

// Status configurations
const STATUS_CONFIG = {
  RUNNING: {
    color: '#00C853',
    bgColor: '#00C85315',
    borderColor: '#00C85340',
    icon: '▶',
    pulse: true
  },
  STOPPED: {
    color: '#9E9E9E',
    bgColor: '#9E9E9E15',
    borderColor: '#9E9E9E40',
    icon: '⏹',
    pulse: false
  },
  ERROR: {
    color: '#FF5252',
    bgColor: '#FF525215',
    borderColor: '#FF525240',
    icon: '⚠',
    pulse: true
  },
  PENDING: {
    color: '#FFAB00',
    bgColor: '#FFAB0015',
    borderColor: '#FFAB0040',
    icon: '⏳',
    pulse: false
  }
};

const StrategyDashboard = ({ 
  wsClient, 
  accountId,
  onStrategyError,
  onStrategySelect
}) => {
  const [strategies, setStrategies] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [criticalAlerts, setCriticalAlerts] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(Date.now());

  // 🔴 STEP 1: Show critical alert for strategy failure
  const showCritical = useCallback((message, strategyId) => {
    const alert = {
      id: Date.now(),
      message,
      strategyId,
      timestamp: Date.now()
    };
    
    setCriticalAlerts(prev => [...prev, alert]);
    
    // Notify parent
    if (onStrategyError) {
      onStrategyError({ message, strategyId });
    }
    
    // Auto-remove after 10 seconds
    setTimeout(() => {
      setCriticalAlerts(prev => prev.filter(a => a.id !== alert.id));
    }, 10000);
  }, [onStrategyError]);

  // 🔴 STEP 1: Process WebSocket messages for strategy updates
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (rawData) => {
      try {
        const parsedData = (rawData && rawData.data) ? JSON.parse(rawData.data) : rawData;
        const data = normalizeTelemetryEvent(parsedData);

        // Strategy status update
        if (data.type === 'strategy_update' || data.type === 'strategy_status') {
          setStrategies(prev => {
            const existing = prev.find(s => s.id === data.strategy_id);
            
            // Check for ERROR status transition
            if (data.status === 'ERROR' && (!existing || existing.status !== 'ERROR')) {
              showCritical(
                `Strategy "${data.strategy_name || data.strategy_id}" has failed`,
                data.strategy_id
              );
            }
            
            // Update or add strategy
            const updated = prev.map(s => 
              s.id === data.strategy_id 
                ? { 
                    ...s, 
                    ...data,
                    lastSignalTime: data.last_signal_time || s.lastSignalTime,
                    signalsPerMinute: data.signals_per_minute || s.signalsPerMinute,
                    pnlContribution: data.pnl_contribution || s.pnlContribution,
                    activePositions: data.active_positions || s.activePositions
                  }
                : s
            );
            
            if (!existing) {
              updated.push({
                id: data.strategy_id,
                name: data.strategy_name || data.strategy_id,
                status: data.status || 'PENDING',
                signalsPerMinute: data.signals_per_minute || 0,
                lastSignalTime: data.last_signal_time || null,
                pnlContribution: data.pnl_contribution || 0,
                activePositions: data.active_positions || 0,
                symbol: data.symbol || '-',
                timeframe: data.timeframe || '-',
                ...data
              });
            }
            
            return updated;
          });
          
          setLastUpdate(Date.now());
        }

        // Signal update
        if (data.type === 'signal_update' && data.strategy_id) {
          setStrategies(prev => prev.map(s => 
            s.id === data.strategy_id
              ? { 
                  ...s, 
                  lastSignalTime: data.timestamp || Date.now(),
                  signalsPerMinute: data.signals_per_minute || s.signalsPerMinute,
                  lastSignal: data.signal
                }
              : s
          ));
        }

        // PnL update
        if (data.type === 'strategy_pnl' && data.strategy_id) {
          setStrategies(prev => prev.map(s => 
            s.id === data.strategy_id
              ? { ...s, pnlContribution: data.pnl || data.pnl_contribution || s.pnlContribution }
              : s
          ));
        }

        // Position update
        if (data.type === 'strategy_positions' && data.strategy_id) {
          setStrategies(prev => prev.map(s => 
            s.id === data.strategy_id
              ? { ...s, activePositions: data.count || data.positions?.length || s.activePositions }
              : s
          ));
        }

      } catch (err) {
        console.error('[STEP 1] Failed to process WebSocket message:', err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubUpdate = null;
    let unsubStatus = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubUpdate = wsClient.subscribe('strategy_update', handleMessage);
      unsubStatus = wsClient.subscribe('strategy_status', handleMessage);
    } else {
      console.warn('[STEP 1] wsClient.subscribe is not available');
    }
    
    return () => {
      if (unsubUpdate) unsubUpdate();
      if (unsubStatus) unsubStatus();
    };
  }, [wsClient, showCritical]);

  // 🔴 STEP 1: Format time ago
  const getTimeAgo = (timestamp) => {
    if (!timestamp) return 'Never';
    const seconds = Math.floor((Date.now() - timestamp) / 1000);
    if (seconds < 5) return 'Just now';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };

  // 🔴 STEP 1: Handle strategy selection
  const handleStrategyClick = (strategy) => {
    setSelectedStrategy(strategy.id === selectedStrategy ? null : strategy.id);
    if (onStrategySelect) {
      onStrategySelect(strategy);
    }
  };

  // 🔴 STEP 1: Calculate aggregate stats
  const totalRunning = strategies.filter(s => s.status === 'RUNNING').length;
  const totalError = strategies.filter(s => s.status === 'ERROR').length;
  const totalStopped = strategies.filter(s => s.status === 'STOPPED').length;
  const totalPnl = strategies.reduce((sum, s) => sum + (s.pnlContribution || 0), 0);
  const totalPositions = strategies.reduce((sum, s) => sum + (s.activePositions || 0), 0);

  return (
    <div style={{
      backgroundColor: '#0d1117',
      border: '1px solid #30363d',
      borderRadius: '12px',
      padding: '16px',
      fontFamily: 'monospace'
    }}>
      {/* 🔴 Critical Alerts */}
      {criticalAlerts.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          {criticalAlerts.map(alert => (
            <div
              key={alert.id}
              style={{
                backgroundColor: '#FF525220',
                border: '1px solid #FF5252',
                borderRadius: '8px',
                padding: '12px 16px',
                marginBottom: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                animation: 'slideIn 0.3s ease-out'
              }}
            >
              <span style={{ fontSize: '20px' }}>🚨</span>
              <div>
                <div style={{
                  color: '#FF5252',
                  fontSize: '12px',
                  fontWeight: 900,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px'
                }}>
                  CRITICAL ALERT
                </div>
                <div style={{ color: '#ffffff', fontSize: '11px', marginTop: '2px' }}>
                  {alert.message}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 🔴 Header with aggregate stats */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '16px',
        paddingBottom: '16px',
        borderBottom: '1px solid #30363d'
      }}>
        <div>
          <div style={{
            color: '#ffffff',
            fontSize: '14px',
            fontWeight: 900,
            letterSpacing: '0.5px'
          }}>
            STRATEGY EXECUTION DASHBOARD
          </div>
          <div style={{
            color: '#8b949e',
            fontSize: '10px',
            marginTop: '4px'
          }}>
            {strategies.length} strategies | {totalRunning} running | {totalError} errors
          </div>
        </div>
        
        <div style={{ display: 'flex', gap: '16px' }}>
          {/* Total PnL */}
          <div style={{ textAlign: 'right' }}>
            <div style={{ color: '#8b949e', fontSize: '9px', textTransform: 'uppercase' }}>
              Total PnL
            </div>
            <div style={{
              color: totalPnl >= 0 ? '#00C853' : '#FF5252',
              fontSize: '14px',
              fontWeight: 900
            }}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
            </div>
          </div>
          
          {/* Active Positions */}
          <div style={{ textAlign: 'right' }}>
            <div style={{ color: '#8b949e', fontSize: '9px', textTransform: 'uppercase' }}>
              Positions
            </div>
            <div style={{ color: '#58a6ff', fontSize: '14px', fontWeight: 900 }}>
              {totalPositions}
            </div>
          </div>
        </div>
      </div>

      {/* 🔴 Strategy List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {strategies.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '40px',
            color: '#8b949e',
            fontSize: '11px'
          }}>
            No active strategies. Start a strategy to see execution data.
          </div>
        ) : (
          strategies.map(strategy => {
            const config = STATUS_CONFIG[strategy.status] || STATUS_CONFIG.PENDING;
            const isSelected = selectedStrategy === strategy.id;
            const pnlColor = strategy.pnlContribution >= 0 ? '#00C853' : '#FF5252';
            
            return (
              <div
                key={strategy.id}
                onClick={() => handleStrategyClick(strategy)}
                style={{
                  backgroundColor: isSelected ? `${config.color}10` : '#161b22',
                  border: `1px solid ${isSelected ? config.color : '#30363d'}`,
                  borderRadius: '8px',
                  padding: '12px 14px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease'
                }}
              >
                {/* Strategy Header */}
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: '8px'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {/* Status Indicator */}
                    <div style={{
                      width: '10px',
                      height: '10px',
                      borderRadius: '50%',
                      backgroundColor: config.color,
                      boxShadow: config.pulse ? `0 0 8px ${config.color}` : 'none',
                      animation: config.pulse ? 'pulse 2s infinite' : 'none'
                    }} />
                    
                    {/* Strategy Name */}
                    <span style={{
                      color: '#ffffff',
                      fontSize: '12px',
                      fontWeight: 700
                    }}>
                      {strategy.name}
                    </span>
                    
                    {/* Status Badge */}
                    <span style={{
                      backgroundColor: config.bgColor,
                      color: config.color,
                      border: `1px solid ${config.borderColor}`,
                      borderRadius: '4px',
                      padding: '2px 8px',
                      fontSize: '9px',
                      fontWeight: 900,
                      textTransform: 'uppercase'
                    }}>
                      {config.icon} {strategy.status}
                    </span>
                  </div>
                  
                  {/* PnL Contribution */}
                  <div style={{
                    color: pnlColor,
                    fontSize: '12px',
                    fontWeight: 900
                  }}>
                    {strategy.pnlContribution >= 0 ? '+' : ''}
                    ${Math.abs(strategy.pnlContribution).toFixed(2)}
                  </div>
                </div>
                
                {/* Strategy Metrics */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: '12px',
                  fontSize: '10px'
                }}>
                  <div>
                    <div style={{ color: '#8b949e', marginBottom: '2px' }}>Signals/min</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {strategy.signalsPerMinute?.toFixed(1) || '0.0'}
                    </div>
                  </div>
                  
                  <div>
                    <div style={{ color: '#8b949e', marginBottom: '2px' }}>Last Signal</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {getTimeAgo(strategy.lastSignalTime)}
                    </div>
                  </div>
                  
                  <div>
                    <div style={{ color: '#8b949e', marginBottom: '2px' }}>Active Positions</div>
                    <div style={{ color: '#58a6ff', fontWeight: 700 }}>
                      {strategy.activePositions || 0}
                    </div>
                  </div>
                  
                  <div>
                    <div style={{ color: '#8b949e', marginBottom: '2px' }}>Symbol</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {strategy.symbol || '-'}
                    </div>
                  </div>
                </div>
                
                {/* Error Message (if applicable) */}
                {strategy.status === 'ERROR' && strategy.error_message && (
                  <div style={{
                    marginTop: '8px',
                    padding: '8px',
                    backgroundColor: '#FF525215',
                    border: '1px solid #FF525240',
                    borderRadius: '4px',
                    color: '#FF5252',
                    fontSize: '10px'
                  }}>
                    Error: {strategy.error_message}
                  </div>
                )}
                
                {/* Last Signal Details (expanded) */}
                {isSelected && strategy.lastSignal && (
                  <div style={{
                    marginTop: '8px',
                    padding: '10px',
                    backgroundColor: '#0d1117',
                    border: '1px solid #30363d',
                    borderRadius: '6px',
                    fontSize: '10px'
                  }}>
                    <div style={{ color: '#8b949e', marginBottom: '6px', fontWeight: 700 }}>
                      LAST SIGNAL
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                      <div>
                        <span style={{ color: '#8b949e' }}>Type: </span>
                        <span style={{ 
                          color: strategy.lastSignal.side === 'buy' ? '#00C853' : '#FF5252',
                          fontWeight: 700,
                          textTransform: 'uppercase'
                        }}>
                          {strategy.lastSignal.side}
                        </span>
                      </div>
                      <div>
                        <span style={{ color: '#8b949e' }}>Size: </span>
                        <span style={{ color: '#ffffff' }}>{strategy.lastSignal.size}</span>
                      </div>
                      <div>
                        <span style={{ color: '#8b949e' }}>Price: </span>
                        <span style={{ color: '#ffffff' }}>${strategy.lastSignal.price?.toFixed(2)}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Last Update Time */}
      <div style={{
        marginTop: '12px',
        paddingTop: '12px',
        borderTop: '1px solid #30363d',
        textAlign: 'right',
        color: '#8b949e',
        fontSize: '9px'
      }}>
        Last update: {getTimeAgo(lastUpdate)}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(0.95); }
        }
        
        @keyframes slideIn {
          from { transform: translateX(-100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 1: Hook for strategy monitoring
export const useStrategyMonitor = () => {
  const [strategies, setStrategies] = useState([]);
  const [errors, setErrors] = useState([]);

  const handleStrategyError = useCallback((error) => {
    setErrors(prev => [...prev, { ...error, time: Date.now() }]);
  }, []);

  const getRunningStrategies = useCallback(() => {
    return strategies.filter(s => s.status === 'RUNNING');
  }, [strategies]);

  const getStrategyById = useCallback((id) => {
    return strategies.find(s => s.id === id);
  }, [strategies]);

  return {
    strategies,
    setStrategies,
    errors,
    handleStrategyError,
    getRunningStrategies,
    getStrategyById
  };
};

export default StrategyDashboard;
export { STATUS_CONFIG };
