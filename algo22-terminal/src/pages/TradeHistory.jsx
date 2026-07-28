import React, { useState, useEffect } from "react";
import { Filter, Download } from "lucide-react";
import { api } from "../api";
import { C, Card, SectionH, Btn } from "../components/ui-legacy/primitives";
export default function TradeHistory() {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("ALL");
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    const toNumber = (v, fallback = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };

    const normalizeTrades = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, i) => ({
        id: row.id ?? row.trade_id ?? i + 1,
        pair: row.pair ?? row.symbol ?? "N/A",
        side: String(row.side ?? "buy").toLowerCase(),
        entry: toNumber(row.entry ?? row.entry_price),
        exit: toNumber(row.exit ?? row.exit_price),
        size: toNumber(row.size ?? row.quantity),
        pnl: toNumber(row.pnl ?? row.profit_loss),
        fees: toNumber(row.fees ?? row.fee),
        slip: toNumber(row.slip ?? row.slippage),
        strat: row.strat ?? row.strategy ?? row.strategy_name ?? "Direct",
        time: row.time ?? row.executed_at ?? row.timestamp ?? new Date().toISOString(),
      }));

    const loadTrades = async () => {
      setError(null);
      try {
        // Utilize globally authenticated api instance
        const data = await api.orders.getHistory();
        const rows = Array.isArray(data) ? data : data?.data || data?.trades || [];
        setTrades(normalizeTrades(rows));
      } catch (err) {
        if (err?.name !== "CanceledError" && err?.name !== "AbortError") {
          console.error("Failed loading trade history:", err);
          setError("Failed to fetch ledger. Please check backend connection.");
        }
      } finally {
        setLoading(false);
      }
    };

    loadTrades();
    return () => controller.abort();
  }, []);

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
    const headers = ["ID", "Time", "Pair", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"];
    const escapeCsv = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = filtered.map((t) => [
      t.id, t.time, t.pair, t.side, t.entry, t.exit, t.size, t.pnl, t.fees, t.slip, t.strat
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.map(escapeCsv).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `algo22_ledger_${activeFilter.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Trade Ledger" sub={`${trades.length} total trades ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Institutional Execution Log`}
        right={<div style={{ display: "flex", gap: 6 }}><Btn v="outline" sz="sm" Icon={Filter}>Filter</Btn><Btn v="outline" sz="sm" Icon={Download} onClick={exportFilteredToCsv} disabled={filtered.length === 0}>Export CSV</Btn></div>} />

      {/* Summary Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 14 }}>
        {[
          { l: "Total Trades", v: loading ? "..." : totalTrades, c: C.cyan },
          { l: "Profitable", v: loading ? "..." : profitableTrades, c: C.green },
          { l: "Losing", v: loading ? "..." : losingTrades, c: C.red },
          { l: "Win Rate", v: loading ? "..." : `${winRate.toFixed(1)}%`, c: C.cyan },
          { l: "Total P&L", v: loading ? "..." : `$${totalPnl.toFixed(2)}`, c: totalPnl >= 0 ? C.green : C.red },
        ].map(s => (
          <Card key={s.l} cls="p-3">
            <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 3 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 16, fontWeight: 900, fontFamily: "monospace" }}>{s.v}</div>
          </Card>
        ))}
      </div>

      {/* Filters & Errors */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 4 }}>
          {["ALL", "BUY", "SELL", "PROFIT"].map(f => (
            <button key={f} onClick={() => setActiveFilter(f)}
              style={{ background: activeFilter === f ? C.cyan + "20" : "transparent", color: activeFilter === f ? C.cyan : C.t3, border: `1px solid ${activeFilter === f ? C.cyan + "40" : C.border}`, borderRadius: 6, padding: "4px 12px", fontSize: 9, fontFamily: "monospace", fontWeight: 900, cursor: "pointer", letterSpacing: 2, textTransform: "uppercase" }}>
              {f}
            </button>
          ))}
        </div>
        {error && <span style={{ color: C.red, fontSize: 10, fontFamily: "monospace" }}>{error}</span>}
      </div>

      {/* Ledger Table */}
      <Card>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["#", "Time", "Pair", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"].map(h => (
                <th key={h} style={{ color: C.t3, fontWeight: 900, letterSpacing: 1.5, fontSize: 8, padding: "10px 12px", textAlign: "left", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={11} style={{ padding: "20px", textAlign: "center", color: C.t3, fontFamily: "monospace" }}>Loading trade history from engine...</td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={11} style={{ padding: "20px", textAlign: "center", color: C.t3, fontFamily: "monospace" }}>No trades found. Deploy a strategy to see ledger data.</td>
              </tr>
            ) : (
              filtered.map(t => (
                <tr key={t.id} style={{ borderBottom: `1px solid ${C.border}15` }} className="hover:bg-white/5 transition-colors cursor-pointer">
                  <td style={{ padding: "8px 12px", color: C.t3 }}>#{t.id}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>{String(t.time).includes('T') ? String(t.time).split('T')[1].slice(0, 8) : String(t.time).slice(11, 19)}</td>
                  <td style={{ padding: "8px 12px", color: C.t1, fontWeight: 700 }}>{t.pair}</td>
                  <td style={{ padding: "8px 12px" }}><Tag2 c={t.side === "buy" ? "green" : "red"}>{t.side.toUpperCase()}</Tag2></td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>${t.entry.toLocaleString()}</td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>${t.exit.toLocaleString()}</td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>{t.size}</td>
                  <td style={{ padding: "8px 12px", color: t.pnl >= 0 ? C.green : C.red, fontWeight: 700 }}>{t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>${t.fees}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>{t.slip}%</td>
                  <td style={{ padding: "8px 12px", color: C.t2, fontSize: 9 }}>{String(t.strat).slice(0, 14)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ========================
//  PAGE: EXCHANGE MANAGER
// ========================
