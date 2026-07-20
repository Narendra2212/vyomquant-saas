import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  Activity, Clock, AlertTriangle, CheckCircle, XCircle,
  ArrowRight, ChevronDown, ChevronRight, Zap, Brain,
  Shield, Server, WifiOff, Database, TrendingUp,
  TrendingDown, Minus, GitBranch, Layers, Cpu,
  Radio, AlertOctagon, Ban
} from 'lucide-react';
import WS_CHANNELS from '../constants/wsChannels';
import { normalizeTelemetryEvent } from '../websocketClient';

/**
 * 🔴 SIGNAL TRACE VISUALIZATION
 * 
 * Comprehensive algo execution debugging and observability dashboard
 * 
 * Shows complete signal lifecycle:
 * Market Data → Indicators → DAG Nodes → ML Inference → Risk Validation → Execution → Exchange Response
 */

// ═══════════════════════════════════════════════════════════════════
//  CONFIGURATION
// ═══════════════════════════════════════════════════════════════════

const PIPELINE_STAGES = {
  MARKET_DATA: { 
    id: 'market_data', 
    label: 'Market Data', 
    icon: Database,
    color: '#2196F3'
  },
  INDICATORS: { 
    id: 'indicators', 
    label: 'Indicators', 
    icon: Activity,
    color: '#00BCD4'
  },
  DAG_NODES: { 
    id: 'dag_nodes', 
    label: 'DAG Nodes', 
    icon: GitBranch,
    color: '#FFAB00'
  },
  ML_INFERENCE: { 
    id: 'ml_inference', 
    label: 'ML Inference', 
    icon: Brain,
    color: '#9C27B0'
  },
  RISK_VALIDATION: { 
    id: 'risk_validation', 
    label: 'Risk Check', 
    icon: Shield,
    color: '#FF5722'
  },
  EXECUTION: { 
    id: 'execution', 
    label: 'Execution', 
    icon: Zap,
    color: '#00C853'
  },
  EXCHANGE: { 
    id: 'exchange', 
    label: 'Exchange', 
    icon: Server,
    color: '#607D8B'
  }
};

const SIGNAL_STATES = {
  RECEIVED: { 
    color: '#2196F3', 
    bgColor: '#2196F315',
    icon: Radio,
    label: 'Received'
  },
  VALIDATED: { 
    color: '#00BCD4', 
    bgColor: '#00BCD415',
    icon: CheckCircle,
    label: 'Validated'
  },
  RISK_CHECKED: { 
    color: '#FFAB00', 
    bgColor: '#FFAB0015',
    icon: Shield,
    label: 'Risk Checked'
  },
  EXECUTED: { 
    color: '#00C853', 
    bgColor: '#00C85315',
    icon: CheckCircle,
    label: 'Executed'
  },
  REJECTED: { 
    color: '#FF5722', 
    bgColor: '#FF572215',
    icon: Ban,
    label: 'Rejected'
  },
  FAILED: { 
    color: '#FF5252', 
    bgColor: '#FF525215',
    icon: XCircle,
    label: 'Failed'
  }
};

const ERROR_TYPES = {
  WEBSOCKET_DISCONNECT: { icon: WifiOff, color: '#FF5252', label: 'WebSocket Disconnect' },
  EXCHANGE_REJECT: { icon: Server, color: '#FF5722', label: 'Exchange Reject' },
  INVALID_SIGNAL: { icon: AlertTriangle, color: '#FFAB00', label: 'Invalid Signal' },
  NAN_DETECTED: { icon: AlertOctagon, color: '#9C27B0', label: 'NaN Detected' },
  RISK_BLOCK: { icon: Ban, color: '#FF5252', label: 'Risk Block' }
};

const SIGNAL_TYPES = {
  BUY: { color: '#00C853', icon: TrendingUp, label: 'BUY' },
  SELL: { color: '#FF5252', icon: TrendingDown, label: 'SELL' },
  HOLD: { color: '#9E9E9E', icon: Minus, label: 'HOLD' }
};

// ═══════════════════════════════════════════════════════════════════
//  MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════

