import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  Filter, Plus, Layers, Radio, TrendingUp, Target, Edit2,
  BarChart2, Pause, Play, Trash2, PlusCircle
} from "lucide-react";
import { endpoints } from "../api";
import {
  C, Btn, Card, Tag2, StatusDot, ProgressBar
} from "../components/ui-legacy/primitives";
import StrategyBuilder from "./StrategyBuilder";

export default function Strategies() {
  const navigate = useNavigate();
  const [view, setView] = useState("library");
  const [sel, setSel] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState({});
  const [editingStrategy, setEditingStrategy] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");

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

  useEffect(() => {
    if (!resumeBuilderStrategy) return;
    setEditingStrategy(resumeBuilderStrategy);
    setView("builder");
    if (onResumeBuilderConsumed) onResumeBuilderConsumed();
  }, [resumeBuilderStrategy]);

  // Memoised aggregates – only recompute when strategies or filter change
  const totalStrategies = useMemo(() => Array.isArray(strategies) ? strategies.length : 0, [strategies]);
  const runningStrategies = useMemo(() => Array.isArray(strategies) ? strategies.filter((s) => s.status === "running").length : 0, [strategies]);
  const totalPnl = useMemo(() => Array.isArray(strategies) ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.pnl)) ? Number(s.pnl) : 0), 0) : 0, [strategies]);
  const avgWinRate = useMemo(() => totalStrategies
    ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.wr)) ? Number(s.wr) : 0), 0) / totalStrategies
    : 0, [strategies, totalStrategies]);
  const visibleStrategies = useMemo(() => Array.isArray(strategies) ? strategies.filter((s) => filterStatus === "all" || s.status === filterStatus) : [], [strategies, filterStatus]);
  const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";

  const setProcessingFor = (id, value) =>
    setIsProcessing((prev) => ({ ...prev, [id]: value }));

  const handleDeployStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => Array.isArray(prev) ? prev.map((s) => (s.id === id ? { ...s, status: "running" } : s)) : prev);
    try {
      console.log(`📊 API CALL: POST /api/strategies/${id}/deploy`);
      const res = await endpoints.strategies.deploy(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
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
      console.log(`📊 API CALL: POST /api/strategies/${id}/pause`);
      const res = await endpoints.strategies.pause(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
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
      console.log(`📊 API CALL: DELETE /api/strategies/${id}`);
      const res = await endpoints.strategies.delete(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
      setStrategies(prevStrategies);
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
              <Btn v="outline" sz="sm" Icon={Filter} onClick={() => setFilterOpen(v => !v)}>Filter</Btn>
              {filterOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 6, minWidth: 130 }}>
                  {[
                    { id: "all", label: "All" },
                    { id: "running", label: "Running" },
                    { id: "paused", label: "Paused" },
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
                </div>
              )}
              <Btn v="primary" sz="sm" Icon={Plus} onClick={() => setView("builder")}>New Strategy</Btn>
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
              <Card key={s.id} cls="p-4 hover:border-cyan-500/20 transition-all cursor-pointer" onClick={() => setSel(sel === s.id ? null : s.id)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <StatusDot status={s.status} />
                    <span style={{ color: C.t1, fontWeight: 900, fontSize: 12 }}>{s.name}</span>
                  </div>
                  <Tag2 c={s.status === "running" ? "green" : s.status === "backtesting" ? "cyan" : s.status === "paused" ? "orange" : "red"}>
                    {s.status}
                  </Tag2>
                </div>
                <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <Tag2 c="purple">{s.type}</Tag2>
                  <Tag2 c="gold">{s.tf}</Tag2>
                </div>
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
                <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? C.green : C.red} h={3} />
                <div style={{ display: "flex", gap: 4, marginTop: 10 }}>
                  <Btn v="ghost" sz="xs" Icon={Edit2} onClick={e => { e.stopPropagation(); setEditingStrategy(s); setView("builder"); }} disabled={!!isProcessing[s.id]}>Edit</Btn>
                  <Btn v="ghost" sz="xs" Icon={BarChart2} onClick={e => e.stopPropagation()} disabled={!!isProcessing[s.id]}>Backtest</Btn>
                  {s.status === "running"
                    ? <Btn v="ghost" sz="xs" Icon={Pause} onClick={e => { e.stopPropagation(); handlePauseStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Pause</Btn>
                    : <Btn v="success" sz="xs" Icon={Play} onClick={e => { e.stopPropagation(); handleDeployStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Run</Btn>}
                  <Btn v="danger" sz="xs" Icon={Trash2} cls="ml-auto" onClick={e => { e.stopPropagation(); handleDeleteStrategy(s.id); }} disabled={!!isProcessing[s.id]} />
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
