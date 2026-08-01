/**
 * ═══════════════════════════════════════════════════════════════════════════
 * NOTIFICATION CENTER
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Institutional notification center with:
 * - Chronological feed of real-time events
 * - Severity indicators
 * - Category filtering
 * - Mark as read functionality
 * - Real-time WebSocket updates
 * 
 * Categories:
 * - Trade Executed, Order Filled, Order Rejected
 * - Strategy Started, Strategy Stopped
 * - Risk Alert, Margin Alert
 * - Subscription, Billing
 * - Security, API Key
 * - System Maintenance, Database, WebSocket
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  Bell, 
  RefreshCw, 
  Check, 
  X, 
  Filter,
  TrendingUp,
  Shield,
  CreditCard,
  Lock,
  Database,
  Activity,
  AlertTriangle,
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader2
} from 'lucide-react';
import { api } from '../api/typed-client';
import wsClient from '../websocketClient';

// ═══════════════════════════════════════════════════════════════════════════
// COLOR PALETTE
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
  green: "#00ff88",
  red: "#ff3366",
  orange: "#ff9500",
  purple: "#8b5cf6",
  yellow: "#fbbf24"
};

// ═══════════════════════════════════════════════════════════════════════════
// CATEGORY CONFIGURATION
// ═══════════════════════════════════════════════════════════════════════════
const CATEGORIES = [
  { id: 'all', label: 'All', icon: Bell },
  { id: 'trade', label: 'Trade', icon: TrendingUp },
  { id: 'strategy', label: 'Strategy', icon: Activity },
  { id: 'risk', label: 'Risk', icon: AlertTriangle },
  { id: 'security', label: 'Security', icon: Shield },
  { id: 'billing', label: 'Billing', icon: CreditCard },
  { id: 'system', label: 'System', icon: Database }
];

const SEVERITY_COLORS = {
  info: C.t2,
  warning: C.orange,
  critical: C.red,
  emergency: C.red
};

const SEVERITY_ICONS = {
  info: CheckCircle,
  warning: AlertTriangle,
  critical: XCircle,
  emergency: AlertCircle
};

// ═══════════════════════════════════════════════════════════════════════════
// NOTIFICATION CENTER COMPONENT
// ═══════════════════════════════════════════════════════════════════════════
function NotificationCenter() {
  const [notifications, setNotifications] = useState([]);
  const [filteredNotifications, setFilteredNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [unreadCount, setUnreadCount] = useState(0);
  const [wsConnected, setWsConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const wsSubscriptionRef = useRef(null);

  // ── Fetch Notifications ─────────────────────────────────────────────────────
  const fetchNotifications = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.notifications.list({ limit: 100 });
      setNotifications(data.items || []);
      setUnreadCount(data.unread_count || 0);
    } catch (err) {
      console.error('Failed to fetch notifications:', err);
      setError('Failed to load notifications. Please try again.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // ── Mark as Read ───────────────────────────────────────────────────────────
  const markAsRead = async (id) => {
    try {
      await api.notifications.markRead(id);
      setNotifications(prev =>
        prev.map(n => n.id === id ? { ...n, read: true } : n)
      );
      setUnreadCount(prev => Math.max(0, prev - 1));
    } catch (err) {
      console.error('Failed to mark as read:', err);
    }
  };

  // ── Mark All as Read ───────────────────────────────────────────────────────
  const markAllAsRead = async () => {
    try {
      await api.notifications.markAllRead();
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch (err) {
      console.error('Failed to mark all as read:', err);
    }
  };

  // ── Delete Notification ────────────────────────────────────────────────────
  const deleteNotification = async (id) => {
    try {
      await api.notifications.delete(id);
      setNotifications(prev => prev.filter(n => n.id !== id));
      setUnreadCount(prev => {
        const deleted = notifications.find(n => n.id === id);
        return deleted && !deleted.read ? prev - 1 : prev;
      });
    } catch (err) {
      console.error('Failed to delete notification:', err);
    }
  };

  // ── Refresh ───────────────────────────────────────────────────────────────
  const handleRefresh = () => {
    setRefreshing(true);
    fetchNotifications();
  };

  // ── Filter Notifications ────────────────────────────────────────────────────
  useEffect(() => {
    if (selectedCategory === 'all') {
      setFilteredNotifications(notifications);
    } else {
      setFilteredNotifications(
        notifications.filter(n => n.category === selectedCategory)
      );
    }
  }, [notifications, selectedCategory]);

  // ── Initial Load ───────────────────────────────────────────────────────────
  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  // ── WebSocket Setup ───────────────────────────────────────────────────────
  useEffect(() => {
    // Subscribe to notification events
    wsSubscriptionRef.current = wsClient.subscribe('notification', (message) => {
      console.log('📨 Notification received:', message);
      
      if (message.data) {
        setNotifications(prev => [
          {
            ...message.data,
            read: false
          },
          ...prev.slice(0, 99) // Keep last 100
        ]);
        setUnreadCount(prev => prev + 1);
      }
    });

    // Track connection status
    const checkConnection = setInterval(() => {
      setWsConnected(wsClient.connectionStatus === 'connected');
    }, 1000);

    return () => {
      if (wsSubscriptionRef.current) {
        wsSubscriptionRef.current();
      }
      clearInterval(checkConnection);
    };
  }, []);

  // ── Format Time ────────────────────────────────────────────────────────────
  const formatTime = (timestamp) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    
    return date.toLocaleDateString();
  };

  // ── Render ───────────────────────────────────────────────────────────────
  if (loading && notifications.length === 0) {
    return (
      <div style={{ 
        height: '100vh', 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        background: C.bg 
      }}>
        <Loader2 size={32} color={C.accent} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ color: C.t2, marginLeft: 12 }}>Loading notifications...</span>
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
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Bell size={28} color={C.accent} />
            <h1 style={{ 
              fontSize: 24, 
              fontWeight: 700, 
              color: C.t1,
              margin: 0 
            }}>
              Notification Center
            </h1>
            {unreadCount > 0 && (
              <span style={{
                background: C.red,
                color: '#fff',
                fontSize: 12,
                padding: '2px 8px',
                borderRadius: 10,
                fontWeight: 600
              }}>
                {unreadCount} unread
              </span>
            )}
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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
            
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              style={{
                background: 'transparent',
                border: `1px solid ${C.border}`,
                borderRadius: 8,
                padding: '8px 12px',
                color: C.t2,
                cursor: refreshing ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 12
              }}
            >
              <RefreshCw size={14} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
              Refresh
            </button>
            
            {unreadCount > 0 && (
              <button
                onClick={markAllAsRead}
                style={{
                  background: `${C.accent}15`,
                  border: `1px solid ${C.accent}50`,
                  borderRadius: 8,
                  padding: '8px 12px',
                  color: C.accent,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12,
                  fontWeight: 600
                }}
              >
                <Check size={14} />
                Mark All Read
              </button>
            )}
          </div>
        </div>

        {/* Category Filter */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {CATEGORIES.map(cat => {
            const Icon = cat.icon;
            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                style={{
                  background: selectedCategory === cat.id ? `${C.accent}15` : C.bg2,
                  border: `1px solid ${selectedCategory === cat.id ? C.accent : C.border}`,
                  borderRadius: 8,
                  padding: '8px 16px',
                  color: selectedCategory === cat.id ? C.accent : C.t2,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12,
                  fontWeight: selectedCategory === cat.id ? 600 : 400,
                  transition: 'all 0.15s'
                }}
              >
                <Icon size={14} />
                {cat.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Error State */}
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

      {/* Notifications List */}
      <div style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: 12,
        overflow: 'hidden'
      }}>
        {filteredNotifications.length === 0 ? (
          <div style={{ padding: '60px 20px', textAlign: 'center' }}>
            <Bell size={48} color={C.t3} style={{ marginBottom: 16 }} />
            <h3 style={{ color: C.t1, fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
              No Notifications
            </h3>
            <p style={{ color: C.t2, fontSize: 14 }}>
              {selectedCategory === 'all' 
                ? 'Your systems are running normally. Notifications will appear here.'
                : `No ${CATEGORIES.find(c => c.id === selectedCategory)?.label} notifications.`
              }
            </p>
          </div>
        ) : (
          filteredNotifications.map(notification => {
            const SeverityIcon = SEVERITY_ICONS[notification.severity] || SEVERITY_ICONS.info;
            const categoryConfig = CATEGORIES.find(c => c.id === notification.category);
            const CategoryIcon = categoryConfig?.icon || Bell;
            
            return (
              <div
                key={notification.id}
                style={{
                  padding: '16px 20px',
                  borderBottom: `1px solid ${C.border}`,
                  background: notification.read ? C.bg3 : 'transparent',
                  cursor: 'pointer',
                  transition: 'background 0.15s',
                }}
                onClick={() => !notification.read && markAsRead(notification.id)}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
                  {/* Severity Icon */}
                  <div style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: `${SEVERITY_COLORS[notification.severity]}15`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}>
                    <SeverityIcon size={16} color={SEVERITY_COLORS[notification.severity]} />
                  </div>

                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'space-between',
                      marginBottom: 4,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ 
                          fontSize: 14, 
                          fontWeight: 600, 
                          color: C.t1,
                        }}>
                          {notification.title}
                        </span>
                        <span style={{
                          background: `${C.accent}15`,
                          color: C.accent,
                          fontSize: 10,
                          padding: '2px 6px',
                          borderRadius: 4,
                          fontWeight: 600
                        }}>
                          {categoryConfig?.label || notification.category}
                        </span>
                      </div>
                      <span style={{ 
                        fontSize: 11, 
                        color: C.t3,
                        fontFamily: 'monospace'
                      }}>
                        {formatTime(notification.created_at)}
                      </span>
                    </div>
                    
                    <p style={{ 
                      margin: 0, 
                      fontSize: 13, 
                      color: C.t2,
                      lineHeight: 1.4,
                      marginBottom: 8
                    }}>
                      {notification.message}
                    </p>

                    {/* Metadata */}
                    {(notification.strategy_id || notification.exchange) && (
                      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: C.t3 }}>
                        {notification.strategy_id && (
                          <span>Strategy: {notification.strategy_id.slice(0, 8)}...</span>
                        )}
                        {notification.exchange && (
                          <span>Exchange: {notification.exchange}</span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Delete Button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteNotification(notification.id);
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: C.t3,
                      cursor: 'pointer',
                      padding: 4,
                      opacity: 0.7,
                      transition: 'opacity 0.15s'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.opacity = 1}
                    onMouseLeave={(e) => e.currentTarget.style.opacity = 0.7}
                  >
                    <X size={16} />
                  </button>
                </div>
              </div>
            );
          })
        )}
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

export default NotificationCenter;
