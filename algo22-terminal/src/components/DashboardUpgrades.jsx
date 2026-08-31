/**
 * DashboardUpgrades.jsx
 * Enhanced dashboard components for monitoring
 * - EquityCurveChart
 * - DrawdownChart
 * - LivePositions (with PnL color coding)
 * - PerformanceMetrics
 * - RiskAlertBanner
 */

import React, { useState, useMemo } from "react";
import {
  TrendingUp, TrendingDown, Activity, DollarSign,
  Target, Zap, Shield, AlertTriangle, CheckCircle,
  Clock, BarChart3, PieChart, ArrowUpRight, ArrowDownRight,
  X, Flame
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine
} from "recharts";
import { C } from './ui-legacy/primitives';


// ═══════════════════════════════════════════════════════════════════
// COLOR THEME — uses canonical C from ui-legacy/primitives
// (imported above — do not redefine locally)
// ═══════════════════════════════════════════════════════════════════


// ═══════════════════════════════════════════════════════════════════
// RISK ALERT BANNER
// ═══════════════════════════════════════════════════════════════════
export const RiskAlertBanner = React.memo(function RiskAlertBanner({ level = "warning", title, message, onDismiss, onAction, actionLabel }) {
  const configs = {
    critical: {
      icon: Flame,
      bg: C.lossBg,
      border: `1px solid rgba(239, 68, 68, 0.3)`,
      accent: C.loss,
      title: "CRITICAL RISK"
    },
    warning: {
      icon: AlertTriangle,
      bg: "rgba(245, 158, 11, 0.1)",
      border: `1px solid rgba(245, 158, 11, 0.3)`,
      accent: C.warning,
      title: "RISK WARNING"
    },
    info: {
      icon: Shield,
      bg: "rgba(74, 158, 255, 0.1)",
      border: `1px solid rgba(74, 158, 255, 0.3)`,
      accent: C.accent,
      title: "RISK ALERT"
    }
  };

  const config = configs[level] || configs.warning;
  const Icon = config.icon;

  return (
    <div style={{
      background: C.bg2,
      border: `1px solid ${C.border}`,
      borderRadius: 6,
      boxShadow: C.shadow,
      padding: "8px 12px",
      display: "flex",
      alignItems: "flex-start",
      gap: 12,
      marginBottom: 12
    }}>
      <Icon size={20} color={config.accent} style={{ flexShrink: 0, marginTop: 2 }} />

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          color: config.accent,
          fontSize: 11,
          fontWeight: 700,
          fontFamily: "monospace",
          letterSpacing: 0.5,
          marginBottom: 4,
          textTransform: "uppercase"
        }}>
          {title || config.title}
        </div>
        <div style={{ color: C.t1, fontSize: 13, lineHeight: 1.5 }}>
          {message}
        </div>

        {onAction && (
          <button
            onClick={onAction}
            style={{
              marginTop: 10,
              padding: "6px 12px",
              background: config.accent,
              color: "#000",
              border: "none",
              borderRadius: 4,
              fontSize: 11,
              fontWeight: 600,
              fontFamily: "monospace",
              cursor: "pointer"
            }}
          >
            {actionLabel || "Take Action"}
          </button>
        )}
      </div>

      {onDismiss && (
        <button
          onClick={onDismiss}
          style={{
            background: "transparent",
            border: "none",
            color: C.t3,
            cursor: "pointer",
            padding: 4,
            flexShrink: 0
          }}
        >
          <X size={16} />
        </button>
      )}
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════
// EQUITY CURVE CHART COMPONENT
// ═══════════════════════════════════════════════════════════════════
export const EquityCurveChart = React.memo(function EquityCurveChart({ data, height = 240, showDrawdown = false }) {
  const [hoverData, setHoverData] = useState(null);

  const processedData = useMemo(() => {
    if (!data || data.length === 0) return [];

    let peak = 0;
    return data.map((point, index) => {
      const value = Number(point.value ?? point.equity ?? point.v ?? 0);
      if (value > peak) peak = value;
      const drawdown = peak > 0 ? ((peak - value) / peak) * 100 : 0;

      return {
        index,
        timestamp: point.timestamp || point.date || point.t || index,
        value,
        drawdown,
        isPeak: value === peak
      };
    });
  }, [data]);

  const stats = useMemo(() => {
    if (processedData.length === 0) return null;
    const values = processedData.map(d => d.value);
    const start = values[0];
    const end = values[values.length - 1];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const return_pct = start > 0 ? ((end - start) / start) * 100 : 0;
    const max_dd = Math.max(...processedData.map(d => d.drawdown));

    return { start, end, min, max, return_pct, max_dd };
  }, [processedData]);

  if (!data || data.length < 2) {
    return (
      <div style={{
        height,
        background: C.card,
        borderRadius: 8,
        border: `1px solid ${C.border}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: C.t3,
        fontSize: 12,
        fontFamily: "monospace"
      }}>
        No equity data available
      </div>
    );
  }

  const isPositiveReturn = (stats?.return_pct || 0) >= 0;
  const chartColor = isPositiveReturn ? C.profit : C.loss;
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.card,
        borderRadius: 8,
        border: `1px solid ${isHovered ? C.borderLight : C.border}`,
        boxShadow: isHovered ? C.shadowMd : C.shadow,
        transform: isHovered ? "translateY(-1px)" : "none",
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)",
        padding: 16
      }}
    >
      {/* Header with stats */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TrendingUp size={18} color={chartColor} />
          <span style={{ color: C.t1, fontWeight: 600, fontSize: 14 }}>Equity Curve</span>
        </div>

        {stats && (
          <div style={{ display: "flex", gap: 16, fontSize: 11, fontFamily: "monospace" }}>
            <div>
              <span style={{ color: C.t3 }}>Return: </span>
              <span style={{ color: chartColor, fontWeight: 600 }}>
                {isPositiveReturn ? "+" : ""}{stats.return_pct.toFixed(2)}%
              </span>
            </div>
            <div>
              <span style={{ color: C.t3 }}>Max DD: </span>
              <span style={{ color: C.warning, fontWeight: 600 }}>
                -{stats.max_dd.toFixed(2)}%
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Chart */}
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={processedData}
            onMouseMove={(e) => setHoverData(e.activePayload?.[0]?.payload)}
            onMouseLeave={() => setHoverData(null)}
          >
            <defs>
              <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={chartColor} stopOpacity={0.25} />
                <stop offset="95%" stopColor={chartColor} stopOpacity={0} />
              </linearGradient>
            </defs>

            <XAxis
              dataKey="timestamp"
              hide
            />
            <YAxis
              domain={['auto', 'auto']}
              hide
            />

            {showDrawdown && (
              <ReferenceLine
                y={stats?.max}
                stroke={C.borderLight}
                strokeDasharray="3 3"
              />
            )}

            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                return (
                  <div style={{
                    background: C.bg2,
                    border: `1px solid ${C.border}`,
                    borderRadius: 6,
                    padding: "8px 12px",
                    fontFamily: "monospace"
                  }}>
                    <div style={{ color: C.t2, fontSize: 10, marginBottom: 4 }}>
                      {typeof p.timestamp === 'string' ? p.timestamp : `Point ${p.index}`}
                    </div>
                    <div style={{ color: C.t1, fontSize: 13, fontWeight: 700 }}>
                      ${Number(p?.value ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    {p.drawdown > 0 && (
                      <div style={{ color: C.warning, fontSize: 10, marginTop: 2 }}>
                        DD: -{p.drawdown.toFixed(2)}%
                      </div>
                    )}
                  </div>
                );
              }}
            />

            <Area
              type="monotone"
              dataKey="value"
              stroke={chartColor}
              strokeWidth={2}
              fill="url(#equityGradient)"
              dot={false}
              activeDot={{ r: 4, stroke: chartColor, strokeWidth: 2, fill: C.card }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Hover info */}
      {hoverData && (
        <div style={{
          marginTop: 8,
          padding: "8px 12px",
          background: C.bg2,
          borderRadius: 6,
          fontSize: 11,
          fontFamily: "monospace",
          display: "flex",
          justifyContent: "space-between"
        }}>
          <span style={{ color: C.t3 }}>Value: <span style={{ color: C.t1 }}>${hoverData.value.toFixed(2)}</span></span>
          <span style={{ color: C.t3 }}>Drawdown: <span style={{ color: C.warning }}>-{hoverData.drawdown.toFixed(2)}%</span></span>
        </div>
      )}
    </div>
  );
});
// DRAWDOWN CHART COMPONENT
// ═══════════════════════════════════════════════════════════════════
export const DrawdownChart = React.memo(function DrawdownChart({ data, height = 200 }) {
  const [hoverPoint, setHoverPoint] = useState(null);

  const { path, maxDrawdown, avgDrawdown } = useMemo(() => {
    if (!data || data.length < 2) return { path: "", maxDrawdown: 0, avgDrawdown: 0 };

    const drawdowns = data.map(d => d.drawdown_pct || 0);
    const maxDD = Math.max(...drawdowns);
    const avgDD = drawdowns.reduce((a, b) => a + b, 0) / drawdowns.length;

    const width = 100;
    const step = width / (data.length - 1);

    let pathStr = "";
    data.forEach((point, i) => {
      const x = i * step;
      const y = 100 - (point.drawdown_pct || 0);
      pathStr += i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`;
    });

    return { path: pathStr, maxDrawdown: maxDD, avgDrawdown: avgDD };
  }, [data]);

  if (!data || data.length < 2) {
    return (
      <div style={{
        height,
        background: C.card,
        borderRadius: 12,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: C.t3
      }}>
        No drawdown data available
      </div>
    );
  }

  return (
    <div style={{ background: C.card, borderRadius: 12, padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Activity size={18} color={C.danger} />
          <span style={{ color: C.t1, fontWeight: 600, fontSize: 14 }}>Drawdown History</span>
        </div>
        <div style={{ display: "flex", gap: 16 }}>
          <div>
            <span style={{ color: C.t3, fontSize: 11 }}>Max DD: </span>
            <span style={{ color: C.danger, fontWeight: 600 }}>{maxDrawdown.toFixed(2)}%</span>
          </div>
          <div>
            <span style={{ color: C.t3, fontSize: 11 }}>Avg DD: </span>
            <span style={{ color: C.warning, fontWeight: 600 }}>{avgDrawdown.toFixed(2)}%</span>
          </div>
        </div>
      </div>

      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        style={{ height, width: "100%" }}
        onMouseLeave={() => setHoverPoint(null)}
      >
        {/* Grid lines */}
        {[0, 25, 50, 75, 100].map(y => (
          <line key={y} x1="0" y1={y} x2="100" y2={y} stroke={C.border} strokeWidth="0.5" strokeDasharray="2" />
        ))}

        {/* Drawdown area */}
        <defs>
          <linearGradient id="ddGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.danger} stopOpacity="0.3" />
            <stop offset="100%" stopColor={C.danger} stopOpacity="0.05" />
          </linearGradient>
        </defs>

        <path
          d={`${path} L 100 100 L 0 100 Z`}
          fill="url(#ddGradient)"
        />

        <path
          d={path}
          fill="none"
          stroke={C.danger}
          strokeWidth="2"
          strokeLinecap="round"
        />

        {/* Data points */}
        {data.map((point, i) => {
          const x = (i / (data.length - 1)) * 100;
          const y = 100 - (point.drawdown_pct || 0);
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={hoverPoint === i ? 4 : 2}
              fill={hoverPoint === i ? C.danger : "transparent"}
              stroke={C.danger}
              strokeWidth="1"
              onMouseEnter={() => setHoverPoint(i)}
              style={{ cursor: "pointer" }}
            />
          );
        })}
      </svg>

      {hoverPoint !== null && (
        <div style={{
          marginTop: 8,
          padding: "8px 12px",
          background: C.border,
          borderRadius: 6,
          fontSize: 12,
          color: C.t1
        }}>
          <span style={{ color: C.t3 }}>{new Date(data[hoverPoint].timestamp).toLocaleString()}</span>
          <span style={{ marginLeft: 12, color: C.danger }}>
            DD: {data[hoverPoint].drawdown_pct?.toFixed(2)}%
          </span>
        </div>
      )}
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════
// LIVE POSITIONS COMPONENT
// ═══════════════════════════════════════════════════════════════════
export const LivePositions = React.memo(function LivePositions({ positions = [] }) {
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [hoveredPos, setHoveredPos] = useState(null);

  const totalPnL = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);
  const longCount = positions.filter(p => p.side === "long").length;
  const shortCount = positions.filter(p => p.side === "short").length;

  return (
    <div style={{ background: C.card, borderRadius: 8, padding: 16, border: `1px solid ${C.border}`, boxShadow: C.shadow }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Target size={18} color={C.accent} />
          <span style={{ color: C.t1, fontWeight: 600, fontSize: 14 }}>Live Positions</span>
          <span style={{
            background: totalPnL >= 0 ? `${C.success}20` : `${C.danger}20`,
            color: totalPnL >= 0 ? C.success : C.danger,
            padding: "2px 8px",
            borderRadius: 4,
            fontSize: 12,
            fontWeight: 600
          }}>
            {totalPnL >= 0 ? "+" : ""}${totalPnL.toFixed(2)}
          </span>
        </div>
        <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
          <span style={{ color: C.success }}>● {longCount} Long</span>
          <span style={{ color: C.danger }}>● {shortCount} Short</span>
        </div>
      </div>

      {positions.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 0", color: C.t3 }}>
          <Activity size={32} style={{ marginBottom: 8, opacity: 0.5 }} />
          <p>No open positions</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {positions.map((pos, i) => (
            <div
              key={i}
              onClick={() => setSelectedPosition(selectedPosition === i ? null : i)}
              onMouseEnter={() => setHoveredPos(i)}
              onMouseLeave={() => setHoveredPos(null)}
              style={{
                padding: 12,
                background: selectedPosition === i ? C.bg3 : (hoveredPos === i ? C.bg4 : "transparent"),
                borderRadius: 6,
                border: `1px solid ${selectedPosition === i ? C.accent : (hoveredPos === i ? C.borderLight : C.border)}`,
                cursor: "pointer",
                transform: hoveredPos === i ? "translateX(4px)" : "none",
                boxShadow: hoveredPos === i ? C.shadow : "none",
                transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{
                    color: pos.side === "long" ? C.success : C.danger,
                    fontWeight: 600,
                    fontSize: 12,
                    textTransform: "uppercase"
                  }}>
                    {pos.side}
                  </span>
                  <span style={{ color: C.t1, fontWeight: 600 }}>{pos.symbol}</span>
                  <span style={{ color: C.t3, fontSize: 12 }}>{pos.size} units</span>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{
                    color: (pos.unrealized_pnl || 0) >= 0 ? C.success : C.danger,
                    fontWeight: 600,
                    fontSize: 14
                  }}>
                    {(pos.unrealized_pnl || 0) >= 0 ? "+" : ""}${(pos.unrealized_pnl || 0).toFixed(2)}
                  </div>
                  <div style={{ color: C.t3, fontSize: 11 }}>
                    {pos.unrealized_pnl_pct?.toFixed(2)}%
                  </div>
                </div>
              </div>

              {selectedPosition === i && (
                <div style={{
                  marginTop: 12,
                  paddingTop: 12,
                  borderTop: `1px solid ${C.border}`,
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                  fontSize: 12
                }}>
                  <div>
                    <span style={{ color: C.t3 }}>Entry: </span>
                    <span style={{ color: C.t1 }}>${pos.entry_price?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span style={{ color: C.t3 }}>Current: </span>
                    <span style={{ color: C.t1 }}>${pos.current_price?.toFixed(2)}</span>
                  </div>
                  <div>
                    <span style={{ color: C.t3 }}>Opened: </span>
                    <span style={{ color: C.t1 }}>
                      {pos.opened_at ? new Date(pos.opened_at).toLocaleDateString() : "N/A"}
                    </span>
                  </div>
                  <div>
                    <span style={{ color: C.t3 }}>Duration: </span>
                    <span style={{ color: C.t1 }}>
                      {pos.opened_at ?
                        Math.floor((Date.now() - new Date(pos.opened_at).getTime()) / 3600000) + "h"
                        : "N/A"}
                    </span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════
// STAT GRID COMPONENT (with micro-interactions)
// ═══════════════════════════════════════════════════════════════════
function StatGrid({ statItems }) {
  const [hoveredIndex, setHoveredIndex] = useState(null);

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(3, 1fr)",
      gap: 12
    }}>
      {statItems.map((item, i) => (
        <div
          key={i}
          onMouseEnter={() => setHoveredIndex(i)}
          onMouseLeave={() => setHoveredIndex(null)}
          style={{
            padding: 12,
            background: hoveredIndex === i ? C.bg3 : C.bg,
            borderRadius: 6,
            border: `1px solid ${hoveredIndex === i ? C.borderLight : C.border}`,
            boxShadow: hoveredIndex === i ? C.shadowMd : C.shadow,
            transform: hoveredIndex === i ? "translateY(-2px)" : "none",
            cursor: "default",
            transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <item.icon size={14} color={item.color} />
            <span style={{ color: C.t3, fontSize: 11 }}>{item.label}</span>
          </div>
          <div style={{ color: item.color, fontWeight: 700, fontSize: 16 }}>
            {item.value}
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// PERFORMANCE METRICS COMPONENT
// ═══════════════════════════════════════════════════════════════════
export const PerformanceMetrics = React.memo(function PerformanceMetrics({ metrics }) {
  if (!metrics) {
    return (
      <div style={{
        background: C.card,
        borderRadius: 12,
        padding: 16,
        textAlign: "center",
        color: C.t3
      }}>
        <BarChart3 size={32} style={{ marginBottom: 8, opacity: 0.5 }} />
        <p>No performance data available</p>
      </div>
    );
  }

  const statItems = [
    { label: "Total Trades", value: metrics.total_trades || 0, icon: Activity, color: C.accent },
    { label: "Win Rate", value: `${(metrics.win_rate_pct || 0).toFixed(1)}%`, icon: Target, color: C.success },
    { label: "Sharpe Ratio", value: (metrics.sharpe_ratio || 0).toFixed(2), icon: Zap, color: C.warning },
    { label: "Max Drawdown", value: `${(metrics.max_drawdown_pct || 0).toFixed(1)}%`, icon: Shield, color: C.danger },
    { label: "Profit Factor", value: (metrics.profit_factor || 0).toFixed(2), icon: DollarSign, color: C.purple },
    { label: "Total Return", value: `${(metrics.total_return_pct || 0).toFixed(1)}%`, icon: TrendingUp, color: metrics.total_return_pct >= 0 ? C.success : C.danger },
  ];

  return (
    <div style={{ background: C.card, borderRadius: 12, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <BarChart3 size={18} color={C.purple} />
        <span style={{ color: C.t1, fontWeight: 600, fontSize: 14 }}>Performance Metrics</span>
      </div>

      <StatGrid statItems={statItems} />

      {/* Additional stats row */}
      <div style={{
        marginTop: 12,
        paddingTop: 12,
        borderTop: `1px solid ${C.border}`,
        display: "flex",
        justifyContent: "space-around",
        fontSize: 11,
        color: C.t2
      }}>
        <div>
          <span style={{ color: C.t3 }}>Winning: </span>
          <span style={{ color: C.success, fontWeight: 600 }}>{metrics.winning_trades || 0}</span>
        </div>
        <div>
          <span style={{ color: C.t3 }}>Losing: </span>
          <span style={{ color: C.danger, fontWeight: 600 }}>{metrics.losing_trades || 0}</span>
        </div>
        <div>
          <span style={{ color: C.t3 }}>Avg Trade: </span>
          <span style={{ color: C.accent, fontWeight: 600 }}>
            {(metrics.avg_trade_return_pct || 0).toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════
// SYSTEM STATUS COMPONENT
// ═══════════════════════════════════════════════════════════════════
export const SystemStatus = React.memo(function SystemStatus({ status }) {
  const isHealthy = status?.is_running && !status?.error_message;

  return (
    <div style={{
      background: C.card,
      borderRadius: 12,
      padding: 16,
      border: `1px solid ${isHealthy ? C.success : C.danger}`
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {isHealthy ? (
            <CheckCircle size={18} color={C.success} />
          ) : (
            <AlertTriangle size={18} color={C.danger} />
          )}
          <span style={{ color: C.t1, fontWeight: 600, fontSize: 14 }}>
            System Status
          </span>
        </div>
        <div style={{
          padding: "4px 12px",
          borderRadius: 20,
          background: isHealthy ? `${C.success}20` : `${C.danger}20`,
          color: isHealthy ? C.success : C.danger,
          fontSize: 12,
          fontWeight: 600,
        }}>
          {isHealthy ? "ONLINE" : "ERROR"}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={{ color: C.t3, fontSize: 11, marginBottom: 4 }}>Current Equity</div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 700 }}>
            ${(status?.current_equity || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </div>
          <div style={{
            color: (status?.total_return_pct || 0) >= 0 ? C.success : C.danger,
            fontSize: 12,
            display: "flex",
            alignItems: "center",
            gap: 4
          }}>
            {(status?.total_return_pct || 0) >= 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
            {(status?.total_return_pct || 0).toFixed(2)}%
          </div>
        </div>

        <div>
          <div style={{ color: C.t3, fontSize: 11, marginBottom: 4 }}>Open Positions</div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 700 }}>
            {status?.open_positions_count || 0}
          </div>
          <div style={{ color: C.t3, fontSize: 12 }}>
            {status?.total_trades_today || 0} trades today
          </div>
        </div>
      </div>

      {status?.error_message && (
        <div style={{
          marginTop: 12,
          padding: 8,
          background: `${C.danger}10`,
          borderRadius: 6,
          color: C.danger,
          fontSize: 12,
        }}>
          {status.error_message}
        </div>
      )}

      <div style={{ marginTop: 12, color: C.t3, fontSize: 10 }}>
        <Clock size={10} style={{ display: "inline", marginRight: 4 }} />
        Last update: {status?.last_update ? new Date(status.last_update).toLocaleString() : "N/A"}
      </div>
    </div>
  );
});

// ═══════════════════════════════════════════════════════════════════
// DEFAULT EXPORT
// ═══════════════════════════════════════════════════════════════════
export default {
  EquityCurveChart,
  DrawdownChart,
  LivePositions,
  PerformanceMetrics,
  SystemStatus,
  RiskAlertBanner,
  StatGrid,
};
