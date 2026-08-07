import React, { useState, useEffect } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from "recharts";
import { DollarSign, TrendingUp, TrendingDown, Percent, Target, Activity } from "lucide-react";
import { C, SectionH, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Card } from "../components/ui/Card";

export default function Portfolio() {
  const [summary, setSummary] = useState(null);
  const [equityCurve, setEquityCurve] = useState([]);
  const [allocation, setAllocation] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3];

  useEffect(() => {
    const controller = new AbortController();

    const loadPortfolio = async () => {
      try {
        // Portfolio endpoints not available in backend - using empty data
        setSummary({});
        setEquityCurve([]);
        setAllocation([]);
        setHeatmapData([]);
      } catch (error) {
        if (error?.name !== "CanceledError") {
          console.error("Failed to load portfolio data", error);
        }
      } finally {
        setIsLoading(false);
      }
    };

    loadPortfolio();
    return () => controller.abort();
  }, []);

  return (
    <div style={{ padding: "20px", overflowY: "auto", flex: 1 }}>
      <SectionH title="Portfolio Overview" sub="Real-time asset distribution and performance analytics" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "10px", marginBottom: "16px" }}>
        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: C.t2, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Total Value</span>
            <DollarSign size={13} style={{ color: C.cyan }} />
          </div>
          <div className="text-heading-lg" style={{ color: C.t1, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: C.t3 }} />
                Loading...
              </span>
            ) : `$${(summary?.total_value || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
          </div>
        </Card>
        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: C.t2, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Unrealized P&L</span>
            {summary && summary.unrealized_pnl >= 0 ? <TrendingUp size={13} style={{ color: C.green }} /> : <TrendingDown size={13} style={{ color: C.red }} />}
          </div>
          <div className="text-heading-lg" style={{ color: summary && summary.unrealized_pnl >= 0 ? C.green : C.red, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: C.t3 }} />
                Loading...
              </span>
            ) : `${summary && summary.unrealized_pnl >= 0 ? "+" : ""}${(summary?.unrealized_pnl || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
          </div>
        </Card>
        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: C.t2, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Realized P&L</span>
            {summary && summary.realized_pnl >= 0 ? <TrendingUp size={13} style={{ color: C.green }} /> : <TrendingDown size={13} style={{ color: C.red }} />}
          </div>
          <div className="text-heading-lg" style={{ color: summary && summary.realized_pnl >= 0 ? C.green : C.red, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: C.t3 }} />
                Loading...
              </span>
            ) : `${summary && summary.realized_pnl >= 0 ? "+" : ""}${(summary?.realized_pnl || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
          </div>
        </Card>
        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-caption-sm" style={{ color: C.t2, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>ROI</span>
            <Percent size={13} style={{ color: C.cyan }} />
          </div>
          <div className="text-heading-lg" style={{ color: C.t1, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoading ? (
              <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Activity size={16} className="animate-spin" style={{ color: C.t3 }} />
                Loading...
              </span>
            ) : `${(summary?.roi_percentage || 0).toFixed(1)}%`}
          </div>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: "12px", marginBottom: "12px" }}>
        {/* Equity Curve */}
        <Card className="p-4">
          <PanelTitle title="Equity Curve" sub="90-day portfolio growth" />
          {equityCurve.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace" }}>
              <TrendingUp size={32} style={{ color: C.t4, marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No equity data available</span>
              <span className="text-caption-sm" style={{ color: C.t4, marginTop: "4px" }}>Connect an exchange to begin tracking</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10B981" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="value" stroke="#10B981" strokeWidth={1.5} fill="url(#eg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Asset Allocation */}
        <Card className="p-4">
          <PanelTitle title="Asset Allocation" />
          {allocation.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace" }}>
              <Target size={32} style={{ color: C.t4, marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No allocation data available</span>
              <span className="text-caption-sm" style={{ color: C.t4, marginTop: "4px" }}>Open positions to see allocation</span>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: "8px" }}>
                <PieChart width={160} height={160}>
                  <Pie data={allocation} cx={80} cy={80} innerRadius={50} outerRadius={75} dataKey="percentage" strokeWidth={0}>
                    {allocation.map((e, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                </PieChart>
              </div>
              <ul style={{ display: "flex", flexDirection: "column", gap: "5px", listStyle: "none", padding: 0, margin: 0 }} role="list">
                {allocation.map((a, i) => (
                  <li key={a.asset} style={{ display: "flex", alignItems: "center", gap: "8px" }} role="listitem">
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} aria-hidden="true" />
                    <span className="text-caption" style={{ color: C.t2, fontFamily: "monospace", flex: 1 }}>{a.asset}</span>
                    <span className="text-caption" style={{ color: C.t1, fontFamily: "monospace", fontWeight: 700 }}>{a.percentage.toFixed(1)}%</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      {/* P&L Heatmap */}
      <Card className="p-4">
        <PanelTitle title="P&L Heatmap" sub="Daily performance calendar" />
        {heatmapData.length === 0 ? (
          <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace" }}>
            <Activity size={32} style={{ color: C.t4, marginBottom: "12px", opacity: 0.5 }} />
            <span className="text-body">No P&L data available</span>
            <span className="text-caption-sm" style={{ color: C.t4, marginTop: "4px" }}>Trade history will appear here</span>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: "3px" }}>
            {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map(d => <div key={d} className="text-micro" style={{ color: C.t3, fontFamily: "monospace", textAlign: "center", padding: "2px 0", letterSpacing: 1 }}>{d}</div>)}
            {heatmapData.map((d, i) => (
              <div key={i} className="text-micro" style={{
                height: 28, borderRadius: 4,
                background: d.pnl === null ? C.bg3 : d.pnl > 200 ? `#10B981aa` : d.pnl > 50 ? `#10B98155` : d.pnl > 0 ? `#10B98122` : d.pnl < -200 ? `#EF4444aa` : d.pnl < -50 ? `#EF444455` : `#EF444422`,
                border: `1px solid ${d.pnl === null ? C.border : d.pnl > 0 ? `#10B98140` : `#EF444440`}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontFamily: "monospace", color: d.pnl === null ? C.t4 : d.pnl > 0 ? "#10B981" : "#EF4444",
                cursor: d.pnl !== null ? "pointer" : "default", fontWeight: 700,
              }} title={d.pnl !== null ? `${d.pnl >= 0 ? "+" : ""}$${d.pnl.toFixed(2)}` : ""}>
                {d.pnl !== null ? (d.pnl >= 0 ? "+" : "") + d.pnl.toFixed(0) : ""}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
