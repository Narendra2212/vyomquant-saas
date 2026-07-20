/**
 * ═══════════════════════════════════════════════════════════════════════════
 * NOTIFICATIONS PAGE
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Complete notifications settings page with:
 * - Fetch settings from API
 * - Toggle notification types
 * - Real-time updates via WebSocket
 * - UI for alert management
 * 
 * Connects to: api.notifications.*
 */

import React, { useState, useEffect, useCallback } from 'react';
import { 
  Bell, 
  Mail, 
  Smartphone, 
  TrendingUp, 
  AlertTriangle, 
  Shield,
  Volume2,
  Save,
  Check,
  X,
  Loader2,
  MessageSquare
} from 'lucide-react';
import { api } from '../api/typed-client';
import wsClient from '../websocketClient';
import ActionableEmptyState from './ActionableEmptyState';
import { useAppState } from '../AppState';

// ═══════════════════════════════════════════════════════════════════════════
// COLOR PALETTE (matches App.jsx)
// ═══════════════════════════════════════════════════════════════════════════
const C = {
  bg: "#0a0a0a",
  bg2: "#0f1115",
  bg3: "#13161c",
  border: "#1f2937",
  t1: "#f1f5f9",
  t2: "#94a3b8",
  t3: "#64748b",
  accent: "#00d4ff",
  cyan: "#00d4ff",
  green: "#00ff88",
  red: "#ff3366",
  orange: "#ff9500",
  purple: "#8b5cf6",
};

