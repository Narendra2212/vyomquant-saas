import React, { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  ShieldCheck, AlertTriangle, ArrowRight,
  RefreshCw, BarChart2, Server,
  Play, Pause, ShieldAlert, AlertOctagon, CheckCircle2,
  Sliders, Maximize2, Minimize2, Wifi, WifiOff
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip
} from "recharts";
import { dashboardApi } from "../api/modules/dashboard";
import { riskApi } from "../api/modules/risk";
import wsClient from "../websocketClient";

// Safe float conversion helper
export function floatVal(v) {
  const num = parseFloat(v);
  if (isNaN(num) || !isFinite(num)) return 0.00;
  return num;
}

// Compute Liquidation Distance % for derivatives
export function computeLiquidationDistance(markPrice, liqPrice, side, marketType) {
  if (marketType === "spot" || liqPrice == null || liqPrice <= 0 || markPrice <= 0) {
    return null;
  }
  if (side === "long") {
    return ((markPrice - liqPrice) / markPrice) * 100;
  } else {
    return ((liqPrice - markPrice) / markPrice) * 100;
  }
}

export default function Dashboard() {
  const navigate = useNavigate();
  
  // Environment state (default: 'live' with toggle to 'paper')
  const [environment, setEnvironment] = useState("live");
  const [timeframe, setTimeframe] = useState("1M");
  const [layoutDensity, setLayoutDensity] = useState(() => {
    try {
      return localStorage.getItem("vyomquant_dashboard_density") || "standard";
    } catch {
      return "standard";
    }
  });

  const [showStatusModal, setShowStatusModal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [wsStatus, setWsStatus] = useState("connected");

  // Authoritative sync timestamp to prevent stale WebSocket overwrites (2D.4)
  const lastSyncTimestampRef = useRef(Date.now());

  // Emergency Halt Modal State (P0.1 / 2D.5)
  const [showKillSwitchModal, setShowKillSwitchModal] = useState(false);
  const [killSwitchAction, setKillSwitchAction] = useState("activate"); // "activate" or "recover"
  const [isKillSwitchProcessing, setIsKillSwitchProcessing] = useState(false);
  const [killSwitchError, setKillSwitchError] = useState(null);

  // Operational Critical Alerts State (P0.2 / 2D.2)
  const [criticalAlerts, setCriticalAlerts] = useState([]);

  // Capital & Performance State (Zone 2)
  const [portfolioData, setPortfolioData] = useState({
    totalValue: 0.00,
    totalEquity: 0.00,
    availableBalance: 0.00,
    freeBalance: 0.00,
    usedBalance: 0.00,
    todayPnl: 0.00,
    todayRealizedPnl: 0.00,
    todayReturnPct: 0.00,
    unrealizedPnl: 0.00,
    cumulativePnl: 0.00,
    totalExposure: 0.00,
    currency: "USDT"
  });

  // Open Positions State (Zone 3 / P0.3 / 2D.3)
  const [positions, setPositions] = useState([]);

  // Active Strategies State (Zone 4 / P1.3)
  const [strategies, setStrategies] = useState([]);

  // Risk & Safety State (Zone 5)
  const [riskState, setRiskState] = useState(null);

  // Exchange Health State (Zone 6)
  const [exchangeConnections, setExchangeConnections] = useState([]);
  const [systemHealth, setSystemHealth] = useState(null);

  // Recent Executions State (Zone 7)
  const [executions, setExecutions] = useState([]);
  const [tradingInsights, setTradingInsights] = useState([]);

  // Equity Curve State (Zone 8)
  const [equityCurve, setEquityCurve] = useState([]);
  const [equityLoading, setEquityLoading] = useState(false);

  // Toggle density preference handler (2D.1)
  const handleDensityToggle = (density) => {
    setLayoutDensity(density);
    try {
      localStorage.setItem("vyomquant_dashboard_density", density);
    } catch {
      // Ignore local storage error in private mode
    }
  };

  // Core Authoritative Data Fetcher (2D.4)
  const loadDashboardData = useCallback(async (targetEnv = environment, targetTf = timeframe) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const dayMap = { "1D": 1, "1W": 7, "1M": 30, "3M": 90, "ALL": 365 };
      const days = dayMap[targetTf] || 30;

      const dashboardRes = await dashboardApi.getDashboard({
        environment: targetEnv,
        equity_days: days
      });

      if (dashboardRes) {
        lastSyncTimestampRef.current = Date.now();

        // 1. Primary Capital & Performance Overview
        const ov = dashboardRes.overview || {};
        setPortfolioData({
          totalValue: floatVal(ov.total_value || ov.total_equity || 0.00),
          totalEquity: floatVal(ov.total_equity || ov.total_value || 0.00),
          availableBalance: floatVal(ov.available_balance || 0.00),
          freeBalance: floatVal(ov.free_balance || ov.available_balance || 0.00),
          usedBalance: floatVal(ov.used_balance || 0.00),
          todayPnl: floatVal(ov.today_pnl || 0.00),
          todayRealizedPnl: floatVal(ov.today_realized_pnl || 0.00),
          todayReturnPct: floatVal(ov.today_return_pct || 0.00),
          unrealizedPnl: floatVal(ov.unrealized_pnl || 0.00),
          cumulativePnl: floatVal(ov.cumulative_pnl || 0.00),
          totalExposure: floatVal(ov.total_exposure || ov.used_balance || 0.00),
          currency: ov.currency || (targetEnv === "paper" ? "USD" : "USDT")
        });

        // 2. Open Positions (Zone 3 / P0.3 / 2D.3)
        const rawPositions = Array.isArray(dashboardRes.positions) ? dashboardRes.positions : [];
        setPositions(rawPositions.map(pos => {
          const mType = pos.market_type || "spot";
          const side = (pos.side || "long").toLowerCase();
          const entryP = floatVal(pos.entry_price || 0);
          const markP = floatVal(pos.mark_price || entryP);
          const liqP = pos.liquidation_price != null ? floatVal(pos.liquidation_price) : null;
          const liqDist = computeLiquidationDistance(markP, liqP, side, mType);

          return {
            id: pos.id || `${pos.exchange_id}_${pos.symbol}`,
            symbol: pos.symbol || "UNKNOWN",
            exchangeId: pos.exchange_id || (targetEnv === "paper" ? "paper" : "binance"),
            side,
            marketType: mType,
            marginType: (pos.margin_type || "cross").toLowerCase(),
            contracts: floatVal(pos.contracts || pos.quantity || 0),
            entryPrice: entryP,
            markPrice: markP,
            unrealizedPnl: floatVal(pos.unrealized_pnl || pos.unrealized_pnl_usd || 0),
            unrealizedPnlPct: floatVal(pos.unrealized_pnl_pct || 0),
            leverage: pos.leverage ? parseInt(pos.leverage, 10) : 1,
            liquidationPrice: liqP,
            liquidationDistancePct: liqDist
          };
        }));

        // 3. Active Strategies / Bots (Zone 4 / P1.3)
        const rawStrategies = dashboardRes.strategies?.items || [];
        setStrategies(rawStrategies.map(s => ({
          id: s.id,
          name: s.name || "Automated Strategy",
          pair: s.pair || s.symbol || "BTC/USDT",
          status: s.status || "paused",
          health: s.health || "idle",
          errorMessage: s.error_message || s.error || s.reason || null,
          todayPnl: floatVal(s.today_pnl || 0.00),
          todayReturnPct: floatVal(s.today_return_pct || 0.00),
          lastSignalTime: s.last_signal_time ? new Date(s.last_signal_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "No signals"
        })));

        // 4. Risk & Safety Guard (Zone 5)
        if (dashboardRes.risk) {
          setRiskState(dashboardRes.risk);
        }

        // 5. Exchange Health & Latency (Zone 6)
        if (dashboardRes.exchange) {
          setExchangeConnections(Array.isArray(dashboardRes.exchange.exchanges) ? dashboardRes.exchange.exchanges : []);
        }
        if (dashboardRes.health) {
          setSystemHealth(dashboardRes.health);
        }

        // 6. Recent Executions (Zone 7)
        const rawExecs = Array.isArray(dashboardRes.executions) ? dashboardRes.executions : [];
        setExecutions(rawExecs.slice(0, 5).map(e => ({
          id: e.id,
          symbol: e.symbol || "BTC/USDT",
          exchangeId: e.exchange_id || (targetEnv === "paper" ? "paper" : "binance"),
          side: (e.side || "buy").toLowerCase(),
          price: floatVal(e.price || 0),
          amount: floatVal(e.amount || 0),
          cost: floatVal(e.cost || (e.amount * e.price) || 0),
          fee: floatVal(e.fee || 0),
          realizedPnl: floatVal(e.realized_pnl || 0),
          timestamp: e.timestamp ? new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "Just now"
        })));

        // Critical alerts extraction (P0.2)
        const alerts = [];
        const rawInsights = dashboardRes.recent_activity?.insights || [];
        rawInsights.forEach(ins => {
          if (ins.type === "warning" || ins.type === "error" || ins.type === "critical") {
            alerts.push({
              id: ins.id || `ins_${Math.random()}`,
              severity: ins.type === "error" ? "critical" : "warning",
              title: ins.type === "error" ? "Execution Alert" : "Risk Notice",
              message: ins.text,
              actionPath: ins.actionPath,
              actionText: ins.actionText,
              timestamp: "Active"
            });
          }
        });
        setTradingInsights(rawInsights.slice(0, 3));
        setCriticalAlerts(alerts);

        // 7. Equity Curve (Zone 8)
        if (dashboardRes.equity_curve && Array.isArray(dashboardRes.equity_curve)) {
          setEquityCurve(
            dashboardRes.equity_curve.map(row => ({
              d: row.timestamp
                ? new Date(row.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
                : "",
              v: parseFloat(row.equity ?? row.value ?? 0)
            }))
          );
        } else {
          setEquityCurve([]);
        }

        setLastUpdated(new Date().toLocaleTimeString());
      }
    } catch (err) {
      console.error("Error loading dashboard data:", err);
      setLoadError("Failed to synchronize trading cockpit data");
    } finally {
      setIsLoading(false);
    }
  }, [environment, timeframe]);

  // Initial load
  useEffect(() => {
    loadDashboardData(environment, timeframe);
  }, [environment, loadDashboardData]);

  // Real-time WebSocket Subscriptions & Reconnect Reconciliation (2D.4)
  useEffect(() => {
    // Helper to filter out stale WebSocket events
    const isEventFresh = (data) => {
      if (!data) return true;
      if (data.environment && data.environment !== environment) return false;
      if (data.timestamp) {
        const eventTime = new Date(data.timestamp).getTime();
        if (!isNaN(eventTime) && eventTime < lastSyncTimestampRef.current - 1000) {
          console.warn("[WS/Dashboard] Discarding stale event:", data);
          return false;
        }
      }
      return true;
    };

    // 1. Reconnect & Open Reconciliation Handler (2D.4)
    const unsubOpen = typeof wsClient.onOpen === "function"
      ? wsClient.onOpen(() => {
          console.log("[WS/Dashboard] Connection re-established. Reconciling with authoritative server state...");
          loadDashboardData(environment, timeframe);
        })
      : null;

    // 2. Connection Status Tracker
    const unsubStatus = typeof wsClient.onStatusChange === "function"
      ? wsClient.onStatusChange((status) => {
          setWsStatus(status);
        })
      : null;

    // 3. Risk Kill Switch Activated Event
    const unsubRiskActivated = wsClient.subscribe("risk.kill_switch_activated", (data) => {
      if (!isEventFresh(data)) return;
      console.log("[WS/Dashboard] Risk kill switch activated event received:", data);
      setRiskState(prev => ({
        ...prev,
        kill_switch_active: true,
        risk_level: "blocked"
      }));
      setCriticalAlerts(prev => [
        {
          id: `ks_${Date.now()}`,
          severity: "critical",
          title: "Emergency Kill Switch Activated",
          message: data?.message || "Emergency Kill Switch is ACTIVE. All trading executions are halted.",
          timestamp: "Just now",
          actionPath: "/app/risk",
          actionText: "Risk Controls"
        },
        ...prev
      ]);
    });

    // 4. Risk Kill Switch Recovered Event
    const unsubRiskRecovered = wsClient.subscribe("risk.kill_switch_recovered", (data) => {
      if (!isEventFresh(data)) return;
      console.log("[WS/Dashboard] Risk kill switch recovered event received");
      setRiskState(prev => ({
        ...prev,
        kill_switch_active: false,
        risk_level: "low"
      }));
      setCriticalAlerts(prev => prev.filter(a => !a.title.includes("Kill Switch")));
    });

    // 5. Strategy Status Changes
    const unsubStrategy = wsClient.subscribe("strategy_status", (data) => {
      if (!isEventFresh(data)) return;
      if (data?.strategy_id) {
        setStrategies(prev => prev.map(s => s.id === data.strategy_id ? {
          ...s,
          status: data.status || s.status,
          health: data.health || s.health,
          errorMessage: data.error || s.errorMessage
        } : s));
      }
    });

    // 6. Exchange Health Changes
    const unsubExchange = wsClient.subscribe("exchange_health", (data) => {
      if (!isEventFresh(data)) return;
      if (data?.exchange_id) {
        setExchangeConnections(prev => prev.map(ex => (ex.exchange_id === data.exchange_id || ex.id === data.exchange_id) ? {
          ...ex,
          ...data
        } : ex));
      }
    });

    // 7. Critical Notifications Broadcast
    const unsubNotif = wsClient.subscribe("notification", (notif) => {
      if (!isEventFresh(notif)) return;
      if (notif?.severity === "critical" || notif?.severity === "warning") {
        setCriticalAlerts(prev => [
          {
            id: notif.id || `notif_${Date.now()}`,
            severity: notif.severity,
            title: notif.title || "Trading Alert",
            message: notif.message,
            timestamp: "Just now"
          },
          ...prev.slice(0, 4)
        ]);
      }
    });

    return () => {
      if (unsubOpen) unsubOpen();
      if (unsubStatus) unsubStatus();
      if (unsubRiskActivated) unsubRiskActivated();
      if (unsubRiskRecovered) unsubRiskRecovered();
      if (unsubStrategy) unsubStrategy();
      if (unsubExchange) unsubExchange();
      if (unsubNotif) unsubNotif();
    };
  }, [environment, timeframe, loadDashboardData]);

  // Timeframe change handler
  const handleTimeframeChange = async (newTf) => {
    setTimeframe(newTf);
    setEquityLoading(true);
    const dayMap = { "1D": 1, "1W": 7, "1M": 30, "3M": 90, "ALL": 365 };
    const days = dayMap[newTf] || 30;
    try {
      const data = await dashboardApi.getDashboard({ environment, equity_days: days });
      if (data?.equity_curve && Array.isArray(data.equity_curve)) {
        setEquityCurve(
          data.equity_curve.map(row => ({
            d: row.timestamp
              ? new Date(row.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
              : "",
            v: parseFloat(row.equity ?? row.value ?? 0)
          }))
        );
      }
    } catch (err) {
      console.error("Failed to load equity curve timeframe:", err);
    } finally {
      setEquityLoading(false);
    }
  };

  // Strategy pause/resume handler
  const handleToggleStrategy = (id) => {
    setStrategies(prev => prev.map(s => {
      if (s.id === id) {
        const newStatus = s.status === "active" || s.status === "running" ? "paused" : "active";
        return {
          ...s,
          status: newStatus,
          health: newStatus === "active" ? "healthy" : "idle"
        };
      }
      return s;
    }));
  };

  // Emergency Halt / Kill Switch Actions (P0.1 / 2D.5)
  const handleConfirmKillSwitchAction = async () => {
    setIsKillSwitchProcessing(true);
    setKillSwitchError(null);
    try {
      if (killSwitchAction === "activate") {
        const res = await riskApi.killSwitch({
          reason: `Emergency manual halt triggered from Dashboard (${environment.toUpperCase()})`
        });
        if (res && res.kill_switch_active) {
          setRiskState(prev => ({
            ...prev,
            kill_switch_active: true,
            risk_level: "blocked"
          }));
          setShowKillSwitchModal(false);
        }
      } else {
        const res = await riskApi.recoverKillSwitch();
        if (res && !res.kill_switch_active) {
          setRiskState(prev => ({
            ...prev,
            kill_switch_active: false,
            risk_level: "low"
          }));
          setShowKillSwitchModal(false);
        }
      }
    } catch (err) {
      console.error("Kill switch operation failed:", err);
      setKillSwitchError(err?.response?.data?.message || err?.message || "Operation failed. Please check network or risk settings.");
    } finally {
      setIsKillSwitchProcessing(false);
    }
  };

  // Compute operational risk level badge
  const riskLevel = riskState?.risk_level || "low";
  const riskScore = riskState?.risk_score != null ? riskState.risk_score : 25;
  const isCircuitBreakerArmed = riskState?.circuit_breaker_armed ?? true;
  const isKillSwitchActive = riskState?.kill_switch_active ?? false;
  const isDense = layoutDensity === "dense";

  return (
    <div style={{
      flex: 1,
      overflowY: "auto",
      padding: isDense ? "0.875rem 1.25rem" : "1.25rem 1.75rem",
      background: "#080a0e",
      color: "#e2e8f0",
      fontFamily: "'IBM Plex Mono', 'Fira Code', monospace",
      transition: "padding 0.15s ease"
    }}>

      {/* ── ZONE 1: TOP HEADER & GLOBAL TRADING STATUS ─────────────────────────── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: isDense ? "0.875rem" : "1.25rem",
        paddingBottom: isDense ? "0.75rem" : "1rem",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        flexWrap: "wrap",
        gap: "0.875rem"
      }}>
        {/* Title and Freshness */}
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <h1 style={{
              fontSize: isDense ? "1.125rem" : "1.25rem",
              fontWeight: 800,
              letterSpacing: "-0.02em",
              color: "#f8fafc",
              margin: 0
            }}>
              Trading Cockpit
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
                  transition: "all 0.15s ease",
                  background: environment === "live" ? "rgba(16, 185, 129, 0.2)" : "transparent",
                  color: environment === "live" ? "#10b981" : "#64748b",
                  boxShadow: environment === "live" ? "inset 0 0 0 1px #10b981" : "none"
                }}
              >
                <span style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: environment === "live" ? "#10b981" : "#475569",
                  boxShadow: environment === "live" ? "0 0 6px #10b981" : "none"
                }} />
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
                  transition: "all 0.15s ease",
                  background: environment === "paper" ? "rgba(99, 102, 241, 0.2)" : "transparent",
                  color: environment === "paper" ? "#818cf8" : "#64748b",
                  boxShadow: environment === "paper" ? "inset 0 0 0 1px #6366f1" : "none"
                }}
              >
                <span style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: environment === "paper" ? "#818cf8" : "#475569",
                  boxShadow: environment === "paper" ? "0 0 6px #818cf8" : "none"
                }} />
                PAPER
              </button>
            </div>

            {/* Real Money Warning Badge in Live Mode */}
            {environment === "live" ? (
              <span style={{
                fontSize: "0.6875rem",
                padding: "2px 8px",
                borderRadius: 4,
                background: "rgba(16, 185, 129, 0.12)",
                color: "#10b981",
                border: "1px solid rgba(16, 185, 129, 0.3)",
                fontWeight: 600
              }}>
                REAL CAPITAL ACTIVE
              </span>
            ) : (
              <span style={{
                fontSize: "0.6875rem",
                padding: "2px 8px",
                borderRadius: 4,
                background: "rgba(99, 102, 241, 0.12)",
                color: "#818cf8",
                border: "1px solid rgba(99, 102, 241, 0.3)",
                fontWeight: 600
              }}>
                SIMULATED EXECUTION
              </span>
            )}
          </div>

          <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: "0.25rem" }}>
            Real-time capital deployment, position risk, and algorithmic execution engine.
            {lastUpdated && <span style={{ marginLeft: "0.5rem" }}>• Updated {lastUpdated}</span>}
          </div>
        </div>

        {/* Global Operational Controls & Density Selector */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", flexWrap: "wrap" }}>
          
          {/* 2D.1 STANDARD / DENSE VIEW TOGGLE */}
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            background: "#0f141c",
            padding: "2px",
            borderRadius: "8px",
            border: "1px solid #1e293b"
          }}>
            <button
              onClick={() => handleDensityToggle("standard")}
              title="Standard Spacing Layout"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "3px 8px",
                borderRadius: 6,
                fontSize: "0.6875rem",
                fontWeight: 600,
                border: "none",
                cursor: "pointer",
                background: layoutDensity === "standard" ? "#1e293b" : "transparent",
                color: layoutDensity === "standard" ? "#f8fafc" : "#64748b"
              }}
            >
              <Maximize2 size={11} /> Standard
            </button>
            <button
              onClick={() => handleDensityToggle("dense")}
              title="Compact Density Layout (Optimized for Multi-Position Screens)"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                padding: "3px 8px",
                borderRadius: 6,
                fontSize: "0.6875rem",
                fontWeight: 600,
                border: "none",
                cursor: "pointer",
                background: layoutDensity === "dense" ? "#1e293b" : "transparent",
                color: layoutDensity === "dense" ? "#f8fafc" : "#64748b"
              }}
            >
              <Minimize2 size={11} /> Dense
            </button>
          </div>

          {/* P0.1 EMERGENCY HALT / RESUME BUTTON */}
          {!isKillSwitchActive ? (
            <button
              onClick={() => {
                setKillSwitchAction("activate");
                setKillSwitchError(null);
                setShowKillSwitchModal(true);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.375rem",
                padding: isDense ? "0.3125rem 0.75rem" : "0.375rem 0.875rem",
                background: "rgba(239, 68, 68, 0.15)",
                border: "1px solid #ef4444",
                borderRadius: 8,
                color: "#ef4444",
                fontSize: "0.75rem",
                fontWeight: 700,
                cursor: "pointer",
                transition: "all 0.15s ease"
              }}
            >
              <ShieldAlert size={14} />
              EMERGENCY HALT
            </button>
          ) : (
            <button
              onClick={() => {
                setKillSwitchAction("recover");
                setKillSwitchError(null);
                setShowKillSwitchModal(true);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.375rem",
                padding: isDense ? "0.3125rem 0.75rem" : "0.375rem 0.875rem",
                background: "rgba(234, 179, 8, 0.2)",
                border: "1px solid #eab308",
                borderRadius: 8,
                color: "#eab308",
                fontSize: "0.75rem",
                fontWeight: 700,
                cursor: "pointer"
              }}
            >
              <AlertOctagon size={14} />
              RESUME TRADING
            </button>
          )}

          {/* Refresh Sync Button */}
          <button
            onClick={() => loadDashboardData(environment, timeframe)}
            disabled={isLoading}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.375rem",
              padding: isDense ? "0.3125rem 0.625rem" : "0.375rem 0.75rem",
              background: "#0f141c",
              border: "1px solid #1e293b",
              borderRadius: 8,
              color: "#94a3b8",
              fontSize: "0.75rem",
              fontWeight: 600,
              cursor: "pointer"
            }}
          >
            <RefreshCw size={13} style={{ animation: isLoading ? "spin 1s linear infinite" : "none" }} />
            Sync
          </button>

          {/* Diagnostics Popover Trigger */}
          <div style={{ position: "relative" }}>
            <button
              onClick={() => setShowStatusModal(!showStatusModal)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                padding: isDense ? "0.3125rem 0.75rem" : "0.375rem 0.875rem",
                background: isKillSwitchActive ? "rgba(239,68,68,0.15)" : "#0f141c",
                border: `1px solid ${isKillSwitchActive ? "#ef4444" : "#1e293b"}`,
                borderRadius: 8,
                color: isKillSwitchActive ? "#ef4444" : "#94a3b8",
                fontSize: "0.75rem",
                fontWeight: 600,
                cursor: "pointer"
              }}
            >
              <span style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: isKillSwitchActive ? "#ef4444" : wsStatus === "connected" ? "#10b981" : "#eab308",
                boxShadow: isKillSwitchActive ? "0 0 6px #ef4444" : wsStatus === "connected" ? "0 0 6px #10b981" : "0 0 6px #eab308"
              }} />
              <span>{isKillSwitchActive ? "Trading Blocked" : wsStatus === "connected" ? "Engine Operational" : "Stream Connecting"}</span>
            </button>

            {/* Diagnostics Popover Modal */}
            {showStatusModal && (
              <div style={{
                position: "absolute",
                top: 38,
                right: 0,
                width: 300,
                background: "#0b0f17",
                border: "1px solid #1e293b",
                borderRadius: 10,
                padding: "1rem",
                boxShadow: "0 20px 25px -5px rgba(0,0,0,0.7)",
                zIndex: 100
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                  <span style={{ fontSize: "0.6875rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase" }}>
                    Diagnostics ({environment.toUpperCase()})
                  </span>
                  <button
                    onClick={() => setShowStatusModal(false)}
                    style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: "0.875rem" }}
                  >
                    ✕
                  </button>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem", fontSize: "0.75rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Exchange API Latency</span>
                    <span style={{
                      color: systemHealth?.exchange_api_latency_ms != null ? "#10b981" : "#94a3b8",
                      fontWeight: 600
                    }}>
                      {systemHealth?.exchange_api_latency_ms != null
                        ? `${systemHealth.exchange_api_latency_ms} ms (${systemHealth.exchange_api_latency_status || "optimal"})`
                        : "Latency unavailable"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Real-time Stream</span>
                    <span style={{ color: wsStatus === "connected" ? "#10b981" : "#eab308", fontWeight: 600, textTransform: "capitalize" }}>
                      {wsStatus === "connected" ? "Live Connected" : "Reconnecting..."}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Circuit Breaker</span>
                    <span style={{ color: isCircuitBreakerArmed ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                      {isCircuitBreakerArmed ? "Armed / 0 Breaches" : "Triggered"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Order State Sync</span>
                    <span style={{ color: "#10b981", fontWeight: 600 }}>
                      {systemHealth?.order_state_sync_status || "Synchronized"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Connected Venues</span>
                    <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                      {exchangeConnections.length} Active
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── P0.1 / 2D.5 EMERGENCY KILL SWITCH CONFIRMATION MODAL ───────────────── */}
      {showKillSwitchModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(0, 0, 0, 0.75)",
          backdropFilter: "blur(4px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
          padding: "1rem"
        }}>
          <div style={{
            background: "#0c1017",
            border: `1px solid ${killSwitchAction === "activate" ? "#ef4444" : "#eab308"}`,
            borderRadius: 14,
            padding: "1.5rem",
            maxWidth: 480,
            width: "100%",
            boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.8)"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem" }}>
              <div style={{
                padding: "0.5rem",
                borderRadius: "50%",
                background: killSwitchAction === "activate" ? "rgba(239, 68, 68, 0.2)" : "rgba(234, 179, 8, 0.2)"
              }}>
                <ShieldAlert size={24} color={killSwitchAction === "activate" ? "#ef4444" : "#eab308"} />
              </div>
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 800, color: "#f8fafc", margin: 0 }}>
                  {killSwitchAction === "activate" ? "Activate Emergency Kill Switch" : "Deactivate Emergency Kill Switch"}
                </h3>
                <span style={{ fontSize: "0.6875rem", color: "#94a3b8" }}>
                  Environment Context: <strong style={{ color: environment === "live" ? "#10b981" : "#818cf8" }}>
                    {environment === "live" ? "LIVE TRADING (REAL CAPITAL)" : "PAPER SIMULATION"}
                  </strong>
                </span>
              </div>
            </div>

            <p style={{ fontSize: "0.8125rem", color: "#cbd5e1", lineHeight: 1.5, marginBottom: "1.25rem" }}>
              {killSwitchAction === "activate" ? (
                environment === "live" ? (
                  "⚠️ WARNING: Activating the Emergency Kill Switch will IMMEDIATELY halt all live strategy execution loops and block all new order submissions on connected live exchanges."
                ) : (
                  "Activating the Emergency Kill Switch will freeze all paper simulation bots and prevent new simulated trades."
                )
              ) : (
                "Deactivating the Emergency Kill Switch will resume normal algorithmic execution and order submissions."
              )}
            </p>

            {killSwitchError && (
              <div style={{
                padding: "0.625rem",
                background: "rgba(239, 68, 68, 0.15)",
                border: "1px solid #ef4444",
                borderRadius: 6,
                fontSize: "0.75rem",
                color: "#f8fafc",
                marginBottom: "1rem"
              }}>
                {killSwitchError}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                onClick={() => setShowKillSwitchModal(false)}
                disabled={isKillSwitchProcessing}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#94a3b8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Cancel
              </button>

              <button
                onClick={handleConfirmKillSwitchAction}
                disabled={isKillSwitchProcessing}
                style={{
                  padding: "0.5rem 1.25rem",
                  background: killSwitchAction === "activate" ? "#ef4444" : "#eab308",
                  border: "none",
                  borderRadius: 8,
                  color: killSwitchAction === "activate" ? "#fff" : "#000",
                  fontSize: "0.75rem",
                  fontWeight: 700,
                  cursor: isKillSwitchProcessing ? "not-allowed" : "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem"
                }}
              >
                {isKillSwitchProcessing ? (
                  <>
                    <RefreshCw size={13} style={{ animation: "spin 1s linear infinite" }} />
                    Processing...
                  </>
                ) : (
                  killSwitchAction === "activate" ? "Yes, HALT TRADING IMMEDIATELY" : "Resume Operations"
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── P0.2 / 2D.2 HIGH-VISIBILITY CRITICAL OPERATIONAL ALERT BANNER ──────── */}
      {(isKillSwitchActive || !isCircuitBreakerArmed || criticalAlerts.length > 0) && (
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
          marginBottom: isDense ? "0.875rem" : "1.25rem"
        }}>
          {/* Active Kill Switch Alert */}
          {isKillSwitchActive && (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: isDense ? "0.625rem 1rem" : "0.75rem 1.25rem",
              background: "rgba(239, 68, 68, 0.15)",
              border: "1px solid #ef4444",
              borderRadius: 10
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <ShieldAlert size={20} color="#ef4444" />
                <div>
                  <div style={{ fontSize: "0.8125rem", fontWeight: 800, color: "#f8fafc" }}>
                    EMERGENCY KILL SWITCH ACTIVE — ALL EXECUTIONS HALTED
                  </div>
                  <div style={{ fontSize: "0.6875rem", color: "#94a3b8", marginTop: 2 }}>
                    All algorithmic order placements are blocked by institutional safety guard.
                  </div>
                </div>
              </div>
              <button
                onClick={() => {
                  setKillSwitchAction("recover");
                  setKillSwitchError(null);
                  setShowKillSwitchModal(true);
                }}
                style={{
                  padding: "4px 12px",
                  background: "#eab308",
                  border: "none",
                  borderRadius: 6,
                  color: "#000",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  cursor: "pointer"
                }}
              >
                Resume Trading
              </button>
            </div>
          )}

          {/* Circuit Breaker Breach Alert */}
          {!isCircuitBreakerArmed && (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: isDense ? "0.625rem 1rem" : "0.75rem 1.25rem",
              background: "rgba(234, 179, 8, 0.15)",
              border: "1px solid #eab308",
              borderRadius: 10
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <AlertTriangle size={20} color="#eab308" />
                <div>
                  <div style={{ fontSize: "0.8125rem", fontWeight: 800, color: "#f8fafc" }}>
                    RISK CIRCUIT BREAKER TRIGGERED
                  </div>
                  <div style={{ fontSize: "0.6875rem", color: "#94a3b8", marginTop: 2 }}>
                    Daily loss threshold or max drawdown reached. Review open risk parameters.
                  </div>
                </div>
              </div>
              <button
                onClick={() => navigate("/app/risk")}
                style={{
                  padding: "4px 12px",
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 6,
                  color: "#f8fafc",
                  fontSize: "0.6875rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Review Risk Settings
              </button>
            </div>
          )}

          {/* Dynamic Execution / Order Failure Alerts */}
          {criticalAlerts.map(alert => (
            <div
              key={alert.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: isDense ? "0.5rem 1rem" : "0.625rem 1.25rem",
                background: alert.severity === "critical" ? "rgba(239, 68, 68, 0.12)" : "rgba(234, 179, 8, 0.12)",
                border: `1px solid ${alert.severity === "critical" ? "#ef4444" : "#eab308"}`,
                borderRadius: 8
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                <AlertTriangle size={16} color={alert.severity === "critical" ? "#ef4444" : "#eab308"} />
                <span style={{ fontSize: "0.75rem", color: "#f8fafc", fontWeight: 600 }}>
                  {alert.message}
                </span>
              </div>
              {alert.actionPath && (
                <button
                  onClick={() => navigate(alert.actionPath)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "#38bdf8",
                    fontSize: "0.6875rem",
                    fontWeight: 700,
                    cursor: "pointer"
                  }}
                >
                  {alert.actionText || "View Details"} →
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── ZONE 2: PRIMARY CAPITAL & PERFORMANCE HERO CARDS ───────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
        gap: isDense ? "0.75rem" : "1rem",
        marginBottom: isDense ? "0.875rem" : "1.25rem"
      }}>
        {/* Total Equity */}
        <div style={{
          background: "#0c1017",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: isDense ? "0.75rem 1rem" : "1rem 1.25rem"
        }}>
          <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Total Equity ({portfolioData.currency})
          </div>
          <div style={{ fontSize: isDense ? "1.25rem" : "1.5rem", fontWeight: 800, color: "#f8fafc", marginTop: "0.25rem", letterSpacing: "-0.03em" }}>
            ${(portfolioData?.totalEquity ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "0.6875rem", color: (portfolioData?.cumulativePnl ?? 0) >= 0 ? "#10b981" : "#ef4444", marginTop: "0.25rem" }}>
            Lifetime P&L: {(portfolioData?.cumulativePnl ?? 0) >= 0 ? "+" : ""}${(portfolioData?.cumulativePnl ?? 0).toFixed(2)}
          </div>
        </div>

        {/* Available Cash / Liquidity */}
        <div style={{
          background: "#0c1017",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: isDense ? "0.75rem 1rem" : "1rem 1.25rem"
        }}>
          <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Available Liquidity
          </div>
          <div style={{ fontSize: isDense ? "1.25rem" : "1.5rem", fontWeight: 800, color: "#f8fafc", marginTop: "0.25rem", letterSpacing: "-0.03em" }}>
            ${(portfolioData?.availableBalance ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "0.6875rem", color: "#64748b", marginTop: "0.25rem" }}>
            Free: ${(portfolioData?.freeBalance ?? 0).toFixed(0)} • Used: ${(portfolioData?.usedBalance ?? 0).toFixed(0)}
          </div>
        </div>

        {/* Today's Total P&L */}
        <div style={{
          background: "#0c1017",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: isDense ? "0.75rem 1rem" : "1rem 1.25rem"
        }}>
          <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Today's Total P&L
          </div>
          <div style={{
            fontSize: isDense ? "1.25rem" : "1.5rem",
            fontWeight: 800,
            color: (portfolioData?.todayPnl ?? 0) >= 0 ? "#10b981" : "#ef4444",
            marginTop: "0.25rem",
            letterSpacing: "-0.03em"
          }}>
            {(portfolioData?.todayPnl ?? 0) >= 0 ? "+" : ""}${(portfolioData?.todayPnl ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
            <span style={{ fontSize: "0.8125rem", fontWeight: 600, marginLeft: "0.375rem" }}>
              ({(portfolioData?.todayReturnPct ?? 0) >= 0 ? "+" : ""}{portfolioData?.todayReturnPct ?? 0}%)
            </span>
          </div>
          <div style={{ fontSize: "0.6875rem", color: "#94a3b8", marginTop: "0.25rem" }}>
            Realized: <span style={{ color: (portfolioData?.todayRealizedPnl ?? 0) >= 0 ? "#10b981" : "#ef4444" }}>
              {(portfolioData?.todayRealizedPnl ?? 0) >= 0 ? "+" : ""}${(portfolioData?.todayRealizedPnl ?? 0).toFixed(2)}
            </span> • uPnL: <span style={{ color: (portfolioData?.unrealizedPnl ?? 0) >= 0 ? "#10b981" : "#ef4444" }}>
              {(portfolioData?.unrealizedPnl ?? 0) >= 0 ? "+" : ""}${(portfolioData?.unrealizedPnl ?? 0).toFixed(2)}
            </span>
          </div>
        </div>

        {/* Capital Exposure */}
        <div style={{
          background: "#0c1017",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: isDense ? "0.75rem 1rem" : "1rem 1.25rem"
        }}>
          <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Market Exposure
          </div>
          <div style={{ fontSize: isDense ? "1.25rem" : "1.5rem", fontWeight: 800, color: "#f8fafc", marginTop: "0.25rem", letterSpacing: "-0.03em" }}>
            ${(portfolioData?.totalExposure ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: "0.6875rem", color: "#64748b", marginTop: "0.25rem" }}>
            {positions.length} Open Positions Active
          </div>
        </div>

        {/* Risk & Safety Level */}
        <div style={{
          background: "#0c1017",
          border: "1px solid #1e293b",
          borderRadius: 12,
          padding: isDense ? "0.75rem 1rem" : "1rem 1.25rem"
        }}>
          <div style={{ fontSize: "0.6875rem", fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Risk Guard State
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.375rem" }}>
            <span style={{
              fontSize: "0.8125rem",
              fontWeight: 800,
              padding: "2px 8px",
              borderRadius: 4,
              textTransform: "uppercase",
              background: isKillSwitchActive || riskLevel === "blocked" ? "rgba(239,68,68,0.2)" : riskLevel === "low" ? "rgba(16,185,129,0.15)" : "rgba(234,179,8,0.15)",
              color: isKillSwitchActive || riskLevel === "blocked" ? "#ef4444" : riskLevel === "low" ? "#10b981" : "#eab308",
              border: `1px solid ${isKillSwitchActive || riskLevel === "blocked" ? "#ef4444" : riskLevel === "low" ? "#10b981" : "#eab308"}`
            }}>
              {isKillSwitchActive ? "BLOCKED" : riskLevel}
            </span>
            <span style={{ fontSize: "0.75rem", color: "#94a3b8", fontWeight: 600 }}>
              Score: {riskScore}/100
            </span>
          </div>
          <div style={{ fontSize: "0.6875rem", color: "#64748b", marginTop: "0.25rem" }}>
            Circuit Breaker: {isCircuitBreakerArmed ? "ARMED" : "TRIGGERED"}
          </div>
        </div>
      </div>

      {/* ── COCKPIT MAIN LAYOUT GRID (LEFT 65% / RIGHT 35%) ───────────────────── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.8fr) minmax(0, 1.2fr)",
        gap: isDense ? "0.875rem" : "1.25rem",
        marginBottom: isDense ? "1rem" : "1.5rem"
      }}>

        {/* ── LEFT COLUMN: POSITIONS, PERFORMANCE & EXECUTIONS ───────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: isDense ? "0.875rem" : "1.25rem", minWidth: 0 }}>

          {/* ── ZONE 3 / P0.3 / 2D.3: OPEN POSITIONS LIVE TABLE ────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Open Positions ({positions.length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Live mark-to-market valuations</span>
              </div>

              <button
                onClick={() => navigate("/app/portfolio")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                View all in Portfolio <ArrowRight size={12} />
              </button>
            </div>

            {positions.length === 0 ? (
              <div style={{
                padding: "2rem 1rem",
                textAlign: "center",
                background: "#080a0e",
                borderRadius: 8,
                border: "1px dashed #1e293b"
              }}>
                <p style={{ fontSize: "0.8125rem", color: "#94a3b8", margin: 0, fontWeight: 500 }}>
                  No open positions currently held
                </p>
                <p style={{ fontSize: "0.6875rem", color: "#64748b", marginTop: "0.25rem" }}>
                  Deploy an automated strategy or connect exchange keys to begin trading.
                </p>
                <button
                  onClick={() => navigate("/app/strategies")}
                  style={{
                    marginTop: "0.75rem",
                    padding: "4px 12px",
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 6,
                    color: "#f8fafc",
                    fontSize: "0.6875rem",
                    fontWeight: 600,
                    cursor: "pointer"
                  }}
                >
                  View Strategy Hub
                </button>
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", minWidth: 780, borderCollapse: "collapse", fontSize: isDense ? "0.6875rem" : "0.75rem" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Symbol / Market</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Venue</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Mode</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Side</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Contracts</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Entry</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Mark</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>uPnL</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Liq. Price</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Liq. Dist</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map(pos => {
                      const isDeriv = pos.marketType === "future" || pos.marketType === "swap" || pos.liquidationPrice != null;
                      const dist = pos.liquidationDistancePct;

                      return (
                        <tr key={pos.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", color: "#f8fafc", fontWeight: 700 }}>
                            {pos.symbol}
                            <span style={{ fontSize: "0.625rem", color: "#64748b", marginLeft: "0.375rem", textTransform: "uppercase" }}>
                              {pos.marketType}
                            </span>
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "#0f172a",
                              border: "1px solid #334155",
                              color: "#cbd5e1",
                              textTransform: "uppercase",
                              fontWeight: 700
                            }}>
                              {pos.exchangeId}
                            </span>
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              color: "#94a3b8",
                              fontWeight: 600,
                              textTransform: "uppercase"
                            }}>
                              {isDeriv ? pos.marginType : "SPOT"}
                            </span>
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: pos.side === "long" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                              color: pos.side === "long" ? "#10b981" : "#ef4444",
                              fontWeight: 700,
                              textTransform: "uppercase"
                            }}>
                              {pos.side} {pos.leverage > 1 ? `${pos.leverage}x` : ""}
                            </span>
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                            {pos.contracts}
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", textAlign: "right", color: "#94a3b8" }}>
                            ${Number(pos?.entryPrice ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                            ${Number(pos?.markPrice ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                          </td>
                          <td style={{
                            padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem",
                            textAlign: "right",
                            fontWeight: 700,
                            color: pos.unrealizedPnl >= 0 ? "#10b981" : "#ef4444"
                          }}>
                            {pos.unrealizedPnl >= 0 ? "+" : ""}${pos.unrealizedPnl.toFixed(2)}
                            <span style={{ fontSize: "0.625rem", display: "block" }}>
                              ({pos.unrealizedPnlPct >= 0 ? "+" : ""}{pos.unrealizedPnlPct}%)
                            </span>
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", textAlign: "right", color: "#64748b" }}>
                            {pos.liquidationPrice != null ? `$${pos.liquidationPrice.toFixed(2)}` : "—"}
                          </td>
                          <td style={{ padding: isDense ? "0.4375rem 0.5rem" : "0.625rem 0.75rem", textAlign: "right", fontWeight: 700 }}>
                            {dist != null ? (
                              <span style={{
                                color: dist < 10 ? "#ef4444" : dist < 20 ? "#eab308" : "#10b981"
                              }}>
                                {dist < 10 ? `⚠️ ${dist.toFixed(1)}%` : `${dist.toFixed(1)}%`}
                              </span>
                            ) : (
                              <span style={{ color: "#64748b" }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ── ZONE 8: EQUITY PERFORMANCE CURVE ───────────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Equity Trajectory ({portfolioData.currency})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Historical NAV progression</span>
              </div>

              {/* Timeframe Selector */}
              <div style={{ display: "flex", gap: 3, background: "#080a0e", padding: 2, borderRadius: 6, border: "1px solid #1e293b" }}>
                {["1D", "1W", "1M", "3M", "ALL"].map(tf => (
                  <button
                    key={tf}
                    onClick={() => handleTimeframeChange(tf)}
                    style={{
                      padding: "3px 8px",
                      borderRadius: 4,
                      fontSize: "0.6875rem",
                      fontWeight: 700,
                      border: "none",
                      background: timeframe === tf ? "#0284c7" : "transparent",
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

            <div style={{ height: isDense ? 150 : 180, position: "relative" }}>
              {equityLoading ? (
                <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#475569", fontSize: "0.75rem" }}>
                  Loading trajectory...
                </div>
              ) : equityCurve.length === 0 ? (
                <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "0.375rem", color: "#475569" }}>
                  <BarChart2 size={24} style={{ opacity: 0.4 }} />
                  <span style={{ fontSize: "0.75rem" }}>No historical curve points yet.</span>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve}>
                    <defs>
                      <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#0284c7" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#0284c7" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="d" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis domain={["auto", "auto"]} hide />
                    <Tooltip
                      contentStyle={{ background: "#090d16", border: "1px solid #1e293b", borderRadius: 6, fontSize: "0.6875rem" }}
                      formatter={(val) => [`$${Number(val ?? 0).toLocaleString()}`, "Equity"]}
                    />
                    <Area type="monotone" dataKey="v" stroke="#0284c7" strokeWidth={2} fill="url(#equityGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* ── ZONE 7: RECENT EXECUTIONS LIVE TABLE ───────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Recent Order Executions ({executions.length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Audited exchange fills</span>
              </div>

              <button
                onClick={() => navigate("/app/trades")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Trade History <ArrowRight size={12} />
              </button>
            </div>

            {executions.length === 0 ? (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
                No recent order executions recorded for this session.
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", minWidth: 600, borderCollapse: "collapse", fontSize: isDense ? "0.6875rem" : "0.75rem" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Time</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Symbol</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Venue</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600 }}>Side</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Price</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Amount</th>
                      <th style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Realized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map(exec => (
                      <tr key={exec.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", color: "#64748b" }}>
                          {exec.timestamp}
                        </td>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", color: "#f8fafc", fontWeight: 700 }}>
                          {exec.symbol}
                        </td>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem" }}>
                          <span style={{
                            fontSize: "0.625rem",
                            padding: "2px 5px",
                            borderRadius: 4,
                            background: "#0f172a",
                            border: "1px solid #334155",
                            color: "#cbd5e1",
                            textTransform: "uppercase",
                            fontWeight: 700
                          }}>
                            {exec.exchangeId}
                          </span>
                        </td>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem" }}>
                          <span style={{
                            fontSize: "0.625rem",
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: exec.side === "buy" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                            color: exec.side === "buy" ? "#10b981" : "#ef4444",
                            fontWeight: 700,
                            textTransform: "uppercase"
                          }}>
                            {exec.side}
                          </span>
                        </td>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                          ${Number(exec?.price ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem", textAlign: "right", color: "#94a3b8" }}>
                          {exec.amount}
                        </td>
                        <td style={{
                          padding: isDense ? "0.375rem 0.5rem" : "0.5rem 0.75rem",
                          textAlign: "right",
                          fontWeight: 700,
                          color: exec.realizedPnl > 0 ? "#10b981" : exec.realizedPnl < 0 ? "#ef4444" : "#64748b"
                        }}>
                          {exec.realizedPnl > 0 ? "+" : ""}${exec.realizedPnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </div>

        {/* ── RIGHT COLUMN: STRATEGIES, RISK, HEALTH & ACTIONS ───────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: isDense ? "0.875rem" : "1.25rem", minWidth: 0 }}>

          {/* ── ZONE 4 / P1.3: ACTIVE STRATEGIES / BOTS ─────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Active Bot Fleet ({strategies.filter(s => s.status === "active" || s.status === "running").length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Algorithmic deployment instances</span>
              </div>

              <button
                onClick={() => navigate("/app/strategies")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Manage All <ArrowRight size={12} />
              </button>
            </div>

            {strategies.length === 0 ? (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
                No active strategy bots deployed.
                <button
                  onClick={() => navigate("/app/builder")}
                  style={{
                    display: "block",
                    margin: "0.5rem auto 0",
                    padding: "4px 12px",
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 6,
                    color: "#f8fafc",
                    fontSize: "0.6875rem",
                    fontWeight: 600,
                    cursor: "pointer"
                  }}
                >
                  Create Strategy
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: isDense ? "0.375rem" : "0.5rem" }}>
                {strategies.map(strat => {
                  const isRunning = strat.status === "active" || strat.status === "running";
                  const isError = strat.status === "error" || strat.status === "failed" || strat.health === "error";

                  return (
                    <div
                      key={strat.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: isDense ? "0.5rem 0.75rem" : "0.625rem 0.875rem",
                        background: "#080a0e",
                        border: `1px solid ${isError ? "#ef4444" : "#1e293b"}`,
                        borderRadius: 8
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                        <span style={{
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          background: isError ? "#ef4444" : isRunning ? "#10b981" : "#eab308"
                        }} />
                        <div>
                          <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "#f8fafc" }}>
                            {strat.name}
                            {isError && (
                              <span style={{
                                fontSize: "0.5625rem",
                                padding: "1px 4px",
                                borderRadius: 3,
                                background: "rgba(239, 68, 68, 0.2)",
                                color: "#ef4444",
                                marginLeft: "0.375rem",
                                fontWeight: 800
                              }}>
                                FAILED
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: "0.625rem", color: isError ? "#ef4444" : "#64748b", marginTop: "0.125rem" }}>
                            {isError && strat.errorMessage ? strat.errorMessage : `${strat.pair} • ${strat.lastSignalTime}`}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                        <div style={{ textAlign: "right" }}>
                          <div style={{
                            fontSize: "0.75rem",
                            fontWeight: 700,
                            color: strat.todayPnl >= 0 ? "#10b981" : "#ef4444"
                          }}>
                            {strat.todayPnl >= 0 ? "+" : ""}${strat.todayPnl.toFixed(2)}
                          </div>
                          <div style={{ fontSize: "0.5625rem", color: "#64748b" }}>Today's P&L</div>
                        </div>

                        {isError ? (
                          <button
                            onClick={() => navigate("/app/strategies")}
                            style={{
                              padding: "4px 8px",
                              borderRadius: 6,
                              fontSize: "0.625rem",
                              fontWeight: 700,
                              border: "1px solid #ef4444",
                              background: "rgba(239,68,68,0.15)",
                              color: "#ef4444",
                              cursor: "pointer"
                            }}
                          >
                            Inspect
                          </button>
                        ) : (
                          <button
                            onClick={() => handleToggleStrategy(strat.id)}
                            style={{
                              padding: "4px 8px",
                              borderRadius: 6,
                              fontSize: "0.625rem",
                              fontWeight: 700,
                              border: "1px solid #334155",
                              background: "#0f172a",
                              color: "#f8fafc",
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              gap: 3
                            }}
                          >
                            {isRunning ? <Pause size={10} /> : <Play size={10} />}
                            {isRunning ? "Pause" : "Run"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* ── ZONE 5: RISK & SAFETY MONITOR ──────────────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <ShieldCheck size={16} color="#10b981" />
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Risk & Safety Matrix
                </h2>
              </div>

              <button
                onClick={() => navigate("/app/risk")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Controls <ArrowRight size={12} />
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: isDense ? "0.5rem" : "0.75rem", fontSize: "0.75rem" }}>
              {/* Daily Loss Utilization Bar */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                  <span style={{ color: "#94a3b8" }}>Daily Loss Limit Utilized</span>
                  <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                    ${riskState?.daily_loss_utilized != null ? floatVal(riskState.daily_loss_utilized).toFixed(2) : "0.00"} / ${riskState?.max_daily_loss != null ? floatVal(riskState.max_daily_loss).toFixed(2) : "500.00"}
                  </span>
                </div>
                <div style={{ height: 5, background: "#1e293b", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{
                    height: "100%",
                    width: `${Math.min(100, Math.max(0, ((riskState?.daily_loss_utilized || 0) / (riskState?.max_daily_loss || 500)) * 100))}%`,
                    background: riskState?.daily_loss_utilized > (riskState?.max_daily_loss * 0.8) ? "#ef4444" : "#10b981"
                  }} />
                </div>
              </div>

              {/* Position Capacity */}
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0.3125rem 0", borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                <span style={{ color: "#94a3b8" }}>Open Position Capacity</span>
                <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                  {positions.length} / {riskState?.max_positions || 10} Slots
                </span>
              </div>

              {/* Drawdown */}
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0.3125rem 0", borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                <span style={{ color: "#94a3b8" }}>Current Drawdown</span>
                <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                  {riskState?.current_drawdown_pct != null ? `${floatVal(riskState.current_drawdown_pct).toFixed(2)}%` : "0.00%"}
                </span>
              </div>

              {/* Emergency Kill Switch */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "0.25rem" }}>
                <span style={{ color: "#94a3b8" }}>Emergency Kill Switch</span>
                <span style={{
                  padding: "2px 6px",
                  borderRadius: 4,
                  fontSize: "0.625rem",
                  fontWeight: 700,
                  background: isKillSwitchActive ? "rgba(239,68,68,0.2)" : "rgba(16,185,129,0.2)",
                  color: isKillSwitchActive ? "#ef4444" : "#10b981"
                }}>
                  {isKillSwitchActive ? "TRIGGERED (BLOCKED)" : "STANDBY (READY)"}
                </span>
              </div>
            </div>
          </div>

          {/* ── ZONE 6: EXCHANGE HEALTH & CONNECTIVITY ─────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: isDense ? "0.875rem 1rem" : "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <Server size={16} color="#38bdf8" />
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Exchange Venues ({exchangeConnections.length})
                </h2>
              </div>

              <button
                onClick={() => navigate("/app/exchange")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Manage <ArrowRight size={12} />
              </button>
            </div>

            {exchangeConnections.length === 0 ? (
              <div style={{ padding: "1rem", textAlign: "center", background: "#080a0e", borderRadius: 8, border: "1px dashed #1e293b" }}>
                <p style={{ fontSize: "0.75rem", color: "#94a3b8", margin: 0 }}>
                  No exchange accounts connected
                </p>
                <button
                  onClick={() => navigate("/app/exchange")}
                  style={{
                    marginTop: "0.5rem",
                    padding: "4px 12px",
                    background: "#0284c7",
                    border: "none",
                    borderRadius: 6,
                    color: "#fff",
                    fontSize: "0.6875rem",
                    fontWeight: 700,
                    cursor: "pointer"
                  }}
                >
                  Connect Exchange Keys
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: isDense ? "0.375rem" : "0.5rem" }}>
                {exchangeConnections.map(ex => (
                  <div
                    key={ex.exchange_id || ex.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: isDense ? "0.375rem 0.625rem" : "0.5rem 0.75rem",
                      background: "#080a0e",
                      borderRadius: 6,
                      border: "1px solid #1e293b"
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{
                        width: 7,
                        height: 7,
                        borderRadius: "50%",
                        background: ex.status === "connected" ? "#10b981" : "#ef4444"
                      }} />
                      <span style={{ fontSize: "0.75rem", color: "#f8fafc", fontWeight: 700, textTransform: "uppercase" }}>
                        {ex.exchange_id || ex.name}
                      </span>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{
                        fontSize: "0.625rem",
                        color: ex.latency_ms != null ? "#10b981" : "#64748b",
                        fontWeight: 600
                      }}>
                        {ex.latency_ms != null ? `${ex.latency_ms} ms` : "Latency unavailable"}
                      </span>
                      <span style={{
                        fontSize: "0.625rem",
                        padding: "1px 5px",
                        borderRadius: 3,
                        background: ex.status === "connected" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                        color: ex.status === "connected" ? "#10b981" : "#ef4444",
                        textTransform: "uppercase",
                        fontWeight: 700
                      }}>
                        {ex.status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── ZONE 7 (PART B): ACTIONABLE NOTIFICATIONS / INSIGHTS ───────── */}
          {tradingInsights.length > 0 && (
            <div style={{
              background: "#0c1017",
              border: "1px solid #1e293b",
              borderRadius: 12,
              padding: isDense ? "0.875rem 1rem" : "1.25rem"
            }}>
              <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, marginBottom: "0.75rem", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                Operational Insights
              </h2>

              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {tradingInsights.map(item => (
                  <div
                    key={item.id}
                    style={{
                      padding: "0.625rem 0.75rem",
                      background: "#080a0e",
                      borderLeft: `3px solid ${item.type === "warning" ? "#eab308" : item.type === "info" ? "#38bdf8" : "#10b981"}`,
                      borderRadius: "0 6px 6px 0",
                      fontSize: "0.6875rem"
                    }}
                  >
                    <p style={{ color: "#cbd5e1", margin: 0, lineHeight: 1.4 }}>
                      {item.text}
                    </p>
                    {item.actionPath && (
                      <button
                        onClick={() => navigate(item.actionPath)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "#38bdf8",
                          fontSize: "0.625rem",
                          fontWeight: 700,
                          padding: 0,
                          marginTop: 4,
                          cursor: "pointer"
                        }}
                      >
                        {item.actionText || "Action"} →
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      </div>

    </div>
  );
}
