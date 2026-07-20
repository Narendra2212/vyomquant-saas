import React, { useState, useEffect, useCallback, useRef } from 'react';

/**
 * 🔴 STEP 5 — LATENCY MONITOR
 * 
 * Tracks infrastructure performance metrics
 * 
 * Monitors:
 * - API latency (REST calls)
 * - WebSocket delay (message round-trip)
 * - Order execution time
 * - Database query time
 * 
 * Alerts on high latency
 */

// Latency thresholds (ms)
const THRESHOLDS = {
  API: { warning: 500, critical: 1000 },
  WEBSOCKET: { warning: 100, critical: 300 },
  EXECUTION: { warning: 500, critical: 1000 },
  DATABASE: { warning: 200, critical: 500 }
};

const LatencyMonitor = ({
  wsClient,
  apiClient,
  showAlerts = true
}) => {
  const [metrics, setMetrics] = useState({
    api: { current: 0, avg: 0, max: 0, min: 999999 },
    websocket: { current: 0, avg: 0, max: 0, min: 999999 },
    execution: { current: 0, avg: 0, max: 0, min: 999999 },
    database: { current: 0, avg: 0, max: 0, min: 999999 }
  });
  
  const [alerts, setAlerts] = useState([]);
  const [history, setHistory] = useState([]);
  const wsPingRef = useRef(Date.now());
  const historyRef = useRef([]);

  // 🔴 STEP 5: Record latency measurement
  const recordLatency = useCallback((type, latency) => {
    setMetrics(prev => {
      const current = prev[type];
      const values = [...historyRef.current.filter(h => h.type === type).map(h => h.value), latency];
      const avg = values.length > 0 ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : latency;
      
      return {
        ...prev,
        [type]: {
          current: latency,
          avg,
          max: Math.max(current.max, latency),
          min: Math.min(current.min, latency)
        }
      };
    });

    // Add to history
    historyRef.current = [{ type, value: latency, timestamp: Date.now() }, ...historyRef.current].slice(0, 100);
    setHistory(historyRef.current);

    // Check thresholds
    const threshold = THRESHOLDS[type.toUpperCase()];
    if (threshold && showAlerts) {
      if (latency > threshold.critical) {
        addAlert(type, 'CRITICAL', `Critical ${type} latency: ${latency}ms`);
      } else if (latency > threshold.warning) {
        addAlert(type, 'WARNING', `High ${type} latency: ${latency}ms`);
      }
    }
  }, [showAlerts]);

  // 🔴 STEP 5: Add alert
  const addAlert = useCallback((type, severity, message) => {
    const alert = {
      id: Date.now(),
      type,
      severity,
      message,
      timestamp: Date.now()
    };
    
    setAlerts(prev => [alert, ...prev].slice(0, 10));
    
    // Auto-remove after 10 seconds
    setTimeout(() => {
      setAlerts(prev => prev.filter(a => a.id !== alert.id));
    }, 10000);
  }, []);

  // 🔴 STEP 5: Monitor API latency
  useEffect(() => {
    if (!apiClient) return;

    const originalRequest = apiClient.interceptors?.request?.use;
    const originalResponse = apiClient.interceptors?.response?.use;
    
    // Track request start times
    const requestTimes = new Map();

    // Request interceptor
    const requestInterceptor = (config) => {
      config.metadata = { startTime: Date.now() };
      return config;
    };

    // Response interceptor
    const responseInterceptor = (response) => {
      if (response.config?.metadata?.startTime) {
        const latency = Date.now() - response.config.metadata.startTime;
        recordLatency('api', latency);
      }
      return response;
    };

    // Add interceptors if available
    if (apiClient.interceptors) {
      const reqId = apiClient.interceptors.request.use(requestInterceptor);
      const resId = apiClient.interceptors.response.use(responseInterceptor, (error) => {
        if (error.config?.metadata?.startTime) {
          const latency = Date.now() - error.config.metadata.startTime;
          recordLatency('api', latency);
        }
        return Promise.reject(error);
      });

      return () => {
        apiClient.interceptors.request.eject(reqId);
        apiClient.interceptors.response.eject(resId);
      };
    }
  }, [apiClient, recordLatency]);

  // 🔴 STEP 5: Monitor WebSocket latency
  useEffect(() => {
    if (!wsClient) return;

    // Send periodic ping to measure latency
    const pingInterval = setInterval(() => {
      wsPingRef.current = Date.now();
      if (wsClient.readyState === WebSocket.OPEN) {
        wsClient.send(JSON.stringify({ type: 'ping', timestamp: wsPingRef.current }));
      }
    }, 5000);

    // Handle pong response
    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'pong' && data.timestamp) {
          const latency = Date.now() - data.timestamp;
          recordLatency('websocket', latency);
        }
      } catch (err) {
        // Not a ping/pong message
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribeExecution = null;
    let unsubscribeDbLatency = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeExecution = wsClient.subscribe('order_filled', handleMessage);
      unsubscribeDbLatency = wsClient.subscribe('db_latency', handleMessage);
    } else {
      console.warn('[LatencyMonitor] wsClient.subscribe is not available');
    }

    return () => {
      clearInterval(pingInterval);
      if (unsubscribeExecution) unsubscribeExecution();
      if (unsubscribeDbLatency) unsubscribeDbLatency();
    };
  }, [wsClient, recordLatency]);

  // 🔴 STEP 5: Monitor execution latency from WebSocket
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        // Execution latency from order lifecycle
        if (data.type === 'order_filled' && data.execution_time_ms) {
          recordLatency('execution', data.execution_time_ms);
        }

        // Database latency from backend
        if (data.type === 'db_latency' && data.latency_ms) {
          recordLatency('database', data.latency_ms);
        }

      } catch (err) {
        // Ignore parse errors
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribe = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribe = wsClient.subscribe('pong', handleMessage);
    }
    
    return () => {
      if (unsubscribe) unsubscribe();
    };
  }, [wsClient, recordLatency]);

  // 🔴 STEP 5: Format latency value
  const formatLatency = (ms) => {
    if (ms === 0 || ms === 999999) return '-';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  // 🔴 STEP 5: Get status color
  const getStatusColor = (type, value) => {
    const threshold = THRESHOLDS[type.toUpperCase()];
    if (!threshold) return '#9E9E9E';
    if (value > threshold.critical) return '#FF1744';
    if (value > threshold.warning) return '#FFAB00';
    return '#00C853';
  };

  // 🔴 STEP 5: Get sparkline data
  const getSparkline = (type) => {
    return history
      .filter(h => h.type === type)
      .slice(0, 20)
      .reverse()
      .map(h => h.value);
  };

  return (
    <div style={{
      backgroundColor: '#0d1117',
      border: '1px solid #30363d',
      borderRadius: '12px',
      padding: '16px',
      fontFamily: 'monospace'
    }}>
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
            LATENCY MONITOR
          </div>
          <div style={{
            color: '#8b949e',
            fontSize: '10px',
            marginTop: '4px'
          }}>
            Infrastructure performance metrics
          </div>
        </div>

        {/* Alerts */}
        {alerts.length > 0 && (
          <div style={{
            backgroundColor: alerts[0].severity === 'CRITICAL' ? '#FF174420' : '#FFAB0020',
            border: `1px solid ${alerts[0].severity === 'CRITICAL' ? '#FF1744' : '#FFAB00'}`,
            borderRadius: '6px',
            padding: '6px 12px',
            color: alerts[0].severity === 'CRITICAL' ? '#FF1744' : '#FFAB00',
            fontSize: '10px',
            fontWeight: 700,
            animation: 'pulse 1s infinite'
          }}>
            ⚠️ {alerts.length} active alerts
          </div>
        )}
      </div>

      {/* 🔴 Metrics Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(2, 1fr)',
        gap: '12px'
      }}>
        {[
          { key: 'api', label: 'API Latency', icon: '🌐' },
          { key: 'websocket', label: 'WebSocket', icon: '⚡' },
          { key: 'execution', label: 'Execution', icon: '🚀' },
          { key: 'database', label: 'Database', icon: '💾' }
        ].map(({ key, label, icon }) => {
          const metric = metrics[key];
          const color = getStatusColor(key, metric.current);
          const sparkline = getSparkline(key);
          
          return (
            <div
              key={key}
              style={{
                backgroundColor: '#161b22',
                border: `1px solid ${metric.current > (THRESHOLDS[key.toUpperCase()]?.warning || 500) ? color + '40' : '#30363d'}`,
                borderRadius: '8px',
                padding: '12px'
              }}
            >
              {/* Header */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                marginBottom: '8px'
              }}>
                <span style={{ fontSize: '14px' }}>{icon}</span>
                <span style={{
                  color: '#8b949e',
                  fontSize: '10px',
                  fontWeight: 700,
                  textTransform: 'uppercase'
                }}>
                  {label}
                </span>
              </div>

              {/* Current Value */}
              <div style={{
                color: color,
                fontSize: '24px',
                fontWeight: 900,
                fontFamily: 'monospace'
              }}>
                {formatLatency(metric.current)}
              </div>

              {/* Sparkline */}
              {sparkline.length > 1 && (
                <svg
                  width="100%"
                  height="20"
                  style={{ marginTop: '8px' }}
                >
                  <path
                    d={sparkline.map((v, i) => {
                      const max = Math.max(...sparkline, 100);
                      const x = (i / (sparkline.length - 1)) * 100;
                      const y = 20 - (v / max) * 18;
                      return `${i === 0 ? 'M' : 'L'}${x},${y}`;
                    }).join(' ')}
                    fill="none"
                    stroke={color}
                    strokeWidth="2"
                    opacity="0.7"
                  />
                </svg>
              )}

              {/* Stats */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '8px',
                marginTop: '8px',
                paddingTop: '8px',
                borderTop: '1px solid #30363d',
                fontSize: '9px'
              }}>
                <div>
                  <div style={{ color: '#8b949e' }}>Avg</div>
                  <div style={{ color: '#c9d1d9', fontWeight: 700 }}>
                    {formatLatency(metric.avg)}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#8b949e' }}>Max</div>
                  <div style={{ color: '#FF5252', fontWeight: 700 }}>
                    {formatLatency(metric.max)}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#8b949e' }}>Min</div>
                  <div style={{ color: '#00C853', fontWeight: 700 }}>
                    {formatLatency(metric.min)}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 🔴 Alert List */}
      {alerts.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <div style={{
            color: '#8b949e',
            fontSize: '10px',
            fontWeight: 700,
            marginBottom: '8px',
            textTransform: 'uppercase'
          }}>
            Recent Alerts
          </div>
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '4px'
          }}>
            {alerts.map(alert => (
              <div
                key={alert.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '6px 10px',
                  backgroundColor: alert.severity === 'CRITICAL' ? '#FF174415' : '#FFAB0015',
                  border: `1px solid ${alert.severity === 'CRITICAL' ? '#FF174440' : '#FFAB0040'}`,
                  borderRadius: '4px',
                  fontSize: '10px'
                }}
              >
                <span style={{
                  color: alert.severity === 'CRITICAL' ? '#FF1744' : '#FFAB00',
                  fontWeight: 900
                }}>
                  {alert.severity}
                </span>
                <span style={{ color: '#c9d1d9' }}>
                  {alert.message}
                </span>
                <span style={{ color: '#8b949e', marginLeft: 'auto' }}>
                  {new Date(alert.timestamp).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 5: Hook for latency tracking
export const useLatencyMonitor = () => {
  const [measurements, setMeasurements] = useState({});

  const recordMeasurement = useCallback((type, latency) => {
    setMeasurements(prev => ({
      ...prev,
      [type]: {
        current: latency,
        history: [...(prev[type]?.history || []), latency].slice(-50),
        timestamp: Date.now()
      }
    }));
  }, []);

  const getAverageLatency = useCallback((type) => {
    const history = measurements[type]?.history || [];
    if (history.length === 0) return 0;
    return Math.round(history.reduce((a, b) => a + b, 0) / history.length);
  }, [measurements]);

  return {
    measurements,
    recordMeasurement,
    getAverageLatency
  };
};

export default LatencyMonitor;
export { THRESHOLDS };
