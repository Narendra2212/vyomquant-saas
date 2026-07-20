import React, { useState, useEffect, useCallback, useRef } from 'react';

/**
 * 🔴 STEP 9 — EVENT LOG PANEL
 * 
 * Comprehensive system event logging and monitoring
 * 
 * Features:
 * - Real-time event streaming
 * - Severity-based filtering
 * - Strategy-specific filtering
 * - Event search
 * - Export capability
 */

// Event severity levels
const SEVERITY_LEVELS = {
  DEBUG: { color: '#9E9E9E', bgColor: '#9E9E9E15', level: 0 },
  INFO: { color: '#58a6ff', bgColor: '#58a6ff15', level: 1 },
  WARN: { color: '#FFAB00', bgColor: '#FFAB0015', level: 2 },
  ERROR: { color: '#FF5252', bgColor: '#FF525215', level: 3 },
  CRITICAL: { color: '#FF1744', bgColor: '#FF174415', level: 4 }
};

// Event type configs
const EVENT_TYPES = {
  STRATEGY: { icon: '🎯', label: 'Strategy' },
  SIGNAL: { icon: '⚡', label: 'Signal' },
  ORDER: { icon: '📋', label: 'Order' },
  EXECUTION: { icon: '✅', label: 'Execution' },
  ERROR: { icon: '❌', label: 'Error' },
  SYSTEM: { icon: '🔧', label: 'System' },
  RISK: { icon: '⚠️', label: 'Risk' },
  WEBSOCKET: { icon: '🌐', label: 'WebSocket' },
  API: { icon: '🔌', label: 'API' }
};

