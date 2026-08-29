import React, { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { 
  UserCheck, Mail, Shield, Edit2, Check, AlertCircle, CreditCard, 
  TrendingUp, Lock, Bell, Settings, Copy, ExternalLink, RefreshCw, 
  Loader2, Link2, Key, Cpu, Activity, Eye, EyeOff, CheckCircle2, 
  ChevronRight, Send, Globe
} from "lucide-react";
import { api } from "../api";
import { C, SectionH, Inp } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

export default function Profile() {
  const navigate = useNavigate();

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
  const [showAccountId, setShowAccountId] = useState(false);
  const [copiedKey, setCopiedKey] = useState(null);

  const abortControllerRef = useRef(null);

  const loadProfileData = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();

    setLoading(true);
    setError(null);
    setErrorType(null);

    try {
      const [
        profileRes,
        billingRes,
        referralRes,
        statsRes,
        securityRes,
        notifRes
      ] = await Promise.allSettled([
        api.user.getProfile(),
        api.user.getBillingPlan(),
        api.referral.getStats(),
        api.user.getStats(),
        api.user.getSecurityLogs(20),
        api.user.getNotificationSettings()
      ]);

      // Primary Profile Contract Gate (Mandatory)
      if (profileRes.status === 'fulfilled' && profileRes.value) {
        setProfile(profileRes.value);
        setEditData({
          username: profileRes.value.username || "",
          display_name: profileRes.value.display_name || "",
          bio: profileRes.value.bio || "",
          telegram_id: profileRes.value.telegram_id || ""
        });
      } else {
        const err = profileRes.reason;
        const status = err?.response?.status || err?.status;
        console.error("Primary profile fetch failed:", err);

        if (status === 401 || status === 403) {
          setError("Authentication required. Please log in again.");
          setErrorType("auth");
        } else if (status === 404) {
          setError("Profile not found. Please contact support.");
          setErrorType("not_found");
        } else if (status === 422) {
          setError("Invalid request data. Please refresh the page.");
          setErrorType("validation");
        } else if (status >= 500) {
          setError("Server error. Please try again later.");
          setErrorType("server");
        } else if (typeof window !== "undefined" && !window.navigator?.onLine) {
          setError("Network offline. Check your internet connection.");
          setErrorType("network");
        } else if (err?.message?.includes("timeout") || err?.code === "ECONNABORTED") {
          setError("Request timeout. Please try again.");
          setErrorType("timeout");
        } else {
          setError("Failed to load profile data. Please try again.");
          setErrorType("unknown");
        }
        setProfile(null);
        return;
      }

      // Secondary Datasets (Resilient Isolated Fallbacks)
      if (billingRes.status === 'fulfilled') {
        setBilling(billingRes.value);
      } else {
        console.warn("Secondary billing plan fetch failed, rendering fallback:", billingRes.reason);
        setBilling(null);
      }

      if (referralRes.status === 'fulfilled') {
        setReferral(referralRes.value);
      } else {
        console.warn("Secondary referral stats fetch failed, rendering fallback:", referralRes.reason);
        setReferral(null);
      }

      if (statsRes.status === 'fulfilled') {
        setStats(statsRes.value);
      } else {
        console.warn("Secondary trading stats fetch failed, rendering fallback:", statsRes.reason);
        setStats(null);
      }

      if (securityRes.status === 'fulfilled') {
        setSecurityLogs(securityRes.value);
      } else {
        console.warn("Secondary security logs fetch failed, rendering fallback:", securityRes.reason);
        setSecurityLogs([]);
      }

      if (notifRes.status === 'fulfilled') {
        setNotificationSettings(notifRes.value);
      } else {
        console.warn("Secondary notification settings fetch failed, rendering fallback:", notifRes.reason);
        setNotificationSettings(null);
      }

    } catch (err) {
      if (err?.name !== "CanceledError") {
        console.error("Unexpected error loading profile data:", err);
        setError("Failed to load profile data. Please try again.");
        setErrorType("unknown");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfileData();

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [loadProfileData]);

  const handleProfileUpdate = async () => {
    try {
      setSaving(true);
      setError(null);
      await api.user.updateProfile(editData);
      setProfile(prev => ({ ...prev, ...editData }));
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
    loadProfileData();
  };

  const handleNotificationSettingChange = async (targetGroup, key, value) => {
    const previousSettings = notificationSettings;

    const currentChannels = notificationSettings?.channels || {
      email: true,
      telegram: false,
      mobile: false
    };

    const currentEvents = notificationSettings?.events || {
      trade_executed: true,
      stop_loss_triggered: true,
      daily_pnl_summary: true,
      bot_state_change: false,
      kill_switch_activated: true,
      new_login_detected: true,
      api_key_expiring: true,
      backtest_complete: false
    };

    const updatedChannels = {
      email: typeof currentChannels.email === 'boolean' ? currentChannels.email : Boolean(currentChannels.email?.active ?? true),
      telegram: typeof currentChannels.telegram === 'boolean' ? currentChannels.telegram : Boolean(currentChannels.telegram?.active ?? false),
      mobile: typeof currentChannels.mobile === 'boolean' ? currentChannels.mobile : Boolean(currentChannels.mobile?.active ?? false),
      ...(targetGroup === 'channels' ? { [key]: Boolean(value) } : {})
    };

    const updatedEvents = {
      trade_executed: Boolean(currentEvents.trade_executed ?? currentEvents.trade ?? true),
      stop_loss_triggered: Boolean(currentEvents.stop_loss_triggered ?? true),
      daily_pnl_summary: Boolean(currentEvents.daily_pnl_summary ?? true),
      bot_state_change: Boolean(currentEvents.bot_state_change ?? currentEvents.bot ?? false),
      kill_switch_activated: Boolean(currentEvents.kill_switch_activated ?? currentEvents.margin ?? true),
      new_login_detected: Boolean(currentEvents.new_login_detected ?? currentEvents.login ?? true),
      api_key_expiring: Boolean(currentEvents.api_key_expiring ?? true),
      backtest_complete: Boolean(currentEvents.backtest_complete ?? false),
      ...(targetGroup === 'events' ? { [key]: Boolean(value) } : {})
    };

    const payload = {
      channels: updatedChannels,
      events: updatedEvents
    };

    try {
      setNotificationSettings(payload);
      await api.user.updateNotificationSettings(payload);
    } catch (err) {
      console.error('Failed to update notification settings:', err);
      // Revert to previous authoritative state on error
      setNotificationSettings(previousSettings);
    }
  };

  const copyToClipboard = (text, keyName) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedKey(keyName);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  // ── Notification Toggle Switch Component ───────────────────────────────────────
  function NotificationSwitch({ label, description, icon: Icon, checked, onChange }) {
    return (
      <div 
        onClick={() => onChange(!checked)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 14px",
          background: C.bg2,
          border: `1px solid ${checked ? `${C.accent}33` : C.border}`,
          borderRadius: 8,
          cursor: "pointer",
          transition: "all 0.15s"
        }}
      >
        <div style={{ 
          color: checked ? C.accent : C.t3,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 32,
          height: 32,
          borderRadius: 6,
          background: checked ? `${C.accent}15` : C.bg3,
          transition: "all 0.15s"
        }}>
          {Icon ? <Icon size={16} /> : <Bell size={16} />}
        </div>
        
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ 
            fontSize: 12, 
            fontWeight: 600, 
            color: C.t1,
            marginBottom: 2,
            display: "flex",
            alignItems: "center",
            gap: 6
          }}>
            {label}
          </div>
          <div style={{ fontSize: 10, color: C.t3, lineHeight: 1.3 }}>
            {description}
          </div>
        </div>

        <div style={{
          width: 38,
          height: 20,
          background: checked ? C.accent : C.border,
          borderRadius: 10,
          position: "relative",
          transition: "background 0.2s",
          flexShrink: 0
        }}>
          <div style={{
            width: 16,
            height: 16,
            background: "#fff",
            borderRadius: "50%",
            position: "absolute",
            top: 2,
            left: checked ? 20 : 2,
            transition: "left 0.2s",
            boxShadow: "0 1px 3px rgba(0,0,0,0.3)"
          }} />
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "50vh", color: C.t2, fontFamily: "monospace" }}>
          <Loader2 className="animate-spin" size={32} style={{ color: C.accent, marginBottom: 12 }} />
          <div style={{ fontSize: 13, color: C.t1, fontWeight: 600 }}>Loading Trading Account Controls...</div>
          <div style={{ fontSize: 11, color: C.t3, marginTop: 4 }}>Synchronizing authoritative user contracts</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "50vh", textAlign: "center" }}>
          <AlertCircle size={48} style={{ color: C.red, marginBottom: 16 }} />
          <div style={{ color: C.red, fontSize: 16, fontWeight: 700, marginBottom: 8, letterSpacing: -0.3 }}>
            Trading Account Synchronization Error
          </div>
          <div style={{ color: C.t2, fontSize: 13, marginBottom: 24, maxWidth: 460, lineHeight: 1.5 }}>
            {error}
          </div>
          
          {errorType === "auth" && (
            <Button onClick={() => window.location.href = "/signin"} icon={Lock}>
              Go to Login
            </Button>
          )}
          
          {errorType === "network" && (
            <Button onClick={handleRetry} icon={RefreshCw}>
              Retry Connection
            </Button>
          )}
          
          {(errorType === "server" || errorType === "timeout" || errorType === "unknown" || errorType === "validation") && (
            <div style={{ display: "flex", gap: 12 }}>
              <Button onClick={handleRetry} icon={RefreshCw}>
                Retry
              </Button>
              <Button onClick={() => window.location.reload()} variant="secondary">
                Refresh Page
              </Button>
            </div>
          )}
          
          {errorType === "not_found" && (
            <Button onClick={() => navigate("/app/support")}>
              Contact Support
            </Button>
          )}
          
          {retryCount > 0 && (
            <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", marginTop: 16 }}>
              Sync attempts: {retryCount}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Format subscription and account metadata
  const planDisplay = billing?.plan 
    ? (typeof billing.plan === 'string' ? billing.plan.charAt(0).toUpperCase() + billing.plan.slice(1) + " Tier" : String(billing.plan))
    : (billing?.name || "Free Tier");
    
  const isSubscriptionActive = billing?.subscription_status === 'active' || Boolean(billing?.autoRenew) || Boolean(billing?.plan && billing.plan !== 'free');
  
  const memberSince = profile?.created_at 
    ? new Date(profile.created_at).toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
    : null;

  return (
    <div style={{ padding: "20px 24px", overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 20 }}>
      
      {/* ── Top Header & Status Bar ─────────────────────────────────────────────── */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 16, borderBottom: `1px solid ${C.border}`, paddingBottom: 16 }}>
        <div>
          <h1 style={{ color: C.t1, fontSize: 20, fontWeight: 800, letterSpacing: "-0.5px", margin: 0 }}>
            PROFILE & ACCOUNT
          </h1>
          <p style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", marginTop: 4 }}>
            Trading terminal identity, automation context, security posture, and account preferences
          </p>
        </div>

        {/* Global Platform Status Badges */}
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div style={{ 
            display: "flex", 
            alignItems: "center", 
            gap: 6, 
            background: "rgba(38, 166, 154, 0.12)", 
            border: `1px solid rgba(38, 166, 154, 0.3)`, 
            padding: "4px 10px", 
            borderRadius: 6,
            fontSize: 10,
            fontWeight: 700,
            fontFamily: "monospace",
            color: C.green
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.green, display: "inline-block" }} />
            ACCOUNT ACTIVE
          </div>

          <div style={{ 
            display: "flex", 
            alignItems: "center", 
            gap: 6, 
            background: `${C.accent}15`, 
            border: `1px solid ${C.accent}40`, 
            padding: "4px 10px", 
            borderRadius: 6,
            fontSize: 10,
            fontWeight: 700,
            fontFamily: "monospace",
            color: C.accent
          }}>
            TIER: {planDisplay}
          </div>

          {memberSince && (
            <div style={{ 
              display: "flex", 
              alignItems: "center", 
              gap: 6, 
              background: C.bg3, 
              border: `1px solid ${C.border}`, 
              padding: "4px 10px", 
              borderRadius: 6,
              fontSize: 10,
              fontFamily: "monospace",
              color: C.t2
            }}>
              Member since {memberSince}
            </div>
          )}
        </div>
      </div>

      {/* ── Main Two-Column Control Center Grid ─────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(460px, 1fr))", gap: 20 }}>
        
        {/* ══ COLUMN 1: Account, Security, Automation ════════════════════════════ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          
          {/* Card 1: Account Identity */}
          <Card cls="p-5">
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <div style={{
                  width: 56,
                  height: 56,
                  borderRadius: "50%",
                  background: C.bg3,
                  border: `2px solid ${C.border}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 24,
                  color: C.accent,
                  overflow: "hidden",
                  flexShrink: 0
                }}>
                  {profile?.avatar_url ? (
                    <img src={profile.avatar_url} alt="Avatar" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : (
                    <UserCheck size={26} />
                  )}
                </div>
                <div>
                  <h2 style={{ fontSize: 16, fontWeight: 700, color: C.t1, margin: 0 }}>
                    {profile?.display_name || profile?.username || "Trader"}
                  </h2>
                  <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", marginTop: 2 }}>
                    @{profile?.username || "unconfigured"}
                  </div>
                  <div style={{ display: "inline-block", background: C.bg3, border: `1px solid ${C.border}`, padding: "2px 6px", borderRadius: 4, fontSize: 9, fontFamily: "monospace", color: C.cyan, marginTop: 4 }}>
                    ROLE: {(profile?.role || "USER").toUpperCase()}
                  </div>
                </div>
              </div>

              <Button onClick={() => setEditingProfile(!editingProfile)} variant="outline" size="sm" icon={Edit2}>
                {editingProfile ? "Cancel" : "Edit Profile"}
              </Button>
            </div>

            {/* Inline Profile Editor Form */}
            {editingProfile ? (
              <div style={{ marginTop: 14, padding: 14, background: C.bg3, borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ display: "grid", gap: 10 }}>
                  <div>
                    <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block", fontFamily: "monospace" }}>Username</label>
                    <Inp
                      value={editData.username}
                      onChange={(e) => setEditData({ ...editData, username: e.target.value })}
                    />
                  </div>
                  <div>
                    <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block", fontFamily: "monospace" }}>Display Name</label>
                    <Inp
                      value={editData.display_name}
                      onChange={(e) => setEditData({ ...editData, display_name: e.target.value })}
                    />
                  </div>
                  <div>
                    <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block", fontFamily: "monospace" }}>Telegram Handle (@)</label>
                    <Inp
                      value={editData.telegram_id}
                      onChange={(e) => setEditData({ ...editData, telegram_id: e.target.value })}
                      placeholder="@yourtelegram"
                    />
                  </div>
                  <div>
                    <label style={{ color: C.t2, fontSize: 10, fontWeight: 600, marginBottom: 4, display: "block", fontFamily: "monospace" }}>Trader Bio / Strategy Notes</label>
                    <textarea
                      value={editData.bio}
                      onChange={(e) => setEditData({ ...editData, bio: e.target.value })}
                      style={{
                        width: "100%",
                        padding: 8,
                        background: C.bg2,
                        border: `1px solid ${C.border}`,
                        borderRadius: 6,
                        color: C.t1,
                        fontSize: 11,
                        minHeight: 60,
                        resize: "vertical",
                        fontFamily: "monospace"
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                    <Button onClick={handleProfileUpdate} disabled={saving} size="sm" icon={saving ? Loader2 : Check}>
                      {saving ? "Saving Changes..." : "Save Changes"}
                    </Button>
                    <Button onClick={() => setEditingProfile(false)} variant="secondary" size="sm">
                      Cancel
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              /* Account Metadata Details Table */
              <div style={{ display: "flex", flexDirection: "column", gap: 10, borderTop: `1px solid ${C.border}`, paddingTop: 14 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: C.t3, fontFamily: "monospace" }}>Email Address</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, color: C.t1 }}>
                    <Mail size={13} style={{ color: C.t2 }} />
                    <span>{profile?.email || "No email"}</span>
                    {profile?.email_confirmed_at ? (
                      <span style={{ display: "flex", alignItems: "center", gap: 3, color: C.green, fontSize: 10, background: "rgba(38,166,154,0.1)", padding: "1px 6px", borderRadius: 4 }}>
                        <CheckCircle2 size={11} /> Verified
                      </span>
                    ) : (
                      <span style={{ color: C.t3, fontSize: 10 }}>Unverified</span>
                    )}
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: C.t3, fontFamily: "monospace" }}>Telegram Dispatch</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, color: C.t1 }}>
                    <Send size={13} style={{ color: profile?.telegram_id ? C.cyan : C.t3 }} />
                    <span>{profile?.telegram_id ? `@${profile.telegram_id.replace(/^@/, '')}` : "Not configured"}</span>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ color: C.t3, fontFamily: "monospace" }}>Account Identifier</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <code style={{ background: C.bg3, padding: "2px 8px", borderRadius: 4, color: C.t1, fontSize: 11, fontFamily: "monospace" }}>
                      {showAccountId ? profile?.id : (profile?.id ? `${profile.id.substring(0, 8)}••••••••` : "Loading...")}
                    </code>
                    <button 
                      onClick={() => setShowAccountId(!showAccountId)}
                      style={{ background: "transparent", border: "none", color: C.t2, cursor: "pointer", display: "flex", alignItems: "center", padding: 2 }}
                      title={showAccountId ? "Hide Account ID" : "Reveal Account ID"}
                    >
                      {showAccountId ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                    <button 
                      onClick={() => copyToClipboard(profile?.id, 'account_id')}
                      style={{ background: "transparent", border: "none", color: copiedKey === 'account_id' ? C.green : C.t2, cursor: "pointer", display: "flex", alignItems: "center", padding: 2 }}
                      title="Copy Account ID"
                    >
                      {copiedKey === 'account_id' ? <Check size={13} /> : <Copy size={13} />}
                    </button>
                  </div>
                </div>

                {profile?.bio && (
                  <div style={{ fontSize: 11, color: C.t2, background: C.bg2, padding: "8px 12px", borderRadius: 6, border: `1px solid ${C.border}`, marginTop: 4 }}>
                    {profile.bio}
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* Card 2: Security & Authentication Posture */}
          <Card cls="p-5">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Shield size={18} style={{ color: C.accent }} />
                <h3 style={{ fontSize: 14, fontWeight: 700, color: C.t1, margin: 0 }}>
                  Security & Access
                </h3>
              </div>
              <span style={{ display: "flex", alignItems: "center", gap: 4, background: "rgba(38,166,154,0.12)", color: C.green, fontSize: 9, fontFamily: "monospace", fontWeight: 700, padding: "2px 8px", borderRadius: 4 }}>
                <CheckCircle2 size={10} /> PROTECTED
              </span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
              <div style={{ background: C.bg2, padding: "10px 12px", borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 4 }}>MFA AUTHENTICATION</div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: C.green, display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.green }} /> Configured
                  </span>
                  <Button onClick={() => navigate("/app/2fa")} variant="outline" size="xs">
                    Manage MFA
                  </Button>
                </div>
              </div>

              <div style={{ background: C.bg2, padding: "10px 12px", borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 4 }}>LAST ACCESS TIME</div>
                <div style={{ fontSize: 11, fontWeight: 600, color: C.t1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {securityLogs?.[0]?.created_at ? new Date(securityLogs[0].created_at).toLocaleString() : "Active Session"}
                </div>
                <div style={{ fontSize: 9, color: C.t3, fontFamily: "monospace", marginTop: 2 }}>
                  IP: {securityLogs?.[0]?.ip_address || securityLogs?.[0]?.ip || "127.0.0.1"}
                </div>
              </div>
            </div>

            {/* Recent Security Activity Stream */}
            <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 12 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: C.t2, fontFamily: "monospace" }}>RECENT SECURITY AUDIT TRAIL</span>
                <button 
                  onClick={() => navigate("/app/security-logs")} 
                  style={{ background: "transparent", border: "none", color: C.accent, fontSize: 10, fontFamily: "monospace", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                >
                  Review All Logs <ChevronRight size={12} />
                </button>
              </div>

              {securityLogs && securityLogs.length > 0 ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {securityLogs.slice(0, 3).map((log, idx) => (
                    <div key={idx} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.bg3, padding: "6px 10px", borderRadius: 6, fontSize: 11 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Lock size={12} style={{ color: C.t3 }} />
                        <span style={{ color: C.t1, fontWeight: 500 }}>{log.event_type || log.event || log.action || "Security Event"}</span>
                      </div>
                      <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                        {log.created_at ? new Date(log.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "Recent"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ color: C.t3, fontSize: 11, fontStyle: "italic", padding: "6px 0" }}>
                  No security logs available
                </div>
              )}
            </div>
          </Card>

          {/* Card 3: Automation Account Context */}
          <Card cls="p-5">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Cpu size={18} style={{ color: C.cyan }} />
                <h3 style={{ fontSize: 14, fontWeight: 700, color: C.t1, margin: 0 }}>
                  Automation Account Context
                </h3>
              </div>
              <span style={{ background: C.bg3, border: `1px solid ${C.border}`, padding: "2px 8px", borderRadius: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                TRADING ENGINE
              </span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 14 }}>
              <div style={{ background: C.bg2, padding: "10px 12px", borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>ACTIVE BOTS</div>
                <div style={{ fontSize: 18, fontWeight: 800, color: C.cyan, fontFamily: "monospace" }}>{stats?.active_bots || 0}</div>
                <div style={{ fontSize: 9, color: C.t3, marginTop: 2 }}>Total Strategies: {stats?.total_strategies || 0}</div>
              </div>

              <div style={{ background: C.bg2, padding: "10px 12px", borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>TOTAL PNL</div>
                <div style={{ fontSize: 16, fontWeight: 800, color: (stats?.total_pnl || 0) >= 0 ? C.green : C.red, fontFamily: "monospace" }}>
                  ${(stats?.total_pnl || 0).toFixed(2)}
                </div>
                <div style={{ fontSize: 9, color: C.t3, marginTop: 2 }}>Trades: {stats?.total_trades || 0}</div>
              </div>

              <div style={{ background: C.bg2, padding: "10px 12px", borderRadius: 8, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>ENVIRONMENT</div>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.t1, marginTop: 4 }}>Live + Paper</div>
                <div style={{ fontSize: 9, color: C.green, marginTop: 4, display: "flex", alignItems: "center", gap: 3 }}>
                  <span style={{ width: 5, height: 5, borderRadius: "50%", background: C.green }} /> Isolated
                </div>
              </div>
            </div>

            {/* Contextual Navigation Buttons */}
            <div style={{ display: "flex", gap: 10 }}>
              <Button onClick={() => navigate("/app/exchange")} variant="secondary" size="sm" className="flex-1" icon={Globe}>
                Manage Exchanges
              </Button>
              <Button onClick={() => navigate("/app/strategies")} variant="secondary" size="sm" className="flex-1" icon={Activity}>
                Manage Strategies
              </Button>
            </div>
          </Card>

        </div>

        {/* ══ COLUMN 2: Subscription, Notifications, Referral ═══════════════════ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          
          {/* Card 4: Subscription & Billing */}
          <Card cls="p-5">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <CreditCard size={18} style={{ color: C.accent }} />
                <h3 style={{ fontSize: 14, fontWeight: 700, color: C.t1, margin: 0 }}>
                  Subscription & Plan
                </h3>
              </div>
              <Button onClick={() => navigate("/app/billing")} variant="outline" size="xs">
                Manage Subscription
              </Button>
            </div>

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.bg2, padding: "12px 16px", borderRadius: 8, border: `1px solid ${C.border}`, marginBottom: 14 }}>
              <div>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>CURRENT TIER</div>
                <div style={{ fontSize: 16, fontWeight: 800, color: C.t1, letterSpacing: -0.3 }}>{planDisplay}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>STATUS</div>
                <div style={{ fontSize: 12, fontWeight: 700, color: isSubscriptionActive ? C.green : C.t2, display: "flex", alignItems: "center", gap: 4, justifyContent: "flex-end" }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: isSubscriptionActive ? C.green : C.t3 }} />
                  {isSubscriptionActive ? "Active" : "Inactive"}
                </div>
              </div>
            </div>

            <div style={{ fontSize: 11, color: C.t3, fontFamily: "monospace", marginBottom: 10 }}>
              Renewal Cycle: <span style={{ color: C.t1 }}>{billing?.renewal_date ? new Date(billing.renewal_date).toLocaleDateString() : "Standard 30-day Cycle"}</span>
            </div>

            {billing?.features && Array.isArray(billing.features) && (
              <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
                <div style={{ fontSize: 10, color: C.t3, fontFamily: "monospace", marginBottom: 8 }}>INCLUDED CAPABILITIES</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {billing.features.map((feature, idx) => (
                    <span key={idx} style={{
                      padding: "3px 8px",
                      background: C.bg3,
                      border: `1px solid ${C.border}`,
                      borderRadius: 4,
                      fontSize: 10,
                      color: C.t2
                    }}>
                      ✓ {feature}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {/* Card 5: Categorized Notification Preferences */}
          <Card cls="p-5">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Bell size={18} style={{ color: C.accent }} />
                <h3 style={{ fontSize: 14, fontWeight: 700, color: C.t1, margin: 0 }}>
                  Notification Preferences
                </h3>
              </div>
            </div>
            <p style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginBottom: 14 }}>
              Notification Dispatch Preferences: trade execution, risk trigger, and account security alert dispatches
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* Category 1: Trading */}
              <div>
                <div style={{ fontSize: 10, fontWeight: 700, color: C.cyan, fontFamily: "monospace", marginBottom: 6, letterSpacing: 0.5 }}>
                  ── TRADING EXECUTIONS
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <NotificationSwitch
                    label="Trade Executions"
                    description="Real-time order fill alerts and position entries"
                    icon={TrendingUp}
                    checked={Boolean(notificationSettings?.events?.trade_executed ?? notificationSettings?.events?.trade ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'trade_executed', checked)}
                  />
                  <NotificationSwitch
                    label="Daily PnL Summaries"
                    description="Daily performance and closed trade balance recap"
                    icon={Activity}
                    checked={Boolean(notificationSettings?.events?.daily_pnl_summary ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'daily_pnl_summary', checked)}
                  />
                </div>
              </div>

              {/* Category 2: Risk & Safety */}
              <div>
                <div style={{ fontSize: 10, fontWeight: 700, color: C.red, fontFamily: "monospace", marginBottom: 6, letterSpacing: 0.5 }}>
                  ── RISK & CIRCUIT BREAKERS
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <NotificationSwitch
                    label="Stop Loss & Margin Alerts"
                    description="Stop loss execution and drawdown limit notifications"
                    icon={AlertCircle}
                    checked={Boolean(notificationSettings?.events?.stop_loss_triggered ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'stop_loss_triggered', checked)}
                  />
                  <NotificationSwitch
                    label="Emergency Kill Switch & Liquidations"
                    description="Venue liquidation proximity and emergency halt activations"
                    icon={Shield}
                    checked={Boolean(notificationSettings?.events?.kill_switch_activated ?? notificationSettings?.events?.margin ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'kill_switch_activated', checked)}
                  />
                </div>
              </div>

              {/* Category 3: Automation & Security */}
              <div>
                <div style={{ fontSize: 10, fontWeight: 700, color: C.t2, fontFamily: "monospace", marginBottom: 6, letterSpacing: 0.5 }}>
                  ── AUTOMATION & SECURITY
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <NotificationSwitch
                    label="Email Notifications"
                    description="Receive verified critical alerts to your primary email address"
                    icon={Mail}
                    checked={typeof notificationSettings?.channels?.email === 'boolean' ? notificationSettings.channels.email : Boolean(notificationSettings?.channels?.email?.active ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('channels', 'email', checked)}
                  />
                  <NotificationSwitch
                    label="Bot Lifecycle State Changes"
                    description="Strategy start, pause, runtime errors, and halts"
                    icon={Cpu}
                    checked={Boolean(notificationSettings?.events?.bot_state_change ?? notificationSettings?.events?.bot ?? false)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'bot_state_change', checked)}
                  />
                  <NotificationSwitch
                    label="Security & Login Events"
                    description="New login detections and authentication state alterations"
                    icon={Lock}
                    checked={Boolean(notificationSettings?.events?.new_login_detected ?? notificationSettings?.events?.login ?? true)}
                    onChange={(checked) => handleNotificationSettingChange('events', 'new_login_detected', checked)}
                  />
                </div>
              </div>
            </div>

            <div style={{ marginTop: 12, fontSize: 10, color: C.t3, display: "flex", alignItems: "center", gap: 4 }}>
              <Lock size={11} style={{ flexShrink: 0 }} />
              <span>Mandatory risk alerts (global kill switch triggers) cannot be disabled.</span>
            </div>
          </Card>

          {/* Card 6: Referral Program (Lowered visual priority / compact) */}
          {referral && (
            <Card cls="p-5">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <ExternalLink size={16} style={{ color: C.t2 }} />
                  <h3 style={{ fontSize: 13, fontWeight: 700, color: C.t1, margin: 0 }}>
                    Affiliate & Referral Program
                  </h3>
                </div>
                <span style={{ background: "rgba(38,166,154,0.1)", color: C.green, fontSize: 9, fontFamily: "monospace", fontWeight: 700, padding: "2px 6px", borderRadius: 4 }}>
                  20% RECURRING
                </span>
              </div>

              {/* Compact Code & Link Grid */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
                <div style={{ background: C.bg2, padding: "8px 10px", borderRadius: 6, border: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 9, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>REFERRAL CODE</div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <code style={{ color: C.cyan, fontSize: 12, fontWeight: 700, fontFamily: "monospace" }}>
                      {referral.referral_code || profile?.id?.substring(0, 8).toUpperCase() || "..."}
                    </code>
                    <button 
                      onClick={() => copyToClipboard(referral.referral_code || profile?.id?.substring(0, 8).toUpperCase(), 'ref_code')}
                      style={{ background: "transparent", border: "none", color: copiedKey === 'ref_code' ? C.green : C.t2, cursor: "pointer", padding: 2 }}
                    >
                      {copiedKey === 'ref_code' ? <Check size={13} /> : <Copy size={13} />}
                    </button>
                  </div>
                </div>

                <div style={{ background: C.bg2, padding: "8px 10px", borderRadius: 6, border: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 9, color: C.t3, fontFamily: "monospace", marginBottom: 2 }}>SIGNUP LINK</div>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 120 }}>
                      {referral.referral_link || "https://..."}
                    </span>
                    <button 
                      onClick={() => copyToClipboard(referral.referral_link, 'ref_link')}
                      style={{ background: "transparent", border: "none", color: copiedKey === 'ref_link' ? C.green : C.t2, cursor: "pointer", padding: 2 }}
                    >
                      {copiedKey === 'ref_link' ? <Check size={13} /> : <Copy size={13} />}
                    </button>
                  </div>
                </div>
              </div>

              {/* Compact Metrics Grid */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, background: C.bg2, padding: 8, borderRadius: 6, border: `1px solid ${C.border}` }}>
                <div>
                  <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace" }}>TOTAL</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.cyan, fontFamily: "monospace" }}>{referral.total_referrals || 0}</div>
                </div>
                <div>
                  <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace" }}>ACTIVE</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.green, fontFamily: "monospace" }}>{referral.active_referrals || 0}</div>
                </div>
                <div>
                  <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace" }}>PENDING</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.t1, fontFamily: "monospace" }}>${(referral.pending_earnings || 0).toFixed(2)}</div>
                </div>
                <div>
                  <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace" }}>LIFETIME</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.gold || C.accent, fontFamily: "monospace" }}>${(referral.lifetime_earnings || 0).toFixed(2)}</div>
                </div>
              </div>
            </Card>
          )}

        </div>

      </div>
    </div>
  );
}
