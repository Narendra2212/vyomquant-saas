import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  Bot, Activity, Wifi, Shield, Server, 
  Clock, AlertTriangle, CheckCircle, XCircle, 
  Pause, Play, RefreshCw, Radio, Zap,
  TrendingUp, TrendingDown, Minus, Database,
  Layers, Globe, Cpu, HardDrive, GitBranch, ExternalLink
} from 'lucide-react';
import WS_CHANNELS from '../constants/wsChannels';
import { normalizeTelemetryEvent } from '../websocketClient';
import SignalTraceVisualization from './SignalTraceVisualization';

/**
 * 🔴 PHASE 2 — BOT MONITORING CONSOLE
 * 
 * Professional algo trading infrastructure monitoring dashboard
 * 
 * Sections:
 * 1. Active Bots - Deployed bot instances and their status
 * 2. Signal Activity - Real-time signal trace and execution flow
 * 3. Exchange Connectivity - Exchange connection health
 * 4. Risk Status - Drawdown, exposure, kill switch status
 * 5. Execution Queue - Order lifecycle and queue depth
 * 6. Bot Health - System health metrics per bot
 * 7. Infrastructure Status - WebSocket, Redis, latency metrics
 */

// ═══════════════════════════════════════════════════════════════════
//  CONFIGURATION
// ═══════════════════════════════════════════════════════════════════

const STATUS_CONFIG = {
  RUNNING: {
    color: '#00C853',
    bgColor: '#00C85315',
    borderColor: '#00C85340',
    icon: Play,
    label: 'Running',
    pulse: true
  },
  PAUSED: {
    color: '#FFAB00',
    bgColor: '#FFAB0015',
    borderColor: '#FFAB0040',
    icon: Pause,
    label: 'Paused',
    pulse: false
  },
  ERROR: {
    color: '#FF5252',
    bgColor: '#FF525215',
    borderColor: '#FF525240',
    icon: XCircle,
    label: 'Error',
    pulse: true
  },
  RECONNECTING: {
    color: '#9C27B0',
    bgColor: '#9C27B015',
    borderColor: '#9C27B040',
    icon: RefreshCw,
    label: 'Reconnecting',
    pulse: true
  }
};

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
  BLOCKED: {
    color: '#FFAB00',
    bgColor: '#FFAB0015',
    borderColor: '#FFAB0040',
    icon: '⊘',
    label: 'BLOCKED'
  },
  'RISK REJECTED': {
    color: '#FF5722',
    bgColor: '#FF572215',
    borderColor: '#FF572240',
    icon: '⚠',
    label: 'RISK REJECTED'
  },
  EXECUTED: {
    color: '#2196F3',
    bgColor: '#2196F315',
    borderColor: '#2196F340',
    icon: '✓',
    label: 'EXECUTED'
  }
};

const EXCHANGE_STATUS = {
  CONNECTED: { color: '#00C853', label: 'Connected' },
  DISCONNECTED: { color: '#FF5252', label: 'Disconnected' },
  DEGRADED: { color: '#FFAB00', label: 'Degraded' }
};

// ═══════════════════════════════════════════════════════════════════
//  MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════