// ═══════════════════════════════════════════════════════════════════════════
// NOTIFICATION SETTINGS COMPONENT
// ═══════════════════════════════════════════════════════════════════════════
function NotificationsPage() {
  const appState = useAppState();
  const demoMode = appState?.demoMode || false;

  // ── State ───────────────────────────────────────────────────────────────
  const [settings, setSettings] = useState({
    email_enabled: true,
    push_enabled: true,
    trade_alerts: true,
    price_alerts: true,
    security_alerts: true,
    marketing_emails: false,
  });
  
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [realTimeAlerts, setRealTimeAlerts] = useState([]);
  const [wsConnected, setWsConnected] = useState(false);

  // ── Fetch Settings ──────────────────────────────────────────────────────
  const fetchSettings = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      
      const data = await api.notifications.getSettings();
      
      setSettings({
        email_enabled: data.email_enabled ?? true,
        push_enabled: data.push_enabled ?? true,
        trade_alerts: data.trade_alerts ?? true,
        price_alerts: data.price_alerts ?? true,
        security_alerts: data.security_alerts ?? true,
        marketing_emails: data.marketing_emails ?? false,
      });
    } catch (err) {
      console.error('Failed to fetch notification settings:', err);
      setError('Failed to load notification settings. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Save Settings ───────────────────────────────────────────────────────
  const saveSettings = async () => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      
      await api.notifications.updateSettings(settings);
      
      setSuccess('Notification settings saved successfully');
      
      // Clear success after 3 seconds
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      console.error('Failed to save notification settings:', err);
      setError('Failed to save settings. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  // ── Toggle Handler ──────────────────────────────────────────────────────
  const toggleSetting = (key) => {
    setSettings(prev => ({
      ...prev,
      [key]: !prev[key]
    }));
    // Clear success message when user makes changes
    setSuccess(null);
  };

  // ── WebSocket Setup ─────────────────────────────────────────────────────
  useEffect(() => {
    if (demoMode) {
      setRealTimeAlerts([
        { id: '1', title: 'BTC/USDT Target Hit', message: 'Take profit order executed at $65,420', type: 'trade', timestamp: new Date().toISOString(), read: false },
        { id: '2', title: 'RSI Divergence', message: 'Bearish divergence detected on ETH/USDT 15m timeframe', type: 'alert', timestamp: new Date(Date.now() - 3600000).toISOString(), read: false },
        { id: '3', title: 'Security Alert', message: 'New device login detected from IP 192.168.1.45', type: 'security', timestamp: new Date(Date.now() - 7200000).toISOString(), read: true },
      ]);
      
      const interval = setInterval(() => {
        setRealTimeAlerts(prev => {
          const newAlert = {
            id: Date.now().toString(),
            title: 'Bot Engine Update',
            message: `Strategy rebalance executed. Market volatility at ${(Math.random() * 5).toFixed(2)}%`,
            type: ['trade', 'alert', 'system'][Math.floor(Math.random() * 3)],
            timestamp: new Date().toISOString(),
            read: false
          };
          return [newAlert, ...prev].slice(0, 50);
        });
      }, 5000);
      return () => clearInterval(interval);
    }

    // Connect to WebSocket
    wsClient.connect('/ws/telemetry');
    
    // Subscribe to notification events
    const unsubscribeNotifications = wsClient.subscribe('NOTIFICATION', (message) => {
      console.log('📨 Real-time notification:', message);
      
      setRealTimeAlerts(prev => [
        {
          id: message.id || Date.now(),
          type: message.type || 'info',
          title: message.title || 'New Notification',
          message: message.message || '',
          timestamp: new Date().toISOString(),
          read: false,
        },
        ...prev.slice(0, 49), // Keep last 50 alerts
      ]);
    });

    // Subscribe to trade alerts
    const unsubscribeTrade = wsClient.subscribe('TRADE_ALERT', (message) => {
      if (settings.trade_alerts) {
        setRealTimeAlerts(prev => [
          {
            id: `trade-${Date.now()}`,
            type: 'trade',
            title: 'Trade Executed',
            message: `${message.symbol} - ${message.side} @ $${message.price}`,
            timestamp: new Date().toISOString(),
            read: false,
          },
          ...prev.slice(0, 49),
        ]);
      }
    });

    // Subscribe to price alerts
    const unsubscribePrice = wsClient.subscribe('PRICE_ALERT', (message) => {
      if (settings.price_alerts) {
        setRealTimeAlerts(prev => [
          {
            id: `price-${Date.now()}`,
            type: 'price',
            title: 'Price Alert',
            message: `${message.symbol} reached $${message.price}`,
            timestamp: new Date().toISOString(),
            read: false,
          },
          ...prev.slice(0, 49),
        ]);
      }
    });

    // Subscribe to security alerts
    const unsubscribeSecurity = wsClient.subscribe('SECURITY_ALERT', (message) => {
      if (settings.security_alerts) {
        setRealTimeAlerts(prev => [
          {
            id: `security-${Date.now()}`,
            type: 'security',
            title: 'Security Alert',
            message: message.message || 'Security event detected',
            timestamp: new Date().toISOString(),
            read: false,
          },
          ...prev.slice(0, 49),
        ]);
      }
    });

    // Track connection status
    const checkConnection = setInterval(() => {
      setWsConnected(wsClient.connectionStatus === 'connected');
    }, 1000);

    return () => {
      unsubscribeNotifications();
      unsubscribeTrade();
      unsubscribePrice();
      unsubscribeSecurity();
      clearInterval(checkConnection);
    };
  }, [settings.trade_alerts, settings.price_alerts, settings.security_alerts]);

  // ── Initial Load ─────────────────────────────────────────────────────────
  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // ── Mark Alert as Read ──────────────────────────────────────────────────
  const markAsRead = (alertId) => {
    setRealTimeAlerts(prev =>
      prev.map(alert =>
        alert.id === alertId ? { ...alert, read: true } : alert
      )
    );
  };

  // ── Clear All Alerts ────────────────────────────────────────────────────
  const clearAllAlerts = () => {
    setRealTimeAlerts([]);
  };

  // ── Render ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={{ 
        height: '100vh', 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        background: C.bg 
      }}>
        <Loader2 size={32} color={C.cyan} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ color: C.t2, marginLeft: 12 }}>Loading notification settings...</span>
      </div>
    );
  }

  return (
    <div style={{ 
      padding: '24px', 
      maxWidth: '1200px', 
      margin: '0 auto',
      background: C.bg,
      minHeight: '100vh'
    }}>
      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <Bell size={28} color={C.cyan} />
          <h1 style={{ 
            fontSize: 28, 
            fontWeight: 800, 
            color: C.t1,
            margin: 0 
          }}>
            Activity Timeline
          </h1>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 6,
            background: wsConnected ? `${C.green}15` : `${C.red}15`,
            border: `1px solid ${wsConnected ? C.green : C.red}40`,
            borderRadius: 20,
            padding: '4px 12px',
          }}>
            <div style={{ 
              width: 8, 
              height: 8, 
              borderRadius: '50%', 
              background: wsConnected ? C.green : C.red,
              animation: wsConnected ? 'pulse 2s infinite' : 'none'
            }} />
            <span style={{ 
              fontSize: 11, 
              color: wsConnected ? C.green : C.red,
              fontFamily: 'monospace'
            }}>
              {wsConnected ? 'LIVE' : 'OFFLINE'}
            </span>
          </div>
        </div>
        <p style={{ color: C.t2, fontSize: 14, margin: 0 }}>
          Manage your notification preferences and view real-time alerts
        </p>
      </div>

      {/* Error/Success Messages */}
      {error && (
        <div style={{
          background: `${C.red}15`,
          border: `1px solid ${C.red}40`,
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 24,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <X size={16} color={C.red} />
          <span style={{ color: C.red, fontSize: 13 }}>{error}</span>
        </div>
      )}

      {success && (
        <div style={{
          background: `${C.green}15`,
          border: `1px solid ${C.green}40`,
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 24,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <Check size={16} color={C.green} />
          <span style={{ color: C.green, fontSize: 13 }}>{success}</span>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
        {/* Left Column - Settings */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          
          {/* Delivery Methods */}
          <div style={{
            background: C.bg2,
            border: `1px solid ${C.border}`,
            borderRadius: 12,
            padding: 20,
          }}>
            <h3 style={{ 
              fontSize: 14, 
              fontWeight: 700, 
              color: C.t1, 
              margin: '0 0 16px 0',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <Volume2 size={16} color={C.cyan} />
              Delivery Methods
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <ToggleRow
                icon={<Mail size={18} />}
                label="Email Notifications"
                description="Receive updates via email"
                checked={settings.email_enabled}
                onChange={() => toggleSetting('email_enabled')}
              />
              <ToggleRow
                icon={<Smartphone size={18} />}
                label="Push Notifications"
                description="Browser and mobile push alerts"
                checked={settings.push_enabled}
                onChange={() => toggleSetting('push_enabled')}
              />
            </div>
          </div>

          {/* Alert Types */}
          <div style={{
            background: C.bg2,
            border: `1px solid ${C.border}`,
            borderRadius: 12,
            padding: 20,
          }}>
            <h3 style={{ 
              fontSize: 14, 
              fontWeight: 700, 
              color: C.t1, 
              margin: '0 0 16px 0',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <AlertTriangle size={16} color={C.orange} />
              Alert Types
            </h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <ToggleRow
                icon={<TrendingUp size={18} color={C.green} />}
                label="Trade Alerts"
                description="Order executions and position updates"
                checked={settings.trade_alerts}
                onChange={() => toggleSetting('trade_alerts')}
              />
              <ToggleRow
                icon={<Bell size={18} color={C.cyan} />}
                label="Price Alerts"
                description="Price targets and threshold breaches"
                checked={settings.price_alerts}
                onChange={() => toggleSetting('price_alerts')}
              />
              <ToggleRow
                icon={<Shield size={18} color={C.purple} />}
                label="Security Alerts"
                description="Login attempts and security events"
                checked={settings.security_alerts}
                onChange={() => toggleSetting('security_alerts')}
              />
              <ToggleRow
                icon={<MessageSquare size={18} color={C.t3} />}
                label="Marketing Emails"
                description="Product updates and promotions"
                checked={settings.marketing_emails}
                onChange={() => toggleSetting('marketing_emails')}
              />
            </div>
          </div>

          {/* Save Button */}
          <button
            onClick={saveSettings}
            disabled={saving}
            style={{
              background: `linear-gradient(135deg, ${C.cyan}20, ${C.cyan}10)`,
              border: `1px solid ${C.cyan}50`,
              borderRadius: 8,
              padding: '12px 24px',
              color: C.cyan,
              fontSize: 14,
              fontWeight: 600,
              cursor: saving ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              opacity: saving ? 0.7 : 1,
              transition: 'all 0.2s',
            }}
          >
            {saving ? (
              <>
                <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
                Saving...
              </>
            ) : (
              <>
                <Save size={16} />
                Save Settings
              </>
            )}
          </button>
        </div>

        {/* Right Column - Real-Time Alerts */}
        <div style={{
          background: C.bg2,
          border: `1px solid ${C.border}`,
          borderRadius: 12,
          padding: 20,
          maxHeight: '600px',
          display: 'flex',
          flexDirection: 'column',
        }}>
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'space-between',
            marginBottom: 16,
          }}>
            <h3 style={{ 
              fontSize: 14, 
              fontWeight: 700, 
              color: C.t1, 
              margin: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <Bell size={16} color={C.cyan} />
              Chronological Feed
              {realTimeAlerts.length > 0 && (
                <span style={{
                  background: C.red,
                  color: '#fff',
                  fontSize: 11,
                  padding: '2px 8px',
                  borderRadius: 10,
                  fontWeight: 600,
                }}>
                  {realTimeAlerts.filter(a => !a.read).length}
                </span>
              )}
            </h3>
            
            {realTimeAlerts.length > 0 && (
              <button
                onClick={clearAllAlerts}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: C.t3,
                  fontSize: 12,
                  cursor: 'pointer',
                  padding: '4px 8px',
                }}
              >
                Clear all
              </button>
            )}
          </div>

          {/* Alerts List */}
          <div style={{ 
            flex: 1,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}>
            {realTimeAlerts.length === 0 ? (
              <div style={{ padding: '20px 0' }}>
                <ActionableEmptyState 
                  icon={Bell}
                  title="No Alerts Yet"
                  description="Your systems are running normally. Active trading, price, and security alerts will appear here in real-time."
                  actions={[{ label: "Configure Webhooks", onClick: () => document.querySelector('h1').scrollIntoView({ behavior: 'smooth' }), variant: 'primary' }]}
                />
              </div>
            ) : (
              realTimeAlerts.map(alert => (
                <div
                  key={alert.id}
                  onClick={() => markAsRead(alert.id)}
                  style={{
                    background: alert.read ? C.bg3 : `${C.cyan}08`,
                    border: `1px solid ${alert.read ? C.border : `${C.cyan}30`}`,
                    borderRadius: 8,
                    padding: 12,
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                >
                  <div style={{ 
                    display: 'flex', 
                    alignItems: 'flex-start', 
                    gap: 10,
                  }}>
                    <div style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: getAlertColor(alert.type),
                      marginTop: 4,
                      flexShrink: 0,
                    }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'space-between',
                        marginBottom: 4,
                      }}>
                        <span style={{ 
                          fontSize: 13, 
                          fontWeight: 600, 
                          color: C.t1,
                        }}>
                          {alert.title}
                        </span>
                        <span style={{ 
                          fontSize: 11, 
                          color: C.t3,
                          fontFamily: 'monospace',
                        }}>
                          {formatTime(alert.timestamp)}
                        </span>
                      </div>
                      <p style={{ 
                        margin: 0, 
                        fontSize: 12, 
                        color: C.t2,
                        lineHeight: 1.4,
                      }}>
                        {alert.message}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TOGGLE ROW COMPONENT
// ═══════════════════════════════════════════════════════════════════════════
function ToggleRow({ icon, label, description, checked, onChange }) {
  return (
    <div 
      onClick={onChange}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '12px',
        background: C.bg3,
        border: `1px solid ${checked ? `${C.cyan}40` : C.border}`,
        borderRadius: 8,
        cursor: 'pointer',
        transition: 'all 0.15s',
      }}
    >
      <div style={{ 
        color: checked ? C.cyan : C.t3,
        transition: 'color 0.15s',
      }}>
        {icon}
      </div>
      
      <div style={{ flex: 1 }}>
        <div style={{ 
          fontSize: 13, 
          fontWeight: 600, 
          color: C.t1,
          marginBottom: 2,
        }}>
          {label}
        </div>
        <div style={{ fontSize: 11, color: C.t3 }}>
          {description}
        </div>
      </div>

      {/* Toggle Switch */}
      <div style={{
        width: 44,
        height: 24,
        background: checked ? C.cyan : C.border,
        borderRadius: 12,
        position: 'relative',
        transition: 'background 0.2s',
        flexShrink: 0,
      }}>
        <div style={{
          width: 20,
          height: 20,
          background: '#fff',
          borderRadius: '50%',
          position: 'absolute',
          top: 2,
          left: checked ? 22 : 2,
          transition: 'left 0.2s',
          boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
        }} />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════════
function getAlertColor(type) {
  switch (type) {
    case 'trade': return C.green;
    case 'price': return C.cyan;
    case 'security': return C.purple;
    case 'error': return C.red;
    case 'warning': return C.orange;
    default: return C.t3;
  }
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = now - date;
  
  if (diff < 60000) return 'Just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  
  return date.toLocaleDateString();
}

export default NotificationsPage;
