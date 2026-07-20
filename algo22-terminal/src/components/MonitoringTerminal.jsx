import React, { useState, useEffect } from 'react';
import { api } from '../api'; // existing API client

const MonitoringTerminal = () => {
  const [metrics, setMetrics] = useState({
    redis: 'UNKNOWN',
    websocket: 'UNKNOWN',
    exchangeLatency: 0,
    signalLatency: 0,
    orderLatency: 0,
    replayStatus: 'UNKNOWN',
    reconciliationStatus: 'UNKNOWN',
    workerHealth: 'UNKNOWN'
  });

  const [loading, setLoading] = useState(true);

  const fetchMetrics = async () => {
    try {
      // Integrate with existing system health endpoints
      // Fallback to sensible defaults if specific endpoints aren't strictly defined yet
      const [healthRes, latencyRes, workerRes] = await Promise.allSettled([
        api.get('/health'),
        api.get('/metrics/latency'),
        api.get('/workers/status')
      ]);

      setMetrics({
        redis: healthRes.status === 'fulfilled' && healthRes.value.data.redis_status ? 'ONLINE' : 'DEGRADED',
        websocket: healthRes.status === 'fulfilled' && healthRes.value.data.ws_status ? 'CONNECTED' : 'DISCONNECTED',
        exchangeLatency: latencyRes.status === 'fulfilled' ? latencyRes.value.data.exchange_ms : 0,
        signalLatency: latencyRes.status === 'fulfilled' ? latencyRes.value.data.signal_ms : 0,
        orderLatency: latencyRes.status === 'fulfilled' ? latencyRes.value.data.order_ms : 0,
        replayStatus: workerRes.status === 'fulfilled' ? workerRes.value.data.replay_queue : 'IDLE',
        reconciliationStatus: workerRes.status === 'fulfilled' ? workerRes.value.data.reconciliation : 'SYNCED',
        workerHealth: workerRes.status === 'fulfilled' ? workerRes.value.data.status : 'ONLINE'
      });
    } catch (err) {
      console.error('Failed to fetch telemetry', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 2000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div style={containerStyle}>Initializing Telemetry...</div>;

  return (
    <div style={containerStyle}>
      <div style={headerStyle}>MONITORING TERMINAL</div>
      
      <div style={gridStyle}>
        
        {/* INFRASTRUCTURE HEALTH */}
        <div style={sectionStyle}>
          <h3 style={titleStyle}>Infrastructure Health</h3>
          <StatRow label="Redis Engine" value={metrics.redis} isGood={metrics.redis === 'ONLINE'} />
          <StatRow label="WebSocket Gateway" value={metrics.websocket} isGood={metrics.websocket === 'CONNECTED'} />
          <StatRow label="Worker Nodes" value={metrics.workerHealth} isGood={metrics.workerHealth === 'ONLINE'} />
        </div>

        {/* LATENCY METRICS */}
        <div style={sectionStyle}>
          <h3 style={titleStyle}>Execution Latency</h3>
          <StatRow label="Exchange API Latency" value={`${metrics.exchangeLatency}ms`} isGood={metrics.exchangeLatency < 100} />
          <StatRow label="Signal Processing" value={`${metrics.signalLatency}ms`} isGood={metrics.signalLatency < 50} />
          <StatRow label="Order Routing" value={`${metrics.orderLatency}ms`} isGood={metrics.orderLatency < 50} />
        </div>

        {/* STATE ENGINE */}
        <div style={sectionStyle}>
          <h3 style={titleStyle}>State Engine</h3>
          <StatRow label="Replay Queue Status" value={metrics.replayStatus} isGood={metrics.replayStatus === 'IDLE'} />
          <StatRow label="Reconciliation State" value={metrics.reconciliationStatus} isGood={metrics.reconciliationStatus === 'SYNCED'} />
        </div>
      </div>
    </div>
  );
};

const StatRow = ({ label, value, isGood }) => (
  <div style={rowStyle}>
    <span>{label}</span>
    <span style={{ color: isGood ? '#00ff00' : '#ff4444', fontWeight: 'bold' }}>{value}</span>
  </div>
);

const containerStyle = {
  background: '#0a0a0a',
  color: '#fff',
  fontFamily: 'JetBrains Mono, monospace',
  padding: '16px',
  height: '100%',
  boxSizing: 'border-box'
};

const headerStyle = {
  borderBottom: '1px solid #333',
  paddingBottom: '12px',
  marginBottom: '16px',
  fontSize: '18px',
  color: '#00ff00'
};

const gridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(3, 1fr)',
  gap: '16px'
};

const sectionStyle = {
  background: '#151515',
  border: '1px solid #222',
  padding: '16px',
  borderRadius: '4px'
};

const titleStyle = {
  marginTop: 0,
  marginBottom: '16px',
  fontSize: '14px',
  color: '#888',
  textTransform: 'uppercase'
};

const rowStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  padding: '8px 0',
  borderBottom: '1px solid #222',
  fontSize: '12px'
};

export default MonitoringTerminal;
