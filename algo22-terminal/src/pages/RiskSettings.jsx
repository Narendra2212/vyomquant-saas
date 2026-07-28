import React, { useState, useEffect, useRef } from "react";
import { Shield, AlertTriangle, Sliders, Target, Zap, CheckCircle } from "lucide-react";
import { endpoints } from "../api";
import { C, Card, SectionH, PanelTitle, Btn, Inp, Toast, ToastContainer, ProgressBar, RiskMeter } from "../components/ui-legacy/primitives";
export default function RiskSettings() {
  const [maxLoss, setMaxLoss] = useState(500);
  const [maxPos, setMaxPos] = useState(10);
  const [leverage, setLeverage] = useState(3);
  const [killSwitches, setKillSwitches] = useState([
    { key: "loss", l: "Stop all bots if daily loss > Max Limit", active: true },
    { key: "blackswan", l: "Halt trading on Black Swan event", active: true },
    { key: "streak", l: "Pause on 3 consecutive losses", active: false },
    { key: "capital", l: "Stop at 80% capital utilization", active: true },
  ]);
  const [strategyLimits, setStrategyLimits] = useState([]);
  const [marginData, setMarginData] = useState([
    { l: "Margin Ratio", v: 0, max: 100, c: C.green, key: "margin_ratio" },
    { l: "Free Margin", v: 0, max: 100, c: C.cyan, key: "free_margin" },
    { l: "Risk Score", v: 0, max: 100, c: C.orange, key: "risk_score" },
  ]);
  const [isLoadingRisk, setIsLoadingRisk] = useState(true);
  const [toast, setToast] = useState(null);
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
          setMaxPos(cfg.max_open_positions ?? 10);
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
          const limits = Array.isArray(limitsRes.value.data) ? limitsRes.value.data : [];
          setStrategyLimits(limits.map((s, i) => ({
            id: s.id ?? s.strategy_id ?? i + 1,
            name: s.name ?? s.strategy_name ?? `Strategy #${i + 1}`,
            allocationPct: Math.max(0, Math.min(100, Number(s.capital_limit_pct ?? 20))),
            capitalText: s.capital_text ?? `$${Number(s.capital_limit_usd ?? 0).toLocaleString()}`
          })));
        }

        // 3. Process Live Margin Health
        if (marginRes.status === 'fulfilled' && marginRes.value.data) {
          const m = marginRes.value.data;
          setMarginData([
            { l: "Margin Ratio", v: Number(m.margin_ratio ?? 0), max: 100, c: C.green, key: "margin_ratio" },
            { l: "Free Margin", v: Number(m.free_margin ?? 0), max: 100, c: C.cyan, key: "free_margin" },
            { l: "Risk Score", v: Number(m.risk_score ?? 0), max: 100, c: C.orange, key: "risk_score" },
          ]);
        }
      } catch (err) {
        console.error("Risk data load error:", err);
      } finally {
        setIsLoadingRisk(false);
      }
    };

    loadRiskData();
    return () => controller.abort();
  }, []);

  const saveRiskConfig = async (overrides = {}) => {
    try {
      await api.risk.updateConfig({
        max_daily_loss: maxLoss,
        max_open_positions: maxPos,
        max_leverage: leverage,
        kill_switches: killSwitches.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {}),
        ...overrides,
      });
      setToast({ type: "success", msg: "Risk parameters updated safely." });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to sync risk parameters." });
    }
  };

  const handleSliderChange = (key, value) => {
    if (key === "maxLoss") setMaxLoss(value);
    if (key === "maxPos") setMaxPos(value);
    if (key === "leverage") setLeverage(value);

    clearTimeout(sliderDebounceRef.current);
    sliderDebounceRef.current = setTimeout(() => {
      saveRiskConfig({
        max_daily_loss: key === "maxLoss" ? value : maxLoss,
        max_open_positions: key === "maxPos" ? value : maxPos,
        max_leverage: key === "leverage" ? value : leverage,
      });
    }, 600); // 600ms debounce prevents API spam while dragging
  };

  const handleToggleSwitch = (switchKey) => {
    setKillSwitches(prev => {
      const next = prev.map(ks => ks.key === switchKey ? { ...ks, active: !ks.active } : ks);
      const switchPayload = next.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {});
      saveRiskConfig({ kill_switches: switchPayload });
      return next;
    });
  };

  const handleStrategyAllocationChange = (id, value) => {
    const numeric = Math.max(0, Math.min(100, Number(value)));
    setStrategyLimits(prev => prev.map(s => s.id === id ? { ...s, allocationPct: numeric } : s));

    clearTimeout(strategyDebounceMapRef.current[id]);
    strategyDebounceMapRef.current[id] = setTimeout(async () => {
      try {
        await endpoints.risk.updateStrategyLimit(id, { capital_limit_pct: numeric });
        setToast({ type: "success", msg: "Strategy allocation updated." });
      } catch (err) {
        setToast({ type: "error", msg: "Failed to update strategy limit." });
      }
    }, 600);
  };

  const Toggle = ({ active, onClick }) => (
    <div onClick={onClick} style={{
      width: 38, height: 20, borderRadius: 99, cursor: "pointer", position: "relative", transition: "all 0.2s",
      background: active ? `${C.green}30` : C.bg3, border: `1px solid ${active ? `${C.green}80` : C.border}`, flexShrink: 0
    }}>
      <div style={{
        position: "absolute", top: 2, left: active ? 20 : 2, width: 14, height: 14, borderRadius: "50%", transition: "all 0.2s",
        background: active ? C.green : C.t3, boxShadow: active ? `0 0 8px ${C.green}` : "none"
      }} />
    </div>
  );

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Risk Settings" sub="Configure kill switches and position limits to protect your capital" />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Left Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Card cls="p-5">
            <PanelTitle title="Global Risk Limits" sub="Account-wide exposure ceilings" />
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[
                { k: "maxLoss", l: "Max Daily Loss", v: maxLoss, min: 100, max: 5000, step: 50, prefix: "$", c: C.red },
                { k: "maxPos", l: "Max Open Positions", v: maxPos, min: 1, max: 50, step: 1, prefix: "", c: C.orange },
                { k: "leverage", l: "Max Leverage", v: leverage, min: 1, max: 20, step: 1, prefix: "", suffix: "x", c: C.purple },
              ].map(ctrl => (
                <div key={ctrl.l}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                    <label style={{ color: C.t1, fontSize: 11, fontWeight: 700 }}>{ctrl.l}{isLoadingRisk ? " ..." : ""}</label>
                    <span style={{ color: ctrl.c, fontFamily: "monospace", fontSize: 13, fontWeight: 900 }}>{ctrl.prefix}{ctrl.v}{ctrl.suffix || ""}</span>
                  </div>
                  <input type="range" min={ctrl.min} max={ctrl.max} step={ctrl.step} value={ctrl.v} onChange={e => handleSliderChange(ctrl.k, +e.target.value)} style={{ width: "100%", accentColor: ctrl.c }} />
                </div>
              ))}
            </div>
          </Card>

          <Card cls="p-5">
            <PanelTitle title="Active Kill Switches" sub="Automated trading halts" />
            <div style={{ display: "flex", flexDirection: "column" }}>
              {killSwitches.map((ks, i) => (
                <div key={ks.key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 0", borderBottom: i === killSwitches.length - 1 ? "none" : `1px solid ${C.border}` }}>
                  <span style={{ color: ks.active ? C.t1 : C.t3, fontSize: 11, fontFamily: "monospace", flex: 1, transition: "color 0.2s" }}>{ks.l}</span>
                  <Toggle active={ks.active} onClick={() => handleToggleSwitch(ks.key)} />
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Right Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Card cls="p-5">
            <PanelTitle title="Position Limits" sub="Per-strategy capital allocation maximums" />
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {isLoadingRisk ? <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Loading limits...</div> : strategyLimits.length === 0 ? <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>No active strategies deployed.</div> : strategyLimits.map(s => (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>{s.name}</div>
                    <input type="range" min={0} max={100} step={1} value={s.allocationPct} onChange={(e) => handleStrategyAllocationChange(s.id, e.target.value)} style={{ width: "100%", accentColor: C.cyan }} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", width: 50 }}>
                    <span style={{ color: C.cyan, fontSize: 12, fontWeight: 900, fontFamily: "monospace" }}>{s.allocationPct}%</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card cls="p-5">
            <PanelTitle title="Margin Safety" sub="Live exchange margin health" />
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {marginData.map(m => (
                <div key={m.l}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{m.l}</span>
                    <span style={{ color: m.c, fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>{m.v}%</span>
                  </div>
                  <ProgressBar v={m.v} max={100} color={m.c} h={5} />
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
//  PAGE: BILLING & SUBSCRIPTIONS
// ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
