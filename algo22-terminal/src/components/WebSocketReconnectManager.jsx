/**
 * WebSocket Reconnect Manager
 * 
 * Manages WebSocket connection lifecycle with automatic reconnect
 * and event replay synchronization.
 * 
 * Features:
 * - Connection state monitoring
 * - Automatic reconnect with exponential backoff
 * - Replay synchronization after reconnect
 * - Connection health indicators
 * - Manual reconnect trigger
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { WSClientWithReplay } from '../utils/wsClientWithReplay';
import WS_CHANNELS from '../constants/wsChannels';

// Connection states
const ConnectionState = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  RECONNECTING: 'reconnecting',
  ERROR: 'error'
};

export function useWebSocketWithReplay(wsUrl, token, options = {}) {
  const [connectionState, setConnectionState] = useState(ConnectionState.DISCONNECTED);
  const [lastEventTimestamps, setLastEventTimestamps] = useState({});
  const [replayStatus, setReplayStatus] = useState({});
  const [connectionStats, setConnectionStats] = useState({
    connectTime: null,
    disconnectTime: null,
    reconnectCount: 0,
    eventsReceived: 0,
    eventsReplayed: 0
  });
  
  const wsClientRef = useRef(null);
  const isMountedRef = useRef(true);
  
  // Initialize WebSocket client
  useEffect(() => {
    if (!wsUrl || !token) return;
    
    const client = new WSClientWithReplay({
      maxReconnectAttempts: options.maxReconnectAttempts || 10,
      reconnectDelay: options.reconnectDelay || 1000,
      replayWindowMs: options.replayWindowMs || 300000,
      ...options
    });
    
    wsClientRef.current = client;
    
    // Set up event handlers
    client.onConnect(() => {
      if (!isMountedRef.current) return;
      
      setConnectionState(ConnectionState.CONNECTED);
      setConnectionStats(prev => ({
        ...prev,
        connectTime: Date.now(),
        reconnectCount: 0
      }));
      
      // Auto-subscribe to channels based on options
      if (options.autoSubscribe) {
        options.autoSubscribe.forEach(channel => {
          client.subscribe(channel, (message) => {
            if (!isMountedRef.current) return;
            
            // Track event timestamp
            setLastEventTimestamps(prev => ({
              ...prev,
              [channel]: message.timestamp
            }));
            
            // Forward to callback
            if (options.onMessage) {
              options.onMessage(channel, message);
            }
            
            // Update stats
            setConnectionStats(prev => ({
              ...prev,
              eventsReceived: prev.eventsReceived + 1
            }));
          });
        });
      }
      
      if (options.onConnect) {
        options.onConnect();
      }
    });
    
    client.onDisconnect((event) => {
      if (!isMountedRef.current) return;
      
      setConnectionState(ConnectionState.DISCONNECTED);
      setConnectionStats(prev => ({
        ...prev,
        disconnectTime: Date.now(),
        reconnectCount: prev.reconnectCount + 1
      }));
      
      if (options.onDisconnect) {
        options.onDisconnect(event);
      }
    });
    
    client.onError((error) => {
      if (!isMountedRef.current) return;
      
      setConnectionState(ConnectionState.ERROR);
      
      if (options.onError) {
        options.onError(error);
      }
    });
    
    client.onReplayComplete((channel, count) => {
      if (!isMountedRef.current) return;
      
      setReplayStatus(prev => ({
        ...prev,
        [channel]: {
          status: 'complete',
          eventsReplayed: count,
          timestamp: Date.now()
        }
      }));
      
      setConnectionStats(prev => ({
        ...prev,
        eventsReplayed: prev.eventsReplayed + count
      }));
      
      if (options.onReplayComplete) {
        options.onReplayComplete(channel, count);
      }
    });
    
    // Connect
    setConnectionState(ConnectionState.CONNECTING);
    client.connect(wsUrl, token).catch(err => {
      if (!isMountedRef.current) return;
      console.error('[WSManager] Initial connection failed:', err);
      setConnectionState(ConnectionState.ERROR);
    });
    
    return () => {
      isMountedRef.current = false;
      client.disconnect();
    };
  }, [wsUrl, token]);
  
  // Manual reconnect
  const reconnect = useCallback(() => {
    if (wsClientRef.current) {
      setConnectionState(ConnectionState.RECONNECTING);
      wsClientRef.current.disconnect();
      
      setTimeout(() => {
        wsClientRef.current?.connect().catch(err => {
          console.error('[WSManager] Manual reconnect failed:', err);
        });
      }, 1000);
    }
  }, []);
  
  // Request replay for a channel
  const requestReplay = useCallback((channel) => {
    if (wsClientRef.current) {
      wsClientRef.current.requestReplay(channel);
      
      setReplayStatus(prev => ({
        ...prev,
        [channel]: { status: 'requested', timestamp: Date.now() }
      }));
    }
  }, []);
  
  // Subscribe to a channel
  const subscribe = useCallback((channel, callback) => {
    if (wsClientRef.current) {
      return wsClientRef.current.subscribe(channel, callback);
    }
    return () => {};
  }, []);
  
  // Get connection state indicator
  const getConnectionIndicator = useCallback(() => {
    switch (connectionState) {
      case ConnectionState.CONNECTED:
        return { color: '#00C853', text: 'Connected', icon: '●' };
      case ConnectionState.CONNECTING:
        return { color: '#FFAB00', text: 'Connecting...', icon: '◐' };
      case ConnectionState.RECONNECTING:
        return { color: '#FFAB00', text: 'Reconnecting...', icon: '↻' };
      case ConnectionState.ERROR:
        return { color: '#FF5252', text: 'Error', icon: '✕' };
      default:
        return { color: '#9E9E9E', text: 'Disconnected', icon: '○' };
    }
  }, [connectionState]);
  
  return {
    connectionState,
    lastEventTimestamps,
    replayStatus,
    connectionStats,
    reconnect,
    requestReplay,
    subscribe,
    getConnectionIndicator,
    client: wsClientRef.current
  };
}

/**
 * Reconnect Manager UI Component
 */
