import React, { useState, useEffect, useCallback } from 'react';
import { 
  Server, Database, Wifi, Globe, Layers,
  Cpu, HardDrive, Activity, AlertTriangle, 
  CheckCircle, XCircle, RefreshCw, Radio,
  Zap, AlertOctagon, Ban, TrendingUp,
  TrendingDown, Clock, Shield, Flame,
  PauseCircle, PlayCircle, RotateCcw
} from 'lucide-react';
import WS_CHANNELS from '../constants/wsChannels';

/**
 * 🔴 INFRASTRUCTURE OPERATIONS PANEL
 * 
 * DevOps/SRE observability dashboard for algo trading infrastructure
 * 
 * Health Monitoring:
 * - API Health
 * - Redis Health
 * - WebSocket Connections
 * - Exchange Latency
 * - Queue Backpressure
 * - Active Workers
 * - Event Loop Health
 * - Reconnect Events
 * - Memory Usage
 * - Failed Orders
 * 
 * Critical Alerts:
 * - Exchange disconnected
 * - WebSocket reconnect storm
 * - High latency
 * - Risk engine halted
 * - Kill switch active
 */

// ═══════════════════════════════════════════════════════════════════
//  CONFIGURATION
// ═══════════════════════════════════════════════════════════════════

const STATUS_COLORS = {
  HEALTHY: { color: '#00C853', bgColor: '#00C85315', borderColor: '#00C85340', label: 'Healthy' },
  DEGRADED: { color: '#FFAB00', bgColor: '#FFAB0015', borderColor: '#FFAB0040', label: 'Degraded' },
  FAILURE: { color: '#FF5252', bgColor: '#FF525215', borderColor: '#FF525240', label: 'Failure' },
  UNKNOWN: { color: '#9E9E9E', bgColor: '#9E9E9E15', borderColor: '#9E9E9E40', label: 'Unknown' }
};

const ALERT_TYPES = {
  EXCHANGE_DISCONNECTED: { 
    icon: Wifi, 
    color: '#FF5252', 
    bgColor: '#FF525220',
    label: 'Exchange Disconnected',
    severity: 'critical'
  },
  WEBSOCKET_STORM: { 
    icon: RefreshCw, 
    color: '#FF5722', 
    bgColor: '#FF572220',
    label: 'WebSocket Reconnect Storm',
    severity: 'critical'
  },
  HIGH_LATENCY: { 
    icon: Clock, 
    color: '#FFAB00', 
    bgColor: '#FFAB0020',
    label: 'High Latency Detected',
    severity: 'warning'
  },
  RISK_ENGINE_HALTED: { 
    icon: Shield, 
    color: '#9C27B0', 
    bgColor: '#9C27B020',
    label: 'Risk Engine Halted',
    severity: 'critical'
  },
  KILL_SWITCH_ACTIVE: { 
    icon: Ban, 
    color: '#FF5252', 
    bgColor: '#FF525230',
    label: 'Kill Switch Active',
    severity: 'critical'
  },
  MEMORY_PRESSURE: {
    icon: HardDrive,
    color: '#FFAB00',
    bgColor: '#FFAB0020',
    label: 'Memory Pressure',
    severity: 'warning'
  },
  QUEUE_BACKPRESSURE: {
    icon: Layers,
    color: '#FF5722',
    bgColor: '#FF572220',
    label: 'Queue Backpressure',
    severity: 'warning'
  }
};

// ═══════════════════════════════════════════════════════════════════
//  MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════

