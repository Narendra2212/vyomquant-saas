import React, { useState, useEffect, useRef, useCallback } from 'react';
import WS_CHANNELS from '../constants/wsChannels';
import { normalizeTelemetryEvent } from '../websocketClient';

/**
 * 🔴 STEP 8 — LIVE RISK ALERTS
 * 
 * Real-time risk monitoring with toast notifications
 * 
 * Monitors:
 * - High exposure (>30%)
 * - High leverage (>3x)
 * - Rapid trading (>1 trade/min)
 * - Drawdown breach (>10%)
 */

// Alert thresholds
const ALERT_THRESHOLDS = {
  EXPOSURE: 0.30,        // 30%
  LEVERAGE: 3,           // 3x
  TRADES_PER_MIN: 1,     // 1 trade per minute
  DRAWDOWN_WARNING: 0.10, // 10%
  DRAWDOWN_CRITICAL: 0.30 // 30%
};

// Deduplication window (ms)
const ALERT_COOLDOWN = 30000; // 30 seconds

const LiveRiskAlerts = ({ 
  wsClient, 
  accountId,
  onAlert,
  maxTradesPerMinute = 1
}) => {
  // Track alert timestamps for deduplication
  const lastAlertTime = useRef({});
  const tradeHistory = useRef([]);
  
  // Alert state
  const [activeAlerts, setActiveAlerts] = useState([]);

  // 🔴 STEP 8: Check if we should show alert (deduplication)
  const shouldShowAlert = useCallback((alertType) => {
    const now = Date.now();
    const lastTime = lastAlertTime.current[alertType] || 0;
    
    if (now - lastTime > ALERT_COOLDOWN) {
      lastAlertTime.current[alertType] = now;
      return true;
    }
    return false;
  }, []);

  // 🔴 STEP 8: Add alert to active list
  const addAlert = useCallback((alert) => {
    const id = `${alert.type}_${Date.now()}`;
    const newAlert = { ...alert, id, timestamp: Date.now() };
    
    setActiveAlerts(prev => [...prev, newAlert]);
    
    // Notify parent
    if (onAlert) {
      onAlert(newAlert);
    }
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
      setActiveAlerts(prev => prev.filter(a => a.id !== id));
    }, 5000);
    
    return newAlert;
  }, [onAlert]);

  // 🔴 STEP 8: Check rapid trading
  const checkRapidTrading = useCallback(() => {
    const now = Date.now();
    const oneMinuteAgo = now - 60000;
    
    // Clean old trades
    tradeHistory.current = tradeHistory.current.filter(
      time => time > oneMinuteAgo
    );
    
    // Check if too many trades
    if (tradeHistory.current.length >= maxTradesPerMinute) {
      if (shouldShowAlert('rapid_trading')) {
        const alert = addAlert({
          type: 'rapid_trading',
          severity: 'warning',
          title: '⚡ RAPID TRADING DETECTED',
          message: `${tradeHistory.current.length} trades in last minute. Slow down to avoid losses.`,
          color: '#FF9800'
        });
        console.warn(`[STEP 8] ${alert.title}: ${alert.message}`);
      }
    }
  }, [maxTradesPerMinute, shouldShowAlert, addAlert]);

  // 🔴 STEP 8: Record trade for rapid trading detection
  const recordTrade = useCallback(() => {
    tradeHistory.current.push(Date.now());
    checkRapidTrading();
  }, [checkRapidTrading]);

  // 🔴 STEP 8: Monitor WebSocket for risk events
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (rawData) => {
      try {
        // If data is already parsed, use it. If it has .data, it might be a raw event
        const parsedData = (rawData && rawData.data) ? JSON.parse(rawData.data) : rawData;
        const data = normalizeTelemetryEvent(parsedData);

        // 🔴 HIGH EXPOSURE ALERT
        if (data.type === 'exposure_alert' || data.exposure_percent > ALERT_THRESHOLDS.EXPOSURE) {
          if (shouldShowAlert('high_exposure')) {
            const exposure = data.exposure_percent || data.exposure || 0;
            const alert = addAlert({
              type: 'high_exposure',
              severity: 'warning',
              title: '⚠️ HIGH EXPOSURE WARNING',
              message: `Portfolio exposure ${(exposure * 100).toFixed(1)}% exceeds 30% threshold`,
              color: '#FF9800'
            });
            console.warn(`[STEP 8] ${alert.title}: ${alert.message}`);
          }
        }

        // 🔴 HIGH LEVERAGE ALERT
        if (data.type === 'leverage_alert' || data.leverage > ALERT_THRESHOLDS.LEVERAGE) {
          if (shouldShowAlert('high_leverage')) {
            const leverage = data.leverage || 0;
            const alert = addAlert({
              type: 'high_leverage',
              severity: 'warning',
              title: '⚠️ HIGH LEVERAGE WARNING',
              message: `Current leverage ${leverage.toFixed(1)}x exceeds 3x threshold`,
              color: '#FF9800'
            });
            console.warn(`[STEP 8] ${alert.title}: ${alert.message}`);
          }
        }

        // 🔴 DRAWDOWN BREACH ALERT
        if (data.type === 'drawdown_alert' || data.drawdown > ALERT_THRESHOLDS.DRAWDOWN_WARNING) {
          const drawdown = data.drawdown || 0;
          const isCritical = drawdown > ALERT_THRESHOLDS.DRAWDOWN_CRITICAL;
          
          if (shouldShowAlert(isCritical ? 'drawdown_critical' : 'drawdown_warning')) {
            const alert = addAlert({
              type: isCritical ? 'drawdown_critical' : 'drawdown_warning',
              severity: isCritical ? 'critical' : 'warning',
              title: isCritical ? '🚨 DRAWDOWN CRITICAL' : '⚠️ DRAWDOWN WARNING',
              message: `Account drawdown ${(drawdown * 100).toFixed(1)}% ${isCritical ? '- TRADING BLOCKED' : '- Consider reducing positions'}`,
              color: isCritical ? '#FF5252' : '#FF9800'
            });
            console.error(`[STEP 8] ${alert.title}: ${alert.message}`);
          }
        }

        // 🔴 TRADE EXECUTION (for rapid trading detection)
        if (data.type === 'order_update' && data.status === 'filled') {
          recordTrade();
        }

        // 🔴 POSITION UPDATE (check leverage)
        if (data.type === 'positions_update' && data.positions) {
          data.positions.forEach(position => {
            if (position.leverage > ALERT_THRESHOLDS.LEVERAGE) {
              if (shouldShowAlert(`leverage_${position.symbol}`)) {
                const alert = addAlert({
                  type: 'high_leverage',
                  severity: 'warning',
                  title: '⚠️ HIGH LEVERAGE POSITION',
                  message: `${position.symbol}: ${position.leverage}x leverage exceeds safe threshold`,
                  color: '#FF9800'
                });
                console.warn(`[STEP 8] ${alert.title}: ${alert.message}`);
              }
            }
          });
        }

      } catch (err) {
        console.error('[STEP 8] Failed to process WebSocket message:', err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribeRisk = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeRisk = wsClient.subscribe(WS_CHANNELS.RISK_EVENTS, handleMessage);
    } else {
      console.warn('[LiveRiskAlerts] wsClient.subscribe is not available');
    }
    
    return () => {
      if (unsubscribeRisk) unsubscribeRisk();
    };
  }, [wsClient, shouldShowAlert, addAlert, recordTrade]);

  // 🔴 STEP 8: Periodic rapid trading check
  useEffect(() => {
    const interval = setInterval(checkRapidTrading, 10000); // Check every 10 seconds
    return () => clearInterval(interval);
  }, [checkRapidTrading]);

  // Don't render if no active alerts
  if (activeAlerts.length === 0) return null;

  return (
    <div style={{
      position: 'fixed',
      top: '80px',
      right: '16px',
      zIndex: 9998,
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      maxWidth: '400px'
    }}>
      {activeAlerts.map(alert => (
        <div
          key={alert.id}
          style={{
            backgroundColor: `${alert.color}15`,
            border: `1px solid ${alert.color}50`,
            borderRadius: '8px',
            padding: '12px 16px',
            boxShadow: `0 4px 20px ${alert.color}30`,
            animation: 'slideInRight 0.3s ease-out',
            backdropFilter: 'blur(4px)'
          }}
        >
          <div style={{
            fontFamily: 'monospace',
            fontSize: '11px',
            fontWeight: 900,
            color: alert.color,
            marginBottom: '4px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}>
            {alert.title}
          </div>
          <div style={{
            fontFamily: 'monospace',
            fontSize: '10px',
            color: '#ffffff',
            opacity: 0.9
          }}>
            {alert.message}
          </div>
          
          {/* Progress bar for auto-dismiss */}
          <div style={{
            marginTop: '8px',
            height: '2px',
            backgroundColor: `${alert.color}30`,
            borderRadius: '1px',
            overflow: 'hidden'
          }}>
            <div style={{
              height: '100%',
              width: '100%',
              backgroundColor: alert.color,
              animation: 'shrink 5s linear forwards'
            }} />
          </div>
        </div>
      ))}

      <style>{`
        @keyframes slideInRight {
          from {
            transform: translateX(100%);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        
        @keyframes shrink {
          from {
            width: 100%;
          }
          to {
            width: 0%;
          }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 8: Hook for using live risk alerts
export const useLiveRiskAlerts = () => {
  const [alerts, setAlerts] = useState([]);
  
  const addAlert = useCallback((alert) => {
    const id = Date.now().toString();
    const newAlert = { ...alert, id };
    setAlerts(prev => [...prev, newAlert]);
    
    setTimeout(() => {
      setAlerts(prev => prev.filter(a => a.id !== id));
    }, 5000);
    
    return newAlert;
  }, []);
  
  const clearAlerts = useCallback(() => {
    setAlerts([]);
  }, []);
  
  return { alerts, addAlert, clearAlerts };
};

export default LiveRiskAlerts;
export { ALERT_THRESHOLDS };
