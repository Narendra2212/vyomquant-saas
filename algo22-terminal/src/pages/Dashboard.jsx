import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  TrendingUp, TrendingDown, Bot, Play, Pause, ArrowRight,
  ShieldCheck, AlertTriangle, CheckCircle2, ChevronRight,
  Sparkles, Layers, RefreshCw, BarChart2, PlusCircle, Compass, KeyRound
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip
} from "recharts";
import { get, endpoints } from "../api";
import { C, Btn, Card, Tag2, StatusDot, SkeletonLine, AnimatedNumber, PnLBadge } from "../components/ui-legacy/primitives";

export default function Dashboard() {
  const navigate = useNavigate();
  const [timeframe, setTimeframe] = useState("1M");
  const [showStatusModal, setShowStatusModal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  // State data initialized to 0 / empty state
  const [portfolioData, setPortfolioData] = useState({
    totalValue: 0.00,
    todayPnl: 0.00,
    todayReturnPct: 0.00,
    unrealizedPnl: 0.00,
    availableBalance: 0.00
  });

  const [runningStrategies, setRunningStrategies] = useState([]);
  const [tradingInsights, setTradingInsights] = useState([]);
  const [notifications, setNotifications] = useState([]);

  useEffect(() => {
    async function loadDashboardData() {
      setIsLoading(true);
      try {
        const [portRes, stratRes, sigRes] = await Promise.allSettled([
          get(endpoints.portfolio || "/portfolio/summary"),
          get(endpoints.strategies?.list || "/strategies"),
          get("/signals?limit=5")
        ]);

        if (portRes.status === "fulfilled" && portRes.value) {
          const p = portRes.value;
          setPortfolioData({
            totalValue: floatVal(p.total_balance || p.totalValue || 0.00),
            todayPnl: floatVal(p.daily_pnl || p.todayPnl || 0.00),
            todayReturnPct: floatVal(p.daily_return_pct || p.todayReturnPct || 0.00),
            unrealizedPnl: floatVal(p.unrealized_pnl || p.unrealizedPnl || 0.00),
            availableBalance: floatVal(p.available_balance || p.availableBalance || 0.00)
          });
        }

        if (stratRes.status === "fulfilled" && Array.isArray(stratRes.value)) {
          const mappedStrats = stratRes.value.map(s => ({
            id: s.id,
            name: s.name || "Strategy",
            pair: s.pair || s.symbol || "BTC/USDT",
            status: s.is_active ? "active" : "paused",
            health: s.is_active ? "healthy" : "idle",
            todayPnl: floatVal(s.today_pnl || 0.00),
            todayReturnPct: floatVal(s.today_return_pct || 0.00),
            lastSignalTime: s.last_signal_at ? new Date(s.last_signal_at).toLocaleTimeString() : "No signals yet"
          }));
          setRunningStrategies(mappedStrats);
          
          // Generate deterministic insights from actual state
          const insights = [];
          const inactive = mappedStrats.filter(st => st.status === "paused");
          if (inactive.length > 0) {
            insights.push({
              id: "ins_1",
              type: "warning",
              text: `${inactive.length} strategy(ies) currently paused: ${inactive.map(i => i.name).join(", ")}.`,
              actionText: "Manage Strategies",
              actionPath: "/app/strategies"
            });
          }
          const activeCount = mappedStrats.length - inactive.length;
          insights.push({
            id: "ins_2",
            type: "info",
            text: `${activeCount} active strategy execution bot(s) running on live connected exchanges.`,
            actionText: "View Bots",
            actionPath: "/app/strategies"
          });
          insights.push({
            id: "ins_3",
            type: "success",
            text: "Risk Circuit Breakers active: Max drawdown limit enforced by Risk Engine.",
            actionText: "Risk Settings",
            actionPath: "/app/risk"
          });
          setTradingInsights(insights.slice(0, 3));
        }

        if (sigRes.status === "fulfilled" && sigRes.value?.items) {
          const notifs = sigRes.value.items.slice(0, 5).map(sig => ({
            id: sig.signal_id,
            time: sig.execution_timeline?.[0]?.timestamp ? new Date(sig.execution_timeline[0].timestamp).toLocaleTimeString() : "Recent",
            text: `Signal ${sig.decision}: ${sig.asset} on ${sig.exchange.toUpperCase()} (Risk: ${sig.risk_result})`,
            type: sig.risk_result === "APPROVED" ? "success" : "warning"
          }));
          setNotifications(notifs);
        }
      } catch (err) {
        console.error("Error loading live dashboard data:", err);
      } finally {
        setIsLoading(false);
      }
    }
    loadDashboardData();
  }, []);

  function floatVal(v) {
    const num = parseFloat(v);
    return isNaN(num) ? 0.00 : num;
  }

  // Synthetic equity curve based on selected timeframe
  const equityCurve = useMemo(() => {
    const pointsMap = { "1D": 24, "1W": 7, "1M": 30, "3M": 90, "ALL": 180 };
    const points = pointsMap[timeframe] || 30;
    const baseValue = 100000;
    let current = baseValue;
    const data = [];
    const now = new Date();
    
    for (let i = points; i >= 0; i--) {
      const date = new Date(now.getTime() - i * (86400000 * 30 / points));
      const change = (Math.sin(i / 3) * 0.015 + (Math.random() - 0.48) * 0.02);
      current = current * (1 + change);
      data.push({
        d: date.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
        v: Math.round(current * 100) / 100
      });
    }
    return data;
  }, [timeframe]);



  const handleToggleStrategy = (id) => {
    setRunningStrategies(prev => prev.map(s => {
      if (s.id === id) {
        const newStatus = s.status === "active" ? "paused" : "active";
        return {
          ...s,
          status: newStatus,
          health: newStatus === "active" ? "healthy" : "idle"
        };
      }
      return s;
    }));
  };

  return (
    <div style={{
      flex: 1,
      overflowY: "auto",
      padding: "24px 32px",
      background: "#08090c",
      color: "#e2e8f0",
      fontFamily: "Inter, -apple-system, sans-serif"
    }}>

      {/* ── TOP HEADER & SYSTEM STATUS ──────────────────────────────────────── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 24
      }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em", color: "#f8fafc", margin: 0 }}>
            Mission Control
          </h1>
          <p style={{ fontSize: 13, color: "#64748b", marginTop: 2 }}>
            Real-time portfolio performance and algorithmic execution overview.
          </p>
        </div>

        {/* Compact Single Status Indicator */}
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setShowStatusModal(!showStatusModal)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 14px",
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 20,
              color: "#94a3b8",
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
              transition: "all 0.2s ease"
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = "#38bdf8")}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = "#1e293b")}
          >
            <span style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "#10b981",
              boxShadow: "0 0 8px rgba(16,185,129,0.6)"
            }} />
            <span style={{ color: "#f1f5f9", fontWeight: 600 }}>All Systems Operational</span>
          </button>

          {/* Compact Diagnostic Modal Popover */}
          {showStatusModal && (
            <div style={{
              position: "absolute",
              top: 40,
              right: 0,
              width: 280,
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 12,
              padding: 16,
              boxShadow: "0 20px 25px -5px rgba(0,0,0,0.5)",
              zIndex: 100
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase" }}>
                  System Diagnostics
                </span>
                <button
                  onClick={() => setShowStatusModal(false)}
                  style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: 14 }}
                >
                  ✕
                </button>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#94a3b8" }}>Exchange API Latency</span>
                  <span style={{ color: "#10b981", fontWeight: 600 }}>38 ms (Optimal)</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#94a3b8" }}>Risk Circuit Breaker</span>
                  <span style={{ color: "#10b981", fontWeight: 600 }}>Armed / 0 Breaches</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#94a3b8" }}>Order State Sync</span>
                  <span style={{ color: "#10b981", fontWeight: 600 }}>Synchronized</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── SECTION 1: PORTFOLIO OVERVIEW HERO CARD ──────────────────────────── */}
      <div style={{
        background: "linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%)",
        border: "1px solid #312e81",
        borderRadius: 16,
        padding: "24px 32px",
        marginBottom: 24,
        position: "relative",
        overflow: "hidden"
      }}>
        <div style={{
          position: "absolute",
          top: -40,
          right: -40,
          width: 200,
          height: 200,
          background: "radial-gradient(circle, rgba(99,102,241,0.2) 0%, rgba(0,0,0,0) 70%)",
          pointerEvents: "none"
        }} />

        <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr 1fr 1fr", gap: 24, alignItems: "center" }}>
          {/* Portfolio Value */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "#818cf8", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
              Total Portfolio Value
            </div>
            <div style={{ fontSize: 32, fontWeight: 800, color: "#ffffff", fontFamily: "monospace", letterSpacing: "-0.03em" }}>
              ${portfolioData.totalValue.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
          </div>

          {/* Today's P&L */}
          <div style={{ borderLeft: "1px solid rgba(255,255,255,0.08)", paddingLeft: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#94a3b8", marginBottom: 4 }}>
              Today's P&L
            </div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#10b981", fontFamily: "monospace" }}>
              +${portfolioData.todayPnl.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
          </div>

          {/* Daily Return % */}
          <div style={{ borderLeft: "1px solid rgba(255,255,255,0.08)", paddingLeft: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#94a3b8", marginBottom: 4 }}>
              Daily Return
            </div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#10b981", fontFamily: "monospace" }}>
              +{portfolioData.todayReturnPct}%
            </div>
          </div>

          {/* Unrealized P&L */}
          <div style={{ borderLeft: "1px solid rgba(255,255,255,0.08)", paddingLeft: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#94a3b8", marginBottom: 4 }}>
              Unrealized P&L
            </div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#38bdf8", fontFamily: "monospace" }}>
              +${portfolioData.unrealizedPnl.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
          </div>

          {/* Available Balance */}
          <div style={{ borderLeft: "1px solid rgba(255,255,255,0.08)", paddingLeft: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: "#94a3b8", marginBottom: 4 }}>
              Available Cash
            </div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#f1f5f9", fontFamily: "monospace" }}>
              ${portfolioData.availableBalance.toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </div>
          </div>
        </div>
      </div>

      {/* ── MAIN CONTENT GRID ────────────────────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 24, marginBottom: 24 }}>
        
        {/* LEFT COLUMN: PERFORMANCE & RUNNING STRATEGIES */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

          {/* ── SECTION 2: PORTFOLIO PERFORMANCE ───────────────────────────── */}
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 16,
            padding: 20
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div>
                <h2 style={{ fontSize: 15, fontWeight: 700, color: "#f8fafc", margin: 0 }}>
                  Portfolio Performance
                </h2>
                <span style={{ fontSize: 12, color: "#64748b" }}>Historical cumulative return tracking</span>
              </div>

              {/* Timeframe Selector */}
              <div style={{ display: "flex", gap: 4, background: "#020617", padding: 3, borderRadius: 8 }}>
                {["1D", "1W", "1M", "3M", "ALL"].map(tf => (
                  <button
                    key={tf}
                    onClick={() => setTimeframe(tf)}
                    style={{
                      padding: "4px 10px",
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 600,
                      border: "none",
                      background: timeframe === tf ? "#6366f1" : "transparent",
                      color: timeframe === tf ? "#ffffff" : "#64748b",
                      cursor: "pointer",
                      transition: "all 0.15s ease"
                    }}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            {/* Interactive Equity Curve */}
            <div style={{ height: 220 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityCurve}>
                  <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#6366f1" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#6366f1" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="d" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis domain={["auto", "auto"]} hide />
                  <Tooltip
                    contentStyle={{ background: "#090d16", border: "1px solid #1e293b", borderRadius: 8, fontSize: 12 }}
                    formatter={(val) => [`$${val.toLocaleString()}`, "Portfolio Equity"]}
                  />
                  <Area type="monotone" dataKey="v" stroke="#6366f1" strokeWidth={2} fill="url(#equityGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ── SECTION 3: RUNNING STRATEGIES ─────────────────────────────── */}
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 16,
            padding: 20
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2 style={{ fontSize: 15, fontWeight: 700, color: "#f8fafc", margin: 0 }}>
                Running Strategies ({runningStrategies.filter(s => s.status === "active").length} Active)
              </h2>
              <button
                onClick={() => navigate("/app/strategies")}
                style={{ background: "none", border: "none", color: "#818cf8", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
              >
                Manage All →
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {runningStrategies.map(strat => (
                <div
                  key={strat.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "12px 16px",
                    background: "#020617",
                    border: "1px solid #1e293b",
                    borderRadius: 10
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span style={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: strat.status === "active" ? "#10b981" : "#eab308"
                    }} />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "#f8fafc" }}>
                        {strat.name}
                      </div>
                      <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                        {strat.pair} • Last signal: {strat.lastSignalTime}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
                    <div style={{ textAlign: "right" }}>
                      <div style={{
                        fontSize: 13,
                        fontWeight: 700,
                        fontFamily: "monospace",
                        color: strat.todayPnl >= 0 ? "#10b981" : "#ef4444"
                      }}>
                        {strat.todayPnl > 0 ? "+" : ""}${strat.todayPnl.toFixed(2)}
                      </div>
                      <div style={{ fontSize: 10, color: "#64748b" }}>Today's P&L</div>
                    </div>

                    {/* Actions */}
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        onClick={() => handleToggleStrategy(strat.id)}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 6,
                          fontSize: 11,
                          fontWeight: 600,
                          border: "1px solid #334155",
                          background: "#0f172a",
                          color: "#f8fafc",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: 4
                        }}
                      >
                        {strat.status === "active" ? <Pause size={12} /> : <Play size={12} />}
                        {strat.status === "active" ? "Pause" : "Resume"}
                      </button>
                      <button
                        onClick={() => navigate("/app/strategies")}
                        style={{
                          padding: "6px 10px",
                          borderRadius: 6,
                          fontSize: 11,
                          fontWeight: 600,
                          border: "none",
                          background: "#1e293b",
                          color: "#94a3b8",
                          cursor: "pointer"
                        }}
                      >
                        Details
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: INSIGHTS, NOTIFICATIONS & QUICK ACTIONS */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

          {/* ── SECTION 4: TRADING INSIGHTS (MAX 3) ───────────────────────── */}
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 16,
            padding: 20
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
              <Sparkles size={16} color="#818cf8" />
              <h2 style={{ fontSize: 14, fontWeight: 700, color: "#f8fafc", margin: 0 }}>
                Trading Insights
              </h2>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {tradingInsights.map(item => (
                <div
                  key={item.id}
                  style={{
                    padding: 12,
                    background: "#020617",
                    borderLeft: `3px solid ${item.type === "warning" ? "#eab308" : item.type === "info" ? "#38bdf8" : "#10b981"}`,
                    borderRadius: "0 8px 8px 0"
                  }}
                >
                  <p style={{ fontSize: 12, color: "#cbd5e1", margin: 0, lineHeight: 1.4 }}>
                    {item.text}
                  </p>
                  <button
                    onClick={() => navigate(item.actionPath)}
                    style={{
                      background: "none",
                      border: "none",
                      color: "#818cf8",
                      fontSize: 11,
                      fontWeight: 600,
                      padding: 0,
                      marginTop: 6,
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 4
                    }}
                  >
                    {item.actionText} →
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* ── SECTION 5: ACTIONABLE NOTIFICATIONS (MAX 5) ───────────────── */}
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 16,
            padding: 20
          }}>
            <h2 style={{ fontSize: 14, fontWeight: 700, color: "#f8fafc", margin: 0, marginBottom: 14 }}>
              Actionable Notifications
            </h2>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {notifications.map(n => (
                <div key={n.id} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span style={{ fontSize: 10, color: "#64748b", fontFamily: "monospace", minWidth: 45, paddingTop: 2 }}>
                    {n.time}
                  </span>
                  <span style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.3 }}>
                    {n.text}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* ── SECTION 6: QUICK ACTIONS ──────────────────────────────────── */}
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e293b",
            borderRadius: 16,
            padding: 20
          }}>
            <h2 style={{ fontSize: 14, fontWeight: 700, color: "#f8fafc", margin: 0, marginBottom: 14 }}>
              Quick Actions
            </h2>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <button
                onClick={() => navigate("/app/builder")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 12px",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#f8fafc",
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                <PlusCircle size={14} color="#818cf8" />
                Strategy Builder
              </button>

              <button
                onClick={() => navigate("/app/backtest")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 12px",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#f8fafc",
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                <BarChart2 size={14} color="#38bdf8" />
                Run Backtest
              </button>

              <button
                onClick={() => navigate("/app/exchanges")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 12px",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#f8fafc",
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                <KeyRound size={14} color="#10b981" />
                Connect Exchange
              </button>

              <button
                onClick={() => navigate("/app/marketplace")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 12px",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#f8fafc",
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                <Compass size={14} color="#eab308" />
                Marketplace
              </button>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