const BotMonitoringConsole = ({ 
  wsClient,
  accountId,
  onBotError,
  onSignalClick
}) => {
  // State
  const [bots, setBots] = useState([]);
  const [signals, setSignals] = useState([]);
  const [exchanges, setExchanges] = useState([]);
  const [riskMetrics, setRiskMetrics] = useState({
    drawdown: 0,
    exposure: 0,
    killSwitchActive: false,
    blockedSymbols: [],
    rejectedSignals: 0
  });
  const [infrastructure, setInfrastructure] = useState({
    websocket: 'disconnected',
    redis: 'unknown',
    queueDepth: 0,
    latency: 0,
    reconnectCount: 0,
    droppedEvents: 0
  });
  const [selectedBot, setSelectedBot] = useState(null);
  const [filter, setFilter] = useState('ALL');
  const [isPaused, setIsPaused] = useState(false);
  
  const signalBuffer = useRef([]);

  useEffect(() => {
    // Kept empty: Mock data for telemetry (bots, signals, risk) removed to use real WebSocket feed.
    // If backend isn't sending data, components will show their empty states.
  }, []);

  // ═══════════════════════════════════════════════════════════════════
  //  WEBSOCKET INTEGRATION
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    if (!wsClient) return;

    const handleSignal = (rawData) => {
      if (isPaused) return;
      const data = normalizeTelemetryEvent(rawData);
      
      const signal = {
        id: data.message_id || crypto.randomUUID(),
        timestamp: data.timestamp || Date.now(),
        symbol: data.symbol || data.asset || '-',
        strategy: data.strategy_id || data.strategy_name || data.source || 'Unknown',
        type: data.signal_type || data.signal?.toUpperCase() || data.side?.toUpperCase() || 'BUY',
        dagPath: data.dag_path || data.node_id || 'Direct',
        confidence: data.confidence || data.value || 0,
        status: data.status || 'EXECUTED',
        metadata: data.metadata || {}
      };

      signalBuffer.current.unshift(signal);
      if (signalBuffer.current.length > 100) {
        signalBuffer.current = signalBuffer.current.slice(0, 100);
      }
      
      setSignals([...signalBuffer.current]);
    };

    const handleInfrastructure = (rawData) => {
      const data = normalizeTelemetryEvent(rawData);
      // Update bots status
      if (data.type === 'bot_health') {
        setBots(prev => {
          const exists = prev.find(b => b.id === data.bot_id);
          const newBot = {
            id: data.bot_id,
            name: data.name || data.bot_id,
            strategy: data.strategy_id,
            exchange: data.exchange || '-',
            mode: data.mode || 'live',
            uptime: data.uptime_seconds || data.uptime || 0,
            status: (data.status || 'RUNNING').toUpperCase(),
            symbol: data.symbol || '-',
            lastSignal: Date.now(),
            signalsToday: exists ? exists.signalsToday : 0,
            avgLatency: data.latency || 0,
            error: data.error
          };
          if (exists) {
            return prev.map(b => b.id === data.bot_id ? { ...b, ...newBot } : b);
          }
          return [...prev, newBot];
        });
      }
      
      if (data.type === 'infrastructure') {
        setInfrastructure(prev => ({
          ...prev,
          ...data.metrics
        }));
      }
    };

    let unsubscribeSignal = null;
    let unsubscribeBotStatus = null;
    
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeSignal = wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleSignal);
      unsubscribeBotStatus = wsClient.subscribe(WS_CHANNELS.BOT_STATUS, handleInfrastructure);
    }

    return () => {
      if (unsubscribeSignal) unsubscribeSignal();
      if (unsubscribeBotStatus) unsubscribeBotStatus();
    };
  }, [wsClient, isPaused]);

  // ═══════════════════════════════════════════════════════════════════
  //  HELPERS
  // ═══════════════════════════════════════════════════════════════════

  const formatUptime = (seconds) => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
  };

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

  const getTimeAgo = (timestamp) => {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: ACTIVE BOTS PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderActiveBots = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Bot size={18} color="#00d4ff" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Active Bots ({bots.filter(b => b.status === 'RUNNING').length}/{bots.length})
          </span>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          {['ALL', 'RUNNING', 'ERROR', 'PAUSED'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                background: filter === f ? '#00d4ff20' : 'transparent',
                border: `1px solid ${filter === f ? '#00d4ff' : '#30363d'}`,
                borderRadius: '4px',
                padding: '4px 8px',
                color: filter === f ? '#00d4ff' : '#8b949e',
                fontSize: '10px',
                fontWeight: 700,
                cursor: 'pointer'
              }}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {bots
          .filter(b => filter === 'ALL' || b.status === filter)
          .map(bot => {
            const status = STATUS_CONFIG[bot.status];
            const StatusIcon = status.icon;
            
            return (
              <div
                key={bot.id}
                onClick={() => setSelectedBot(selectedBot === bot.id ? null : bot.id)}
                style={{
                  background: selectedBot === bot.id ? `${status.color}10` : '#161b22',
                  border: `1px solid ${selectedBot === bot.id ? status.color : '#30363d'}`,
                  borderLeft: `3px solid ${status.color}`,
                  borderRadius: '6px',
                  padding: '12px',
                  cursor: 'pointer',
                  transition: 'all 0.2s'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: status.color,
                      animation: status.pulse ? 'pulse 2s infinite' : 'none'
                    }} />
                    <div>
                      <div style={{ color: '#ffffff', fontSize: '13px', fontWeight: 700 }}>
                        {bot.name}
                      </div>
                      <div style={{ color: '#8b949e', fontSize: '10px', marginTop: '2px' }}>
                        {bot.strategy} • {bot.exchange}
                      </div>
                    </div>
                  </div>
                  
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{
                      background: bot.mode === 'live' ? '#00C85320' : '#FFAB0020',
                      color: bot.mode === 'live' ? '#00C853' : '#FFAB00',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      fontSize: '9px',
                      fontWeight: 700,
                      textTransform: 'uppercase'
                    }}>
                      {bot.mode}
                    </span>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ color: '#8b949e', fontSize: '10px' }}>
                        <Clock size={10} style={{ display: 'inline', marginRight: '4px' }} />
                        {formatUptime(bot.uptime)}
                      </div>
                      <div style={{ color: '#58a6ff', fontSize: '10px', marginTop: '2px' }}>
                        {bot.symbol}
                      </div>
                    </div>
                  </div>
                </div>
                
                {selectedBot === bot.id && bot.error && (
                  <div style={{
                    marginTop: '8px',
                    padding: '8px',
                    background: '#FF525220',
                    border: '1px solid #FF525240',
                    borderRadius: '4px',
                    color: '#FF5252',
                    fontSize: '11px'
                  }}>
                    <AlertTriangle size={12} style={{ marginRight: '6px', display: 'inline' }} />
                    {bot.error}
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: SIGNAL TRACE PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderSignalTrace = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Activity size={18} color="#00C853" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Signal Activity ({signals.length})
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent('navigate', { detail: 'signal-trace' }))}
            style={{
              background: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '4px',
              padding: '4px 8px',
              color: '#8b949e',
              fontSize: '10px',
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
            title="View detailed signal execution trace"
          >
            <GitBranch size={12} />
            <span>Full Trace</span>
            <ExternalLink size={10} />
          </button>
          <button
            onClick={() => setIsPaused(!isPaused)}
            style={{
              background: isPaused ? '#FFAB0020' : 'transparent',
              border: `1px solid ${isPaused ? '#FFAB00' : '#30363d'}`,
              borderRadius: '4px',
              padding: '4px 8px',
              color: isPaused ? '#FFAB00' : '#8b949e',
              fontSize: '10px',
              fontWeight: 700,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
          >
            {isPaused ? <Pause size={12} /> : <Play size={12} />}
            {isPaused ? 'PAUSED' : 'LIVE'}
          </button>
        </div>
      </div>
      
      <div style={{ 
        display: 'flex', 
        flexDirection: 'column', 
        gap: '6px',
        maxHeight: '400px',
        overflowY: 'auto'
      }}>
        {signals.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#8b949e', fontSize: '11px' }}>
            No signals received yet. Waiting for bot activity...
          </div>
        ) : (
          signals.slice(0, 20).map((signal, index) => {
            const config = SIGNAL_CONFIG[signal.status] || SIGNAL_CONFIG.EXECUTED;
            const isNew = index === 0;
            
            return (
              <div
                key={signal.id}
                onClick={() => onSignalClick?.(signal)}
                style={{
                  background: isNew ? `${config.color}10` : '#161b22',
                  border: `1px solid ${isNew ? config.color : '#30363d'}`,
                  borderLeft: `3px solid ${config.color}`,
                  borderRadius: '6px',
                  padding: '10px 12px',
                  cursor: onSignalClick ? 'pointer' : 'default',
                  animation: isNew && !isPaused ? 'slideIn 0.3s ease-out' : 'none',
                  transition: 'all 0.2s'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '11px' }}>
                  <div style={{ color: '#8b949e', fontWeight: 700, minWidth: '75px', fontFamily: 'monospace' }}>
                    {formatTime(signal.timestamp)}
                  </div>
                  
                  <span style={{ color: config.color, fontSize: '12px', fontWeight: 900 }}>
                    {config.icon}
                  </span>
                  
                  <div style={{ minWidth: '80px' }}>
                    <div style={{ color: '#ffffff', fontWeight: 700 }}>{signal.symbol}</div>
                    <div style={{ color: '#8b949e', fontSize: '9px' }}>{signal.strategy}</div>
                  </div>
                  
                  <div style={{
                    background: config.bgColor,
                    border: `1px solid ${config.borderColor}`,
                    borderRadius: '4px',
                    padding: '2px 8px',
                    color: config.color,
                    fontSize: '10px',
                    fontWeight: 900
                  }}>
                    {signal.type}
                  </div>
                  
                  <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                    <div style={{ color: '#8b949e', fontSize: '10px' }}>
                      DAG: {signal.dagPath}
                    </div>
                    <div style={{ color: '#9C27B0', fontSize: '10px', marginTop: '2px' }}>
                      ML: {(signal.confidence * 100).toFixed(1)}%
                    </div>
                  </div>
                  
                  <div style={{
                    background: config.bgColor,
                    border: `1px solid ${config.borderColor}`,
                    borderRadius: '4px',
                    padding: '2px 6px',
                    color: config.color,
                    fontSize: '9px',
                    fontWeight: 700
                  }}>
                    {signal.status}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: EXCHANGE CONNECTIVITY
  // ═══════════════════════════════════════════════════════════════════

  const renderExchangeConnectivity = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Globe size={18} color="#2196F3" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Exchange Connectivity
          </span>
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
        {exchanges.map(exchange => {
          const status = EXCHANGE_STATUS[exchange.status];
          
          return (
            <div
              key={exchange.name}
              style={{
                background: '#161b22',
                border: `1px solid ${status.color}40`,
                borderRadius: '8px',
                padding: '12px'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ color: '#ffffff', fontSize: '13px', fontWeight: 700 }}>
                  {exchange.name}
                </span>
                <span style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  background: status.color,
                  animation: exchange.status === 'RECONNECTING' ? 'pulse 1s infinite' : 'none'
                }} />
              </div>
              
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#8b949e' }}>
                <span>{status.label}</span>
                <span>{exchange.latency > 0 ? `${exchange.latency}ms` : '—'}</span>
              </div>
              
              <div style={{ marginTop: '8px', fontSize: '9px', color: '#6b7280' }}>
                {exchange.region} • {getTimeAgo(exchange.lastPing)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: RISK STATUS
  // ═══════════════════════════════════════════════════════════════════

  const renderRiskStatus = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Shield size={18} color={riskMetrics.killSwitchActive ? '#FF5252' : '#00C853'} />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Risk Status
          </span>
        </div>
        {riskMetrics.killSwitchActive && (
          <span style={{
            background: '#FF525220',
            color: '#FF5252',
            padding: '2px 8px',
            borderRadius: '4px',
            fontSize: '10px',
            fontWeight: 700
          }}>
            KILL SWITCH ACTIVE
          </span>
        )}
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px', marginBottom: '12px' }}>
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Drawdown</div>
          <div style={{ 
            color: riskMetrics.drawdown > 5 ? '#FF5252' : riskMetrics.drawdown > 2 ? '#FFAB00' : '#00C853',
            fontSize: '20px',
            fontWeight: 900
          }}>
            {riskMetrics.drawdown.toFixed(2)}%
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Exposure</div>
          <div style={{ 
            color: riskMetrics.exposure > 80 ? '#FF5252' : riskMetrics.exposure > 50 ? '#FFAB00' : '#00C853',
            fontSize: '20px',
            fontWeight: 900
          }}>
            {riskMetrics.exposure.toFixed(1)}%
          </div>
        </div>
      </div>
      
      {riskMetrics.blockedSymbols.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '6px' }}>Blocked Symbols</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {riskMetrics.blockedSymbols.map(sym => (
              <span key={sym} style={{
                background: '#FF525220',
                color: '#FF5252',
                padding: '2px 8px',
                borderRadius: '4px',
                fontSize: '10px',
                fontWeight: 700
              }}>
                {sym}
              </span>
            ))}
          </div>
        </div>
      )}
      
      <div style={metricBoxStyle}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ color: '#8b949e', fontSize: '10px' }}>Rejected Signals (24h)</span>
          <span style={{ color: '#FFAB00', fontSize: '14px', fontWeight: 700 }}>
            {riskMetrics.rejectedSignals}
          </span>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: INFRASTRUCTURE STATUS
  // ═══════════════════════════════════════════════════════════════════

  const renderInfrastructure = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Server size={18} color="#9C27B0" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Infrastructure
          </span>
        </div>
      </div>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
        <div style={metricBoxStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <Wifi size={14} color={infrastructure.websocket === 'connected' ? '#00C853' : '#FF5252'} />
            <span style={{ color: '#8b949e', fontSize: '10px' }}>WebSocket</span>
          </div>
          <div style={{ 
            color: infrastructure.websocket === 'connected' ? '#00C853' : '#FF5252',
            fontSize: '14px',
            fontWeight: 700,
            textTransform: 'uppercase'
          }}>
            {infrastructure.websocket}
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <Database size={14} color={infrastructure.redis === 'connected' ? '#00C853' : '#FFAB00'} />
            <span style={{ color: '#8b949e', fontSize: '10px' }}>Redis</span>
          </div>
          <div style={{ 
            color: infrastructure.redis === 'connected' ? '#00C853' : '#FFAB00',
            fontSize: '14px',
            fontWeight: 700,
            textTransform: 'uppercase'
          }}>
            {infrastructure.redis}
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Queue Depth</div>
          <div style={{ color: '#ffffff', fontSize: '18px', fontWeight: 900 }}>
            {infrastructure.queueDepth}
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Latency</div>
          <div style={{ 
            color: infrastructure.latency < 100 ? '#00C853' : infrastructure.latency < 200 ? '#FFAB00' : '#FF5252',
            fontSize: '18px',
            fontWeight: 900
          }}>
            {infrastructure.latency}ms
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Reconnects</div>
          <div style={{ color: infrastructure.reconnectCount > 0 ? '#FFAB00' : '#00C853', fontSize: '18px', fontWeight: 900 }}>
            {infrastructure.reconnectCount}
          </div>
        </div>
        
        <div style={metricBoxStyle}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Dropped Events</div>
          <div style={{ color: infrastructure.droppedEvents > 0 ? '#FF5252' : '#00C853', fontSize: '18px', fontWeight: 900 }}>
            {infrastructure.droppedEvents}
          </div>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: EXECUTION QUEUE
  // ═══════════════════════════════════════════════════════════════════

  const renderExecutionQueue = () => (
    <div style={panelStyle}>
      <div style={panelHeaderStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Layers size={18} color="#FFAB00" />
          <span style={{ color: '#ffffff', fontSize: '14px', fontWeight: 700 }}>
            Execution Queue
          </span>
        </div>
        <span style={{ color: '#8b949e', fontSize: '11px' }}>
          {infrastructure.queueDepth} pending
        </span>
      </div>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {/* Queue visualization */}
        <div style={{
          background: '#0d1117',
          border: '1px solid #30363d',
          borderRadius: '8px',
          padding: '16px',
          height: '80px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative'
        }}>
          <div style={{
            display: 'flex',
            gap: '4px',
            alignItems: 'flex-end',
            height: '50px'
          }}>
            {[...Array(20)].map((_, i) => (
              <div
                key={i}
                style={{
                  width: '8px',
                  background: i < infrastructure.queueDepth ? '#FFAB00' : '#30363d',
                  borderRadius: '2px',
                  // Deterministic sparkline heights based on index — no Math.random() in production code
                  height: `${Math.max(10, 10 + 30 * Math.abs(Math.sin(i * 1.3)))}px`,
                  opacity: i < infrastructure.queueDepth ? 1 : 0.3,
                  transition: 'all 0.3s'
                }}
              />
            ))}
          </div>
          
          <div style={{
            position: 'absolute',
            bottom: '8px',
            left: '50%',
            transform: 'translateX(-50%)',
            fontSize: '10px',
            color: '#8b949e'
          }}>
            Queue Load
          </div>
        </div>
        
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#8b949e' }}>
          <span>Processing: {infrastructure.latency}ms avg</span>
          <span>Throughput: {(1000 / infrastructure.latency * 60).toFixed(0)}/min</span>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  //  MAIN RENDER
  // ═══════════════════════════════════════════════════════════════════

  return (
    <div style={{
      background: '#0d1117',
      minHeight: '100vh',
      padding: '20px',
      fontFamily: 'monospace'
    }}>
      {/* Header */}
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ color: '#ffffff', fontSize: '20px', fontWeight: 900, marginBottom: '8px' }}>
          Bot Monitoring Console
        </h1>
        <p style={{ color: '#8b949e', fontSize: '12px' }}>
          Real-time algo trading infrastructure monitoring • {bots.filter(b => b.status === 'RUNNING').length} bots active
        </p>
      </div>

      {/* Dashboard Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))',
        gap: '20px'
      }}>
        {/* Left Column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {renderActiveBots()}
          {renderExchangeConnectivity()}
          {renderRiskStatus()}
        </div>

        {/* Right Column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {renderSignalTrace()}
          {renderInfrastructure()}
          {renderExecutionQueue()}
        </div>
      </div>

      {/* CSS Animations */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        
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

const metricBoxStyle = {
  background: '#0d1117',
  border: '1px solid #30363d',
  borderRadius: '8px',
  padding: '12px'
};

export default BotMonitoringConsole;
