import React, { useState, useEffect, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from "recharts";
import { DollarSign, TrendingUp, TrendingDown, Percent, Target, Activity, RefreshCw } from "lucide-react";
import { C, SectionH, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { api } from "../api";

export default function Portfolio() {
  const [environment, setEnvironment] = useState("live");
  const [summary, setSummary] = useState(null);
  const [positions, setPositions] = useState([]);
  const [equityCurve, setEquityCurve] = useState([]);
  const [allocation, setAllocation] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3];

  const loadPortfolioData = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);

    try {
      if (environment === "live") {
        // 1. Fetch live portfolio analytics concurrently
        const [summaryRes, posRes, equityRes, allocRes, heatmapRes] = await Promise.allSettled([
          api.portfolio.getSummary(),
          api.portfolio.getOpenPositions().catch(() => api.portfolio.getPositions()),
          api.portfolio.getEquityCurve(90),
          api.portfolio.getAllocation(),
          api.portfolio.getHeatmap(3),
        ]);

        // Process Summary
        if (summaryRes.status === "fulfilled" && summaryRes.value) {
          const s = summaryRes.value;
          const acct = s.account || s;
          setSummary({
            total_value: parseFloat(acct.total_equity ?? acct.total_value ?? 0),
            unrealized_pnl: parseFloat(acct.unrealized_pnl ?? acct.total_pnl ?? 0),
            realized_pnl: parseFloat(acct.realized_pnl ?? 0),
            available_balance: parseFloat(acct.available_balance ?? acct.total_equity ?? 0),
            roi_percentage: parseFloat(acct.pnl_pct ?? s.roi_pct ?? 0),
          });
        } else {
          setSummary({
            total_value: 0,
            unrealized_pnl: 0,
            realized_pnl: 0,
            available_balance: 0,
            roi_percentage: 0,
          });
        }

        // Process Open Positions
        if (posRes.status === "fulfilled" && Array.isArray(posRes.value)) {
          setPositions(posRes.value.map((p, i) => ({
            id: p.id || p.position_id || `pos_${i}`,
            symbol: p.symbol || "UNKNOWN",
            exchange: p.exchange_id || p.exchange || "binance",
            side: (p.side || "long").toLowerCase(),
            size: parseFloat(p.contracts || p.size || p.quantity || 0),
            entryPrice: parseFloat(p.entry_price || p.entryPrice || 0),
            markPrice: parseFloat(p.mark_price || p.currentPrice || p.entry_price || 0),
            unrealizedPnl: parseFloat(p.unrealized_pnl || p.unrealizedPnl || 0),
          })));
        } else {
          setPositions([]);
        }

        // Process Equity Curve
        if (equityRes.status === "fulfilled" && Array.isArray(equityRes.value)) {
          const mappedCurve = equityRes.value.map(row => ({
            date: row.timestamp
              ? new Date(row.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
              : (row.date || ""),
            value: parseFloat(row.equity ?? row.value ?? 0),
          }));
          setEquityCurve(mappedCurve);
        } else {
          setEquityCurve([]);
        }

        // Process Asset Allocation
        if (allocRes.status === "fulfilled" && Array.isArray(allocRes.value)) {
          const mappedAlloc = allocRes.value.map(a => ({
            asset: a.asset || "USDT",
            percentage: parseFloat(a.pct ?? a.percentage ?? 0),
            value_usd: parseFloat(a.value_usd ?? a.value ?? 0),
          }));
          setAllocation(mappedAlloc);
        } else {
          setAllocation([]);
        }

        // Process Heatmap Data
        if (heatmapRes.status === "fulfilled" && Array.isArray(heatmapRes.value)) {
          const mappedHeatmap = heatmapRes.value.map(h => ({
            date: h.date,
            pnl: h.pnl_usd !== undefined ? parseFloat(h.pnl_usd) : (h.pnl !== undefined ? parseFloat(h.pnl) : null),
          }));
          setHeatmapData(mappedHeatmap);
        } else {
          setHeatmapData([]);
        }
      } else {
        // 2. Fetch Paper Trading portfolio analytics
        const [paperSumRes, paperPosRes] = await Promise.allSettled([
          api.paper.getSummary(),
          api.paper.getPositions(),
        ]);

        if (paperSumRes.status === "fulfilled" && paperSumRes.value) {
          const pSum = paperSumRes.value;
          setSummary({
            total_value: parseFloat(pSum.total_equity ?? pSum.balance ?? 100000),
            unrealized_pnl: parseFloat(pSum.unrealized_pnl ?? 0),
            realized_pnl: parseFloat(pSum.realized_pnl ?? 0),
            available_balance: parseFloat(pSum.available_balance ?? pSum.balance ?? 100000),
            roi_percentage: parseFloat(pSum.roi_pct ?? 0),
          });
        } else {
          setSummary({
            total_value: 100000,
            unrealized_pnl: 0,
            realized_pnl: 0,
            available_balance: 100000,
            roi_percentage: 0,
          });
        }

        if (paperPosRes.status === "fulfilled" && Array.isArray(paperPosRes.value)) {
          setPositions(paperPosRes.value.map((p, i) => ({
            id: p.id || `paper_pos_${i}`,
            symbol: p.symbol || "UNKNOWN",
            exchange: "paper",
            side: (p.side || "long").toLowerCase(),
            size: parseFloat(p.quantity || p.size || 0),
            entryPrice: parseFloat(p.entry_price || 0),
            markPrice: parseFloat(p.current_price || p.entry_price || 0),
            unrealizedPnl: parseFloat(p.unrealized_pnl || 0),
          })));
        } else {
          setPositions([]);
        }

        setEquityCurve([]);
        setAllocation([
          { asset: "USD (Simulated)", percentage: 100, value_usd: summary?.total_value || 100000 }
        ]);
        setHeatmapData([]);
      }

    } catch (err) {
      console.error("Portfolio data load error:", err);
      setLoadError("Failed to fetch live portfolio metrics.");
    } finally {
      setIsLoading(false);
    }
  }, [environment]);

  useEffect(() => {
    loadPortfolioData();
  }, [loadPortfolioData]);

  return (
    <div style={{ padding: "20px", overflowY: "auto", flex: 1, background: "#080a0e", color: "#e2e8f0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <h1 style={{ fontSize: "1.25rem", fontWeight: 800, color: "#f8fafc", margin: 0, letterSpacing: "-0.02em" }}>
              Portfolio Analytics
            </h1>

            {/* LIVE / PAPER Environment Toggle */}
            <div style={{
              display: "inline-flex",
              alignItems: "center",
              background: "#0f141c",
              padding: "2px",
              borderRadius: "8px",
              border: "1px solid #1e293b"
            }}>
              <button
                onClick={() => setEnvironment("live")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  border: "none",
                  cursor: "pointer",
                  background: environment === "live" ? "rgba(16, 185, 129, 0.2)" : "transparent",
                  color: environment === "live" ? "#10b981" : "#64748b",
                  boxShadow: environment === "live" ? "inset 0 0 0 1px #10b981" : "none"
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: environment === "live" ? "#10b981" : "#475569" }} />
                LIVE
              </button>

              <button
                onClick={() => setEnvironment("paper")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  border: "none",
                  cursor: "pointer",
                  background: environment === "paper" ? "rgba(99, 102, 241, 0.2)" : "transparent",
                  color: environment === "paper" ? "#818cf8" : "#64748b",
                  boxShadow: environment === "paper" ? "inset 0 0 0 1px #6366f1" : "none"
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: environment === "paper" ? "#818cf8" : "#475569" }} />
                PAPER
              </button>
            </div>
          </div>
          <p style={{ fontSize: "0.75rem", color: "#64748b", margin: "4px 0 0" }}>
            Institutional capital allocation, asset distribution, and historical performance ledger
          </p>
        </div>

        <Button
          variant="outline"
          size="xs"
          onClick={loadPortfolioData}
          disabled={isLoading}
          className="flex items-center gap-1.5"
        >
          <RefreshCw size={12} className={isLoading ? "animate-spin" : ""} />
          <span>Refresh</span>
        </Button>
      </div>

      {/* Top 4 Summary Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "10px", marginBottom: "16px" }}>
        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Total Equity</span>
            <DollarSign size={13} style={{ color: "#00d4ff" }} />
          </div>
          <div className="text-heading-lg" style={{ color: "#f8fafc", fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: "#64748b" }} />
                Loading...
              </span>
            ) : `$${(summary?.total_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Unrealized P&L</span>
            {summary && summary.unrealized_pnl >= 0 ? <TrendingUp size={13} style={{ color: "#10b981" }} /> : <TrendingDown size={13} style={{ color: "#ef4444" }} />}
          </div>
          <div className="text-heading-lg" style={{ color: summary && summary.unrealized_pnl >= 0 ? "#10b981" : "#ef4444", fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: "#64748b" }} />
                Loading...
              </span>
            ) : `${summary && summary.unrealized_pnl >= 0 ? "+" : ""}${(summary?.unrealized_pnl || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Realized P&L</span>
            {summary && summary.realized_pnl >= 0 ? <TrendingUp size={13} style={{ color: "#10b981" }} /> : <TrendingDown size={13} style={{ color: "#ef4444" }} />}
          </div>
          <div className="text-heading-lg" style={{ color: summary && summary.realized_pnl >= 0 ? "#10b981" : "#ef4444", fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: "#64748b" }} />
                Loading...
              </span>
            ) : `${summary && summary.realized_pnl >= 0 ? "+" : ""}${(summary?.realized_pnl || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Available Cash</span>
            <DollarSign size={13} style={{ color: "#00d4ff" }} />
          </div>
          <div className="text-heading-lg" style={{ color: "#f8fafc", fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: "#64748b" }} />
                Loading...
              </span>
            ) : `$${(summary?.available_balance || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          </div>
        </Card>
      </div>

      {/* Open Positions Deep-Dive Table */}
      <Card className="p-4 mb-4 bg-[#0c1017] border-[#1e293b]">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
          <div>
            <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
              Open Positions Ledger ({positions.length})
            </h2>
            <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Live mark-to-market valuations</span>
          </div>
        </div>

        {positions.length === 0 ? (
          <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
            No open positions currently held in {environment.toUpperCase()} mode.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 650, borderCollapse: "collapse", fontSize: "0.75rem", fontFamily: "monospace" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Venue</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Side</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Contracts</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Entry Price</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Mark Price</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(pos => (
                  <tr key={pos.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                    <td style={{ padding: "8px 10px", color: "#f8fafc", fontWeight: 700 }}>{pos.symbol}</td>
                    <td style={{ padding: "8px 10px" }}>
                      <span style={{ fontSize: "0.625rem", padding: "2px 6px", borderRadius: 4, background: "#0f172a", border: "1px solid #334155", color: "#cbd5e1", textTransform: "uppercase", fontWeight: 700 }}>
                        {pos.exchange}
                      </span>
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      <span style={{ fontSize: "0.625rem", padding: "2px 6px", borderRadius: 4, background: pos.side === "long" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)", color: pos.side === "long" ? "#10b981" : "#ef4444", fontWeight: 700, textTransform: "uppercase" }}>
                        {pos.side}
                      </span>
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>{pos.size}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#94a3b8" }}>${pos.entryPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>${pos.markPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: pos.unrealizedPnl >= 0 ? "#10b981" : "#ef4444" }}>
                      {pos.unrealizedPnl >= 0 ? "+" : ""}${pos.unrealizedPnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Analytics Grid: Equity Curve & Allocation */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: "12px", marginBottom: "12px" }}>
        {/* Equity Curve Chart */}
        <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
          <PanelTitle title="Equity Curve" sub="90-day institutional portfolio trajectory" />
          {equityCurve.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
              <TrendingUp size={32} style={{ color: "#334155", marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No equity curve data available</span>
              <span className="text-caption-sm" style={{ color: "#475569", marginTop: "4px" }}>Connect an exchange and launch a strategy to track growth</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10B981" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="date" stroke="#64748b" fontSize={10} tickLine={false} />
                <YAxis domain={["auto", "auto"]} stroke="#64748b" fontSize={10} tickLine={false} tickFormatter={v => `$${v.toLocaleString()}`} />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="value" stroke="#10B981" strokeWidth={2} fill="url(#eg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Asset Allocation Donut */}
        <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
          <PanelTitle title="Asset Allocation" sub="Capital distribution" />
          {allocation.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
              <Target size={32} style={{ color: "#334155", marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No allocation data</span>
              <span className="text-caption-sm" style={{ color: "#475569", marginTop: "4px" }}>Open positions will appear here</span>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: "8px" }}>
                <PieChart width={160} height={160}>
                  <Pie data={allocation} cx={80} cy={80} innerRadius={48} outerRadius={72} dataKey="percentage" strokeWidth={0}>
                    {allocation.map((e, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                </PieChart>
              </div>
              <ul style={{ display: "flex", flexDirection: "column", gap: "6px", listStyle: "none", padding: 0, margin: 0 }} role="list">
                {allocation.map((a, i) => (
                  <li key={a.asset} style={{ display: "flex", alignItems: "center", gap: "8px" }} role="listitem">
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} aria-hidden="true" />
                    <span className="text-caption" style={{ color: "#94a3b8", fontFamily: "monospace", flex: 1 }}>{a.asset}</span>
                    <span className="text-caption" style={{ color: "#f8fafc", fontFamily: "monospace", fontWeight: 700 }}>{a.percentage.toFixed(1)}%</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      {/* P&L Heatmap */}
      <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
        <PanelTitle title="P&L Heatmap" sub="Daily execution performance calendar and trade history" />
        {heatmapData.length === 0 ? (
          <div style={{ height: 160, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
            <Activity size={28} style={{ color: "#334155", marginBottom: "10px", opacity: 0.5 }} />
            <span className="text-body">No daily trade P&L records</span>
            <span className="text-caption-sm" style={{ color: "#475569", marginTop: "4px" }}>Executed trades and trade history from live and paper bots will appear here</span>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: "4px", marginTop: 8 }}>
            {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map(d => (
              <div key={d} className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", textAlign: "center", padding: "2px 0", letterSpacing: 1 }}>{d}</div>
            ))}
            {heatmapData.map((d, i) => (
              <div
                key={i}
                className="text-micro"
                style={{
                  height: 32,
                  borderRadius: 4,
                  background: d.pnl === null ? "#080a0e" : d.pnl > 200 ? `#10B981aa` : d.pnl > 50 ? `#10B98155` : d.pnl > 0 ? `#10B98122` : d.pnl < -200 ? `#EF4444aa` : d.pnl < -50 ? `#EF444455` : `#EF444422`,
                  border: `1px solid ${d.pnl === null ? "#1e293b" : d.pnl > 0 ? `#10B98140` : `#EF444440`}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontFamily: "monospace",
                  color: d.pnl === null ? "#475569" : d.pnl > 0 ? "#10B981" : "#EF4444",
                  cursor: d.pnl !== null ? "pointer" : "default",
                  fontWeight: 700,
                }}
                title={d.pnl !== null ? `${d.date}: ${d.pnl >= 0 ? "+" : ""}$${d.pnl.toFixed(2)}` : d.date || ""}
              >
                {d.pnl !== null ? `${d.pnl >= 0 ? "+" : ""}${d.pnl.toFixed(0)}` : ""}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
