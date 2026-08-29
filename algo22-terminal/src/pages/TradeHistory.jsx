import React, { useState, useEffect, useCallback } from "react";
import { Filter, Download, RefreshCw } from "lucide-react";
import { api } from "../api";
import { C, SectionH, Tag2 } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

export default function TradeHistory() {
  const [environment, setEnvironment] = useState("live");
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("ALL");
  const [error, setError] = useState(null);

  const toNumber = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };

  const normalizeTrades = useCallback((rows = []) =>
    (Array.isArray(rows) ? rows : []).map((row, i) => ({
      id: row.id ?? row.trade_id ?? i + 1,
      pair: row.pair ?? row.symbol ?? "N/A",
      exchange: row.exchange_id ?? row.exchange ?? (environment === "paper" ? "paper" : "binance"),
      side: String(row.side ?? "buy").toLowerCase(),
      entry: toNumber(row.entry ?? row.entry_price ?? row.price),
      exit: toNumber(row.exit ?? row.exit_price ?? row.price),
      size: toNumber(row.size ?? row.quantity ?? row.amount),
      pnl: toNumber(row.pnl ?? row.profit_loss ?? row.realized_pnl),
      fees: toNumber(row.fees ?? row.fee ?? 0),
      slip: toNumber(row.slip ?? row.slippage ?? 0),
      strat: row.strat ?? row.strategy ?? row.strategy_name ?? "Direct",
      time: row.time ?? row.executed_at ?? row.timestamp ?? new Date().toISOString(),
    })), [environment]);

  const loadTrades = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (environment === "live") {
        const data = await api.orders.getHistory();
        const rows = Array.isArray(data) ? data : data?.data || data?.trades || [];
        setTrades(normalizeTrades(rows));
      } else {
        const data = await api.paper.getTrades(100);
        const rows = Array.isArray(data) ? data : data?.trades || data?.data || [];
        setTrades(normalizeTrades(rows));
      }
    } catch (err) {
      if (err?.name !== "CanceledError" && err?.name !== "AbortError") {
        console.error("Failed loading trade history:", err);
        setError("Failed to fetch ledger. Please check backend connection.");
      }
    } finally {
      setLoading(false);
    }
  }, [environment, normalizeTrades]);

  useEffect(() => {
    loadTrades();
  }, [loadTrades]);

  const filtered = trades.filter((t) =>
    activeFilter === "ALL" ||
    (activeFilter === "BUY" && t.side === "buy") ||
    (activeFilter === "SELL" && t.side === "sell") ||
    (activeFilter === "PROFIT" && t.pnl > 0)
  );

  const totalTrades = trades.length;
  const profitableTrades = trades.filter((t) => t.pnl > 0).length;
  const losingTrades = trades.filter((t) => t.pnl < 0).length;
  const winRate = totalTrades ? (profitableTrades / totalTrades) * 100 : 0;
  const totalPnl = trades.reduce((sum, t) => sum + (Number.isFinite(t.pnl) ? t.pnl : 0), 0);

  const exportFilteredToCsv = () => {
    const headers = ["ID", "Time", "Pair", "Venue", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"];
    const escapeCsv = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = filtered.map((t) => [
      t.id, t.time, t.pair, t.exchange, t.side, t.entry, t.exit, t.size, t.pnl, t.fees, t.slip, t.strat
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.map(escapeCsv).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `algo22_ledger_${environment}_${activeFilter.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, background: "#080a0e", color: "#e2e8f0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 style={{ fontSize: "1.25rem", fontWeight: 800, color: "#f8fafc", margin: 0, letterSpacing: "-0.02em" }}>
              Trade Ledger
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
            {trades.length} total trades recorded in {environment.toUpperCase()} execution log
          </p>
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          <Button variant="outline" size="sm" onClick={loadTrades} disabled={loading}>
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            <span>Refresh</span>
          </Button>
          <Button variant="outline" size="sm" onClick={exportFilteredToCsv} disabled={filtered.length === 0}>
            <Download size={12} />
            <span>Export CSV</span>
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8, marginBottom: 14 }}>
        {[
          { l: "Total Trades", v: loading ? "..." : totalTrades, c: "#00d4ff" },
          { l: "Profitable", v: loading ? "..." : profitableTrades, c: "#10b981" },
          { l: "Losing", v: loading ? "..." : losingTrades, c: "#ef4444" },
          { l: "Win Rate", v: loading ? "..." : `${winRate.toFixed(1)}%`, c: "#00d4ff" },
          { l: "Total P&L", v: loading ? "..." : `$${totalPnl.toFixed(2)}`, c: totalPnl >= 0 ? "#10b981" : "#ef4444" },
        ].map(s => (
          <Card key={s.l} className="p-3 bg-[#0c1017] border-[#1e293b]">
            <div style={{ color: "#64748b", fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 3 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 16, fontWeight: 900, fontFamily: "monospace" }}>{s.v}</div>
          </Card>
        ))}
      </div>

      {/* Filters & Errors */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 4 }}>
          {["ALL", "BUY", "SELL", "PROFIT"].map(f => (
            <button key={f} onClick={() => setActiveFilter(f)}
              style={{ background: activeFilter === f ? "rgba(0,212,255,0.15)" : "transparent", color: activeFilter === f ? "#00d4ff" : "#64748b", border: `1px solid ${activeFilter === f ? "rgba(0,212,255,0.3)" : "#1e293b"}`, borderRadius: 6, padding: "4px 12px", fontSize: 9, fontFamily: "monospace", fontWeight: 900, cursor: "pointer", letterSpacing: 2, textTransform: "uppercase" }}>
              {f}
            </button>
          ))}
        </div>
        {error && <span style={{ color: "#ef4444", fontSize: 10, fontFamily: "monospace" }}>{error}</span>}
      </div>

      {/* Ledger Table */}
      <Card className="bg-[#0c1017] border-[#1e293b]">
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", minWidth: 780, borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #1e293b" }}>
                {["#", "Time", "Pair", "Venue", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"].map(h => (
                  <th key={h} style={{ color: "#64748b", fontWeight: 900, letterSpacing: 1.5, fontSize: 8, padding: "10px 12px", textAlign: "left", textTransform: "uppercase" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={12} style={{ padding: "20px", textAlign: "center", color: "#64748b", fontFamily: "monospace" }}>Loading trade history from engine...</td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={12} style={{ padding: "20px", textAlign: "center", color: "#64748b", fontFamily: "monospace" }}>No trades found for {environment.toUpperCase()} mode.</td>
                </tr>
              ) : (
                filtered.map(t => (
                  <tr key={t.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }} className="hover:bg-white/5 transition-colors cursor-pointer">
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>#{t.id}</td>
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>{String(t.time).includes('T') ? String(t.time).split('T')[1].slice(0, 8) : String(t.time).slice(11, 19)}</td>
                    <td style={{ padding: "8px 12px", color: "#f8fafc", fontWeight: 700 }}>{t.pair}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <span style={{ fontSize: "0.625rem", padding: "2px 5px", borderRadius: 4, background: "#0f172a", border: "1px solid #334155", color: "#cbd5e1", textTransform: "uppercase", fontWeight: 700 }}>
                        {t.exchange}
                      </span>
                    </td>
                    <td style={{ padding: "8px 12px" }}><Tag2 c={t.side === "buy" ? "green" : "red"}>{t.side.toUpperCase()}</Tag2></td>
                    <td style={{ padding: "8px 12px", color: "#94a3b8" }}>${t.entry.toLocaleString()}</td>
                    <td style={{ padding: "8px 12px", color: "#94a3b8" }}>${t.exit.toLocaleString()}</td>
                    <td style={{ padding: "8px 12px", color: "#94a3b8" }}>{t.size}</td>
                    <td style={{ padding: "8px 12px", color: t.pnl >= 0 ? "#10b981" : "#ef4444", fontWeight: 700 }}>{t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}</td>
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>${t.fees}</td>
                    <td style={{ padding: "8px 12px", color: "#64748b" }}>{t.slip}%</td>
                    <td style={{ padding: "8px 12px", color: "#94a3b8", fontSize: 9 }}>{String(t.strat).slice(0, 14)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
