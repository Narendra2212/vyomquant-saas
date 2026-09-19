import React, { useState, useEffect, useRef } from "react";
import { Shield, AlertTriangle, Sliders, Target, Zap, CheckCircle, Lock, Activity, TrendingDown, Settings, Save, RefreshCw } from "lucide-react";
import { api } from "../api";
import { SectionH, PanelTitle, Inp, Toast, ToastContainer, ProgressBar, RiskMeter } from "../components/common/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import wsClient from "../websocketClient";

export default function RiskSettings() {
  const [maxLoss, setMaxLoss] = useState(500);
  const [maxPos, setMaxPos] = useState(10);
  const [leverage, setLeverage] = useState(3);
  const [killSwitches, setKillSwitches] = useState([
    { key: "loss", label: "Daily Loss Limit", description: "Stop all bots if daily loss exceeds limit", active: true, icon: TrendingDown },
    { key: "blackswan", label: "Black Swan Protection", description: "Halt trading on extreme market volatility", active: true, icon: AlertTriangle },
    { key: "streak", label: "Consecutive Loss Protection", description: "Pause on 3 consecutive losing trades", active: false, icon: Activity },
    { key: "capital", label: "Capital Utilization Limit", description: "Stop at 80% capital utilization", active: true, icon: Shield },
  ]);
  const [strategyLimits, setStrategyLimits] = useState([]);
  const [marginData, setMarginData] = useState([
    { label: "Margin Ratio", value: 0, max: 100, color: token.status.profit.fg, key: "margin_ratio" },
    { label: "Free Margin", value: 0, max: 100, color: token.brand.base, key: "free_margin" },
    { label: "Risk Score", value: 0, max: 100, color: token.status.warning.fg, key: "risk_score" },
  ]);
  const [isLoadingRisk, setIsLoadingRisk] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [toast, setToast] = useState(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const sliderDebounceRef = useRef(null);
  const strategyDebounceMapRef = useRef({});

  useEffect(() => {
    const controller = new AbortController();

    const loadRiskData = async () => {
      try {
        // Fetch all user-level risk data concurrently
        const [riskRes, limitsRes, marginRes] = await Promise.allSettled([
          api.risk.getConfig(),
          api.risk.getStrategyLimits(),
          api.risk.getMarginHealth()
        ]);

        // 1. Process Base Config & Kill Switches
        if (riskRes.status === 'fulfilled' && riskRes.value) {
          const cfg = riskRes.value?.data ?? riskRes.value;
          if (cfg && typeof cfg === 'object') {
            setMaxLoss(cfg.max_daily_loss ?? 500);
            setMaxPos(cfg.max_positions ?? 10);
            setLeverage(cfg.max_leverage ?? 3);

            if (cfg.kill_switches) {
              setKillSwitches(prev => prev.map(ks => ({
                ...ks,
                active: cfg.kill_switches[ks.key] ?? ks.active
              })));
            }
          }
        }

        // 2. Process Strategy Capital Limits
        if (limitsRes.status === 'fulfilled' && limitsRes.value) {
          const limitsData = limitsRes.value?.data ?? limitsRes.value;
          const limits = Array.isArray(limitsData?.limits) ? limitsData.limits : (Array.isArray(limitsData) ? limitsData : []);
          setStrategyLimits(limits.map((s, i) => ({
            id: s.strategy_id ?? i + 1,
            name: s.strategy_name ?? `Strategy #${i + 1}`,
            allocationPct: Math.max(0, Math.min(100, Number(s.max_position_size ?? 20))),
            capitalText: `$${Number(s.max_position_size ?? 0).toLocaleString()}`,
            maxDailyTrades: s.max_daily_trades ?? 100,
            allowedSymbols: s.allowed_symbols ?? [],
            enabled: s.enabled ?? true
          })));
        }

        // 3. Process Live Margin Health
        if (marginRes.status === 'fulfilled' && marginRes.value) {
          const m = marginRes.value?.data ?? marginRes.value;
          if (m && typeof m === 'object') {
            setMarginData([
              { label: "Margin Ratio", value: Number(m.margin_ratio ?? 0), max: 100, color: token.status.profit.fg, key: "margin_ratio" },
              { label: "Free Margin", value: Number(m.free_margin ?? 0), max: 100, color: token.brand.base, key: "free_margin" },
              { label: "Risk Score", value: Number(m.risk_score ?? 0), max: 100, color: token.status.warning.fg, key: "risk_score" },
            ]);
          }
        }
      } catch (err) {
        console.error("Risk data load error:", err);
        setToast({ type: "error", msg: "Failed to load risk settings." });
      } finally {
        setIsLoadingRisk(false);
      }
    };

    loadRiskData();

    // Subscribe to live risk WebSocket events
    const unsubActivated = wsClient.subscribe("risk.kill_switch_activated", (data) => {
      setToast({ type: "error", msg: data?.message || "Emergency Kill Switch Activated across platform." });
      setKillSwitches(prev => prev.map(ks => ({ ...ks, active: true })));
    });

    const unsubRecovered = wsClient.subscribe("risk.kill_switch_recovered", () => {
      setToast({ type: "info", msg: "Emergency Kill Switch Recovered. Trading active." });
    });

    return () => {
      controller.abort();
      if (unsubActivated) unsubActivated();
      if (unsubRecovered) unsubRecovered();
    };
  }, []);

  const saveRiskConfig = async (overrides = {}) => {
    setIsSaving(true);
    try {
      const payload = {
        max_daily_loss: Number(overrides.max_daily_loss !== undefined ? overrides.max_daily_loss : maxLoss),
        max_positions: Number(overrides.max_positions !== undefined ? overrides.max_positions : maxPos),
        max_leverage: Number(overrides.max_leverage !== undefined ? overrides.max_leverage : leverage),
        circuit_breaker_armed: true,
        kill_switches: overrides.kill_switches || killSwitches.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {}),
      };
      const res = await api.risk.updateConfig(payload);
      if (res?.data) {
        setMaxLoss(res.data.max_daily_loss ?? payload.max_daily_loss);
        setMaxPos(res.data.max_positions ?? payload.max_positions);
        setLeverage(res.data.max_leverage ?? payload.max_leverage);
      }
      setToast({ type: "success", msg: "Risk parameters updated successfully." });
      setHasUnsavedChanges(false);
    } catch (err) {
      console.error("Failed to save risk config:", err);
      const errMsg = err.response?.data?.detail || err.message || "Failed to sync risk parameters.";
      setToast({ type: "error", msg: typeof errMsg === "string" ? errMsg : JSON.stringify(errMsg) });
    } finally {
      setIsSaving(false);
    }
  };

  const handleManualSave = () => {
    saveRiskConfig();
  };

  const handleResetToDefaults = () => {
    setMaxLoss(500);
    setMaxPos(10);
    setLeverage(3);
    setKillSwitches(prev => prev.map(ks => ({ ...ks, active: ks.key === 'loss' || ks.key === 'blackswan' || ks.key === 'capital' })));
    setHasUnsavedChanges(true);
    setToast({ type: "info", msg: "Settings reset to defaults. Click Save to apply." });
  };

  const handleSliderChange = (key, value) => {
    if (key === "maxLoss") setMaxLoss(value);
    if (key === "maxPos") setMaxPos(value);
    if (key === "leverage") setLeverage(value);

    setHasUnsavedChanges(true);
    clearTimeout(sliderDebounceRef.current);
    sliderDebounceRef.current = setTimeout(() => {
      saveRiskConfig({
        max_daily_loss: key === "maxLoss" ? value : maxLoss,
        max_positions: key === "maxPos" ? value : maxPos,
        max_leverage: key === "leverage" ? value : leverage,
      });
    }, 800);
  };

  const handleToggleSwitch = (switchKey) => {
    setKillSwitches(prev => {
      const next = prev.map(ks => ks.key === switchKey ? { ...ks, active: !ks.active } : ks);
      const switchPayload = next.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {});
      setHasUnsavedChanges(true);
      saveRiskConfig({ kill_switches: switchPayload });
      return next;
    });
  };

  const handleStrategyAllocationChange = (id, value) => {
    const numeric = Math.max(0, Math.min(100, Number(value)));
    setStrategyLimits(prev => prev.map(s => s.id === id ? { ...s, allocationPct: numeric } : s));
    setHasUnsavedChanges(true);

    clearTimeout(strategyDebounceMapRef.current[id]);
    strategyDebounceMapRef.current[id] = setTimeout(async () => {
      try {
        await api.risk.updateStrategyLimit(id, { max_position_size: numeric });
        setToast({ type: "success", msg: "Strategy allocation updated." });
        setHasUnsavedChanges(false);
      } catch (err) {
        setToast({ type: "error", msg: "Failed to update strategy limit." });
      }
    }, 800);
  };

  const Toggle = ({ active, onClick, disabled = false }) => (
    <div 
      onClick={!disabled ? onClick : undefined}
      style={{
        width: 44, 
        height: 24, 
        borderRadius: 12, 
        cursor: disabled ? "not-allowed" : "pointer", 
        position: "relative", 
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        background: active ? "#10b981" : "#374151",
        border: `1px solid ${active ? "#059669" : "#4b5563"}`,
        flexShrink: 0,
        opacity: disabled ? 0.5 : 1
      }}
    >
      <div style={{
        position: "absolute", 
        top: 2, 
        left: active ? 22 : 2, 
        width: 20, 
        height: 20, 
        borderRadius: 10, 
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        background: "#ffffff",
        boxShadow: "0 2px 4px rgba(0,0,0,0.2)"
      }} />
    </div>
  );

  return (
    <div style={{ padding: 24, overflowY: "auto", flex: 1, position: "relative", background: "#080a0e", color: "#e2e8f0" }}>
      {/* Toast Notification */}
      {toast && (
        <div style={{
          position: "fixed",
          top: 24,
          right: 24,
          zIndex: 1000,
          background: toast.type === "error" ? "#ef4444" : toast.type === "info" ? "#38bdf8" : "#10b981",
          color: "#ffffff",
          padding: "10px 16px",
          borderRadius: 8,
          fontSize: 12,
          fontFamily: "monospace",
          fontWeight: 700,
          boxShadow: "0 10px 25px rgba(0,0,0,0.5)"
        }}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <Shield size={28} style={{ color: "#10b981" }} />
            <h1 style={{ color: "#f8fafc", fontSize: 24, fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>Risk Management</h1>
          </div>
          <p style={{ color: "#64748b", fontSize: 13, margin: 0, fontFamily: "monospace" }}>Configure risk parameters and institutional safety guards</p>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <button
            onClick={handleResetToDefaults}
            disabled={isSaving}
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              border: "1px solid #334155",
              background: "#0c1017",
              color: "#94a3b8",
              fontSize: 12,
              fontWeight: 600,
              cursor: isSaving ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              transition: "all 0.2s"
            }}
          >
            <RefreshCw size={14} />
            Reset
          </button>
          <button
            onClick={handleManualSave}
            disabled={isSaving || !hasUnsavedChanges}
            style={{
              padding: "8px 18px",
              borderRadius: 8,
              border: "none",
              background: hasUnsavedChanges ? "#10b981" : "#334155",
              color: hasUnsavedChanges ? "#ffffff" : "#64748b",
              fontSize: 12,
              fontWeight: 700,
              cursor: hasUnsavedChanges ? "pointer" : "default",
              display: "flex",
              alignItems: "center",
              gap: 8,
              transition: "all 0.2s"
            }}
          >
            <Save size={14} />
            Save Changes
          </button>
        </div>
      </div>

      {/* Main Grid: Parameters & Kill Switches */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 20, marginBottom: 24 }}>
        {/* Core Limits */}
        <Card className="p-5 bg-[#0c1017] border-[#1e293b]">
          <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", textTransform: "uppercase", letterSpacing: "0.03em", marginBottom: 16 }}>
            Portfolio Risk Limits
          </h2>

          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: "#94a3b8", fontFamily: "monospace" }}>Max Daily Loss ($)</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: "#00d4ff", fontFamily: "monospace" }}>${maxLoss}</span>
              </div>
              <input
                type="range"
                min="50"
                max="5000"
                step="50"
                value={maxLoss}
                onChange={(e) => handleSliderChange("maxLoss", Number(e.target.value))}
                style={{ width: "100%", accentColor: "#00d4ff" }}
              />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: "#94a3b8", fontFamily: "monospace" }}>Max Concurrent Positions</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: "#00d4ff", fontFamily: "monospace" }}>{maxPos} Slots</span>
              </div>
              <input
                type="range"
                min="1"
                max="30"
                step="1"
                value={maxPos}
                onChange={(e) => handleSliderChange("maxPos", Number(e.target.value))}
                style={{ width: "100%", accentColor: "#00d4ff" }}
              />
            </div>

            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: "#94a3b8", fontFamily: "monospace" }}>Max Account Leverage</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: "#00d4ff", fontFamily: "monospace" }}>{leverage}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="20"
                step="1"
                value={leverage}
                onChange={(e) => handleSliderChange("leverage", Number(e.target.value))}
                style={{ width: "100%", accentColor: "#00d4ff" }}
              />
            </div>
          </div>
        </Card>

        {/* Emergency Kill Switches */}
        <Card className="p-5 bg-[#0c1017] border-[#1e293b]">
          <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", textTransform: "uppercase", letterSpacing: "0.03em", marginBottom: 16 }}>
            Automated Protection Guards
          </h2>

          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {killSwitches.map(ks => (
              <div key={ks.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", background: "#080a0e", borderRadius: 8, border: "1px solid #1e293b" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <ks.icon size={18} style={{ color: ks.active ? "#10b981" : "#64748b" }} />
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: "#f8fafc" }}>{ks.label}</div>
                    <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>{ks.description}</div>
                  </div>
                </div>
                <Toggle active={ks.active} onClick={() => handleToggleSwitch(ks.key)} />
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Strategy Level Allocations */}
      <Card className="p-5 bg-[#0c1017] border-[#1e293b]">
        <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", textTransform: "uppercase", letterSpacing: "0.03em", marginBottom: 16 }}>
          Strategy Capital Allocations ({strategyLimits.length})
        </h2>

        {strategyLimits.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", color: "#64748b", fontSize: 12, fontFamily: "monospace" }}>
            No strategy-specific limits configured. Limits apply globally.
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
            {strategyLimits.map(s => (
              <div key={s.id} style={{ padding: 12, background: "#080a0e", borderRadius: 8, border: "1px solid #1e293b" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#f8fafc" }}>{s.name}</span>
                  <span style={{ fontSize: 12, color: "#00d4ff", fontFamily: "monospace", fontWeight: 700 }}>{s.allocationPct}% Allocation</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={s.allocationPct}
                  onChange={(e) => handleStrategyAllocationChange(s.id, e.target.value)}
                  style={{ width: "100%", accentColor: "#00d4ff" }}
                />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
