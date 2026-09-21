/**
 * ═══════════════════════════════════════════════════════════════════════════
 * NOTIFICATION CENTER
 * ═══════════════════════════════════════════════════════════════════════════
 * Institutional notification center with:
 * - Chronological feed of real-time events
 * - Severity indicators & Category filtering
 * - Search by title and content
 * - Click-to-navigate action linking to relevant product surfaces
 * - Mark as read / Mark all as read / Dismiss
 * - Real-time WebSocket updates with deduplication
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Bell, 
  RefreshCw, 
  Check, 
  X, 
  Search,
  TrendingUp,
  Shield,
  CreditCard,
  Database,
  Activity,
  AlertTriangle,
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader2,
  HelpCircle,
  Link2,
  Trash2,
  ExternalLink
} from 'lucide-react';
import { api } from '../api';
import wsClient from '../websocketClient';
// ═══════════════════════════════════════════════════════════════════════════
// COLOR PALETTE — Requirement 1.1: the single token source.
// This page used to declare a competing local `C`, then read the
// `ui-legacy/primitives` shim; it reads `design/tokens.js` directly now
// (task 27.2). The layout is unchanged — every value below is the one the shim
// already returned (design.md §17.2).
// ═══════════════════════════════════════════════════════════════════════════
import { token } from '../design/tokens';

// ═══════════════════════════════════════════════════════════════════════════
// CATEGORY CONFIGURATION
// ═══════════════════════════════════════════════════════════════════════════
const CATEGORIES = [
  { id: 'all', label: 'All', icon: Bell },
  { id: 'trade', label: 'Trade', icon: TrendingUp },
  { id: 'strategy', label: 'Strategy', icon: Activity },
  { id: 'risk', label: 'Risk', icon: AlertTriangle },
  { id: 'exchange', label: 'Exchange', icon: Link2 },
  { id: 'support', label: 'Support', icon: HelpCircle },
  { id: 'security', label: 'Security', icon: Shield },
  { id: 'billing', label: 'Billing', icon: CreditCard },
  { id: 'system', label: 'System', icon: Database }
];

const SEVERITY_COLORS = {
  info: token.brand.base,
  warning: token.status.warning.fg,
  critical: token.status.loss.fg,
  emergency: token.status.loss.fg
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
  const navigate = useNavigate();
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
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
        return deleted && !deleted.read ? Math.max(0, prev - 1) : prev;
      });
    } catch (err) {
      console.error('Failed to delete notification:', err);
    }
  };

  // ── Clear All Notifications ────────────────────────────────────────────────
  const deleteAllNotifications = async () => {
    if (!window.confirm("Are you sure you want to clear all notifications?")) return;
    try {
      await api.notifications.deleteAll();
      setNotifications([]);
      setUnreadCount(0);
    } catch (err) {
      console.error('Failed to clear notifications:', err);
    }
  };

  // ── Refresh ───────────────────────────────────────────────────────────────
  const handleRefresh = () => {
    setRefreshing(true);
    fetchNotifications();
  };

  // ── Initial Load ───────────────────────────────────────────────────────────
  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  // ── WebSocket Setup & Real-time Listeners ──────────────────────────────────
  useEffect(() => {
    const handleIncomingNotification = (message) => {
      const data = message.data || message;
      if (!data || !data.id) return;

      setNotifications(prev => {
        if (prev.some(n => n.id === data.id)) return prev;
        return [{ ...data, read: false }, ...prev.slice(0, 99)];
      });
      setUnreadCount(prev => prev + 1);
    };

    wsSubscriptionRef.current = wsClient.subscribe('notification', handleIncomingNotification);

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

  // ── Navigate Target Helper ─────────────────────────────────────────────────
  const handleNotificationClick = (notification) => {
    if (!notification.read) {
      markAsRead(notification.id);
    }

    const cat = (notification.category || '').toLowerCase();
    const type = (notification.type || '').toLowerCase();

    if (cat === 'support' || type.includes('support')) {
      navigate('/app/support');
    } else if (cat === 'exchange' || type.includes('exchange')) {
      navigate('/app/exchange');
    } else if (cat === 'risk' || type.includes('risk')) {
      navigate('/app/risk');
    } else if (cat === 'strategy' || type.includes('strategy')) {
      navigate('/app/strategies');
    } else if (cat === 'trade' || type.includes('order') || type.includes('trade')) {
      navigate('/app/dashboard');
    } else if (cat === 'billing' || type.includes('billing')) {
      navigate('/app/billing');
    } else if (cat === 'security' || type.includes('security')) {
      navigate('/app/profile');
    }
  };

  // ── Filter Notifications ────────────────────────────────────────────────────
  const filteredNotifications = useMemo(() => {
    return notifications.filter(n => {
      if (selectedCategory !== 'all' && n.category !== selectedCategory) return false;
      if (unreadOnly && n.read) return false;
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const matchTitle = (n.title || '').toLowerCase().includes(query);
        const matchMsg = (n.message || '').toLowerCase().includes(query);
        const matchCat = (n.category || '').toLowerCase().includes(query);
        if (!matchTitle && !matchMsg && !matchCat) return false;
      }
      return true;
    });
  }, [notifications, selectedCategory, unreadOnly, searchQuery]);

  // ── Format Time ────────────────────────────────────────────────────────────
  const formatTime = (timestamp) => {
    if (!timestamp) return 'Just now';
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    
    return date.toLocaleDateString();
  };

  // ── Render Loading ─────────────────────────────────────────────────────────
  if (loading && notifications.length === 0) {
    return (
      <div style={{ 
        height: '100vh', 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        background: token.surface.canvas 
      }}>
        <Loader2 size={32} color={token.brand.base} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ color: token.content.secondary, marginLeft: 12 }}>Loading notifications...</span>
      </div>
    );
  }

  return (
    <div style={{ 
      padding: '24px', 
      maxWidth: '1200px', 
      margin: '0 auto',
      background: token.surface.canvas,
      minHeight: '100vh'
    }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ background: `${token.brand.base}15`, border: `1px solid ${token.brand.base}40`, borderRadius: 10, padding: 8 }}>
              <Bell size={24} color={token.brand.base} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h1 style={{ fontSize: 22, fontWeight: 800, color: token.content.primary, margin: 0, letterSpacing: '-0.02em' }}>
                  Notifications
                </h1>
                {unreadCount > 0 && (
                  <span style={{
                    background: token.brand.base,
                    color: '#000',
                    fontSize: 11,
                    padding: '2px 8px',
                    borderRadius: 12,
                    fontWeight: 700
                  }}>
                    {unreadCount} unread
                  </span>
                )}
              </div>
              <p style={{ margin: '2px 0 0', fontSize: 12, color: token.content.muted, fontFamily: 'monospace' }}>
                LIVE EVENT TELEMETRY & SYSTEM ALERTS
              </p>
            </div>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: 6,
              background: wsConnected ? `${token.status.profit.fg}15` : `${token.status.loss.fg}15`,
              border: `1px solid ${wsConnected ? token.status.profit.fg : token.status.loss.fg}40`,
              borderRadius: 20,
              padding: '4px 12px',
            }}>
              <div style={{ 
                width: 7, 
                height: 7, 
                borderRadius: '50%', 
                background: wsConnected ? token.status.profit.fg : token.status.loss.fg,
                animation: wsConnected ? 'pulse 2s infinite' : 'none'
              }} />
              <span style={{ 
                fontSize: 11, 
                color: wsConnected ? token.status.profit.fg : token.status.loss.fg,
                fontFamily: 'monospace',
                fontWeight: 700
              }}>
                {wsConnected ? 'STREAM LIVE' : 'OFFLINE'}
              </span>
            </div>
            
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              style={{
                background: token.surface.raised,
                border: `1px solid ${token.line.default}`,
                borderRadius: 8,
                padding: '8px 12px',
                color: token.content.secondary,
                cursor: refreshing ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 12,
                fontWeight: 600
              }}
            >
              <RefreshCw size={13} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
              Refresh
            </button>
            
            {unreadCount > 0 && (
              <button
                onClick={markAllAsRead}
                style={{
                  background: `${token.brand.base}15`,
                  border: `1px solid ${token.brand.base}50`,
                  borderRadius: 8,
                  padding: '8px 14px',
                  color: token.brand.base,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12,
                  fontWeight: 700
                }}
              >
                <Check size={14} />
                Mark All Read
              </button>
            )}

            {notifications.length > 0 && (
              <button
                onClick={deleteAllNotifications}
                style={{
                  background: 'transparent',
                  border: `1px solid ${token.line.default}`,
                  borderRadius: 8,
                  padding: '8px 12px',
                  color: token.content.muted,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12
                }}
                title="Clear all notifications"
              >
                <Trash2 size={13} />
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Filter Controls Bar */}
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
          {/* Search Box */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: token.surface.raised,
            border: `1px solid ${token.line.default}`,
            borderRadius: 8,
            padding: '6px 12px',
            flex: '1 1 240px',
            minWidth: 200
          }}>
            <Search size={14} color={token.content.muted} />
            <input
              type="text"
              placeholder="Search notifications..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                background: 'transparent',
                border: 'none',
                color: token.content.primary,
                fontSize: 12,
                outline: 'none',
                width: '100%'
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                style={{ background: 'transparent', border: 'none', color: token.content.muted, cursor: 'pointer', padding: 0 }}
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Unread Only Toggle */}
          <button
            onClick={() => setUnreadOnly(!unreadOnly)}
            style={{
              background: unreadOnly ? `${token.brand.base}20` : token.surface.raised,
              border: `1px solid ${unreadOnly ? token.brand.base : token.line.default}`,
              borderRadius: 8,
              padding: '6px 14px',
              color: unreadOnly ? token.brand.base : token.content.secondary,
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6
            }}
          >
            <div style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: unreadOnly ? token.brand.base : token.content.muted
            }} />
            Unread Only
          </button>
        </div>

        {/* Category Pills */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {CATEGORIES.map(cat => {
            const Icon = cat.icon;
            const active = selectedCategory === cat.id;
            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                style={{
                  background: active ? `${token.brand.base}18` : token.surface.raised,
                  border: `1px solid ${active ? token.brand.base : token.line.default}`,
                  borderRadius: 6,
                  padding: '6px 12px',
                  color: active ? token.brand.base : token.content.secondary,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  fontWeight: active ? 700 : 500,
                  transition: 'all 0.15s'
                }}
              >
                <Icon size={12} />
                {cat.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div style={{
          background: `${token.status.loss.fg}15`,
          border: `1px solid ${token.status.loss.fg}40`,
          borderRadius: 8,
          padding: '12px 16px',
          marginBottom: 20,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <AlertCircle size={16} color={token.status.loss.fg} />
          <span style={{ color: token.status.loss.fg, fontSize: 13 }}>{error}</span>
        </div>
      )}

      {/* Notifications Feed */}
      <div style={{
        background: token.surface.raised,
        border: `1px solid ${token.line.default}`,
        borderRadius: 12,
        overflow: 'hidden'
      }}>
        {filteredNotifications.length === 0 ? (
          <div style={{ padding: '64px 20px', textAlign: 'center' }}>
            <div style={{
              width: 52,
              height: 52,
              borderRadius: '50%',
              background: token.surface.inset,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px'
            }}>
              <Bell size={24} color={token.content.muted} />
            </div>
            <h3 style={{ color: token.content.primary, fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
              {unreadOnly ? "No Unread Notifications" : "No Notifications"}
            </h3>
            <p style={{ color: token.content.muted, fontSize: 13, maxWidth: 380, margin: '0 auto' }}>
              {searchQuery 
                ? `No notifications matching "${searchQuery}".`
                : selectedCategory === 'all'
                ? "Your quantitative trading systems and risk engines are running normally."
                : `No notifications recorded under ${CATEGORIES.find(c => c.id === selectedCategory)?.label}.`
              }
            </p>
          </div>
        ) : (
          filteredNotifications.map((notification, idx) => {
            const SeverityIcon = SEVERITY_ICONS[notification.severity] || SEVERITY_ICONS.info;
            const categoryConfig = CATEGORIES.find(c => c.id === notification.category);
            const isUnread = !notification.read;
            
            return (
              <div
                key={notification.id}
                // A pointer-only row cannot be opened without a mouse. It activates a
                // notification, so it gets the button role, a tab stop and Enter/Space.
                role="button"
                tabIndex={0}
                onClick={() => handleNotificationClick(notification)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
                    // Space scrolls the page by default, which would move the list out from
                    // under the row just activated.
                    event.preventDefault();
                    handleNotificationClick(notification);
                  }
                }}
                style={{
                  padding: '16px 20px',
                  borderBottom: idx < filteredNotifications.length - 1 ? `1px solid ${token.line.default}` : 'none',
                  background: isUnread ? `${token.brand.base}07` : 'transparent',
                  borderLeft: `3px solid ${isUnread ? token.brand.base : 'transparent'}`,
                  cursor: 'pointer',
                  transition: 'background 0.15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
                  {/* Severity Badge */}
                  <div style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: `${SEVERITY_COLORS[notification.severity] || token.brand.base}18`,
                    border: `1px solid ${SEVERITY_COLORS[notification.severity] || token.brand.base}35`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                    marginTop: 2
                  }}>
                    <SeverityIcon size={16} color={SEVERITY_COLORS[notification.severity] || token.brand.base} />
                  </div>

                  {/* Content Area */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'space-between',
                      marginBottom: 4,
                      gap: 8
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ 
                          fontSize: 13.5, 
                          fontWeight: isUnread ? 700 : 600, 
                          color: isUnread ? token.content.primary : token.content.secondary,
                        }}>
                          {notification.title}
                        </span>
                        <span style={{
                          background: `${token.brand.base}15`,
                          color: token.brand.base,
                          fontSize: 10,
                          padding: '1px 6px',
                          borderRadius: 4,
                          fontWeight: 700,
                          fontFamily: 'monospace',
                          textTransform: 'uppercase'
                        }}>
                          {categoryConfig?.label || notification.category}
                        </span>
                        {isUnread && (
                          <span style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: token.brand.base
                          }} />
                        )}
                      </div>
                      <span style={{ 
                        fontSize: 11, 
                        color: token.content.muted, 
                        fontFamily: 'monospace',
                        flexShrink: 0
                      }}>
                        {formatTime(notification.created_at)}
                      </span>
                    </div>
                    
                    <p style={{ 
                      margin: 0, 
                      fontSize: 13, 
                      color: isUnread ? token.content.secondary : token.content.muted,
                      lineHeight: 1.45,
                      marginBottom: (notification.strategy_id || notification.exchange || notification.metadata?.ticket_id) ? 8 : 0
                    }}>
                      {notification.message}
                    </p>

                    {/* Metadata & Navigation Badges */}
                    {(notification.strategy_id || notification.exchange || notification.metadata?.ticket_id) && (
                      <div style={{ display: 'flex', gap: 10, fontSize: 11, color: token.content.muted, flexWrap: 'wrap' }}>
                        {notification.metadata?.ticket_id && (
                          <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: token.brand.base, fontWeight: 600 }}>
                            <HelpCircle size={11} />
                            Ticket: #{notification.metadata.ticket_id}
                          </span>
                        )}
                        {notification.strategy_id && (
                          <span style={{ fontFamily: 'monospace' }}>
                            Strategy: {notification.strategy_id.slice(0, 10)}...
                          </span>
                        )}
                        {notification.exchange && (
                          <span style={{ fontFamily: 'monospace' }}>
                            Venue: {notification.exchange.toUpperCase()}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Actions (Mark Read & Dismiss) */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    {isUnread && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          markAsRead(notification.id);
                        }}
                        title="Mark as read"
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: token.content.muted,
                          cursor: 'pointer',
                          padding: 6,
                          borderRadius: 4,
                        }}
                      >
                        <Check size={14} />
                      </button>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteNotification(notification.id);
                      }}
                      title="Dismiss notification"
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: token.content.muted,
                        cursor: 'pointer',
                        padding: 6,
                        borderRadius: 4,
                      }}
                    >
                      <X size={14} />
                    </button>
                  </div>
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