const SignalTraceVisualization = ({ 
  wsClient,
  accountId,
  onNodeClick,
  maxTraces = 50
}) => {
  const [traces, setTraces] = useState([]);
  const [selectedTrace, setSelectedTrace] = useState(null);
  const [filter, setFilter] = useState('ALL'); // ALL, ERROR, SUCCESS
  const [isPaused, setIsPaused] = useState(false);
  const [expandedNodes, setExpandedNodes] = useState(new Set());
  
  const traceBuffer = useRef([]);

  // ═══════════════════════════════════════════════════════════════════
  //  NO MOCK DATA (Live data only)
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    traceBuffer.current = [];
    setTraces([]);
  }, []);

  // ═══════════════════════════════════════════════════════════════════
  //  WEBSOCKET INTEGRATION (Fixed for custom wsClient)
  // ═══════════════════════════════════════════════════════════════════

  useEffect(() => {
    if (!wsClient) return;

    const handleSignalTrace = (rawData) => {
      if (isPaused) return;

      const data = normalizeTelemetryEvent(rawData);
      if (!data) return;

      const trace = {
        id: `trace-${crypto.randomUUID()}`, // CSPRNG — Math.random() is not cryptographically secure
        signalId: data.signal_id || data.id,
        timestamp: data.timestamp || Date.now(),
        symbol: data.symbol || data.asset,
        strategy: data.strategy_id || data.strategy_name || data.strategy,
        signalType: data.signal_type || data.signal?.toUpperCase(),
        state: data.state || 'RECEIVED',
        pipeline: data.pipeline || [],
        error: data.error,
        totalLatency: data.total_latency || 0,
        success: data.success || false
      };

      traceBuffer.current.unshift(trace);
      if (traceBuffer.current.length > maxTraces) {
        traceBuffer.current = traceBuffer.current.slice(0, maxTraces);
      }

      setTraces([...traceBuffer.current]);
    };

    // Subscribe using custom wsClient interface (NOT addEventListener)
    let unsubscribeSignalTrace = null;
    let unsubscribeRiskEvents = null;

    if (typeof wsClient.subscribe === 'function') {
      unsubscribeSignalTrace = wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleSignalTrace);
      unsubscribeRiskEvents = wsClient.subscribe(WS_CHANNELS.RISK_EVENTS, handleSignalTrace);
    } else {
      console.warn('[SignalTraceVisualization] wsClient.subscribe is not available');
    }

    return () => {
      if (unsubscribeSignalTrace) unsubscribeSignalTrace();
      if (unsubscribeRiskEvents) unsubscribeRiskEvents();
    };
  }, [wsClient, isPaused, maxTraces]);

  // ═══════════════════════════════════════════════════════════════════
  //  HELPERS
  // ═══════════════════════════════════════════════════════════════════

  const formatTime = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3
    });
  };

  const formatDuration = (ms) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const toggleNodeExpansion = (nodeId) => {
    setExpandedNodes(prev => {
      const newSet = new Set(prev);
      if (newSet.has(nodeId)) {
        newSet.delete(nodeId);
      } else {
        newSet.add(nodeId);
      }
      return newSet;
    });
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: PIPELINE STAGE
  // ═══════════════════════════════════════════════════════════════════

  const renderPipelineStage = (stage, index, totalStages) => {
    const config = PIPELINE_STAGES[stage.stage];
    const Icon = config.icon;
    const isCompleted = stage.status === 'completed';
    const isFailed = stage.status === 'failed' || stage.status === 'rejected';
    const isLast = index === totalStages - 1;

    return (
      <div key={stage.stage} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '8px' }}>
        {/* Stage Icon & Connector */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginRight: '12px' }}>
          <div style={{
            width: '36px',
            height: '36px',
            borderRadius: '50%',
            background: isFailed ? '#FF525220' : isCompleted ? `${config.color}20` : '#30363d',
            border: `2px solid ${isFailed ? '#FF5252' : isCompleted ? config.color : '#606770'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <Icon size={16} color={isFailed ? '#FF5252' : isCompleted ? config.color : '#606770'} />
          </div>
          {!isLast && (
            <div style={{
              width: '2px',
              height: '24px',
              background: isCompleted ? config.color : '#30363d',
              marginTop: '4px'
            }} />
          )}
        </div>

        {/* Stage Details */}
        <div style={{ flex: 1, paddingTop: '6px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ color: '#ffffff', fontSize: '12px', fontWeight: 600 }}>
              {config.label}
            </span>
            <span style={{ 
              color: isFailed ? '#FF5252' : isCompleted ? '#00C853' : '#8b949e',
              fontSize: '10px',
              fontFamily: 'monospace'
            }}>
              {stage.latency ? `${stage.latency}ms` : '—'}
            </span>
          </div>

          {/* Stage-specific details */}
          {stage.stage === 'DAG_NODES' && stage.nodes && (
            <div style={{ marginTop: '8px' }}>
              {stage.nodes.map((node, idx) => (
                <div 
                  key={node.id}
                  onClick={() => toggleNodeExpansion(`${stage.stage}-${node.id}`)}
                  style={{
                    background: '#0d1117',
                    border: `1px solid ${node.pass ? '#30363d' : '#FF525240'}`,
                    borderRadius: '4px',
                    padding: '8px',
                    marginBottom: '4px',
                    cursor: 'pointer'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <Cpu size={12} color={node.pass ? '#00C853' : '#FF5252'} />
                      <span style={{ color: '#8b949e', fontSize: '10px' }}>{node.id}</span>
                      <span style={{ 
                        color: node.pass ? '#00C853' : '#FF5252',
                        fontSize: '9px',
                        padding: '2px 6px',
                        background: node.pass ? '#00C85320' : '#FF525220',
                        borderRadius: '4px'
                      }}>
                        {node.pass ? 'PASS' : 'FAIL'}
                      </span>
                    </div>
                    {expandedNodes.has(`${stage.stage}-${node.id}`) ? 
                      <ChevronDown size={12} color="#8b949e" /> : 
                      <ChevronRight size={12} color="#8b949e" />
                    }
                  </div>

                  {expandedNodes.has(`${stage.stage}-${node.id}`) && (
                    <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #30363d' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px', fontSize: '10px' }}>
                        <div><span style={{ color: '#6b7280' }}>Type:</span> <span style={{ color: '#c9d1d9' }}>{node.type}</span></div>
                        <div><span style={{ color: '#6b7280' }}>Exec:</span> <span style={{ color: '#c9d1d9' }}>{node.execTime}ms</span></div>
                        <div><span style={{ color: '#6b7280' }}>Input:</span> <span style={{ color: '#c9d1d9' }}>{typeof node.input === 'number' ? node.input.toFixed(4) : String(node.input)}</span></div>
                        <div><span style={{ color: '#6b7280' }}>Output:</span> <span style={{ color: '#c9d1d9' }}>{typeof node.output === 'number' ? node.output.toFixed(4) : String(node.output)}</span></div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {stage.stage === 'ML_INFERENCE' && (
            <div style={{ marginTop: '8px', fontSize: '10px', color: '#8b949e' }}>
              <div>Confidence: <span style={{ color: '#9C27B0', fontWeight: 700 }}>{(stage.confidence * 100).toFixed(1)}%</span></div>
              <div style={{ marginTop: '4px' }}>Model: {stage.model}</div>
            </div>
          )}

          {stage.stage === 'RISK_VALIDATION' && stage.checks && (
            <div style={{ marginTop: '8px' }}>
              {stage.checks.map((check, idx) => (
                <div key={idx} style={{ 
                  display: 'flex', 
                  alignItems: 'center', 
                  justifyContent: 'space-between',
                  fontSize: '10px',
                  marginBottom: '4px',
                  padding: '4px 8px',
                  background: check.pass ? '#00C85310' : '#FF525210',
                  borderRadius: '4px'
                }}>
                  <span style={{ color: check.pass ? '#00C853' : '#FF5252' }}>
                    {check.name}: {check.value} / {check.limit}
                  </span>
                  {check.pass ? <CheckCircle size={10} color="#00C853" /> : <Ban size={10} color="#FF5252" />}
                </div>
              ))}
              {stage.blocked && (
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
                  {stage.reason}
                </div>
              )}
            </div>
          )}

          {stage.stage === 'EXECUTION' && stage.error && (
            <div style={{ 
              marginTop: '8px', 
              padding: '8px', 
              background: '#FF525220', 
              border: '1px solid #FF525240',
              borderRadius: '4px',
              color: '#FF5252',
              fontSize: '11px'
            }}>
              <XCircle size={12} style={{ marginRight: '6px', display: 'inline' }} />
              {stage.error}
            </div>
          )}

          {stage.stage === 'EXCHANGE' && stage.response && (
            <div style={{ marginTop: '8px', fontSize: '10px', color: '#8b949e' }}>
              <div>Filled: <span style={{ color: '#00C853' }}>{stage.response.filled}</span></div>
              <div>Price: <span style={{ color: '#00C853' }}>${stage.response.price.toFixed(2)}</span></div>
              <div>Fee: <span style={{ color: '#FFAB00' }}>{stage.response.fee}</span></div>
            </div>
          )}
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: SIGNAL STATE BADGE
  // ═══════════════════════════════════════════════════════════════════

  const renderStateBadge = (state) => {
    const config = SIGNAL_STATES[state] || SIGNAL_STATES.RECEIVED;
    const Icon = config.icon;
    
    return (
      <span style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '4px 8px',
        background: config.bgColor,
        border: `1px solid ${config.color}40`,
        borderRadius: '4px',
        color: config.color,
        fontSize: '10px',
        fontWeight: 700
      }}>
        <Icon size={12} />
        {config.label}
      </span>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: ERROR PANEL
  // ═══════════════════════════════════════════════════════════════════

  const renderErrorPanel = (error) => {
    if (!error) return null;
    
    const errorConfig = ERROR_TYPES[error.type] || ERROR_TYPES.INVALID_SIGNAL;
    const Icon = errorConfig.icon;
    
    return (
      <div style={{
        marginTop: '16px',
        padding: '12px',
        background: '#FF525215',
        border: '1px solid #FF525240',
        borderRadius: '8px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
          <Icon size={16} color="#FF5252" />
          <span style={{ color: '#FF5252', fontSize: '12px', fontWeight: 700 }}>
            {errorConfig.label}
          </span>
          {error.recoverable !== undefined && (
            <span style={{
              marginLeft: 'auto',
              padding: '2px 6px',
              background: error.recoverable ? '#00C85320' : '#FF525220',
              color: error.recoverable ? '#00C853' : '#FF5252',
              fontSize: '9px',
              borderRadius: '4px'
            }}>
              {error.recoverable ? 'Recoverable' : 'Non-recoverable'}
            </span>
          )}
        </div>
        <div style={{ color: '#ff6b81', fontSize: '11px' }}>
          {error.message}
        </div>
        {error.stage && (
          <div style={{ marginTop: '8px', fontSize: '10px', color: '#8b949e' }}>
            Failed at: {PIPELINE_STAGES[error.stage]?.label || error.stage}
          </div>
        )}
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  //  RENDER: TRACE LIST ITEM
  // ═══════════════════════════════════════════════════════════════════

  const renderTraceItem = (trace) => {
    const signalConfig = SIGNAL_TYPES[trace.signalType] || SIGNAL_TYPES.HOLD;
    const SignalIcon = signalConfig.icon;
    const isSelected = selectedTrace?.id === trace.id;
    const isNew = trace === traces[0];

    return (
      <div
        key={trace.id}
        style={{
          background: isSelected ? '#161b22' : '#0d1117',
          border: `1px solid ${isSelected ? '#00d4ff' : trace.success ? '#30363d' : '#FF525240'}`,
          borderLeft: `3px solid ${trace.success ? signalConfig.color : '#FF5252'}`,
          borderRadius: '8px',
          marginBottom: '12px',
          overflow: 'hidden',
          animation: isNew ? 'slideIn 0.3s ease-out' : 'none',
          transition: 'all 0.2s'
        }}
      >
        {/* Header */}
        <div 
          onClick={() => setSelectedTrace(isSelected ? null : trace)}
          style={{
            padding: '12px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <SignalIcon size={18} color={signalConfig.color} />
            <div>
              <div style={{ color: '#ffffff', fontSize: '13px', fontWeight: 700 }}>
                {trace.symbol}
              </div>
              <div style={{ color: '#8b949e', fontSize: '10px', marginTop: '2px' }}>
                {trace.strategy} • {formatTime(trace.timestamp)}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {renderStateBadge(trace.state)}
            <div style={{ textAlign: 'right' }}>
              <div style={{ color: '#8b949e', fontSize: '10px' }}>Latency</div>
              <div style={{ 
                color: trace.totalLatency < 500 ? '#00C853' : trace.totalLatency < 1000 ? '#FFAB00' : '#FF5252',
                fontSize: '12px',
                fontWeight: 700,
                fontFamily: 'monospace'
              }}>
                {formatDuration(trace.totalLatency)}
              </div>
            </div>
            {isSelected ? <ChevronDown size={16} color="#8b949e" /> : <ChevronRight size={16} color="#8b949e" />}
          </div>
        </div>

        {/* Expanded Details */}
        {isSelected && (
          <div style={{ 
            padding: '0 12px 12px',
            borderTop: '1px solid #30363d'
          }}>
            {/* Pipeline Flow */}
            <div style={{ marginTop: '16px' }}>
              <div style={{ 
                color: '#8b949e', 
                fontSize: '10px', 
                fontWeight: 700, 
                marginBottom: '12px',
                textTransform: 'uppercase',
                letterSpacing: '0.5px'
              }}>
                Execution Pipeline
              </div>
              {trace.pipeline.map((stage, idx) => 
                renderPipelineStage(stage, idx, trace.pipeline.length)
              )}
            </div>

            {/* Error Details */}
            {renderErrorPanel(trace.error)}

            {/* Signal ID */}
            <div style={{ 
              marginTop: '16px',
              paddingTop: '12px',
              borderTop: '1px solid #30363d',
              fontSize: '9px',
              color: '#6b7280',
              fontFamily: 'monospace'
            }}>
              Signal ID: {trace.signalId}
            </div>
          </div>
        )}
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
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ color: '#ffffff', fontSize: '20px', fontWeight: 900, marginBottom: '8px' }}>
          Signal Trace Visualization
        </h1>
        <p style={{ color: '#8b949e', fontSize: '12px' }}>
          Execution debugging and observability • {traces.length} traces
        </p>
      </div>

      {/* Controls */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        marginBottom: '20px',
        padding: '12px',
        background: '#161b22',
        border: '1px solid #30363d',
        borderRadius: '8px'
      }}>
        <div style={{ display: 'flex', gap: '8px' }}>
          {['ALL', 'SUCCESS', 'ERROR'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                background: filter === f ? '#00d4ff20' : 'transparent',
                border: `1px solid ${filter === f ? '#00d4ff' : '#30363d'}`,
                borderRadius: '4px',
                padding: '6px 12px',
                color: filter === f ? '#00d4ff' : '#8b949e',
                fontSize: '11px',
                fontWeight: 700,
                cursor: 'pointer'
              }}
            >
              {f}
            </button>
          ))}
        </div>

        <button
          onClick={() => setIsPaused(!isPaused)}
          style={{
            background: isPaused ? '#FFAB0020' : 'transparent',
            border: `1px solid ${isPaused ? '#FFAB00' : '#30363d'}`,
            borderRadius: '4px',
            padding: '6px 12px',
            color: isPaused ? '#FFAB00' : '#8b949e',
            fontSize: '11px',
            fontWeight: 700,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
        >
          {isPaused ? <Activity size={14} /> : <Radio size={14} />}
          {isPaused ? 'PAUSED' : 'LIVE'}
        </button>
      </div>

      {/* Stats Summary */}
      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: 'repeat(4, 1fr)', 
        gap: '12px',
        marginBottom: '20px'
      }}>
        <div style={{
          background: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '8px',
          padding: '12px'
        }}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Total Traces</div>
          <div style={{ color: '#ffffff', fontSize: '20px', fontWeight: 900 }}>{traces.length}</div>
        </div>
        <div style={{
          background: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '8px',
          padding: '12px'
        }}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Success Rate</div>
          <div style={{ color: '#00C853', fontSize: '20px', fontWeight: 900 }}>
            {traces.length > 0 ? ((traces.filter(t => t.success).length / traces.length) * 100).toFixed(1) : 0}%
          </div>
        </div>
        <div style={{
          background: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '8px',
          padding: '12px'
        }}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Avg Latency</div>
          <div style={{ color: '#2196F3', fontSize: '20px', fontWeight: 900 }}>
            {traces.length > 0 ? formatDuration(traces.reduce((acc, t) => acc + t.totalLatency, 0) / traces.length) : '0ms'}
          </div>
        </div>
        <div style={{
          background: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '8px',
          padding: '12px'
        }}>
          <div style={{ color: '#8b949e', fontSize: '10px', marginBottom: '4px' }}>Errors</div>
          <div style={{ color: '#FF5252', fontSize: '20px', fontWeight: 900 }}>
            {traces.filter(t => !t.success).length}
          </div>
        </div>
      </div>

      {/* Trace List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {traces
          .filter(t => {
            if (filter === 'SUCCESS') return t.success;
            if (filter === 'ERROR') return !t.success;
            return true;
          })
          .map(trace => renderTraceItem(trace))}
      </div>

      {/* Empty State */}
      {traces.length === 0 && (
        <div style={{ 
          textAlign: 'center', 
          padding: '60px',
          color: '#8b949e'
        }}>
          <Activity size={48} style={{ marginBottom: '16px', opacity: 0.5 }} />
          <p>No signal traces available</p>
          <p style={{ fontSize: '12px', marginTop: '8px' }}>
            Waiting for WebSocket connection...
          </p>
        </div>
      )}

      {/* CSS Animations */}
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

export default SignalTraceVisualization;
