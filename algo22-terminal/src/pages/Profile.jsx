import React, { useState, useEffect, useRef } from "react";
import { UserCheck, Mail, Shield, Edit2, Check, AlertCircle, CreditCard, TrendingUp, Lock, Bell, Settings, Copy, ExternalLink, RefreshCw, Loader2, Link2 } from "lucide-react";
import { api } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Inp } from "../components/ui-legacy/primitives";
import wsClient from "../websocketClient";

export default function Profile() {
  const [profile, setProfile] = useState(null);
  const [billing, setBilling] = useState(null);
  const [referral, setReferral] = useState(null);
  const [stats, setStats] = useState(null);
  const [securityLogs, setSecurityLogs] = useState(null);
  const [notificationSettings, setNotificationSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [errorType, setErrorType] = useState(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [editData, setEditData] = useState({});
  const [saving, setSaving] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const wsSubscriptionRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();

    const fetchAllData = async () => {
      try {
        setLoading(true);
        setError(null);

        const [profileData, billingData, referralData, statsData, securityData, notifData] = await Promise.allSettle([
          api.user.getProfile(),
          api.user.getBillingPlan(),
          api.referral.getStats(),
          api.user.getStats(),
          api.user.getSecurityLogs(20),
          api.user.getNotificationSettings()
        ]);

        if (profileData.status === 'fulfilled') setProfile(profileData.value);
        if (billingData.status === 'fulfilled') setBilling(billingData.value);
        if (referralData.status === 'fulfilled') setReferral(referralData.value);
        if (statsData.status === 'fulfilled') setStats(statsData.value);
        if (securityData.status === 'fulfilled') setSecurityLogs(securityData.value);
        if (notifData.status === 'fulfilled') setNotificationSettings(notifData.value);

      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to load profile data:", err);
          
          // Categorize error type
          if (err?.response?.status === 401 || err?.response?.status === 403) {
            setError("Authentication required. Please log in again.");
            setErrorType("auth");
          } else if (err?.response?.status === 404) {
            setError("Profile not found. Please contact support.");
            setErrorType("not_found");
          } else if (err?.response?.status === 422) {
            setError("Invalid request data. Please refresh the page.");
            setErrorType("validation");
          } else if (err?.response?.status >= 500) {
            setError("Server error. Please try again later.");
            setErrorType("server");
          } else if (!window.navigator.onLine) {
            setError("Network offline. Check your internet connection.");
            setErrorType("network");
          } else if (err?.message?.includes("timeout") || err?.code === "ECONNABORTED") {
            setError("Request timeout. Please try again.");
            setErrorType("timeout");
          } else {
            setError("Failed to load profile data. Please try again.");
            setErrorType("unknown");
          }
        }
      } finally {
        setLoading(false);
      }
    };
    fetchAllData();

    // Subscribe to profile updates via WebSocket
    wsSubscriptionRef.current = wsClient.subscribe('profile_update', (message) => {
      console.log('Profile update received:', message);
      if (message.data) {
        setProfile(prev => ({ ...prev, ...message.data }));
      }
    });

    // Subscribe to billing updates
    const billingSub = wsClient.subscribe('billing_update', (message) => {
      console.log('Billing update received:', message);
      if (message.data) {
        setBilling(prev => ({ ...prev, ...message.data }));
      }
    });

    // Subscribe to stats updates
    const statsSub = wsClient.subscribe('stats_update', (message) => {
      console.log('Stats update received:', message);
      if (message.data) {
        setStats(prev => ({ ...prev, ...message.data }));
      }
    });

    return () => {
      controller.abort();
      if (wsSubscriptionRef.current) {
        wsSubscriptionRef.current();
      }
      billingSub();
      statsSub();
    };
  }, []);

  const handleProfileUpdate = async () => {
    try {
      setSaving(true);
      setError(null);
      await api.user.updateProfile(editData);
      setProfile({ ...profile, ...editData });
      setEditingProfile(false);
    } catch (err) {
      console.error("Failed to update profile:", err);
      if (err?.response?.status === 401 || err?.response?.status === 403) {
        setError("Authentication required. Please log in again.");
        setErrorType("auth");
      } else if (err?.response?.status === 422) {
        setError("Invalid profile data. Please check your inputs.");
        setErrorType("validation");
      } else {
        setError("Failed to update profile. Please try again.");
        setErrorType("unknown");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleRetry = () => {
    setRetryCount(prev => prev + 1);
    setError(null);
    setErrorType(null);
    const controller = new AbortController();

    const fetchAllData = async () => {
      try {
        setLoading(true);
        setError(null);

        const [profileData, billingData, referralData, statsData, securityData, notifData] = await Promise.allSettle([
          api.user.getProfile(),
          api.user.getBillingPlan(),
          api.referral.getStats(),
          api.user.getStats(),
          api.user.getSecurityLogs(20),
          api.user.getNotificationSettings()
        ]);

        if (profileData.status === 'fulfilled') setProfile(profileData.value);
        if (billingData.status === 'fulfilled') setBilling(billingData.value);
        if (referralData.status === 'fulfilled') setReferral(referralData.value);
        if (statsData.status === 'fulfilled') setStats(statsData.value);
        if (securityData.status === 'fulfilled') setSecurityLogs(securityData.value);
        if (notifData.status === 'fulfilled') setNotificationSettings(notifData.value);

      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to load profile data:", err);
          if (err?.response?.status === 401 || err?.response?.status === 403) {
            setError("Authentication required. Please log in again.");
            setErrorType("auth");
          } else if (err?.response?.status >= 500) {
            setError("Server error. Please try again later.");
            setErrorType("server");
          } else if (!window.navigator.onLine) {
            setError("Network offline. Check your internet connection.");
            setErrorType("network");
          } else {
            setError("Failed to load profile data. Please try again.");
            setErrorType("unknown");
          }
        }
      } finally {
        setLoading(false);
      }
    };
    fetchAllData();
  };

  const handleNotificationSettingChange = async (key, value) => {
    try {
      const updatedSettings = {
        ...notificationSettings,
        channels: {
          ...notificationSettings?.channels,
          [key]: { ...notificationSettings?.channels?.[key], active: value }
        },
        events: {
          ...notificationSettings?.events,
          [key]: value
        }
      };
      
      setNotificationSettings(updatedSettings);
      await api.user.updateNotificationSettings(updatedSettings);
    } catch (err) {
      console.error('Failed to update notification settings:', err);
      // Revert on error
      setNotificationSettings(notificationSettings);
    }
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  // ── Notification Toggle Row Component ───────────────────────────────────────
  function NotificationToggleRow({ label, description, checked, onChange }) {
    return (
      <div 
        onClick={() => onChange(!checked)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: 12,
          background: C.bg3,
          border: `1px solid ${checked ? `${C.accent}40` : C.border}`,
          borderRadius: 8,
          cursor: "pointer",
          transition: "all 0.15s"
        }}
      >
        <div style={{ 
          color: checked ? C.accent : C.t3,
          transition: "color 0.15s"
        }}>
          <Bell size={18} />
        </div>
        
        <div style={{ flex: 1 }}>
          <div style={{ 
            fontSize: 13, 
            fontWeight: 600, 
            color: C.t1,
            marginBottom: 2
          }}>
            {label}
          </div>
          <div style={{ fontSize: 11, color: C.t3 }}>
            {description}
          </div>
        </div>

        <div style={{
          width: 44,
          height: 24,
          background: checked ? C.accent : C.border,
          borderRadius: 12,
          position: "relative",
          transition: "background 0.2s",
          flexShrink: 0
        }}>
          <div style={{
            width: 20,
            height: 20,
            background: "#fff",
            borderRadius: "50%",
            position: "absolute",
            top: 2,
            left: checked ? 22 : 2,
            transition: "left 0.2s",
            boxShadow: "0 2px 4px rgba(0,0,0,0.2)"
          }} />
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t2, fontFamily: "monospace" }}>
          <Loader2 className="animate-spin" style={{ marginRight: 8 }} />
          Loading profile...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", textAlign: "center" }}>
          <AlertCircle size={48} style={{ color: C.red, marginBottom: 16 }} />
          <div style={{ color: C.red, fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
            Error Loading Profile
          </div>
          <div style={{ color: C.t2, fontSize: 14, marginBottom: 24, maxWidth: 400 }}>
            {error}
          </div>
          
          {errorType === "auth" && (
            <Btn onClick={() => window.location.href = "/login"}>
              Go to Login
            </Btn>
          )}
          
          {errorType === "network" && (
            <Btn onClick={handleRetry}>
              <RefreshCw size={16} style={{ marginRight: 8 }} />
              Retry Connection
            </Btn>
          )}
          
          {(errorType === "server" || errorType === "timeout" || errorType === "unknown") && (
            <div style={{ display: "flex", gap: 8 }}>
              <Btn onClick={handleRetry}>
                <RefreshCw size={16} style={{ marginRight: 8 }} />
                Retry
              </Btn>
              <Btn onClick={() => window.location.reload()} variant="secondary">
                Refresh Page
              </Btn>
            </div>
          )}
          
          {errorType === "not_found" && (
            <Btn onClick={() => window.location.href = "/support"}>
              Contact Support
            </Btn>
          )}
          
          {retryCount > 0 && (
            <div style={{ color: C.t2, fontSize: 12, marginTop: 16 }}>
              Retry attempts: {retryCount}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Profile" sub="Your account information" />

      {/* Profile Header */}
      <Card cls="p-6 mb-4">
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{
            width: 80,
            height: 80,
            borderRadius: "50%",
            background: C.bg3,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 32,
            color: C.t2
          }}>
            {profile?.avatar_url ? (
              <img src={profile.avatar_url} alt="Avatar" style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }} />
            ) : (
              <UserCheck />
            )}
          </div>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: C.t1, marginBottom: 4 }}>
              {profile?.username || profile?.display_name || "User"}
            </h2>
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.t2, fontSize: 12 }}>
              <Mail size={14} />
              {profile?.email || "No email"}
              {profile?.email_confirmed_at && <Check size={14} style={{ color: C.green }} />}
            </div>
            <div style={{ color: C.t2, fontSize: 11, marginTop: 4 }}>
              Role: {profile?.role || "user"}
            </div>
          </div>
          <Btn onClick={() => setEditingProfile(!editingProfile)}>
            <Edit2 size={16} style={{ marginRight: 6 }} />
            {editingProfile ? "Cancel" : "Edit Profile"}
          </Btn>
        </div>

        {editingProfile && (
          <div style={{ marginTop: 20, padding: 16, background: C.bg3, borderRadius: 8 }}>
            <div style={{ display: "grid", gap: 12 }}>
              <div>
                <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block" }}>Username</label>
                <Inp
                  value={editData.username || profile?.username || ""}
                  onChange={(e) => setEditData({ ...editData, username: e.target.value })}
                />
              </div>
              <div>
                <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block" }}>Display Name</label>
                <Inp
                  value={editData.display_name || profile?.display_name || ""}
                  onChange={(e) => setEditData({ ...editData, display_name: e.target.value })}
                />
              </div>
              <div>
                <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block" }}>Bio</label>
                <textarea
                  value={editData.bio || profile?.bio || ""}
                  onChange={(e) => setEditData({ ...editData, bio: e.target.value })}
                  style={{
                    width: "100%",
                    padding: 10,
                    background: C.bg2,
                    border: `1px solid ${C.border}`,
                    borderRadius: 6,
                    color: C.t1,
                    fontSize: 12,
                    minHeight: 80,
                    resize: "vertical"
                  }}
                />
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Btn onClick={handleProfileUpdate} disabled={saving}>
                  {saving ? <Loader2 className="animate-spin" size={16} /> : <Check size={16} />}
                  {saving ? "Saving..." : "Save Changes"}
                </Btn>
                <Btn onClick={() => setEditingProfile(false)} variant="secondary">
                  Cancel
                </Btn>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Subscription */}
      <Card cls="p-6 mb-4">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <CreditCard size={20} style={{ color: C.accent }} />
          <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Subscription</h3>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 16 }}>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Current Plan</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: C.t1 }}>{billing?.name || "Free Tier"}</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Status</div>
            <div style={{ fontSize: 14, color: C.t1 }}>{billing?.autoRenew ? "Active" : "Inactive"}</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Price</div>
            <div style={{ fontSize: 14, color: C.t1 }}>
              ${billing?.priceUSD || 0}/mo
            </div>
          </div>
        </div>
        {billing?.features && (
          <div style={{ marginTop: 16 }}>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 8 }}>Features</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {billing.features.map((feature, idx) => (
                <span key={idx} style={{
                  padding: "4px 8px",
                  background: C.bg3,
                  borderRadius: 4,
                  fontSize: 11,
                  color: C.t1
                }}>
                  {feature}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* Account Stats */}
      <Card cls="p-6 mb-4">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <TrendingUp size={20} style={{ color: C.accent }} />
          <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Account Statistics</h3>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))", gap: 16 }}>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Total Strategies</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: C.t1 }}>{stats?.total_strategies || 0}</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Active Bots</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: C.t1 }}>{stats?.active_bots || 0}</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Total Trades</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: C.t1 }}>{stats?.total_trades || 0}</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Win Rate</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: C.t1 }}>{(stats?.win_rate || 0).toFixed(1)}%</div>
          </div>
          <div>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>Total PnL</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: stats?.total_pnl >= 0 ? C.green : C.red }}>
              ${(stats?.total_pnl || 0).toFixed(2)}
            </div>
          </div>
        </div>
      </Card>

      {/* Notification Settings */}
      <Card cls="p-6 mb-4">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <Bell size={20} style={{ color: C.accent }} />
          <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Notification Preferences</h3>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <NotificationToggleRow
            label="Email Notifications"
            description="Receive critical alerts via email"
            checked={notificationSettings?.channels?.email?.active ?? true}
            onChange={(checked) => handleNotificationSettingChange('email', checked)}
          />
          <NotificationToggleRow
            label="Trade Alerts"
            description="Order executions and position updates"
            checked={notificationSettings?.events?.trade ?? true}
            onChange={(checked) => handleNotificationSettingChange('trade', checked)}
          />
          <NotificationToggleRow
            label="Risk Alerts"
            description="Margin breaches and kill switches"
            checked={notificationSettings?.events?.margin ?? true}
            onChange={(checked) => handleNotificationSettingChange('margin', checked)}
          />
          <NotificationToggleRow
            label="Security Alerts"
            description="Login attempts and API key changes"
            checked={notificationSettings?.events?.login ?? true}
            onChange={(checked) => handleNotificationSettingChange('login', checked)}
          />
          <NotificationToggleRow
            label="Bot Status"
            description="Strategy start/stop events"
            checked={notificationSettings?.events?.bot ?? true}
            onChange={(checked) => handleNotificationSettingChange('bot', checked)}
          />
        </div>
        <div style={{ marginTop: 16, fontSize: 11, color: C.t3 }}>
          <Lock size={12} style={{ marginRight: 4 }} />
          Critical alerts (system failures, kill switches) cannot be disabled
        </div>
      </Card>

      {/* Referral Program */}
      {referral && (
        <Card cls="p-6 mb-4">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <ExternalLink size={20} style={{ color: C.accent }} />
              <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Referral Program</h3>
            </div>
            <span style={{ background: `${C.green}15`, border: `1px solid ${C.green}33`, color: C.green, fontSize: 9, fontFamily: "monospace", fontWeight: 700, padding: "2px 8px", borderRadius: 4 }}>
              ACTIVE
            </span>
          </div>

          {/* Referral Code and Link */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ color: C.t2, fontSize: 10, marginBottom: 8, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase" }}>
              Your Referral Code
            </div>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <div style={{
                flex: 1,
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 8,
                padding: "12px 16px",
                display: "flex",
                alignItems: "center",
                gap: 10
              }}>
                <code style={{ color: C.cyan, fontSize: 14, fontWeight: 700, fontFamily: "monospace" }}>
                  {referral.referral_code || profile?.id?.substring(0, 8).toUpperCase() || "Loading..."}
                </code>
              </div>
              <Btn onClick={() => copyToClipboard(referral.referral_code || profile?.id?.substring(0, 8).toUpperCase())} size="sm">
                <Copy size={14} />
              </Btn>
            </div>

            <div style={{ color: C.t2, fontSize: 10, marginBottom: 8, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase" }}>
              Your Referral Link
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <div style={{
                flex: 1,
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 8,
                padding: "12px 16px",
                display: "flex",
                alignItems: "center",
                gap: 10
              }}>
                <Link2 size={14} style={{ color: C.cyan, flexShrink: 0 }} />
                <code style={{ color: C.t1, fontSize: 11, fontFamily: "monospace", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {referral.referral_link || "Loading..."}
                </code>
              </div>
              <Btn onClick={() => copyToClipboard(referral.referral_link)} size="sm" disabled={!referral.referral_link}>
                <Copy size={14} />
              </Btn>
            </div>
          </div>

          {/* Statistics Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: 16, marginBottom: 20 }}>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
              <div style={{ color: C.t2, fontSize: 10, marginBottom: 4, fontFamily: "monospace", letterSpacing: 1 }}>TOTAL REFERRALS</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: C.cyan }}>{referral.total_referrals || 0}</div>
            </div>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
              <div style={{ color: C.t2, fontSize: 10, marginBottom: 4, fontFamily: "monospace", letterSpacing: 1 }}>ACTIVE REFERRALS</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: C.green }}>{referral.active_referrals || 0}</div>
            </div>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
              <div style={{ color: C.t2, fontSize: 10, marginBottom: 4, fontFamily: "monospace", letterSpacing: 1 }}>PENDING EARNINGS</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: C.orange }}>${(referral.pending_earnings || 0).toFixed(2)}</div>
            </div>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
              <div style={{ color: C.t2, fontSize: 10, marginBottom: 4, fontFamily: "monospace", letterSpacing: 1 }}>LIFETIME EARNINGS</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: C.gold }}>${(referral.lifetime_earnings || 0).toFixed(2)}</div>
            </div>
          </div>

          <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", lineHeight: 1.5 }}>
            Earn 20% commission on every successful subscription payment from your referrals. Commissions are calculated automatically and credited to your pending wallet.
          </div>
        </Card>
      )}

      {/* Security */}
      <Card cls="p-6 mb-4">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <Lock size={20} style={{ color: C.accent }} />
          <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Security</h3>
        </div>
        {securityLogs && securityLogs.length > 0 ? (
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
            {securityLogs.slice(0, 10).map((log, idx) => (
              <div key={idx} style={{
                padding: "8px 12px",
                borderBottom: `1px solid ${C.border}`,
                fontSize: 11,
                color: C.t1
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontWeight: 500 }}>{log.action || "Security Event"}</span>
                  <span style={{ color: C.t2 }}>{new Date(log.created_at).toLocaleString()}</span>
                </div>
                {log.ip && <div style={{ color: C.t2 }}>IP: {log.ip}</div>}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ color: C.t2, fontSize: 12 }}>No security logs available</div>
        )}
      </Card>

      {/* User ID Display */}
      <Card cls="p-6">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <Settings size={20} style={{ color: C.accent }} />
          <h3 style={{ fontSize: 14, fontWeight: 600, color: C.t1 }}>Account Details</h3>
        </div>
        <div style={{ padding: 12, background: C.bg3, borderRadius: 6, display: "flex", alignItems: "center", gap: 8 }}>
          <input
            value={profile?.id || ""}
            readOnly
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              color: C.t1,
              fontSize: 12,
              fontFamily: "monospace",
              outline: "none"
            }}
          />
          <Btn onClick={() => copyToClipboard(profile?.id || "")} size="sm">
            <Copy size={14} />
          </Btn>
        </div>
      </Card>
    </div>
  );
}