const EventLogPanel = ({
  wsClient,
  maxEvents = 500,
  showFilters = true,
  defaultMinSeverity = 'INFO'
}) => {
  const [events, setEvents] = useState([]);
  const [filteredEvents, setFilteredEvents] = useState([]);
  const [minSeverity, setMinSeverity] = useState(defaultMinSeverity);
  const [eventTypeFilter, setEventTypeFilter] = useState('ALL');
  const [strategyFilter, setStrategyFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [isPaused, setIsPaused] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [stats, setStats] = useState({
    total: 0,
    errors: 0,
    warnings: 0,
    lastMinute: 0
  });
  
  const eventBuffer = useRef([]);
  const scrollRef = useRef(null);

  // 🔴 STEP 9: Format timestamp
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

  // 🔴 STEP 9: Add event to log
  const addEvent = useCallback((event) => {
    const enrichedEvent = {
      ...event,
      id: crypto.randomUUID(), // CSPRNG — Math.random() is not cryptographically secure
      timestamp: event.timestamp || Date.now(),
      severity: event.severity || 'INFO',
      type: event.type || 'SYSTEM'
    };

    eventBuffer.current.unshift(enrichedEvent);
    
    // Keep only max events
    if (eventBuffer.current.length > maxEvents) {
      eventBuffer.current = eventBuffer.current.slice(0, maxEvents);
    }

    if (!isPaused) {
      setEvents([...eventBuffer.current]);
    }
  }, [isPaused, maxEvents]);

  // 🔴 STEP 9: Process WebSocket messages for events
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Strategy events
        if (data.type?.includes('strategy')) {
          addEvent({
            type: 'STRATEGY',
            severity: data.severity || (data.status === 'ERROR' ? 'ERROR' : 'INFO'),
            message: `Strategy ${data.strategy_name || data.strategy_id}: ${data.message || data.status}`,
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // Signal events
        if (data.type === 'signal' || data.type === 'strategy_signal') {
          addEvent({
            type: 'SIGNAL',
            severity: 'INFO',
            message: `Signal: ${data.signal} from ${data.source} (${data.symbol})`,
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // Order events
        if (data.type?.includes('order')) {
          const severity = data.type === 'order_failed' ? 'ERROR' : 
                          data.type === 'order_canceled' ? 'WARN' : 'INFO';
          // 🔴 STEP 7: Extract order ID from multiple possible locations
          const orderId = data?.execution_id || data?.result?.order_id || data?.order_id;
          addEvent({
            type: 'ORDER',
            severity,
            message: `Order #${orderId?.slice(-6)}: ${data.type.replace('order_', '')}`,
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // Execution events
        if (data.type === 'order_filled' || data.type === 'execution_result') {
          addEvent({
            type: 'EXECUTION',
            severity: 'INFO',
            message: `Executed ${data.side} ${data.symbol} @ $${data.price}`,
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // Error events
        if (data.type?.includes('error') || data.error) {
          addEvent({
            type: 'ERROR',
            severity: 'ERROR',
            message: data.error || data.message || 'System error occurred',
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // Risk events
        if (data.type?.includes('risk') || data.type?.includes('drawdown') || data.type?.includes('kill')) {
          addEvent({
            type: 'RISK',
            severity: data.severity || 'WARN',
            message: data.message || `Risk event: ${data.type}`,
            strategyId: data.strategy_id,
            metadata: data
          });
        }

        // WebSocket events
        if (data.type?.includes('websocket') || data.type?.includes('connection')) {
          addEvent({
            type: 'WEBSOCKET',
            severity: data.status === 'disconnected' ? 'WARN' : 'INFO',
            message: `WebSocket: ${data.status || data.message}`,
            metadata: data
          });
        }

        // System events
        if (data.type === 'system' || data.type === 'heartbeat' || data.type === 'status') {
          addEvent({
            type: 'SYSTEM',
            severity: 'DEBUG',
            message: data.message || `System: ${data.type}`,
            metadata: data
          });
        }

      } catch (err) {
        console.error('[STEP 9] Failed to process event:', err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribeEvents = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeEvents = wsClient.subscribe('event', handleMessage);
    } else {
      console.warn('[EventLogPanel] wsClient.subscribe is not available');
    }
    
    return () => {
      if (unsubscribeEvents) unsubscribeEvents();
    };
  }, [wsClient, addEvent]);

  // 🔴 STEP 9: Filter events
  useEffect(() => {
    let filtered = events;

    // Filter by minimum severity
    const minLevel = SEVERITY_LEVELS[minSeverity]?.level || 0;
    filtered = filtered.filter(e => (SEVERITY_LEVELS[e.severity]?.level || 0) >= minLevel);

    // Filter by event type
    if (eventTypeFilter !== 'ALL') {
      filtered = filtered.filter(e => e.type === eventTypeFilter);
    }

    // Filter by strategy
    if (strategyFilter !== 'ALL') {
      filtered = filtered.filter(e => e.strategyId === strategyFilter);
    }

    // Filter by search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(e => 
        e.message?.toLowerCase().includes(query) ||
        e.type?.toLowerCase().includes(query) ||
        e.strategyId?.toLowerCase().includes(query)
      );
    }

    setFilteredEvents(filtered);
  }, [events, minSeverity, eventTypeFilter, strategyFilter, searchQuery]);

  // 🔴 STEP 9: Calculate stats
  useEffect(() => {
    const now = Date.now();
    const oneMinuteAgo = now - 60000;
    
    setStats({
      total: events.length,
      errors: events.filter(e => e.severity === 'ERROR' || e.severity === 'CRITICAL').length,
      warnings: events.filter(e => e.severity === 'WARN').length,
      lastMinute: events.filter(e => e.timestamp > oneMinuteAgo).length
    });
  }, [events]);

  // 🔴 STEP 9: Resume from pause
  useEffect(() => {
    if (!isPaused) {
      setEvents([...eventBuffer.current]);
    }
  }, [isPaused]);

  // 🔴 STEP 9: Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current && !isPaused) {
      scrollRef.current.scrollTop = 0;
    }
  }, [filteredEvents, autoScroll, isPaused]);

  // 🔴 STEP 9: Get unique strategies from events
  const strategies = [...new Set(events.filter(e => e.strategyId).map(e => e.strategyId))];

  // 🔴 STEP 9: Clear all events
  const clearEvents = () => {
    eventBuffer.current = [];
    setEvents([]);
  };

  // 🔴 STEP 9: Export events
  const exportEvents = () => {
    const data = JSON.stringify(filteredEvents, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `events_${new Date().toISOString().slice(0, 19)}.json`;
    a.click();
    URL.revokeObjectURL(url);
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
      {/* 🔴 Header with stats */}
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
            EVENT LOG PANEL
          </div>
          <div style={{
            display: 'flex',
            gap: '12px',
            marginTop: '4px'
          }}>
            <span style={{ color: '#8b949e', fontSize: '10px' }}>
              Total: {stats.total}
            </span>
            <span style={{ color: '#FF5252', fontSize: '10px' }}>
              Errors: {stats.errors}
            </span>
            <span style={{ color: '#FFAB00', fontSize: '10px' }}>
              Warnings: {stats.warnings}
            </span>
            <span style={{ color: '#58a6ff', fontSize: '10px' }}>
              Last min: {stats.lastMinute}
            </span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '8px' }}>
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
              cursor: 'pointer'
            }}
          >
            {isPaused ? '⏸ PAUSED' : '▶ LIVE'}
          </button>

          {/* Clear */}
          <button
            onClick={clearEvents}
            style={{
              backgroundColor: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#8b949e',
              fontSize: '10px',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            🗑 Clear
          </button>

          {/* Export */}
          <button
            onClick={exportEvents}
            style={{
              backgroundColor: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#8b949e',
              fontSize: '10px',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            💾 Export
          </button>
        </div>
      </div>

      {/* 🔴 Filters */}
      {showFilters && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr 1fr',
          gap: '8px',
          marginBottom: '12px'
        }}>
          {/* Search */}
          <input
            type="text"
            placeholder="Search events..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              backgroundColor: '#161b22',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#c9d1d9',
              fontSize: '11px',
              fontFamily: 'monospace',
              outline: 'none'
            }}
          />

          {/* Severity Filter */}
          <select
            value={minSeverity}
            onChange={(e) => setMinSeverity(e.target.value)}
            style={{
              backgroundColor: '#161b22',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#c9d1d9',
              fontSize: '11px',
              fontFamily: 'monospace',
              cursor: 'pointer'
            }}
          >
            {Object.keys(SEVERITY_LEVELS).map(s => (
              <option key={s} value={s}>Min: {s}</option>
            ))}
          </select>

          {/* Type Filter */}
          <select
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
            style={{
              backgroundColor: '#161b22',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#c9d1d9',
              fontSize: '11px',
              fontFamily: 'monospace',
              cursor: 'pointer'
            }}
          >
            <option value="ALL">All Types</option>
            {Object.keys(EVENT_TYPES).map(t => (
              <option key={t} value={t}>{EVENT_TYPES[t].label}</option>
            ))}
          </select>

          {/* Strategy Filter */}
          <select
            value={strategyFilter}
            onChange={(e) => setStrategyFilter(e.target.value)}
            style={{
              backgroundColor: '#161b22',
              border: '1px solid #30363d',
              borderRadius: '6px',
              padding: '6px 12px',
              color: '#c9d1d9',
              fontSize: '11px',
              fontFamily: 'monospace',
              cursor: 'pointer'
            }}
          >
            <option value="ALL">All Strategies</option>
            {strategies.map(s => (
              <option key={s} value={s}>{s.slice(0, 20)}...</option>
            ))}
          </select>
        </div>
      )}

      {/* 🔴 Event List */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '4px'
        }}
      >
        {filteredEvents.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '40px',
            color: '#8b949e',
            fontSize: '11px'
          }}>
            {events.length === 0 
              ? 'No events yet. Events will appear here as the system runs.'
              : 'No events match the current filters.'
            }
          </div>
        ) : (
          filteredEvents.map((event, index) => {
            const severityConfig = SEVERITY_LEVELS[event.severity] || SEVERITY_LEVELS.INFO;
            const typeConfig = EVENT_TYPES[event.type] || EVENT_TYPES.SYSTEM;
            const isSelected = selectedEvent?.id === event.id;
            const isNew = index === 0;

            return (
              <div
                key={event.id}
                onClick={() => setSelectedEvent(isSelected ? null : event)}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '8px',
                  padding: '8px 10px',
                  backgroundColor: isSelected ? `${severityConfig.color}10` : index % 2 === 0 ? '#161b22' : '#0d1117',
                  border: `1px solid ${isSelected ? severityConfig.color : 'transparent'}`,
                  borderLeft: `3px solid ${severityConfig.color}`,
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '11px',
                  animation: isNew && !isPaused ? 'fadeIn 0.3s ease-out' : 'none'
                }}
              >
                {/* Timestamp */}
                <div style={{
                  color: '#8b949e',
                  minWidth: '80px',
                  fontFamily: 'monospace'
                }}>
                  {formatTime(event.timestamp)}
                </div>

                {/* Severity Badge */}
                <div style={{
                  backgroundColor: severityConfig.bgColor,
                  border: `1px solid ${severityConfig.color}40`,
                  borderRadius: '3px',
                  padding: '1px 6px',
                  color: severityConfig.color,
                  fontSize: '9px',
                  fontWeight: 900,
                  minWidth: '50px',
                  textAlign: 'center'
                }}>
                  {event.severity}
                </div>

                {/* Type Icon */}
                <span style={{ fontSize: '12px' }}>{typeConfig.icon}</span>

                {/* Message */}
                <div style={{
                  color: '#c9d1d9',
                  flex: 1,
                  wordBreak: 'break-word'
                }}>
                  {event.message}
                </div>

                {/* Strategy ID (if present) */}
                {event.strategyId && (
                  <div style={{
                    color: '#58a6ff',
                    fontSize: '9px',
                    backgroundColor: '#58a6ff15',
                    padding: '2px 6px',
                    borderRadius: '3px'
                  }}>
                    {event.strategyId.slice(0, 12)}...
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* 🔴 Selected Event Details */}
      {selectedEvent && (
        <div style={{
          marginTop: '12px',
          padding: '12px',
          backgroundColor: '#161b22',
          border: '1px solid #30363d',
          borderRadius: '8px',
          fontSize: '10px'
        }}>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '8px'
          }}>
            <span style={{ color: '#8b949e', fontWeight: 700 }}>EVENT DETAILS</span>
            <button
              onClick={() => setSelectedEvent(null)}
              style={{
                background: 'none',
                border: 'none',
                color: '#8b949e',
                cursor: 'pointer',
                fontSize: '14px'
              }}
            >
              ×
            </button>
          </div>
          <pre style={{
            margin: 0,
            padding: '8px',
            backgroundColor: '#0d1117',
            borderRadius: '4px',
            color: '#c9d1d9',
            overflow: 'auto',
            maxHeight: '200px',
            fontSize: '10px'
          }}>
            {JSON.stringify(selectedEvent, null, 2)}
          </pre>
        </div>
      )}

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(-5px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 9: Hook for event logging
export const useEventLog = () => {
  const [events, setEvents] = useState([]);

  const logEvent = useCallback((event) => {
    const enrichedEvent = {
      ...event,
      id: crypto.randomUUID(), // CSPRNG — Math.random() is not cryptographically secure
      timestamp: Date.now()
    };
    setEvents(prev => [enrichedEvent, ...prev].slice(0, 500));
    return enrichedEvent.id;
  }, []);

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  return {
    events,
    logEvent,
    clearEvents
  };
};

export default EventLogPanel;
export { SEVERITY_LEVELS, EVENT_TYPES };
