/**
 * ═══════════════════════════════════════════════════════════════════════════
 * EVENT-DRIVEN DAG HOOK
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * React hook for managing event-driven DAG execution.
 * 
 * Features:
 * - Start/stop event-driven sessions
 * - Stream signals via WebSocket
 * - Monitor execution statistics
 * - Real-time signal visualization
 * 
 * Connects to: /api/strategies/events/*
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/typed-client';
import wsClient from '../websocketClient';

/**
 * Hook for event-driven DAG execution
 * 
 * @param {Object} options - Configuration options
 * @param {boolean} options.autoStart - Auto-start on mount
 * @param {Function} options.onSignal - Callback for new signals
 * @param {Function} options.onStats - Callback for stats updates
 * @returns {Object} Event-driven DAG controls and state
 */
export function useEventDag(options = {}) {
  const { autoStart = false, onSignal, onStats } = options;
  
  // ── State ──────────────────────────────────────────────────────────────
  const [sessionId, setSessionId] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({
    events_processed: 0,
    signals_emitted: 0,
    runtime_seconds: 0,
    events_per_second: 0,
    queue_size: 0
  });
  const [signals, setSignals] = useState([]);
  const [latestSignal, setLatestSignal] = useState(null);
  
  // Refs for WebSocket and polling
  const wsRef = useRef(null);
  const pollIntervalRef = useRef(null);
  const signalsRef = useRef([]);
  
  // ── Start Event-Driven Session ────────────────────────────────────────
  const start = useCallback(async (config) => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await api.post('/api/strategies/events/start', {
        dag_nodes: config.nodes,
        dag_edges: config.edges,
        symbols: config.symbols,
        timeframe: config.timeframe || '1m',
        max_rolling_window: config.maxWindow || 500,
        simulation_mode: config.simulation !== false, // default true
        simulation_speed: config.speed || 1.0
      });
      
      if (response.error) {
        throw new Error(response.error);
      }
      
      setSessionId(response.session_id);
      setIsRunning(true);
      
      // Connect WebSocket for real-time signals
      connectWebSocket(response.session_id);
      
      // Start polling for stats
      startStatsPolling(response.session_id);
      
      return response.session_id;
      
    } catch (err) {
      setError(err.message);
      setIsRunning(false);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);
  
  // ── Stop Event-Driven Session ─────────────────────────────────────────
  const stop = useCallback(async () => {
    if (!sessionId) return;
    
    try {
      setIsLoading(true);
      
      // Stop polling
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      
      // Disconnect WebSocket
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      
      // Stop session on server
      await api.post(`/api/strategies/events/stop/${sessionId}`);
      
      setIsRunning(false);
      setSessionId(null);
      
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);
  
  // ── Connect WebSocket ────────────────────────────────────────────────
  const connectWebSocket = useCallback((sid) => {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/strategies/events/ws/${sid}`;
    
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    
    ws.onopen = () => {
      console.log('Event-DAG WebSocket connected');
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'signal') {
        const signal = data.data;
        
        // Update signals list
        signalsRef.current = [signal, ...signalsRef.current].slice(0, 100);
        setSignals(signalsRef.current);
        setLatestSignal(signal);
        
        // Call callback if provided
        if (onSignal) {
          onSignal(signal);
        }
      } else if (data.type === 'heartbeat' && data.stats) {
        setStats(data.stats);
        
        if (onStats) {
          onStats(data.stats);
        }
      }
    };
    
    ws.onerror = (error) => {
      console.error('Event-DAG WebSocket error:', error);
      setError('WebSocket connection error');
    };
    
    ws.onclose = () => {
      console.log('Event-DAG WebSocket disconnected');
    };
  }, [onSignal, onStats]);
  
  // ── Stats Polling (Fallback) ───────────────────────────────────────────
  const startStatsPolling = useCallback((sid) => {
    // Poll every 2 seconds for stats
    pollIntervalRef.current = setInterval(async () => {
      try {
        const status = await api.get(`/api/strategies/events/status/${sid}`);
        if (status && !status.error) {
          setStats(status);
          
          if (onStats) {
            onStats(status);
          }
        }
      } catch (err) {
        console.error('Stats polling error:', err);
      }
    }, 2000);
  }, [onStats]);
  
  // ── Fetch Signals (Manual) ───────────────────────────────────────────
  const fetchSignals = useCallback(async (limit = 50) => {
    if (!sessionId) return [];
    
    try {
      const response = await api.get(`/api/strategies/events/signals/${sessionId}?limit=${limit}`);
      if (response && !response.error) {
        signalsRef.current = response.signals;
        setSignals(response.signals);
        return response.signals;
      }
    } catch (err) {
      console.error('Fetch signals error:', err);
    }
    return [];
  }, [sessionId]);
  
  // ── Clear Signals ────────────────────────────────────────────────────
  const clearSignals = useCallback(() => {
    signalsRef.current = [];
    setSignals([]);
    setLatestSignal(null);
  }, []);
  
  // ── Auto-start ───────────────────────────────────────────────────────
  useEffect(() => {
    if (autoStart && !isRunning && !sessionId) {
      // User must provide config via start()
      console.warn('Auto-start enabled but no config provided. Call start(config) manually.');
    }
    
    return () => {
      // Cleanup on unmount
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (sessionId && isRunning) {
        // Stop session on server
        api.post(`/api/strategies/events/stop/${sessionId}`).catch(console.error);
      }
    };
  }, [autoStart, isRunning, sessionId]);
  
  // ── Return ────────────────────────────────────────────────────────────
  return {
    // State
    sessionId,
    isRunning,
    isLoading,
    error,
    stats,
    signals,
    latestSignal,
    
    // Actions
    start,
    stop,
    fetchSignals,
    clearSignals
  };
}

/**
 * Hook for real-time signal streaming (simplified)
 * 
 * @param {string} sessionId - Event-DAG session ID
 * @returns {Object} Signal stream state
 */
export function useSignalStream(sessionId) {
  const [signals, setSignals] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  
  useEffect(() => {
    if (!sessionId) return;
    
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/strategies/events/ws/${sessionId}`;
    
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    
    ws.onopen = () => setConnected(true);
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'signal') {
        setSignals(prev => [data.data, ...prev].slice(0, 50));
      }
    };
    
    ws.onclose = () => setConnected(false);
    
    return () => {
      ws.close();
    };
  }, [sessionId]);
  
  return { signals, connected };
}

export default useEventDag;
