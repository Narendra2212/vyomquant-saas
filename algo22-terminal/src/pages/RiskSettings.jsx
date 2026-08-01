import React, { useState, useEffect, useRef } from "react";
import { Shield, AlertTriangle, Sliders, Target, Zap, CheckCircle, Lock, Activity, TrendingDown, Settings, Save, RefreshCw } from "lucide-react";
import { api } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Inp, Toast, ToastContainer, ProgressBar, RiskMeter } from "../components/ui-legacy/primitives";
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
    { label: "Margin Ratio", value: 0, max: 100, color: C.green, key: "margin_ratio" },
    { label: "Free Margin", value: 0, max: 100, color: C.cyan, key: "free_margin" },
    { label: "Risk Score", value: 0, max: 100, color: C.orange, key: "risk_score" },
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
        if (riskRes.status === 'fulfilled' && riskRes.value.data) {
          const cfg = riskRes.value.data;
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

        // 2. Process Strategy Capital Limits
        if (limitsRes.status === 'fulfilled' && limitsRes.value.data) {
          const limitsData = limitsRes.value.data;
          const limits = Array.isArray(limitsData.limits) ? limitsData.limits : [];
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
        if (marginRes.status === 'fulfilled' && marginRes.value.data) {
          const m = marginRes.value.data;
          setMarginData([
            { label: "Margin Ratio", value: Number(m.margin_ratio ?? 0), max: 100, color: C.green, key: "margin_ratio" },
            { label: "Free Margin", value: Number(m.free_margin ?? 0), max: 100, color: C.cyan, key: "free_margin" },
            { label: "Risk Score", value: Number(m.risk_score ?? 0), max: 100, color: C.orange, key: "risk_score" },
          ]);
        }
      } catch (err) {
        console.error("Risk data load error:", err);
        setToast({ type: "error", msg: "Failed to load risk settings." });
      } finally {
        setIsLoadingRisk(false);
      }
    };

    loadRiskData();
    return () => controller.abort();
  }, []);

  const saveRiskConfig = async (overrides = {}) => {
    setIsSaving(true);
    try {
      await api.risk.updateConfig({
        max_daily_loss: maxLoss,
        max_positions: maxPos,
        max_leverage: leverage,
        kill_switches: killSwitches.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {}),
        ...overrides,
      });
      setToast({ type: "success", msg: "Risk parameters updated successfully." });
      setHasUnsavedChanges(false);
    } catch (err) {
      setToast({ type: "error", msg: "Failed to sync risk parameters." });
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
    <div style={{ padding: 24, overflowY: "auto", flex: 1, position: "relative", background: "#0f172a" }}>
      {/* Header */}
      <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <Shield size={28} style={{ color: "#10b981" }} />
            <h1 style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700, margin: 0 }}>Risk Management</h1>
          </div>
          <p style={{ color: "#94a3b8", fontSize: 14, margin: 0 }}>Configure risk parameters to protect your trading capital</p>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <button
            onClick={handleResetToDefaults}
            disabled={isSaving}
            style={{
              padding: "10px 16px",
              borderRadius: 8,
              border: "1px solid #475569",
              background: "#1e293b",
              color: "#94a3b8",
              fontSize: 13,
              fontWeight: 600,
              cursor: isSaving ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              transition: "all 0.2s"
            }}
          >
            <RefreshCw size={16} />
            Reset
          </button>
          <button
            onClick={handleManualSave}
            disabled={isSaving || !hasUnsavedChanges}
            style={{
              padding: "10px 20px",
              borderRadius: 8,
              border: "none",
              background: hasUnsavedChanges ? "#10b981" : "#374151",
              color: "#ffffff",
              fontSize: 13,
              fontWeight: 600,
              cursor: isSaving || !hasUnsavedChanges ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 8,
              transition: "all 0.2s",
              opacity: !hasUnsavedChanges ? 0.5 : 1
            }}
          >
            {isSaving ? <RefreshCw size={16} style={{ animation: "spin 1s linear infinite" }} /> : <Save size={16} />}
            {isSaving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>

      {isLoadingRisk ? (
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 400, color: "#64748b", fontSize: 14 }}>
          <RefreshCw size={24} style={{ animation: "spin 1s linear infinite", marginRight: 12 }} />
          Loading risk settings...
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(12, 1fr)", gap: 20 }}>
          {/* Global Risk Limits - Left Column */}
          <div style={{ gridColumn: "span 6", display: "flex", flexDirection: "column", gap: 20 }}>
            <Card cls="p-6" style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: "#10b98120", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Sliders size={20} style={{ color: "#10b981" }} />
                </div>
                <div>
                  <h2 style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, margin: 0 }}>Global Risk Limits</h2>
                  <p style={{ color: "#64748b", fontSize: 12, margin: "4px 0 0 0" }}>Account-wide exposure ceilings</p>
                </div>
              </div>
              
              <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                {[
                  { k: "maxLoss", l: "Max Daily Loss", v: maxLoss, min: 100, max: 5000, step: 50, prefix: "$", c: "#ef4444", desc: "Stop trading when daily loss exceeds this amount" },
                  { k: "maxPos", l: "Max Open Positions", v: maxPos, min: 1, max: 50, step: 1, prefix: "", c: "#f59e0b", desc: "Maximum number of concurrent positions" },
                  { k: "leverage", l: "Max Leverage", v: leverage, min: 1, max: 20, step: 1, prefix: "", suffix: "x", c: "#8b5cf6", desc: "Maximum leverage multiplier per position" },
                ].map(ctrl => (
                  <div key={ctrl.l} style={{ padding: 16, background: "#0f172a", borderRadius: 8, border: "1px solid #334155" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                      <div>
                        <label style={{ color: "#f1f5f9", fontSize: 13, fontWeight: 600, display: "block", marginBottom: 4 }}>{ctrl.l}</label>
                        <span style={{ color: "#64748b", fontSize: 11 }}>{ctrl.desc}</span>
                      </div>
                      <span style={{ color: ctrl.c, fontFamily: "monospace", fontSize: 16, fontWeight: 700, background: `${ctrl.c}20`, padding: "4px 12px", borderRadius: 6 }}>
                        {ctrl.prefix}{ctrl.v}{ctrl.suffix || ""}
                      </span>
                    </div>
                    <input 
                      type="range" 
                      min={ctrl.min} 
                      max={ctrl.max} 
                      step={ctrl.step} 
                      value={ctrl.v} 
                      onChange={e => handleSliderChange(ctrl.k, +e.target.value)} 
                      style={{ 
                        width: "100%", 
                        height: 6,
                        borderRadius: 3,
                        background: "#334155",
                        outline: "none",
                        WebkitAppearance: "none",
                        cursor: "pointer"
                      }}
                    />
                  </div>
                ))}
              </div>
            </Card>

            <Card cls="p-6" style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: "#ef444420", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <AlertTriangle size={20} style={{ color: "#ef4444" }} />
                </div>
                <div>
                  <h2 style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, margin: 0 }}>Kill Switches</h2>
                  <p style={{ color: "#64748b", fontSize: 12, margin: "4px 0 0 0" }}>Automated trading halt mechanisms</p>
                </div>
              </div>
              
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {killSwitches.map((ks, i) => {
                  const IconComponent = ks.icon;
                  return (
                    <div 
                      key={ks.key} 
                      style={{ 
                        display: "flex", 
                        alignItems: "center", 
                        gap: 16, 
                        padding: 16, 
                        background: "#0f172a", 
                        borderRadius: 8, 
                        border: `1px solid ${ks.active ? "#10b98150" : "#334155"}`,
                        transition: "all 0.2s"
                      }}
                    >
                      <div style={{ width: 36, height: 36, borderRadius: 8, background: `${ks.active ? "#10b98120" : "#334155"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                        <IconComponent size={18} style={{ color: ks.active ? "#10b981" : "#64748b" }} />
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ color: ks.active ? "#f1f5f9" : "#64748b", fontSize: 13, fontWeight: 600, marginBottom: 2 }}>{ks.label}</div>
                        <div style={{ color: "#64748b", fontSize: 11 }}>{ks.description}</div>
                      </div>
                      <Toggle active={ks.active} onClick={() => handleToggleSwitch(ks.key)} />
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>

          {/* Right Column */}
          <div style={{ gridColumn: "span 6", display: "flex", flexDirection: "column", gap: 20 }}>
            <Card cls="p-6" style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: "#3b82f620", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Target size={20} style={{ color: "#3b82f6" }} />
                </div>
                <div>
                  <h2 style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, margin: 0 }}>Strategy Limits</h2>
                  <p style={{ color: "#64748b", fontSize: 12, margin: "4px 0 0 0" }}>Per-strategy capital allocation</p>
                </div>
              </div>
              
              {strategyLimits.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: "#64748b", fontSize: 13 }}>
                  <Settings size={32} style={{ margin: "0 auto 12px", opacity: 0.5 }} />
                  No active strategies deployed
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {strategyLimits.map(s => (
                    <div key={s.id} style={{ padding: 16, background: "#0f172a", borderRadius: 8, border: "1px solid #334155" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <div style={{ width: 8, height: 8, borderRadius: "50%", background: s.enabled ? "#10b981" : "#64748b" }} />
                          <span style={{ color: "#f1f5f9", fontSize: 13, fontWeight: 600 }}>{s.name}</span>
                        </div>
                        <span style={{ color: "#06b6d4", fontSize: 12, fontWeight: 700, fontFamily: "monospace" }}>{s.allocationPct}%</span>
                      </div>
                      <input 
                        type="range" 
                        min={0} 
                        max={100} 
                        step={1} 
                        value={s.allocationPct} 
                        onChange={(e) => handleStrategyAllocationChange(s.id, e.target.value)} 
                        style={{ 
                          width: "100%", 
                          height: 6,
                          borderRadius: 3,
                          background: "#334155",
                          outline: "none",
                          WebkitAppearance: "none",
                          cursor: "pointer"
                        }}
                      />
                      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 11, color: "#64748b" }}>
                        <span>Max: {s.maxDailyTrades} trades/day</span>
                        <span>{s.capitalText}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card cls="p-6" style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: "#06b6d420", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Activity size={20} style={{ color: "#06b6d4" }} />
                </div>
                <div>
                  <h2 style={{ color: "#f1f5f9", fontSize: 16, fontWeight: 700, margin: 0 }}>Margin Health</h2>
                  <p style={{ color: "#64748b", fontSize: 12, margin: "4px 0 0 0" }}>Live exchange margin metrics</p>
                </div>
              </div>
              
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {marginData.map(m => (
                  <div key={m.key}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                      <span style={{ color: "#94a3b8", fontSize: 12, fontWeight: 500 }}>{m.label}</span>
                      <span style={{ color: m.color, fontSize: 14, fontWeight: 700, fontFamily: "monospace" }}>{m.value}%</span>
                    </div>
                    <div style={{ height: 8, background: "#0f172a", borderRadius: 4, overflow: "hidden" }}>
                      <div 
                        style={{ 
                          height: "100%", 
                          width: `${m.value}%`, 
                          background: m.color, 
                          borderRadius: 4,
                          transition: "width 0.3s ease"
                        }} 
                      />
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toast && (
        <div style={{ 
          position: "fixed", 
          right: 24, 
          bottom: 24, 
          background: toast.type === "success" ? "#10b98120" : toast.type === "error" ? "#ef444420" : "#3b82f620",
          border: `1px solid ${toast.type === "success" ? "#10b981" : toast.type === "error" ? "#ef4444" : "#3b82f6"}`,
          color: toast.type === "success" ? "#10b981" : toast.type === "error" ? "#ef4444" : "#3b82f6",
          borderRadius: 12, 
          padding: "16px 20px", 
          fontSize: 13, 
          fontFamily: "system-ui", 
          fontWeight: 500, 
          zIndex: 120, 
          boxShadow: "0 10px 40px rgba(0,0,0,0.4)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          backdropFilter: "blur(10px)"
        }}>
          {toast.type === "success" && <CheckCircle size={20} />}
          {toast.type === "error" && <AlertTriangle size={20} />}
          {toast.type === "info" && <Zap size={20} />}
          {toast.msg}
        </div>
      )}

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        input[type="range"]::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: #f1f5f9;
          cursor: pointer;
          box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        }
        input[type="range"]::-moz-range-thumb {
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: #f1f5f9;
          cursor: pointer;
          border: none;
          box-shadow: 0 2px 6px rgba(0,0,0,0.3);
        }
      `}</style>
    </div>
  );
}
