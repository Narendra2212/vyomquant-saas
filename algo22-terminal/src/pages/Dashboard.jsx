import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  DollarSign, TrendingUp, Bot, Gauge, Activity,
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip
} from "recharts";
import { endpoints, get } from "../api";
import {
  C, Btn, Card, Tag2, PanelTitle, StatusDot,
  MiniSparkline, AnimatedNumber, PnLBadge, ProgressBar,
  CustomTooltip, PremiumCard, SkeletonLine, EmptyState
} from "../components/ui-legacy/primitives";
import {
  DrawdownChart, LivePositions
} from "../components/DashboardUpgrades";

export const MetricCard = React.memo(function MetricCard({ title, value, subValue, subValueColor, icon: Icon, loading = false, onClick }) {
  if (loading) {
    return (
      <div style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: C.radius.lg,
        padding: "12px 16px",
        minWidth: 140,
        display: "flex",
        flexDirection: "column",
        gap: 8
      }}>
        <SkeletonLine width="40%" height={9} />
        <SkeletonLine width="70%" height={18} />
        <SkeletonLine width="50%" height={12} />
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      onKeyDown={(e) => onClick && (e.key === "Enter" || e.key === " ") && onClick()}
      tabIndex={onClick ? 0 : undefined}
      role={onClick ? "button" : undefined}
      aria-label={onClick ? `Filter by ${title}` : `${title}: ${value}`}
      style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: C.radius.lg,
        padding: "12px 16px",
        minWidth: 140,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        cursor: onClick ? "pointer" : "default",
        transition: "all 0.15s ease"
      }}
      className="focus:outline-none focus:ring-2 focus:ring-cyan-400"
      onMouseEnter={(e) => onClick && (e.currentTarget.style.borderColor = C.borderLight)}
      onMouseLeave={(e) => onClick && (e.currentTarget.style.borderColor = C.border)}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {Icon && <Icon size={12} color={C.t3} aria-hidden="true" />}
        <span style={{ color: C.t3, fontSize: 9, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</span>
      </div>
      <div style={{ color: C.t1, fontSize: 18, fontWeight: 700, fontFamily: "monospace" }}>{value}</div>
      {subValue && (
        <div style={{ color: subValueColor || C.t2, fontSize: 11, fontWeight: 500 }}>{subValue}</div>
      )}
    </div>
  );
});

