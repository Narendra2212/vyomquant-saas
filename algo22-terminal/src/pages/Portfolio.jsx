import React, { useState, useEffect } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from "recharts";
import { DollarSign, TrendingUp, CheckCircle, Percent } from "lucide-react";
import { C, Card, SectionH, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";

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
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Portfolio Overview" sub="Real-time asset distribution and performance analytics" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 16 }}>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Total Value</span>
            <DollarSign size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.total_value?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Unrealized P&L</span>
            <TrendingUp size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.unrealized_pnl?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Realized P&L</span>
            <CheckCircle size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.realized_pnl?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>ROI</span>
            <Percent size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.roi_percentage?.toFixed(1) || "0.0"}%` : "0.0%")}
          </div>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 12, marginBottom: 12 }}>
        {/* Equity Curve */}
        <Card cls="p-4">
          <PanelTitle title="Equity Curve" sub="90-day portfolio growth" />
          {equityCurve.length === 0 ? (
            <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
              No data available yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.green} stopOpacity={0.2} />
                    <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="value" stroke={C.green} strokeWidth={1.5} fill="url(#eg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Asset Allocation */}
        <Card cls="p-4">
          <PanelTitle title="Asset Allocation" />
          {allocation.length === 0 ? (
            <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
              No data available yet
            </div>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}>
                <PieChart width={160} height={160}>
                  <Pie data={allocation} cx={80} cy={80} innerRadius={50} outerRadius={75} dataKey="percentage" strokeWidth={0}>
                    {allocation.map((e, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                </PieChart>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {allocation.map((a, i) => (
                  <div key={a.asset} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} />
                    <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", flex: 1 }}>{a.asset}</span>
                    <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>{a.percentage.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>

      {/* P&L Heatmap */}
      <Card cls="p-4">
        <PanelTitle title="P&L Heatmap" sub="Daily performance calendar" />
        {heatmapData.length === 0 ? (
          <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
            No data available yet
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 3 }}>
            {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map(d => <div key={d} style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", textAlign: "center", padding: "2px 0", letterSpacing: 1 }}>{d}</div>)}
            {heatmapData.map((d, i) => (
              <div key={i} style={{
                height: 28, borderRadius: 4,
                background: d.pnl === null ? C.bg3 : d.pnl > 200 ? `${C.green}aa` : d.pnl > 50 ? `${C.green}55` : d.pnl > 0 ? `${C.green}22` : d.pnl < -200 ? `${C.red}aa` : d.pnl < -50 ? `${C.red}55` : `${C.red}22`,
                border: `1px solid ${d.pnl === null ? C.border : d.pnl > 0 ? `${C.green}40` : `${C.red}40`}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 8, fontFamily: "monospace", color: d.pnl === null ? C.t4 : d.pnl > 0 ? C.green : C.red,
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
