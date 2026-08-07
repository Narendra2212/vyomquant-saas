import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search, Filter, Download, RefreshCw, Clock, Activity,
  TrendingUp, Shield, DollarSign, CheckCircle, XCircle, AlertTriangle,
  ChevronRight, ChevronDown, ExternalLink, Copy
} from "lucide-react";
import { C, Tag2, StatusDot, ProgressBar } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

/**
 * Signal Trace - Professional Execution Audit Console
 * 
 * Complete signal lifecycle tracking from strategy decision to final execution.
 */

export default function SignalTrace() {
  const navigate = useNavigate();
  const [signals, setSignals] = useState([]);
  const [selectedSignal, setSelectedSignal] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [filters, setFilters] = useState({
    strategy_id: "",
    exchange_id: "",
    symbol: "",
    decision: "",
    status: "",
    ml_type: "",
    date_from: "",
    date_to: "",
    search: ""
  });
  const [pagination, setPagination] = useState({ limit: 50, offset: 0, total: 0 });
  const [expandedRows, setExpandedRows] = useState({});

  const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";
  const token = sessionStorage.getItem("token");

  useEffect(() => {
    loadSignals();
  }, [filters, pagination.offset]);

  const loadSignals = async () => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
      params.append("limit", pagination.limit);
      params.append("offset", pagination.offset);

      const res = await fetch(`${API_BASE}/api/signal-trace/signals?${params}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      const data = await res.json();
      setSignals(data.signals || []);
      setPagination(prev => ({ ...prev, total: data.total || 0 }));
    } catch (err) {
      console.error("Error loading signals:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const loadSignalDetail = async (signalId) => {
    try {
      const res = await fetch(`${API_BASE}/api/signal-trace/signals/${signalId}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      const data = await res.json();
      setSelectedSignal(data.signal);
      setTimeline(data.timeline || []);
    } catch (err) {
      console.error("Error loading signal detail:", err);
    }
  };

  const toggleRow = (signalId) => {
    setExpandedRows(prev => ({
      ...prev,
      [signalId]: !prev[signalId]
    }));
    if (!expandedRows[signalId]) {
      loadSignalDetail(signalId);
    }
  };

  const exportSignals = async (format) => {
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
      params.append("format", format);

      const res = await fetch(`${API_BASE}/api/signal-trace/signals/export?${params}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      
      if (format === "csv") {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "signal_trace.csv";
        a.click();
      } else {
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "signal_trace.json";
        a.click();
      }
    } catch (err) {
      console.error("Error exporting signals:", err);
    }
  };

  const getStatusColor = (status) => {
    const colors = {
      pending: "orange",
      accepted: "cyan",
      rejected: "red",
      executed: "green",
      failed: "red",
      cancelled: "gray",
      expired: "gray"
    };
    return colors[status] || "gray";
  };

  const getDecisionColor = (decision) => {
    const colors = {
      BUY: "green",
      SELL: "red",
      EXIT: "orange",
      CLOSE: "orange",
      HOLD: "gray"
    };
    return colors[decision] || "gray";
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20, margin: 0 }}>Signal Trace</h1>
          <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", margin: "4px 0 0 0" }}>
            Professional execution audit system
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="ghost" size="sm" icon={RefreshCw} onClick={loadSignals}>Refresh</Button>
          <Button variant="ghost" size="sm" icon={Download} onClick={() => exportSignals("json")}>Export JSON</Button>
          <Button variant="ghost" size="sm" icon={Download} onClick={() => exportSignals("csv")}>Export CSV</Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="p-4 mb-4">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Strategy</label>
            <input
              type="text"
              value={filters.strategy_id}
              onChange={(e) => setFilters(prev => ({ ...prev, strategy_id: e.target.value }))}
              placeholder="Strategy ID"
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            />
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Exchange</label>
            <input
              type="text"
              value={filters.exchange_id}
              onChange={(e) => setFilters(prev => ({ ...prev, exchange_id: e.target.value }))}
              placeholder="Exchange ID"
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            />
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Symbol</label>
            <input
              type="text"
              value={filters.symbol}
              onChange={(e) => setFilters(prev => ({ ...prev, symbol: e.target.value }))}
              placeholder="BTC/USDT"
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            />
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Decision</label>
            <select
              value={filters.decision}
              onChange={(e) => setFilters(prev => ({ ...prev, decision: e.target.value }))}
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            >
              <option value="">All</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="EXIT">EXIT</option>
              <option value="CLOSE">CLOSE</option>
              <option value="HOLD">HOLD</option>
            </select>
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Status</label>
            <select
              value={filters.status}
              onChange={(e) => setFilters(prev => ({ ...prev, status: e.target.value }))}
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            >
              <option value="">All</option>
              <option value="pending">Pending</option>
              <option value="accepted">Accepted</option>
              <option value="rejected">Rejected</option>
              <option value="executed">Executed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
              <option value="expired">Expired</option>
            </select>
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>ML Type</label>
            <select
              value={filters.ml_type}
              onChange={(e) => setFilters(prev => ({ ...prev, ml_type: e.target.value }))}
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            >
              <option value="">All</option>
              <option value="ml">ML/DL</option>
              <option value="rule_based">Rule Based</option>
            </select>
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Date From</label>
            <input
              type="date"
              value={filters.date_from}
              onChange={(e) => setFilters(prev => ({ ...prev, date_from: e.target.value }))}
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            />
          </div>
          <div>
            <label style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4, display: "block" }}>Date To</label>
            <input
              type="date"
              value={filters.date_to}
              onChange={(e) => setFilters(prev => ({ ...prev, date_to: e.target.value }))}
              style={{
                width: "100%",
                background: C.bg3,
                border: `1px solid ${C.border}`,
                borderRadius: 6,
                padding: "8px 12px",
                color: C.t1,
                fontSize: 10,
                fontFamily: "monospace"
              }}
            />
          </div>
        </div>
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <input
            type="text"
            value={filters.search}
            onChange={(e) => setFilters(prev => ({ ...prev, search: e.target.value }))}
            placeholder="Search by Signal ID..."
            style={{
              flex: 1,
              background: C.bg3,
              border: `1px solid ${C.border}`,
              borderRadius: 6,
              padding: "8px 12px",
              color: C.t1,
              fontSize: 10,
              fontFamily: "monospace"
            }}
          />
          <Button variant="primary" size="sm" icon={Search} onClick={loadSignals}>Search</Button>
          <Button variant="ghost" size="sm" icon={Filter} onClick={() => setFilters({
            strategy_id: "",
            exchange_id: "",
            symbol: "",
            decision: "",
            status: "",
            ml_type: "",
            date_from: "",
            date_to: "",
            search: ""
          })}>Clear</Button>
        </div>
      </Card>

      {/* Signals Table */}
      <Card className="p-4">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, margin: 0 }}>
            Signals ({pagination.total} total)
          </h3>
        </div>

        {isLoading ? (
          <div style={{ textAlign: "center", padding: 40, color: C.t3 }}>Loading signals...</div>
        ) : signals.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: C.t3 }}>No signals found</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {signals.map(signal => (
              <div key={signal.id}>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 150px 100px 100px 100px 100px 100px 80px 80px 80px 40px",
                    gap: 12,
                    padding: "12px",
                    background: C.bg3,
                    borderRadius: 6,
                    cursor: "pointer",
                    border: `1px solid ${C.border}`,
                    alignItems: "center"
                  }}
                  onClick={() => toggleRow(signal.id)}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {expandedRows[signal.id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>
                      {signal.id.slice(0, 8)}...
                    </span>
                  </div>
                  <Tag2 c={getDecisionColor(signal.decision)}>{signal.decision}</Tag2>
                  <Tag2 c={getStatusColor(signal.status)}>{signal.status}</Tag2>
                  <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{signal.symbol}</span>
                  <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{signal.exchange_id}</span>
                  <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{signal.timeframe}</span>
                  <span style={{ color: signal.ml_info ? "cyan" : C.t3, fontSize: 9, fontFamily: "monospace" }}>
                    {signal.ml_info ? "ML" : "Rule"}
                  </span>
                  <span style={{ color: signal.pnl ? (signal.pnl >= 0 ? C.green : C.red) : C.t3, fontSize: 10, fontWeight: 600 }}>
                    {signal.pnl ? `${signal.pnl.toFixed(2)}` : "-"}
                  </span>
                  <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>
                    {signal.generated_at ? new Date(signal.generated_at).toLocaleString() : "-"}
                  </span>
                  <Button variant="ghost" size="xs" icon={ExternalLink} onClick={(e) => { e.stopPropagation(); navigate(`/app/signal-trace/${signal.id}`); }} />
                </div>

                {expandedRows[signal.id] && selectedSignal && selectedSignal.id === signal.id && (
                  <div style={{ padding: 16, background: C.bg2, borderRadius: 6, marginTop: 2, border: `1px solid ${C.border}` }}>
                    {/* Signal Details */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 16 }}>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Signal Information</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Strategy: {selectedSignal.strategy_id}</div>
                          <div>Version: {selectedSignal.strategy_version}</div>
                          <div>Deployment: {selectedSignal.deployment_id}</div>
                          <div>Worker: {selectedSignal.worker_id}</div>
                        </div>
                      </div>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Market Information</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Price: {selectedSignal.market_info?.price || "-"}</div>
                          <div>Spread: {selectedSignal.market_info?.spread || "-"}</div>
                          <div>Volume: {selectedSignal.market_info?.volume || "-"}</div>
                          <div>Volatility: {selectedSignal.market_info?.volatility || "-"}</div>
                        </div>
                      </div>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Risk Decision</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Passed: {selectedSignal.risk_passed ? "Yes" : "No"}</div>
                          <div>Reason: {selectedSignal.risk_reason || "-"}</div>
                          <div>Position Size: {selectedSignal.position_size || "-"}</div>
                          <div>Exposure: {selectedSignal.exposure ? `${selectedSignal.exposure}%` : "-"}</div>
                        </div>
                      </div>
                    </div>

                    {/* Order Information */}
                    {selectedSignal.order_id && (
                      <div style={{ marginBottom: 16 }}>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Order Information</h4>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Order ID: {selectedSignal.order_id}</div>
                          <div>Exchange Order ID: {selectedSignal.exchange_order_id || "-"}</div>
                          <div>Status: {selectedSignal.order_status}</div>
                          <div>Quantity: {selectedSignal.quantity}</div>
                          <div>Filled: {selectedSignal.filled}</div>
                          <div>Average Price: {selectedSignal.average_price}</div>
                          <div>Fees: {selectedSignal.fees}</div>
                          <div>Slippage: {selectedSignal.slippage}%</div>
                          <div>Latency: {selectedSignal.latency_ms}ms</div>
                        </div>
                      </div>
                    )}

                    {/* Timeline */}
                    <div>
                      <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Execution Timeline</h4>
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {timeline.map((event, idx) => (
                          <div key={idx} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                            <div style={{ width: 2, height: "100%", background: C.border, position: "relative" }}>
                              <div style={{ position: "absolute", top: 6, left: -4, width: 10, height: 10, borderRadius: "50%", background: C.cyan }} />
                            </div>
                            <div style={{ flex: 1 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                <span style={{ color: C.t1, fontSize: 10, fontWeight: 600 }}>{event.event}</span>
                                <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>
                                  {event.timestamp ? new Date(event.timestamp).toLocaleString() : "-"}
                                </span>
                              </div>
                              <pre style={{ color: C.t2, fontSize: 8, fontFamily: "monospace", marginTop: 4, whiteSpace: "pre-wrap" }}>
                                {JSON.stringify(event.data, null, 2)}
                              </pre>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16, paddingTop: 16, borderTop: `1px solid ${C.border}` }}>
          <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
            Showing {pagination.offset + 1}-{Math.min(pagination.offset + pagination.limit, pagination.total)} of {pagination.total}
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <Button
              variant="ghost"
              size="sm"
              disabled={pagination.offset === 0}
              onClick={() => setPagination(prev => ({ ...prev, offset: Math.max(0, prev.offset - prev.limit) }))}
            >
              Previous
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={pagination.offset + pagination.limit >= pagination.total}
              onClick={() => setPagination(prev => ({ ...prev, offset: prev.offset + prev.limit }))}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