export function WebSocketReconnectManager({ 
  wsUrl, 
  token, 
  channels = [],
  onMessage,
  children 
}) {
  const {
    connectionState,
    lastEventTimestamps,
    replayStatus,
    connectionStats,
    reconnect,
    getConnectionIndicator
  } = useWebSocketWithReplay(wsUrl, token, {
    autoSubscribe: channels,
    onMessage
  });
  
  const indicator = getConnectionIndicator();
  
  return (
    <div>
      {/* Connection status bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
        padding: '8px 16px',
        background: '#161b22',
        borderBottom: '1px solid #30363d',
        fontFamily: 'monospace',
        fontSize: '12px'
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          color: indicator.color
        }}>
          <span style={{ fontSize: '10px' }}>{indicator.icon}</span>
          <span>{indicator.text}</span>
        </div>
        
        {/* Show replay status if any pending */}
        {Object.entries(replayStatus).map(([channel, status]) => {
          if (status.status === 'requested') {
            return (
              <span key={channel} style={{ color: '#8b949e', fontSize: '10px' }}>
                Replaying {channel}...
              </span>
            );
          }
          if (status.status === 'complete' && status.eventsReplayed > 0) {
            return (
              <span key={channel} style={{ color: '#00C853', fontSize: '10px' }}>
                ✓ {channel}: {status.eventsReplayed} events
              </span>
            );
          }
          return null;
        })}
        
        {/* Reconnect button if disconnected/error */}
        {(connectionState === 'disconnected' || connectionState === 'error') && (
          <button
            onClick={reconnect}
            style={{
              marginLeft: 'auto',
              background: 'transparent',
              border: '1px solid #30363d',
              borderRadius: '4px',
              padding: '4px 8px',
              color: '#8b949e',
              fontSize: '10px',
              cursor: 'pointer'
            }}
          >
            Reconnect
          </button>
        )}
        
        {/* Stats */}
        <span style={{ marginLeft: 'auto', color: '#6b7280', fontSize: '10px' }}>
          Events: {connectionStats.eventsReceived} | 
          Replay: {connectionStats.eventsReplayed} |
          Reconnects: {connectionStats.reconnectCount}
        </span>
      </div>
      
      {/* Child components */}
      {children}
    </div>
  );
}

export default WebSocketReconnectManager;
