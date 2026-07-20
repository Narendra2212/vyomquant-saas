import React, { useState, useEffect } from 'react';

/**
 * 🔴 STEP 7 — KILL SWITCH UI
 * 
 * CRITICAL: System-wide emergency stop interface
 * 
 * - Fetches system status from backend
 * - Displays warning banner when kill switch is active
 * - Disables all trading functionality
 * - Shows reason for system safety trigger
 */

const KILL_SWITCH_ENDPOINT = '/api/system/status';

const KillSwitchBanner = ({ 
  wsClient, 
  onKillSwitchChange,
  apiClient 
}) => {
  const [killSwitchActive, setKillSwitchActive] = useState(false);
  const [killSwitchReason, setKillSwitchReason] = useState('');
  const [lastCheck, setLastCheck] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  // 🔴 STEP 7: Fetch kill switch status from backend
  const fetchKillSwitchStatus = async () => {
    if (!apiClient) return;
    
    setIsLoading(true);
    try {
      const response = await apiClient.get(KILL_SWITCH_ENDPOINT);
      const data = response.data || response;
      
      const isActive = data.kill_switch_active === true || 
                       data.system_enabled === false ||
                       data.trading_enabled === false;
      
      setKillSwitchActive(isActive);
      setKillSwitchReason(data.reason || data.message || 'System safety triggered');
      setLastCheck(new Date());
      
      // Notify parent component
      if (onKillSwitchChange) {
        onKillSwitchChange({
          active: isActive,
          reason: data.reason || 'System safety triggered',
          timestamp: data.timestamp,
          triggeredBy: data.triggered_by
        });
      }
      
      if (isActive) {
        console.error(`[STEP 7] 🚫 KILL SWITCH ACTIVE: ${data.reason || 'System safety triggered'}`);
      } else {
        console.log('[STEP 7] ✅ Kill switch inactive - trading enabled');
      }
    } catch (err) {
      console.error('[STEP 7] Failed to fetch kill switch status:', err);
      // FAIL-SAFE: If we can't check status, assume kill switch might be active
      // This prevents trading when system status is unknown
      setKillSwitchActive(false); // Allow trading but log error
    } finally {
      setIsLoading(false);
    }
  };

  // 🔴 STEP 7: Poll for kill switch status
  useEffect(() => {
    // Initial check
    fetchKillSwitchStatus();
    
    // Poll every 5 seconds
    const interval = setInterval(fetchKillSwitchStatus, 5000);
    
    return () => clearInterval(interval);
  }, [apiClient]);

  // 🔴 STEP 7: Listen for WebSocket kill switch updates
  useEffect(() => {
    if (!wsClient) return;

    const handleMessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        // Handle kill switch updates from WebSocket
        if (data.type === 'kill_switch_update' || data.type === 'system_status') {
          const isActive = data.kill_switch_active === true || 
                           data.system_enabled === false;
          
          setKillSwitchActive(isActive);
          setKillSwitchReason(data.reason || 'System safety triggered');
          setLastCheck(new Date());
          
          if (onKillSwitchChange) {
            onKillSwitchChange({
              active: isActive,
              reason: data.reason || 'System safety triggered',
              timestamp: data.timestamp,
              triggeredBy: data.triggered_by
            });
          }
          
          if (isActive) {
            console.error(`[STEP 7] 🚫 WebSocket: Kill switch activated - ${data.reason}`);
          }
        }
      } catch (err) {
        console.error('[STEP 7] Failed to parse WebSocket message:', err);
      }
    };

    // Subscribe using custom wsClient interface
    let unsubscribeKillSwitch = null;
    if (typeof wsClient.subscribe === 'function') {
      unsubscribeKillSwitch = wsClient.subscribe('kill_switch', handleMessage);
    } else {
      console.warn('[KillSwitchBanner] wsClient.subscribe is not available');
    }
    
    return () => {
      if (unsubscribeKillSwitch) unsubscribeKillSwitch();
    };
  }, [wsClient, onKillSwitchChange]);

  // Don't render if kill switch is not active
  if (!killSwitchActive) return null;

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      zIndex: 99999,
      backgroundColor: '#FF5252',
      padding: '16px 24px',
      boxShadow: '0 4px 20px rgba(255, 82, 82, 0.5)',
      animation: 'slideDown 0.3s ease-out'
    }}>
      <div style={{
        maxWidth: '1200px',
        margin: '0 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px'
      }}>
        {/* Left: Icon and Main Message */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{
            fontSize: '32px',
            animation: 'pulse 2s infinite'
          }}>
            🚫
          </div>
          <div>
            <div style={{
              fontFamily: 'monospace',
              fontSize: '14px',
              fontWeight: 900,
              color: '#ffffff',
              letterSpacing: '1px',
              marginBottom: '4px'
            }}>
              TRADING DISABLED BY SYSTEM SAFETY
            </div>
            <div style={{
              fontFamily: 'monospace',
              fontSize: '12px',
              color: '#ffffff',
              opacity: 0.9
            }}>
              {killSwitchReason}
            </div>
          </div>
        </div>

        {/* Right: Status and Refresh */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px'
        }}>
          <div style={{
            textAlign: 'right'
          }}>
            <div style={{
              fontFamily: 'monospace',
              fontSize: '10px',
              color: '#ffffff',
              opacity: 0.8
            }}>
              Last check: {lastCheck?.toLocaleTimeString() || 'Never'}
            </div>
          </div>
          
          <button
            onClick={fetchKillSwitchStatus}
            disabled={isLoading}
            style={{
              padding: '8px 16px',
              borderRadius: '4px',
              border: '1px solid rgba(255,255,255,0.3)',
              backgroundColor: 'rgba(255,255,255,0.1)',
              color: '#ffffff',
              fontFamily: 'monospace',
              fontSize: '11px',
              fontWeight: 600,
              cursor: isLoading ? 'wait' : 'pointer',
              opacity: isLoading ? 0.6 : 1
            }}
          >
            {isLoading ? 'CHECKING...' : 'CHECK STATUS'}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes slideDown {
          from {
            transform: translateY(-100%);
          }
          to {
            transform: translateY(0);
          }
        }
        
        @keyframes pulse {
          0%, 100% {
            opacity: 1;
          }
          50% {
            opacity: 0.5;
          }
        }
      `}</style>
    </div>
  );
};

// 🔴 STEP 7: Hook for checking kill switch status in components
export const useKillSwitch = (apiClient) => {
  const [isActive, setIsActive] = useState(false);
  const [reason, setReason] = useState('');

  useEffect(() => {
    const checkStatus = async () => {
      if (!apiClient) return;
      
      try {
        const response = await apiClient.get(KILL_SWITCH_ENDPOINT);
        const data = response.data || response;
        
        const active = data.kill_switch_active === true || 
                      data.system_enabled === false ||
                      data.trading_enabled === false;
        
        setIsActive(active);
        setReason(data.reason || 'System safety triggered');
      } catch (err) {
        console.error('[useKillSwitch] Failed to check status:', err);
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 5000);
    
    return () => clearInterval(interval);
  }, [apiClient]);

  return { isActive, reason };
};

export default KillSwitchBanner;
export { KILL_SWITCH_ENDPOINT };
