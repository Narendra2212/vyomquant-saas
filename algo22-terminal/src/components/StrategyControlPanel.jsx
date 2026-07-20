import React, { useState, useEffect, useCallback } from 'react';
import * as Sentry from "@sentry/react";

/**
 * 🔴 STEP 7 & 8 — STRATEGY CONTROL PANEL
 * 
 * Strategy deployment controls and multi-strategy management
 * 
 * Features:
 * - Deploy/Stop/Pause strategies
 * - Close all positions
 * - Emergency halt
 * - Multi-strategy PnL view
 * - Strategy failure detection
 */

// Confirmation dialog component
const ConfirmDialog = ({ isOpen, title, message, onConfirm, onCancel, confirmText = 'CONFIRM', danger = false }) => {
  if (!isOpen) return null;

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.8)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 10000
    }}>
      <div style={{
        backgroundColor: '#0d1117',
        border: `2px solid ${danger ? '#FF5252' : '#FFAB00'}`,
        borderRadius: '12px',
        padding: '24px',
        maxWidth: '400px',
        width: '90%'
      }}>
        <div style={{
          color: danger ? '#FF5252' : '#FFAB00',
          fontSize: '18px',
          fontWeight: 900,
          marginBottom: '12px'
        }}>
          ⚠️ {title}
        </div>
        <div style={{
          color: '#c9d1d9',
          fontSize: '14px',
          marginBottom: '24px',
          lineHeight: '1.5'
        }}>
          {message}
        </div>
        <div style={{
          display: 'flex',
          gap: '12px'
        }}>
          <button
            onClick={onCancel}
            style={{
              flex: 1,
              padding: '12px',
              backgroundColor: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '6px',
              color: '#8b949e',
              fontSize: '12px',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            CANCEL
          </button>
          <button
            onClick={onConfirm}
            style={{
              flex: 1,
              padding: '12px',
              backgroundColor: danger ? '#FF525220' : '#FFAB0020',
              border: `1px solid ${danger ? '#FF5252' : '#FFAB00'}`,
              borderRadius: '6px',
              color: danger ? '#FF5252' : '#FFAB00',
              fontSize: '12px',
              fontWeight: 900,
              cursor: 'pointer'
            }}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
};

const StrategyControlPanel = ({
  wsClient,
  apiClient,
  onDeployStrategy,
  onStopStrategy,
  onPauseStrategy,
  onCloseAllPositions,
  onEmergencyHalt
}) => {
  const [strategies, setStrategies] = useState([]);
  const [selectedStrategies, setSelectedStrategies] = useState([]);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [inactivityWarnings, setInactivityWarnings] = useState([]);
  const [globalPnL, setGlobalPnL] = useState({ total: 0, byStrategy: {} });
  const [isDeploying, setIsDeploying] = useState(false);

  // 🔴 STEP 6: Strategy failure detection
  const INACTIVITY_THRESHOLD = 60000; // 60 seconds

  // 🔴 STEP 8: Check for strategy inactivity
  useEffect(() => {
    const checkInterval = setInterval(() => {
      const now = Date.now();
      const warnings = [];

      strategies.forEach(strategy => {
        if (strategy.status === 'RUNNING' && strategy.lastSignalTime) {
          const timeSinceLastSignal = now - strategy.lastSignalTime;

          if (timeSinceLastSignal > INACTIVITY_THRESHOLD * 3) {
            // Critical - 3x threshold
            warnings.push({
              strategyId: strategy.id,
              severity: 'CRITICAL',
              message: `Strategy ${strategy.name} has not generated signals for ${Math.round(timeSinceLastSignal / 60000)} minutes`
            });
          } else if (timeSinceLastSignal > INACTIVITY_THRESHOLD * 2) {
            // Warning - 2x threshold
            warnings.push({
              strategyId: strategy.id,
              severity: 'WARNING',
              message: `Strategy ${strategy.name} inactive for ${Math.round(timeSinceLastSignal / 60000)} minutes`
            });
          }
        }
      });

      setInactivityWarnings(warnings);
    }, 10000); // Check every 10 seconds

    return () => clearInterval(checkInterval);
  }, [strategies]);

  // 🔴 STEP 8: Process WebSocket messages
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Strategy updates
        if (data.type === 'strategy_update' || data.type === 'strategy_status') {
          setStrategies(prev => {
            const existing = prev.find(s => s.id === data.strategy_id);

            if (existing) {
              return prev.map(s =>
                s.id === data.strategy_id
                  ? { ...s, ...data, lastUpdate: Date.now() }
                  : s
              );
            } else {
              return [...prev, {
                id: data.strategy_id,
                name: data.strategy_name || data.strategy_id,
                status: data.status || 'PENDING',
                pnl: data.pnl_contribution || 0,
                positions: data.active_positions || 0,
                lastSignalTime: data.last_signal_time,
                lastUpdate: Date.now(),
                ...data
              }];
            }
          });
        }

        // PnL updates
        if (data.type === 'strategy_pnl' && data.strategy_id) {
          setGlobalPnL(prev => {
            const byStrategy = { ...prev.byStrategy };
            byStrategy[data.strategy_id] = data.pnl || 0;

            return {
              total: Object.values(byStrategy).reduce((a, b) => a + b, 0),
              byStrategy
            };
          });
        }

      } catch (err) {
        console.error('[STEP 7/8] Failed to process message:', err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribeStrategy = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeStrategy = wsClient.subscribe('strategy_status', handleMessage);
    } else {
      console.warn('[StrategyControlPanel] wsClient.subscribe is not available');
    }

    return () => {
      if (unsubscribeStrategy) unsubscribeStrategy();
    };
  }, [wsClient]);

  // 🔴 STEP 7: Deploy strategy
  const handleDeploy = async (strategyId) => {
    setConfirmDialog({
      title: 'Deploy Strategy',
      message: `Deploy strategy ${strategyId} to live execution? This will start generating signals and orders.`,
      confirmText: 'DEPLOY',
      onConfirm: async () => {
        setConfirmDialog(null);
        setIsDeploying(true);
        try {
          await onDeployStrategy?.(strategyId);
        } catch (err) {
          Sentry.captureException(err, {
            tags: { type: "bot_deployment_error", strategyId },
            extra: { action: "deploy" }
          });
          throw err;
        } finally {
          setIsDeploying(false);
        }
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  // 🔴 STEP 7: Stop strategy
  const handleStop = (strategyId) => {
    setConfirmDialog({
      title: 'Stop Strategy',
      message: `Stop strategy ${strategyId}? This will halt all signal generation and close pending orders.`,
      confirmText: 'STOP',
      danger: true,
      onConfirm: async () => {
        setConfirmDialog(null);
        try {
          await onStopStrategy?.(strategyId);
        } catch (err) {
          Sentry.captureException(err, {
            tags: { type: "bot_stop_error", strategyId }
          });
          throw err;
        }
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  // 🔴 STEP 7: Pause strategy
  const handlePause = (strategyId) => {
    setConfirmDialog({
      title: 'Pause Strategy',
      message: `Pause strategy ${strategyId}? The strategy will stop generating new signals but maintain existing positions.`,
      confirmText: 'PAUSE',
      onConfirm: async () => {
        setConfirmDialog(null);
        try {
          await onPauseStrategy?.(strategyId);
        } catch (err) {
          Sentry.captureException(err, {
            tags: { type: "bot_pause_error", strategyId }
          });
          throw err;
        }
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  // 🔴 STEP 7: Close all positions
  const handleCloseAll = () => {
    const totalPositions = strategies.reduce((sum, s) => sum + (s.positions || 0), 0);

    setConfirmDialog({
      title: 'Close All Positions',
      message: `Close all ${totalPositions} open positions across ${strategies.length} strategies? This action cannot be undone.`,
      confirmText: 'CLOSE ALL',
      danger: true,
      onConfirm: async () => {
        setConfirmDialog(null);
        try {
          await onCloseAllPositions?.();
        } catch (err) {
          Sentry.captureException(err, {
            tags: { type: "bot_close_all_error" }
          });
          throw err;
        }
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  // 🔴 STEP 7: Emergency halt
  const handleEmergencyHalt = () => {
    setConfirmDialog({
      title: 'EMERGENCY HALT',
      message: 'HALT ALL TRADING IMMEDIATELY? This will:\n\n• Stop all strategies\n• Cancel all pending orders\n• Close all positions\n\nThis is an emergency safety measure.',
      confirmText: 'HALT NOW',
      danger: true,
      onConfirm: async () => {
        setConfirmDialog(null);
        try {
          await onEmergencyHalt?.();
        } catch (err) {
          Sentry.captureException(err, {
            tags: { type: "bot_emergency_halt_error" }
          });
          throw err;
        }
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  // 🔴 STEP 8: Toggle strategy selection
  const toggleSelection = (strategyId) => {
    setSelectedStrategies(prev =>
      prev.includes(strategyId)
        ? prev.filter(id => id !== strategyId)
        : [...prev, strategyId]
    );
  };

  // 🔴 STEP 8: Bulk stop
  const handleBulkStop = () => {
    if (selectedStrategies.length === 0) return;

    setConfirmDialog({
      title: 'Bulk Stop Strategies',
      message: `Stop ${selectedStrategies.length} selected strategies?`,
      confirmText: 'STOP ALL',
      danger: true,
      onConfirm: async () => {
        setConfirmDialog(null);
        for (const strategyId of selectedStrategies) {
          await onStopStrategy?.(strategyId);
        }
        setSelectedStrategies([]);
      },
      onCancel: () => setConfirmDialog(null)
    });
  };

  return (
    <div style={{
      backgroundColor: '#0d1117',
      border: '1px solid #30363d',
      borderRadius: '12px',
      padding: '16px',
      fontFamily: 'monospace'
    }}>
      {/* 🔴 Confirm Dialog */}
      <ConfirmDialog {...confirmDialog} />

      {/* 🔴 Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '16px',
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
            STRATEGY CONTROL CENTER
          </div>
          <div style={{
            color: '#8b949e',
            fontSize: '10px',
            marginTop: '4px'
          }}>
            {strategies.length} strategies | {strategies.filter(s => s.status === 'RUNNING').length} active
          </div>
        </div>

        {/* Global PnL */}
        <div style={{ textAlign: 'right' }}>
          <div style={{ color: '#8b949e', fontSize: '9px', textTransform: 'uppercase' }}>
            Total PnL
          </div>
          <div style={{
            color: globalPnL.total >= 0 ? '#00C853' : '#FF5252',
            fontSize: '18px',
            fontWeight: 900
          }}>
            {globalPnL.total >= 0 ? '+' : ''}${globalPnL.total.toFixed(2)}
          </div>
        </div>
      </div>

      {/* 🔴 Inactivity Warnings */}
      {inactivityWarnings.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          {inactivityWarnings.map(warning => (
            <div
              key={warning.strategyId}
              style={{
                backgroundColor: warning.severity === 'CRITICAL' ? '#FF174415' : '#FFAB0015',
                border: `1px solid ${warning.severity === 'CRITICAL' ? '#FF1744' : '#FFAB00'}`,
                borderRadius: '6px',
                padding: '10px 12px',
                marginBottom: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: '10px'
              }}
            >
              <span style={{ fontSize: '16px' }}>
                {warning.severity === 'CRITICAL' ? '🚨' : '⚠️'}
              </span>
              <span style={{
                color: warning.severity === 'CRITICAL' ? '#FF1744' : '#FFAB00',
                fontSize: '11px'
              }}>
                {warning.message}
              </span>
              <button
                onClick={() => handleStop(warning.strategyId)}
                style={{
                  marginLeft: 'auto',
                  padding: '4px 10px',
                  backgroundColor: '#FF525220',
                  border: '1px solid #FF5252',
                  borderRadius: '4px',
                  color: '#FF5252',
                  fontSize: '9px',
                  fontWeight: 700,
                  cursor: 'pointer'
                }}
              >
                STOP
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 🔴 Emergency Controls */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: '12px',
        marginBottom: '20px'
      }}>
        <button
          onClick={handleEmergencyHalt}
          style={{
            padding: '16px',
            backgroundColor: '#FF174420',
            border: '2px solid #FF1744',
            borderRadius: '8px',
            color: '#FF1744',
            fontSize: '14px',
            fontWeight: 900,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'all 0.2s'
          }}
          onMouseEnter={(e) => {
            e.target.style.backgroundColor = '#FF174430';
            e.target.style.transform = 'scale(1.02)';
          }}
          onMouseLeave={(e) => {
            e.target.style.backgroundColor = '#FF174420';
            e.target.style.transform = 'scale(1)';
          }}
        >
          🛑 EMERGENCY HALT
        </button>

        <button
          onClick={handleCloseAll}
          style={{
            padding: '16px',
            backgroundColor: '#FF525220',
            border: '2px solid #FF5252',
            borderRadius: '8px',
            color: '#FF5252',
            fontSize: '14px',
            fontWeight: 900,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'all 0.2s'
          }}
          onMouseEnter={(e) => {
            e.target.style.backgroundColor = '#FF525230';
            e.target.style.transform = 'scale(1.02)';
          }}
          onMouseLeave={(e) => {
            e.target.style.backgroundColor = '#FF525220';
            e.target.style.transform = 'scale(1)';
          }}
        >
          📤 CLOSE ALL POSITIONS
        </button>
      </div>

      {/* 🔴 Bulk Actions */}
      {selectedStrategies.length > 0 && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          marginBottom: '16px',
          padding: '12px',
          backgroundColor: '#161b22',
          borderRadius: '8px'
        }}>
          <span style={{ color: '#c9d1d9', fontSize: '12px' }}>
            {selectedStrategies.length} selected
          </span>
          <button
            onClick={handleBulkStop}
            style={{
              padding: '6px 12px',
              backgroundColor: '#FF525220',
              border: '1px solid #FF5252',
              borderRadius: '4px',
              color: '#FF5252',
              fontSize: '11px',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            Stop Selected
          </button>
          <button
            onClick={() => setSelectedStrategies([])}
            style={{
              padding: '6px 12px',
              backgroundColor: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '4px',
              color: '#8b949e',
              fontSize: '11px',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            Clear
          </button>
        </div>
      )}

      {/* 🔴 Strategy List */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        maxHeight: '400px',
        overflowY: 'auto'
      }}>
        {strategies.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '40px',
            color: '#8b949e',
            fontSize: '11px'
          }}>
            No strategies deployed. Deploy a strategy to begin monitoring.
          </div>
        ) : (
          strategies.map(strategy => {
            const isSelected = selectedStrategies.includes(strategy.id);
            const pnl = globalPnL.byStrategy[strategy.id] || strategy.pnl || 0;

            return (
              <div
                key={strategy.id}
                style={{
                  backgroundColor: isSelected ? '#58a6ff15' : '#161b22',
                  border: `1px solid ${isSelected ? '#58a6ff' : '#30363d'}`,
                  borderRadius: '8px',
                  padding: '12px'
                }}
              >
                {/* Strategy Header */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  marginBottom: '10px'
                }}>
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleSelection(strategy.id)}
                    style={{ cursor: 'pointer' }}
                  />

                  {/* Status Indicator */}
                  <div style={{
                    width: '10px',
                    height: '10px',
                    borderRadius: '50%',
                    backgroundColor: strategy.status === 'RUNNING' ? '#00C853' :
                      strategy.status === 'ERROR' ? '#FF5252' :
                        strategy.status === 'PAUSED' ? '#FFAB00' : '#9E9E9E',
                    boxShadow: strategy.status === 'RUNNING' ? '0 0 8px #00C853' : 'none'
                  }} />

                  <span style={{
                    color: '#ffffff',
                    fontSize: '13px',
                    fontWeight: 700,
                    flex: 1
                  }}>
                    {strategy.name}
                  </span>

                  {/* PnL */}
                  <span style={{
                    color: pnl >= 0 ? '#00C853' : '#FF5252',
                    fontSize: '13px',
                    fontWeight: 900
                  }}>
                    {pnl >= 0 ? '+' : ''}${Math.abs(pnl).toFixed(2)}
                  </span>
                </div>

                {/* Strategy Info */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: '8px',
                  fontSize: '10px',
                  marginBottom: '10px',
                  padding: '8px',
                  backgroundColor: '#0d1117',
                  borderRadius: '6px'
                }}>
                  <div>
                    <div style={{ color: '#8b949e' }}>Status</div>
                    <div style={{
                      color: strategy.status === 'RUNNING' ? '#00C853' :
                        strategy.status === 'ERROR' ? '#FF5252' :
                          strategy.status === 'PAUSED' ? '#FFAB00' : '#9E9E9E',
                      fontWeight: 700
                    }}>
                      {strategy.status}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: '#8b949e' }}>Positions</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {strategy.positions || 0}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: '#8b949e' }}>Signals/min</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {strategy.signalsPerMinute?.toFixed(1) || '0.0'}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: '#8b949e' }}>Symbol</div>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>
                      {strategy.symbol || '-'}
                    </div>
                  </div>
                </div>

                {/* Control Buttons */}
                <div style={{
                  display: 'flex',
                  gap: '8px'
                }}>
                  {strategy.status !== 'RUNNING' ? (
                    <button
                      onClick={() => handleDeploy(strategy.id)}
                      disabled={isDeploying}
                      style={{
                        flex: 1,
                        padding: '8px',
                        backgroundColor: '#00C85320',
                        border: '1px solid #00C853',
                        borderRadius: '4px',
                        color: '#00C853',
                        fontSize: '11px',
                        fontWeight: 700,
                        cursor: isDeploying ? 'not-allowed' : 'pointer',
                        opacity: isDeploying ? 0.5 : 1
                      }}
                    >
                      {isDeploying ? '⏳' : '▶'} DEPLOY
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={() => handlePause(strategy.id)}
                        style={{
                          flex: 1,
                          padding: '8px',
                          backgroundColor: '#FFAB0020',
                          border: '1px solid #FFAB00',
                          borderRadius: '4px',
                          color: '#FFAB00',
                          fontSize: '11px',
                          fontWeight: 700,
                          cursor: 'pointer'
                        }}
                      >
                        ⏸ PAUSE
                      </button>
                      <button
                        onClick={() => handleStop(strategy.id)}
                        style={{
                          flex: 1,
                          padding: '8px',
                          backgroundColor: '#FF525220',
                          border: '1px solid #FF5252',
                          borderRadius: '4px',
                          color: '#FF5252',
                          fontSize: '11px',
                          fontWeight: 700,
                          cursor: 'pointer'
                        }}
                      >
                        ⏹ STOP
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

// 🔴 STEP 7: Hook for strategy control
export const useStrategyControl = () => {
  const [activeStrategies, setActiveStrategies] = useState([]);
  const [isHalted, setIsHalted] = useState(false);

  const deployStrategy = useCallback(async (strategyId) => {
    // API call to deploy
    console.log(`[STEP 7] Deploying strategy: ${strategyId}`);
    // Implementation would call api.strategies.deploy()
  }, []);

  const stopStrategy = useCallback(async (strategyId) => {
    console.log(`[STEP 7] Stopping strategy: ${strategyId}`);
    // Implementation would call api.strategies.stop()
  }, []);

  const pauseStrategy = useCallback(async (strategyId) => {
    console.log(`[STEP 7] Pausing strategy: ${strategyId}`);
    // Implementation would call api.strategies.pause()
  }, []);

  const emergencyHalt = useCallback(async () => {
    console.log('[STEP 7] EMERGENCY HALT triggered');
    setIsHalted(true);
    // Implementation would halt all trading
  }, []);

  return {
    activeStrategies,
    isHalted,
    deployStrategy,
    stopStrategy,
    pauseStrategy,
    emergencyHalt
  };
};

export default StrategyControlPanel;
export { ConfirmDialog };
