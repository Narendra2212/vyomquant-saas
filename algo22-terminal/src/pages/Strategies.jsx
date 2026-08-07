import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Filter, Plus, Layers, Radio, TrendingUp, Target, Edit2,
  BarChart2, Pause, Play, Trash2, PlusCircle, Copy, Settings,
  Activity, Zap, Globe, Server, Clock, Shield
} from "lucide-react";
import { endpoints } from "../api";
import {
  C, Tag2, StatusDot, ProgressBar
} from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import StrategyBuilder from "./StrategyBuilder";

export default function Strategies() {
  const navigate = useNavigate();
  const location = useLocation();
  const [view, setView] = useState("library");
  const [strategies, setStrategies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState({});
  const [editingStrategy, setEditingStrategy] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterEnvironment, setFilterEnvironment] = useState("all");
  
  const resumeBuilderStrategy = location.state?.resumeBuilderStrategy || null;

  useEffect(() => {
    const controller = new AbortController();
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";

    const toNumber = (v, fallback = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };

    const normalizeStatus = (value = "") => {
      const s = String(value).toLowerCase();
      if (["running", "active", "live", "started"].includes(s)) return "running";
      if (["paused", "pause"].includes(s)) return "paused";
      if (["backtesting", "testing"].includes(s)) return "backtesting";
      if (["stopped", "inactive", "stop"].includes(s)) return "stopped";
      return "stopped";
    };

    const normalizeStrategies = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, i) => ({
        id: row.id ?? row.strategy_id ?? i + 1,
        name: row.name ?? row.strategy_name ?? `Strategy #${i + 1}`,
        pair: row.pair ?? row.symbol ?? "N/A",
        status: normalizeStatus(row.status),
        pnl: toNumber(row.pnl ?? row.pnl_percent ?? row.return_pct, 0),
        wr: toNumber(row.wr ?? row.win_rate ?? row.winRate, 0),
        dd: toNumber(row.dd ?? row.max_dd ?? row.maxDrawdown, 0),
        tf: row.tf ?? row.timeframe ?? "N/A",
        type: row.type ?? row.strategy_type ?? "Custom",
      }));

    const loadStrategies = async () => {
      try {
        setIsLoading(true);
        console.log("📊 API CALL: GET /api/strategies");
        const payload = await endpoints.strategies.list();
        console.log("📊 API RESPONSE:", payload);
        const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
        if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
      } catch (err) {
        console.error("📊 API ERROR: Failed to load strategies:", err.message);
      } finally {
        setIsLoading(false);
      }
    };

    loadStrategies();
    return () => controller.abort();
  }, []);

  // Handle resume from StrategyBuilder
  useEffect(() => {
    if (!resumeBuilderStrategy) return;
    setEditingStrategy(resumeBuilderStrategy);
    setView("builder");
  }, [resumeBuilderStrategy]);

  // Memoised aggregates – only recompute when strategies or filter change
  const totalStrategies = useMemo(() => Array.isArray(strategies) ? strategies.length : 0, [strategies]);
  const runningStrategies = useMemo(() => Array.isArray(strategies) ? strategies.filter((s) => s.status === "running").length : 0, [strategies]);
  const totalPnl = useMemo(() => Array.isArray(strategies) ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.pnl)) ? Number(s.pnl) : 0), 0) : 0, [strategies]);
  const avgWinRate = useMemo(() => totalStrategies
    ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.wr)) ? Number(s.wr) : 0), 0) / totalStrategies
    : 0, [strategies, totalStrategies]);
  const visibleStrategies = useMemo(() => {
    if (!Array.isArray(strategies)) return [];
    return strategies.filter((s) => {
      const statusMatch = filterStatus === "all" || s.status === filterStatus;
      const envMatch = filterEnvironment === "all" || s.environment === filterEnvironment;
      return statusMatch && envMatch;
    });
  }, [strategies, filterStatus, filterEnvironment]);
  const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";

  const setProcessingFor = (id, value) =>
    setIsProcessing((prev) => ({ ...prev, [id]: value }));

  const handleDeployStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => Array.isArray(prev) ? prev.map((s) => (s.id === id ? { ...s, status: "running" } : s)) : prev);
    try {
      // PHASE 2: Use new Strategy Operations API
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";
      const token = sessionStorage.getItem("token");
      const res = await fetch(`${API_BASE}/api/strategies/${id}/deploy`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ environment: "paper" })
      });
      const data = await res.json();
      console.log("📊 DEPLOY RESPONSE:", data);
    } catch (err) {
      console.error("📊 DEPLOY ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handlePauseStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => Array.isArray(prev) ? prev.map((s) => (s.id === id ? { ...s, status: "paused" } : s)) : prev);
    try {
      // PHASE 2: Use new Strategy Operations API
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";
      const token = sessionStorage.getItem("token");
      const res = await fetch(`${API_BASE}/api/strategies/${id}/pause`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        }
      });
      const data = await res.json();
      console.log("📊 PAUSE RESPONSE:", data);
    } catch (err) {
      console.error("📊 PAUSE ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handleDeleteStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => prev.filter((s) => s.id !== id));
    try {
      // PHASE 2: Use new Strategy Operations API
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";
      const token = sessionStorage.getItem("token");
      const res = await fetch(`${API_BASE}/api/strategies/${id}`, {
        method: "DELETE",
        headers: {
          "Authorization": `Bearer ${token}`
        }
      });
      const data = await res.json();
      console.log("📊 DELETE RESPONSE:", data);
    } catch (err) {
      console.error("📊 DELETE ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handleCloneStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    try {
      // PHASE 2: Use new Strategy Operations API
      const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";
      const token = sessionStorage.getItem("token");
      const strategy = strategies.find(s => s.id === id);
      const res = await fetch(`${API_BASE}/api/strategies/${id}/clone`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ new_name: `${strategy.name} (Copy)` })
      });
      const data = await res.json();
      console.log("📊 CLONE RESPONSE:", data);
      // Reload strategies
      const controller = new AbortController();
      const payload = await endpoints.strategies.list();
      const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
      if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
    } catch (err) {
      console.error("📊 CLONE ERROR:", err.message);
    } finally {
      setProcessingFor(id, false);
    }
  };

  if (view === "builder") return <StrategyBuilder onBack={() => setView("library")} strategy={editingStrategy} onBacktest={(payload) => navigate("/app/backtest", { state: { strategy: payload } })} />;

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {isLoading && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t3, fontFamily: "monospace" }}>
          Loading strategies...
        </div>
      )}
      {!isLoading && (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div>
              <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20, letterSpacing: -0.5 }}>Strategy Library</h1>
              <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: 3 }}>Manage, backtest, and deploy your algorithmic strategies</p>
            </div>
            <div style={{ display: "flex", gap: 8, position: "relative" }}>
              <Button variant="outline" size="sm" icon={Filter} onClick={() => setFilterOpen(v => !v)}>Filter</Button>
              {filterOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 6, minWidth: 180 }}>
                  <div style={{ marginBottom: 8, borderBottom: `1px solid ${C.border}`, paddingBottom: 4 }}>
                    <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>STATUS</span>
                  </div>
                  {[
                    { id: "all", label: "All" },
                    { id: "running", label: "Running" },
                    { id: "paused", label: "Paused" },
                    { id: "draft", label: "Draft" },
                    { id: "backtesting", label: "Backtesting" },
                  ].map(opt => (
                    <button
                      key={opt.id}
                      onClick={() => {
                        setFilterStatus(opt.id);
                        setFilterOpen(false);
                      }}
                      style={{ width: "100%", textAlign: "left", background: filterStatus === opt.id ? C.cyan + "18" : "transparent", color: filterStatus === opt.id ? C.cyan : C.t2, border: `1px solid ${filterStatus === opt.id ? C.cyan + "30" : "transparent"}`, borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "monospace", cursor: "pointer", marginBottom: 4 }}
                    >
                      {opt.label}
                    </button>
                  ))}
                  <div style={{ marginTop: 8, marginBottom: 8, borderBottom: `1px solid ${C.border}`, paddingBottom: 4 }}>
                    <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>ENVIRONMENT</span>
                  </div>
                  {[
                    { id: "all", label: "All" },
                    { id: "paper", label: "Paper" },
                    { id: "live", label: "Live" },
                  ].map(opt => (
                    <button
                      key={`env-${opt.id}`}
                      onClick={() => {
                        setFilterEnvironment(opt.id);
                        setFilterOpen(false);
                      }}
                      style={{ width: "100%", textAlign: "left", background: filterEnvironment === opt.id ? C.cyan + "18" : "transparent", color: filterEnvironment === opt.id ? C.cyan : C.t2, border: `1px solid ${filterEnvironment === opt.id ? C.cyan + "30" : "transparent"}`, borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "monospace", cursor: "pointer", marginBottom: 4 }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
              <Button variant="primary" size="sm" icon={Plus} onClick={() => setView("builder")}>New Strategy</Button>
            </div>
          </div>

          {/* Stats row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 16 }}>
            {[
              { l: "Total Strategies", v: String(totalStrategies), I: Layers, c: C.cyan },
              { l: "Running", v: String(runningStrategies), I: Radio, c: C.green },
              { l: "Total P&L", v: `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}%`, I: TrendingUp, c: totalPnl >= 0 ? C.green : C.red },
              { l: "Avg Win Rate", v: `${avgWinRate.toFixed(1)}%`, I: Target, c: C.purple },
            ].map(s => (
              <Card key={s.l} cls="p-4">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>{s.l}</span>
                  <s.I size={12} style={{ color: s.c }} />
                </div>
                <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>{s.v}</div>
              </Card>
            ))}
          </div>

          {/* Strategy Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
            {Array.isArray(visibleStrategies) && visibleStrategies.map(s => (
              <Card key={s.id} cls="p-4 hover:border-cyan-500/20 transition-all cursor-pointer" onClick={() => navigate(`/app/strategies/${s.id}`)}>
                {/* Header: Name, Status, Version */}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <StatusDot status={s.status} />
                    <span style={{ color: C.t1, fontWeight: 900, fontSize: 12 }}>{s.name}</span>
                  </div>
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    <Tag2 c="gray" style={{ fontSize: 9 }}>v{s.current_version || "1.0"}</Tag2>
                    <Tag2 c={s.status === "running" ? "green" : s.status === "backtesting" ? "cyan" : s.status === "paused" ? "orange" : s.status === "draft" ? "gray" : "red"}>
                      {s.status}
                    </Tag2>
                  </div>
                </div>

                {/* Meta Tags: Exchange, Pair, Timeframe, Environment */}
                <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <Tag2 c="purple">{s.type}</Tag2>
                  <Tag2 c="gold">{s.tf}</Tag2>
                  <Tag2 c={s.environment === "live" ? "red" : "green"}>{s.environment || "paper"}</Tag2>
                  {s.worker_region && <Tag2 c="blue"><Globe size={10} style={{ marginRight: 2 }} />{s.worker_region}</Tag2>}
                </div>

                {/* Health and Worker Status */}
                <div style={{ display: "flex", gap: 6, marginBottom: 10, fontSize: 9, fontFamily: "monospace", color: C.t3 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                    <Activity size={10} />
                    <span>Health: {s.health || "healthy"}</span>
                  </div>
                  {s.is_running && (
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <Server size={10} />
                      <span>Worker: Active</span>
                    </div>
                  )}
                  {s.exchange_status && (
                    <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                      <Shield size={10} />
                      <span>Exchange: {s.exchange_status}</span>
                    </div>
                  )}
                </div>

                {/* Performance Metrics */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 10 }}>
                  {[
                    { l: "P&L", v: `${s.pnl >= 0 ? "+" : ""}${s.pnl}%`, c: s.pnl >= 0 ? C.green : C.red },
                    { l: "Win Rate", v: `${s.wr}%`, c: C.cyan },
                    { l: "Max DD", v: `${s.dd}%`, c: C.red },
                  ].map(m => (
                    <div key={m.l} style={{ background: C.bg3, borderRadius: 6, padding: "6px 8px", textAlign: "center" }}>
                      <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, marginBottom: 2 }}>{m.l}</div>
                      <div style={{ color: m.c, fontSize: 12, fontWeight: 900, fontFamily: "monospace" }}>{m.v}</div>
                    </div>
                  ))}
                </div>

                {/* Timeline */}
                <div style={{ display: "flex", gap: 12, marginBottom: 10, fontSize: 9, fontFamily: "monospace", color: C.t3 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                    <Clock size={10} />
                    <span>Created: {s.created_at ? new Date(s.created_at).toLocaleDateString() : "N/A"}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                    <RefreshCw size={10} />
                    <span>Updated: {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : "N/A"}</span>
                  </div>
                </div>

                <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? C.green : C.red} h={3} />

                {/* Action Buttons */}
                <div style={{ display: "flex", gap: 4, marginTop: 10 }}>
                  <Button variant="ghost" size="xs" icon={Edit2} onClick={e => { e.stopPropagation(); setEditingStrategy(s); setView("builder"); }} disabled={!!isProcessing[s.id]}>Edit</Button>
                  <Button variant="ghost" size="xs" icon={Copy} onClick={e => { e.stopPropagation(); handleCloneStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Clone</Button>
                  <Button variant="ghost" size="xs" icon={BarChart2} onClick={e => e.stopPropagation()} disabled={!!isProcessing[s.id]}>Backtest</Button>
                  {s.status === "running"
                    ? <Button variant="ghost" size="xs" icon={Pause} onClick={e => { e.stopPropagation(); handlePauseStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Pause</Button>
                    : <Button variant="success" size="xs" icon={Play} onClick={e => { e.stopPropagation(); handleDeployStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Deploy</Button>}
                  <Button variant="danger" size="xs" icon={Trash2} cls="ml-auto" onClick={e => { e.stopPropagation(); handleDeleteStrategy(s.id); }} disabled={!!isProcessing[s.id]} />
                </div>
              </Card>
            ))}
            {/* Add new card */}
            <div style={{ background: C.bg1, border: `2px dashed ${C.border}`, borderRadius: 12, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: 32, cursor: "pointer", minHeight: 220, transition: "all 0.2s" }}
              onClick={() => setView("builder")} className="hover:border-cyan-500/30 hover:bg-cyan-500/3">
              <PlusCircle size={24} style={{ color: C.t4 }} />
              <span style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>Create New Strategy</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