const InfrastructureOperations = ({ 
  wsClient,
  accountId,
  onAlertClick
}) => {
  const [health, setHealth] = useState({
    api: { status: 'HEALTHY', latency: 45, uptime: 99.9, lastCheck: Date.now() },
    redis: { status: 'HEALTHY', latency: 12, connections: 45, memory: 234 },
    websocket: { status: 'HEALTHY', connections: 1247, messagesPerSec: 8923 },
    eventLoop: { status: 'HEALTHY', lag: 2.4, ticksPerSec: 60 },
    memory: { status: 'HEALTHY', used: 45.2, total: 128, heapUsed: 234 }
  });

  const [metrics, setMetrics] = useState({
    exchangeLatency: {
      Binance: { latency: 45, status: 'HEALTHY' },
      Coinbase: { latency: 78, status: 'HEALTHY' },
      Kraken: { latency: 234, status: 'DEGRADED' },
      Bybit: { latency: 0, status: 'FAILURE' }
    },
    queueDepth: 12,
    queueCapacity: 1000,
    activeWorkers: 8,
    totalWorkers: 12,
    reconnectCount: 2,
    failedOrders: 3,
    totalOrders: 1456
  });

  const [alerts, setAlerts] = useState([
    { id: 1, type: 'EXCHANGE_DISCONNECTED', message: 'Bybit WebSocket disconnected', timestamp: Date.now() - 120000, acknowledged: false },
    { id: 2, type: 'HIGH_LATENCY', message: 'Kraken latency > 200ms', timestamp: Date.now() - 300000, acknowledged: false }
  ]);

  const [selectedMetric, setSelectedMetric] = useState(null);
  const [isPaused, setIsPaused] = useState(false);
  const [history, setHistory] = useState({ latency: [], memory: [], queue: [] });
  
  const historyBuffer = useRef({ latency: [], memory: [], queue: [] });

  // ═══════════════════════════════════════════════════════════════════
  //  WEBSOCKET INTEGRATION (Properly using wsClient.subscribe)
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    if (!wsClient) return;

    const handleHealthUpdate = (data) => {
      if (isPaused) return;

      // Update health metrics
      if (data.api) {
        setHealth(prev => ({ ...prev, api: { ...prev.api, ...data.api } }));
      }
      if (data.redis) {
        setHealth(prev => ({ ...prev, redis: { ...prev.redis, ...data.redis } }));
      }
      if (data.websocket) {
        setHealth(prev => ({ ...prev, websocket: { ...prev.websocket, ...data.websocket } }));
      }
      if (data.eventLoop) {
        setHealth(prev => ({ ...prev, eventLoop: { ...prev.eventLoop, ...data.eventLoop } }));
      }
      if (data.memory) {
        setHealth(prev => ({ ...prev, memory: { ...prev.memory, ...data.memory } }));
      }
    };

    const handleMetricsUpdate = (data) => {
      if (isPaused) return;

      if (data.exchangeLatency) {
        setMetrics(prev => ({ ...prev, exchangeLatency: { ...prev.exchangeLatency, ...data.exchangeLatency } }));
      }
      if (data.queueDepth !== undefined) {
        setMetrics(prev => ({ ...prev, queueDepth: data.queueDepth }));
      }
      if (data.activeWorkers !== undefined) {
        setMetrics(prev => ({ ...prev, activeWorkers: data.activeWorkers }));
      }
      if (data.reconnectCount !== undefined) {
        setMetrics(prev => ({ ...prev, reconnectCount: data.reconnectCount }));
      }
      if (data.failedOrders !== undefined) {
        setMetrics(prev => ({ ...prev, failedOrders: data.failedOrders }));
      }
    };

    const handleAlert = (data) => {
      const alert = {
        id: crypto.randomUUID(), // CSPRNG — Math.random() is not cryptographically secure
        type: data.type,
        message: data.message,
        timestamp: Date.now(),
        acknowledged: false
      };
      
      setAlerts(prev => [alert, ...prev].slice(0, 50)); // Keep last 50 alerts
    };

    // Subscribe using custom wsClient interface (NOT addEventListener)
    let unsubscribeBotStatus = null;
    let unsubscribeMetrics = null;
    let unsubscribeRisk = null;

    if (typeof wsClient.subscribe === 'function') {
      unsubscribeBotStatus = wsClient.subscribe(WS_CHANNELS.BOT_STATUS, handleHealthUpdate);
      unsubscribeMetrics = wsClient.subscribe(WS_CHANNELS.INFRASTRUCTURE, handleMetricsUpdate);
      unsubscribeRisk = wsClient.subscribe(WS_CHANNELS.RISK_EVENTS, handleAlert);
    } else {
      console.warn('[InfrastructureOperations] wsClient.subscribe is not available');
    }

    return () => {
      if (unsubscribeBotStatus) unsubscribeBotStatus();
      if (unsubscribeMetrics) unsubscribeMetrics();
      if (unsubscribeRisk) unsubscribeRisk();
    };
  }, [wsClient, isPaused]);

  // ═══════════════════════════════════════════════════════════════════
  //  HISTORY TRACKING
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    if (isPaused) return;

    const interval = setInterval(() => {
      // Update history buffers
      const avgLatency = Object.values(metrics.exchangeLatency)
        .filter(e => e.latency > 0)
        .reduce((acc, e) => acc + e.latency, 0) / 
        Object.values(metrics.exchangeLatency).filter(e => e.latency > 0).length || 0;

      historyBuffer.current.latency.push({
        timestamp: Date.now(),
        value: avgLatency
      });
      historyBuffer.current.latency = historyBuffer.current.latency.slice(-60);

      historyBuffer.current.memory.push({
        timestamp: Date.now(),
        value: health.memory.used
      });
      historyBuffer.current.memory = historyBuffer.current.memory.slice(-60);

      historyBuffer.current.queue.push({
        timestamp: Date.now(),
        value: metrics.queueDepth
      });
      historyBuffer.current.queue = historyBuffer.current.queue.slice(-60);

      setHistory({ ...historyBuffer.current });
    }, 5000);

    return () => clearInterval(interval);
  }, [isPaused, metrics, health]);

  // ═══════════════════════════════════════════════════════════════════
  //  HELPERS
  // ═══════════════════════════════════════════════════════════════════

  const getStatusConfig = (status) => STATUS_COLORS[status] || STATUS_COLORS.UNKNOWN;

  const formatTime = (timestamp) => {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };

  const acknowledgeAlert = (alertId) => {
    setAlerts(prev => prev.map(a => 
      a.id === alertId ? { ...a, acknowledged: true } : a
    ));
  };

  const clearAlert = (alertId) => {
    setAlerts(prev => prev.filter(a => a.id !== alertId));
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: HEALTH STATUS WIDGET
  // ═══════════════════════════════════════════════════════════════════

  const renderHealthWidget = (title, icon, healthData, details = []) => {
    const config = getStatusConfig(healthData.status);
    const Icon = icon;

    return (
      <div 
        onClick={() => setSelectedMetric(selectedMetric === title ? null : title)}
        style={{
          background: '#161b22',
          border: `1px solid ${config.borderColor}`,
          borderLeft: `3px solid ${config.color}`,
          borderRadius: '8px',
          padding: '16px',
          cursor: 'pointer',
          transition: 'all 0.2s'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px',
              height: '32px',
              borderRadius: '50%',
              background: config.bgColor,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Icon size={16} color={config.color} />
            </div>
            <div>
              <div style={{ color: '#8b949e', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                {title}
              </div>
              <div style={{ color: config.color, fontSize: '14px', fontWeight: 700 }}>
                {config.label}
              </div>
            </div>
          </div>
          <div style={{
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            background: config.color,
            animation: healthData.status === 'DEGRADED' || healthData.status === 'FAILURE' ? 'pulse 1.5s infinite' : 'none'
          }} />
        </div>

        {details.map((detail, idx) => (
          <div key={idx} style={{ 
            display: 'flex', 
            justifyContent: 'space-between',
            fontSize: '11px',
            marginBottom: '4px',
            color: idx === 0 ? '#ffffff' : '#8b949e'
          }}>
            <span>{detail.label}</span>
            <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{detail.value}</span>
          </div>
        ))}

        {selectedMetric === title && healthData.history && (
          <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #30363d' }}>
            <div style={{ color: '#6b7280', fontSize: '9px', marginBottom: '8px' }}>History (5min)</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: '2px', height: '40px' }}>
              {healthData.history.map((h, i) => (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    background: config.color,
                    opacity: 0.6 + (i / healthData.history.length) * 0.4,
                    height: `${Math.min(100, (h.value / Math.max(...healthData.history.map(x => x.value))) * 100)}%`,
                    borderRadius: '1px'
                  }}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: EXCHANGE LATENCY PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderExchangeLatency = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Globe size={18} color="#2196F3" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Exchange Latency
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
        {Object.entries(metrics.exchangeLatency).map(([name, data]) => {
          const config = getStatusConfig(data.status);
          return (
            <div
              key={name}
              style={{
                background: '#0d1117',
                border: `1px solid ${config.borderColor}`,
                borderRadius: '6px',
                padding: '12px'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ color: '#ffffff', fontSize: '13px', fontWeight: 600 }}>{name}</span>
                <div style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  background: config.color
                }} />
              </div>
              <div style={{ 
                color: data.latency > 200 ? '#FF5252' : data.latency > 100 ? '#FFAB00' : '#00C853',
                fontSize: '18px',
                fontWeight: 900,
                fontFamily: 'monospace'
              }}>
                {data.latency > 0 ? `${data.latency}ms` : '—'}
              </div>
              <div style={{ color: '#6b7280', fontSize: '9px', marginTop: '4px' }}>
                {config.label}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: QUEUE & WORKERS PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderQueueWorkers = () => {
    const queueUtilization = (metrics.queueDepth / metrics.queueCapacity) * 100;
    const workerUtilization = (metrics.activeWorkers / metrics.totalWorkers) * 100;
    const queueStatus = queueUtilization > 80 ? 'FAILURE' : queueUtilization > 50 ? 'DEGRADED' : 'HEALTHY';
    const queueConfig = getStatusConfig(queueStatus);

    return (
      <div style={panelStyle}>
        <div style={panelHeaderStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Layers size={18} color="#FFAB00" />
            <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
              Queue & Workers
            </span>
          </div>
        </div>

        {/* Queue Backpressure */}
        <div style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ color: '#8b949e', fontSize: '11px' }}>Queue Depth</span>
            <span style={{ color: queueConfig.color, fontSize: '11px', fontWeight: 700 }}>
              {metrics.queueDepth} / {metrics.queueCapacity}
            </span>
          </div>
          <div style={{
            height: '8px',
            background: '#0d1117',
            borderRadius: '4px',
            overflow: 'hidden'
          }}>
            <div style={{
              width: `${queueUtilization}%`,
              height: '100%',
              background: queueConfig.color,
              borderRadius: '4px',
              transition: 'width 0.3s'
            }} />
          </div>
          <div style={{ 
            marginTop: '6px',
            padding: '6px 10px',
            background: queueConfig.bgColor,
            border: `1px solid ${queueConfig.borderColor}`,
            borderRadius: '4px',
            fontSize: '10px',
            color: queueConfig.color
          }}>
            {queueUtilization > 80 ? '⚠️ CRITICAL: Queue near capacity' : 
             queueUtilization > 50 ? '⚡ WARNING: Queue filling up' : 
             '✅ Queue healthy'}
          </div>
        </div>

        {/* Active Workers */}
        <div style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ color: '#8b949e', fontSize: '11px' }}>Active Workers</span>
            <span style={{ color: '#ffffff', fontSize: '11px', fontWeight: 700 }}>
              {metrics.activeWorkers} / {metrics.totalWorkers}
            </span>
          </div>
          <div style={{
            height: '8px',
            background: '#0d1117',
            borderRadius: '4px',
            overflow: 'hidden'
          }}>
            <div style={{
              width: `${workerUtilization}%`,
              height: '100%',
              background: workerUtilization > 90 ? '#FFAB00' : '#00C853',
              borderRadius: '4px',
              transition: 'width 0.3s'
            }} />
          </div>
        </div>

        {/* Failed Orders */}
        <div style={{
          background: metrics.failedOrders > 0 ? '#FF525210' : '#00C85310',
          border: `1px solid ${metrics.failedOrders > 0 ? '#FF525240' : '#00C85340'}`,
          borderRadius: '6px',
          padding: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: '#8b949e', fontSize: '11px' }}>Failed Orders (24h)</span>
            <span style={{ 
              color: metrics.failedOrders > 0 ? '#FF5252' : '#00C853',
              fontSize: '18px',
              fontWeight: 900 
            }}>
              {metrics.failedOrders}
            </span>
          </div>
          <div style={{ color: '#6b7280', fontSize: '9px', marginTop: '4px' }}>
            Success rate: {((1 - metrics.failedOrders / metrics.totalOrders) * 100).toFixed(2)}%
          </div>
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: RECONNECT & EVENT LOOP PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderEventMetrics = () => {
    const eventLoopConfig = getStatusConfig(health.eventLoop.status);

    return (
      <div style={panelStyle}>
        <div style={panelHeaderStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Cpu size={18} color="#9C27B0" />
            <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
              Event Loop & System
            </span>
          </div>
        </div>

        {/* Reconnect Events */}
        <div style={{
          background: metrics.reconnectCount > 5 ? '#FF525215' : '#FFAB0015',
          border: `1px solid ${metrics.reconnectCount > 5 ? '#FF525240' : '#FFAB0040'}`,
          borderRadius: '6px',
          padding: '12px',
          marginBottom: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <RefreshCw size={16} color={metrics.reconnectCount > 5 ? '#FF5252' : '#FFAB00'} />
            <span style={{ color: '#ffffff', fontSize: '12px', fontWeight: 600 }}>
              WebSocket Reconnects
            </span>
          </div>
          <div style={{ 
            color: metrics.reconnectCount > 5 ? '#FF5252' : '#FFAB00',
            fontSize: '24px',
            fontWeight: 900 
          }}>
            {metrics.reconnectCount}
          </div>
          <div style={{ color: '#6b7280', fontSize: '9px', marginTop: '4px' }}>
            {metrics.reconnectCount > 5 ? '⚠️ Potential reconnect storm' : 'Within normal range'}
          </div>
        </div>

        {/* Event Loop Health */}
        <div style={{
          background: eventLoopConfig.bgColor,
          border: `1px solid ${eventLoopConfig.borderColor}`,
          borderRadius: '6px',
          padding: '12px',
          marginBottom: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ color: '#ffffff', fontSize: '12px', fontWeight: 600 }}>
              Event Loop Lag
            </span>
            <span style={{ color: eventLoopConfig.color, fontSize: '14px', fontWeight: 700 }}>
              {health.eventLoop.lag.toFixed(1)}ms
            </span>
          </div>
          <div style={{ color: '#6b7280', fontSize: '9px' }}>
            Ticks/sec: {health.eventLoop.ticksPerSec}
          </div>
        </div>

        {/* Memory Usage */}
        <div style={{
          background: '#0d1117',
          border: `1px solid ${health.memory.used > 80 ? '#FF525240' : '#30363d'}`,
          borderRadius: '6px',
          padding: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ color: '#8b949e', fontSize: '11px' }}>Memory Usage</span>
            <span style={{ 
              color: health.memory.used > 80 ? '#FF5252' : '#00C853',
              fontSize: '14px',
              fontWeight: 700 
            }}>
              {health.memory.used}%
            </span>
          </div>
          <div style={{
            height: '6px',
            background: '#161b22',
            borderRadius: '3px',
            overflow: 'hidden'
          }}>
            <div style={{
              width: `${health.memory.used}%`,
              height: '100%',
              background: health.memory.used > 80 ? '#FF5252' : health.memory.used > 60 ? '#FFAB00' : '#00C853',
              borderRadius: '3px'
            }} />
          </div>
          <div style={{ color: '#6b7280', fontSize: '9px', marginTop: '6px' }}>
            Heap: {(health.memory.heapUsed / 1024).toFixed(1)} MB
          </div>
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: ALERT BANNERS
  // ═══════════════════════════════════════════════════════════════════

  const renderAlerts = () => {
    const unacknowledgedAlerts = alerts.filter(a => !a.acknowledged);
    
    if (unacknowledgedAlerts.length === 0) return null;

    return (
      <div style={{ marginBottom: '20px' }}>
        {unacknowledgedAlerts.map(alert => {
          const config = ALERT_TYPES[alert.type] || ALERT_TYPES.HIGH_LATENCY;
          const Icon = config.icon;
          const isCritical = config.severity === 'critical';

          return (
            <div
              key={alert.id}
              onClick={() => onAlertClick?.(alert)}
              style={{
                background: config.bgColor,
                border: `1px solid ${config.color}60`,
                borderRadius: '8px',
                padding: '12px 16px',
                marginBottom: '8px',
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                animation: isCritical ? 'alertPulse 2s infinite' : 'none',
                cursor: 'pointer'
              }}
            >
              <Icon size={20} color={config.color} />
              
              <div style={{ flex: 1 }}>
                <div style={{ color: config.color, fontSize: '13px', fontWeight: 700 }}>
                  {config.label}
                </div>
                <div style={{ color: '#ff6b81', fontSize: '11px', marginTop: '2px' }}>
                  {alert.message}
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#6b7280', fontSize: '10px' }}>
                  {formatTime(alert.timestamp)}
                </span>
                
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    acknowledgeAlert(alert.id);
                  }}
                  style={{
                    background: 'transparent',
                    border: `1px solid ${config.color}40`,
                    borderRadius: '4px',
                    padding: '4px 8px',
                    color: config.color,
                    fontSize: '10px',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px'
                  }}
                >
                  <CheckCircle size={12} />
                  ACK
                </button>

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    clearAlert(alert.id);
                  }}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: '#6b7280',
                    cursor: 'pointer'
                  }}
                >
                  <XCircle size={16} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  MAIN RENDER
  // ═══════════════════════════════════════════════════════════════════

  return (
    <div style={{
      background: '#0d1117',
      minHeight: '100vh',
      padding: '20px',
      fontFamily: "'IBM Plex Mono', 'Fira Code', monospace"
    }}>
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        marginBottom: '20px' 
      }}>
        <div>
          <h1 style={{ color: '#ffffff', fontSize: '20px', fontWeight: 900, marginBottom: '4px' }}>
            Infrastructure Operations
          </h1>
          <p style={{ color: '#8b949e', fontSize: '12px' }}>
            Real-time system health monitoring • {alerts.filter(a => !a.acknowledged).length} active alerts
          </p>
        </div>

        <div style={{ display: 'flex', gap: '12px' }}>
          <button
            onClick={() => setIsPaused(!isPaused)}
            style={{
              background: isPaused ? '#FFAB0020' : 'transparent',
              border: `1px solid ${isPaused ? '#FFAB00' : '#30363d'}`,
              borderRadius: '6px',
              padding: '8px 16px',
              color: isPaused ? '#FFAB00' : '#8b949e',
              fontSize: '12px',
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}
          >
            {isPaused ? <PauseCircle size={16} /> : <PlayCircle size={16} />}
            {isPaused ? 'PAUSED' : 'LIVE'}
          </button>

          <button
            onClick={() => {
              // Trigger refresh
              window.location.reload();
            }}
            style={{
              background: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '8px 16px',
              color: '#8b949e',
              fontSize: '12px',
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}
          >
            <RotateCcw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {/* Critical Alerts */}
      {renderAlerts()}

      {/* Health Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '16px',
        marginBottom: '20px'
      }}>
        {renderHealthWidget('API Health', Server, health.api, [
          { label: 'Latency', value: `${health.api.latency}ms` },
          { label: 'Uptime', value: `${health.api.uptime}%` }
        ])}

        {renderHealthWidget('Redis', Database, health.redis, [
          { label: 'Latency', value: `${health.redis.latency}ms` },
          { label: 'Connections', value: health.redis.connections },
          { label: 'Memory', value: `${health.redis.memory}MB` }
        ])}

        {renderHealthWidget('WebSocket', Wifi, health.websocket, [
          { label: 'Connections', value: health.websocket.connections },
          { label: 'Msg/sec', value: health.websocket.messagesPerSec }
        ])}
      </div>

      {/* Detailed Panels */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))',
        gap: '20px'
      }}>
        {renderExchangeLatency()}
        {renderQueueWorkers()}
        {renderEventMetrics()}
      </div>

      {/* System Status Summary */}
      <div style={{
        marginTop: '20px',
        padding: '16px',
        background: '#161b22',
        border: '1px solid #30363d',
        borderRadius: '8px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            background: '#00C853',
            animation: 'pulse 2s infinite'
          }} />
          <div>
            <div style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
              System Operational
            </div>
            <div style={{ color: '#8b949e', fontSize: '11px' }}>
              All critical services responding normally
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '24px', fontSize: '11px', color: '#8b949e' }}>
          <div>API: <span style={{ color: '#00C853' }}>UP</span></div>
          <div>Redis: <span style={{ color: '#00C853' }}>UP</span></div>
          <div>WebSocket: <span style={{ color: '#00C853' }}>UP</span></div>
          <div>Workers: <span style={{ color: '#FFAB00' }}>{metrics.activeWorkers}/{metrics.totalWorkers}</span></div>
        </div>
      </div>

      {/* CSS Animations */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        
        @keyframes alertPulse {
          0%, 100% { border-color: #FF525260; }
          50% { border-color: #FF5252; }
        }
      `}</style>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  STYLES
// ═══════════════════════════════════════════════════════════════════

const panelStyle = {
  background: '#161b22',
  border: '1px solid #30363d',
  borderRadius: '12px',
  padding: '16px'
};

const panelHeaderStyle = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: '16px',
  paddingBottom: '12px',
  borderBottom: '1px solid #30363d'
};

export default InfrastructureOperations;