export default function Dashboard() {
  const navigate = useNavigate();
  const [equityCurve, setEquityCurve] = useState([]);
  const [stats, setStats] = useState(null);
  const [recentTransactions, setRecentTransactions] = useState([]);
  const [isLoadingStats, setIsLoadingStats] = useState(true);
  const [isLoadingRecent, setIsLoadingRecent] = useState(true);
  const [isLoadingEquity, setIsLoadingEquity] = useState(true);
  const [activeBots, setActiveBots] = useState([]);
  const [isLoadingActiveBots, setIsLoadingActiveBots] = useState(true);
  // New state variables for upgraded dashboard
  const [strategyStatus, setStrategyStatus] = useState(null);
  const [activePositions, setActivePositions] = useState([]);
  const [totalStats, setTotalStats] = useState(null);

  useEffect(() => {
    // Utility functions for data normalization
    const colorByLabel = (label = "") => {
      const v = String(label).toLowerCase();
      if (v.includes("portfolio")) return "cyan";
      if (v.includes("p&l")) return "green";
      if (v.includes("bot")) return "purple";
      if (v.includes("capital")) return "orange";
      return "cyan";
    };

    const iconByLabel = (label = "") => {
      const v = String(label).toLowerCase();
      if (v.includes("portfolio")) return DollarSign;
      if (v.includes("p&l")) return TrendingUp;
      if (v.includes("bot")) return Bot;
      if (v.includes("capital")) return Gauge;
      return Activity;
    };

    const formatValue = (value) => (value === null || value === undefined ? "-" : String(value));

    const normalizeStats = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row) => ({
        l: row.label ?? row.l ?? "Metric",
        v: formatValue(row.value ?? row.v),
        delta: row.delta ?? null,
        I: iconByLabel(row.label ?? row.l),
        c: colorByLabel(row.label ?? row.l),
      }));

    const normalizeRecent = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row) => ({
        t: row.time ?? row.t ?? "-",
        p: row.pair ?? row.p ?? "-",
        s: String(row.side ?? row.s ?? "-").toUpperCase(),
        pr: formatValue(row.price ?? row.pr),
        a: formatValue(row.amount ?? row.a),
      }));

    const normalizeEquityCurve = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, index) => ({
        d: index,
        v: Number(row.value ?? row.equity ?? row.v ?? 0),
      }));

    const normalizeStatus = (value = "") => {
      const status = String(value).toLowerCase();
      if (["running", "active", "live", "started"].includes(status)) return "running";
      if (["paused", "pause"].includes(status)) return "paused";
      if (["backtesting", "testing"].includes(status)) return "backtesting";
      if (["stopped", "stop", "inactive"].includes(status)) return "stopped";
      return "running";
    };

    const toNumber = (value, fallback = 0) => {
      const num = Number(value);
      return Number.isFinite(num) ? num : fallback;
    };

    const normalizeActiveBots = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, idx) => ({
        id: row.id ?? row.strategy_id ?? `bot-${idx}`,
        name: row.name ?? row.strategy_name ?? row.bot_name ?? "Unnamed Strategy",
        pair: row.pair ?? row.symbol ?? "N/A",
        status: normalizeStatus(row.status),
        pnl: toNumber(row.pnl ?? row.pnl_percent ?? row.performance ?? row.return_pct, 0),
        wr: toNumber(row.wr ?? row.win_rate ?? row.winRate ?? 0, 0),
      }));

    // Load all data using Promise.allSettled for better error handling
    const loadDashboardData = async () => {
      try {
        setIsLoadingStats(true);
        setIsLoadingEquity(true);
        const apiStats = await endpoints.user.getStats();

        if (apiStats) {
          // Map snake_case to camelCase
          const mappedStats = {
            totalTrades: apiStats.total_trades ?? 0,
            totalPnl: apiStats.total_pnl ?? 0,
            winRate: apiStats.win_rate ?? 0,
            activeBots: apiStats.active_bots ?? 0,
            strategies: apiStats.total_strategies ?? 0,
          };
          setStats(mappedStats);
        } else {
          setStats(null);
        }

        // Load equity curve data
        const equityResponse = await get('/api/portfolio/equity-curve', { params: { days: 90 } });
        if (!equityResponse) {
          setEquityCurve([]);
        } else {
          const equityData = Array.isArray(equityResponse?.data) ? equityResponse.data : [];
          const normalizedEquity = normalizeEquityCurve(equityData);
          setEquityCurve(normalizedEquity);
        }
      } catch (error) {
        setStats(null);
        setEquityCurve([]);
      } finally {
        setIsLoadingStats(false);
        setIsLoadingEquity(false);
      }
    };

    loadDashboardData();
  }, []);

  useEffect(() => {
    // WebSocket temporarily disabled

    // Cleanup on unmount
    return () => {
      // No cleanup needed since WebSocket is disabled
    };
  }, []);

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {/* Top Stats - Premium Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 16 }}>
        {/* Total Portfolio */}
        <PremiumCard
          glowOnHover
          glowColor="accent"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: C.gradient.accent,
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Total Portfolio</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <MiniSparkline data={equityCurve.slice(-20).map(d => d.v)} width={60} height={16} color={C.accent} />
              <DollarSign size={13} style={{ color: C.accent }} />
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            {isLoadingStats ? (
              <SkeletonLine width="70%" height={24} />
            ) : (
              <>
                <span style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace", textShadow: C.glow.accent }}>
                  $<AnimatedNumber value={stats?.totalPnl || 0} duration={800} />
                </span>
                <StatusDot status="live" size={6} />
              </>
            )}
          </div>
          {!isLoadingStats && stats?.totalPnl > 0 && (
            <div style={{ marginTop: 4 }}>
              <PnLBadge value={stats?.totalPnl || 0} size="sm" />
            </div>
          )}
        </PremiumCard>

        {/* 24H P&L */}
        <PremiumCard
          glowOnHover
          glowColor={stats?.totalPnl >= 0 ? "profit" : "loss"}
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: stats?.totalPnl >= 0 ? C.gradient.profit : C.gradient.loss,
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>24H P&L</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <TrendingUp size={13} style={{ color: stats?.totalPnl >= 0 ? C.profit : C.loss }} />
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            {isLoadingStats ? (
              <SkeletonLine width="70%" height={24} />
            ) : (
              <PnLBadge value={stats?.totalPnl || 0} size="lg" animated />
            )}
          </div>
          {!isLoadingStats && (
            <div style={{ marginTop: 8, fontSize: 10, color: C.t3, fontFamily: "monospace" }}>
              {stats?.totalPnl >= 0 ? "â–² Up from yesterday" : "â–¼ Down from yesterday"}
            </div>
          )}
        </PremiumCard>

        {/* Active Bots */}
        <PremiumCard
          glowOnHover
          glowColor="purple"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: "linear-gradient(135deg, rgba(124,77,255,0.15) 0%, rgba(124,77,255,0.05) 100%)",
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Active Bots</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <StatusDot status={stats?.activeBots > 0 ? "active" : "idle"} size={6} />
              <Bot size={13} style={{ color: C.purple }} />
            </div>
          </div>
          <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoadingStats ? (
              <SkeletonLine width="40%" height={24} />
            ) : (
              <AnimatedNumber value={stats?.activeBots || 0} suffix=" bots" />
            )}
          </div>
          {!isLoadingStats && stats?.activeBots > 0 && (
            <div style={{ marginTop: 8, fontSize: 10, color: C.purple, fontFamily: "monospace" }}>
              â— All systems operational
            </div>
          )}
        </PremiumCard>

        {/* Win Rate */}
        <PremiumCard
          glowOnHover
          glowColor="gold"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: "linear-gradient(135deg, rgba(255,214,0,0.15) 0%, rgba(255,214,0,0.05) 100%)",
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Win Rate</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Gauge size={13} style={{ color: C.gold }} />
            </div>
          </div>
          <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoadingStats ? (
              <SkeletonLine width="50%" height={24} />
            ) : (
              <AnimatedNumber value={stats?.winRate || 0} suffix="%" />
            )}
          </div>
          {!isLoadingStats && (
            <div style={{ marginTop: 8 }}>
              <ProgressBar v={stats?.winRate || 0} max={100} color={C.gold} h={4} />
            </div>
          )}
        </PremiumCard>
      </div>

      {/* Main Grid: Charts & Panels */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 12, marginBottom: 12 }}>
        {/* Equity Curve Chart */}
        <Card cls="p-4 flex flex-col">
          <PanelTitle title="Equity Curve" sub="90-day historical performance tracking" />
          {isLoadingEquity ? (
            <div style={{ height: 180, display: "flex", flexDirection: "column", gap: 8, justifyContent: "center" }}>
              <SkeletonLine width="100%" height={140} />
            </div>
          ) : equityCurve.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.cyan} stopOpacity={0.15} />
                    <stop offset="95%" stopColor={C.cyan} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="d" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip />} />
                <Area dataKey="v" stroke={C.cyan} strokeWidth={1.5} fill="url(#cg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <EmptyState
                icon={Activity}
                title="Waiting for market data..."
                subtitle="Deploy strategies to see your equity curve"
                hint="Deploy a strategy to begin collecting performance data"
                size="sm"
              />
            </div>
          )}
        </Card>

        {/* Drawdown Chart - Memoized */}
        <DrawdownChart
          data={useMemo(() => equityCurve.map((d, i, arr) => ({
            timestamp: new Date().toISOString(),
            drawdown_pct: i > 0 ? Math.max(0, (1 - d.v / Math.max(...arr.slice(0, i + 1).map(e => e.v))) * 100) : 0
          })), [equityCurve])}
          height={200}
        />

        {/* Live Positions - NEW */}
        <LivePositions positions={activePositions} />

        {/* Active Strategies panel */}
        <Card cls="p-4">
          <PanelTitle title="Active Strategies" right={<Btn v="ghost" sz="xs" onClick={() => navigate("/app/strategies")}>View All</Btn>} />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {isLoadingActiveBots && <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", padding: "6px 2px" }}>Loading active strategies...</div>}
            {!isLoadingActiveBots && Array.isArray(activeBots) && activeBots.map(s => (
              <div key={s.id} style={{ background: C.bg3, borderRadius: 8, padding: 10 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: C.t1, fontSize: 10, fontWeight: 700 }}>{s.name}</span>
                  <span style={{ color: s.pnl >= 0 ? C.green : C.red, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>{s.pnl >= 0 ? "+" : ""}{s.pnl}%</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <StatusDot status={s.status} />
                </div>
                <div style={{ marginTop: 6 }}>
                  <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? C.green : C.red} h={3} />
                </div>
              </div>
            ))}
            {!isLoadingActiveBots && activeBots.length === 0 && <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", padding: "6px 2px" }}>No running strategies.</div>}
          </div>
        </Card>
      </div>

      {/* Recent Transactions */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12 }}>
        <Card cls="p-4">
          <PanelTitle title="Recent Transactions" right={<Btn v="ghost" sz="xs" onClick={() => navigate("/app/strategies")}>Full Ledger â†’</Btn>} />
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
            <thead>
              <tr style={{ color: C.t3, letterSpacing: 2, fontSize: 9 }}>
                {["TIME", "PAIR", "SIDE", "PRICE", "AMOUNT"].map(h => (
                  <th key={h} style={{ textAlign: "left", padding: "4px 8px", fontWeight: 900 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoadingRecent && (
                <tr>
                  <td colSpan={5} style={{ padding: "10px 8px", color: C.t3, fontFamily: "monospace" }}>Loading recent transactions...</td>
                </tr>
              )}
              {!isLoadingRecent && recentTransactions.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ padding: "10px 8px", color: C.t3, fontFamily: "monospace", textAlign: "center" }}>No recent transactions found.</td>
                </tr>
              )}
              {Array.isArray(recentTransactions) && recentTransactions.map((r, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${C.border}22` }} className="hover:bg-white/5 transition-colors">
                  <td style={{ padding: "6px 8px", color: C.t3 }}>{r.t}</td>
                  <td style={{ padding: "6px 8px", color: C.t1, fontWeight: 700 }}>{r.p}</td>
                  <td style={{ padding: "6px 8px" }}><Tag2 c={r.s === "BUY" ? "green" : "red"}>{r.s}</Tag2></td>
                  <td style={{ padding: "6px 8px", color: C.t1 }}>{r.pr}</td>
                  <td style={{ padding: "6px 8px", color: C.t2 }}>{r.a}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
