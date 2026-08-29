import React, { useState, useEffect, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { CONFIG } from "../config";
import {
  Search, Filter, Download, RefreshCw, Clock, Activity,
  TrendingUp, Shield, DollarSign, CheckCircle, XCircle, AlertTriangle,
  ChevronRight, ChevronDown, ExternalLink, Copy, ArrowLeft
} from "lucide-react";
import { C, Tag2, StatusDot, ProgressBar } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { get } from "../apiClient";
import wsClient from "../websocketClient";
import SignalTraceVisualization from "../components/SignalTraceVisualization";
import useSignalTraceRealtime from "../hooks/useSignalTraceRealtime";
import {
  SIGNAL_REALTIME_STATES,
  deploymentIdsFromSignals,
  mergeRealtimeSignals,
  unlistedSignalIds,
} from "../lib/signalTraceRealtime";

/**
 * Signal Trace - Professional Execution Audit Console
 *
 * Complete signal lifecycle tracking from strategy decision to final execution.
 *
 * REALTIME (trading-lifecycle-integration tasks 19.1, 19.2)
 * ========================================================
 * Requirements 17.4, 18.1, 18.2, 18.4, 18.5, 18.6, 18.7, 18.8, 23.6.
 *
 * The live updates are wired into THIS view — the list and its detail — rather than only
 * into the optional `SignalTraceVisualization` panel below. That panel subscribes by
 * event type to the legacy, unauthorised `signal_trace` broadcast and never takes a hold
 * on the socket at all, so before this task the page's own rows changed only when the
 * user pressed Refresh, and after task 14.3 stopped publishing signal lifecycle traffic
 * on that broadcast the panel no longer carries it either. It is kept as an opt-in
 * pipeline visualisation, which is all `design.md` asks of it.
 *
 * `SIGNAL_FAMILY` is scoped to `deployment_id`, so the page holds one
 * `signal.{deployment_id}` channel per deployment it is watching (Requirement 18.2) —
 * see `hooks/useSignalTraceRealtime.js` for which deployments those are and why there are
 * two sources for them.
 *
 * The connection-status indicator below is task 19.3 (Requirement 18.9).
 */

// ── The connection, as this page reports it (task 19.3, Requirement 18.9) ───────────────

/**
 * How each realtime state is drawn.
 *
 * Every entry carries a `glyph` *and* a `word` beside its colour, following
 * `components/DeployPreflightPanel.jsx`'s vocabulary (task 18.1): a user who cannot
 * distinguish green from red must still be able to tell a live page from a stalled one, so
 * the state is never signalled by colour alone.
 *
 * WHY THERE ARE FOUR ENTRIES FOR REQUIREMENT 18.9's THREE STATES
 * -------------------------------------------------------------
 * The hook reports four (`SIGNAL_REALTIME_STATES`), and `UNAVAILABLE` is not a fourth
 * shade of "disconnected": it means this page has no connection to lose — no session
 * token, or no active deployment to subscribe to. Drawing it as `DISCONNECTED` would claim
 * a connection had dropped when none was ever opened, and drawing it as `RECONNECTING`
 * would promise a retry that is not coming and could not succeed. So it gets its own word,
 * its own glyph and its own wording for the reason, which the page supplies because the
 * page is what knows whether there was a deployment to watch.
 *
 * `CONNECTING` is worded as "RECONNECTING" because that is what it is from the user's
 * side: `websocketClient` reports `connecting` both for the first attempt and for every
 * jittered backoff retry (Requirement 18.6), and the thing the user needs to know in both
 * cases is the same — frames are not arriving yet.
 */
export const SIGNAL_CONNECTION_PRESENTATION = Object.freeze({
  [SIGNAL_REALTIME_STATES.CONNECTED]: Object.freeze({
    word: "CONNECTED",
    glyph: "●",
    color: "#10b981",
    border: "1px solid rgba(16,185,129,0.35)",
    background: "rgba(16,185,129,0.08)",
    note: "Live updates are arriving; this list changes on its own.",
  }),
  [SIGNAL_REALTIME_STATES.CONNECTING]: Object.freeze({
    word: "RECONNECTING",
    glyph: "◌",
    // Dashed, as the preflight panel's unfinished state is: an attempt in progress is not
    // a verdict about the connection.
    color: "#eab308",
    border: "1px dashed rgba(234,179,8,0.55)",
    background: "rgba(234,179,8,0.08)",
    note: "Not receiving updates yet. A status change happening now will be read back when the connection returns.",
  }),
  [SIGNAL_REALTIME_STATES.DISCONNECTED]: Object.freeze({
    word: "DISCONNECTED",
    glyph: "✕",
    color: "#ef4444",
    border: "1px solid rgba(239,68,68,0.45)",
    background: "rgba(239,68,68,0.10)",
    note: "Live updates have stopped. What is shown may be out of date until the connection returns or you refresh.",
  }),
  [SIGNAL_REALTIME_STATES.UNAVAILABLE]: Object.freeze({
    word: "NOT LIVE",
    glyph: "–",
    color: "#94a3b8",
    border: "1px dashed rgba(148,163,184,0.45)",
    background: "rgba(148,163,184,0.08)",
    // Deliberately absent: the reason is the page's to state, see UNAVAILABLE_REASONS.
    note: null,
  }),
});

/**
 * A state this build cannot read. Reported as unreadable rather than as connected, for the
 * same reason the hook maps an unknown socket status to `DISCONNECTED`: a state we cannot
 * read is not evidence that frames are arriving.
 */
export const UNREADABLE_CONNECTION_PRESENTATION = Object.freeze({
  word: "UNKNOWN",
  glyph: "?",
  color: "#94a3b8",
  border: "1px dashed rgba(148,163,184,0.45)",
  background: "rgba(148,163,184,0.08)",
  note: "This page cannot read the state of its live connection, so treat the list as not updating.",
});

/**
 * The two things `UNAVAILABLE` can mean, told apart by whether there was anything to
 * subscribe to. `useSignalTraceRealtime` connects only with a token AND at least one
 * channel, so a page reporting `UNAVAILABLE` while holding channels is a page with no
 * session, and one holding none has nothing to watch.
 */
export const UNAVAILABLE_REASONS = Object.freeze({
  NOTHING_TO_WATCH:
    "No active deployment to watch, so there are no live updates to receive. This list updates when you refresh it.",
  NOT_AUTHENTICATED:
    "Not signed in for live updates, so this list changes only when you refresh it.",
});

/** @param {string|null|undefined} status @returns {Object} */
export function signalConnectionPresentation(status) {
  return SIGNAL_CONNECTION_PRESENTATION[status] ?? UNREADABLE_CONNECTION_PRESENTATION;
}

/**
 * Requirement 18.9's visible connection-status indicator.
 *
 * Rendered in every state, not only while the connection is down. The requirement names
 * "connected" as one of the states to be distinguished, and an indicator that appears only
 * on failure cannot distinguish a live page from one whose indicator has not been reached
 * yet — the user would have to know the control exists to read its absence.
 *
 * `role="status"` (with `aria-live="polite"`) because the whole point is that this changes
 * with no action from the user, so the change has to be announced and not only painted.
 * The glyph is `aria-hidden`, since the word beside it already carries the state.
 *
 * This is a separate element from the refusal banner on purpose: a refused channel
 * (Requirement 18.3) is a live connection that will not carry one deployment, which is a
 * different fact from the connection itself being down, and collapsing the two would make
 * a partial failure read as a total one.
 *
 * @param {Object} props
 * @param {string} props.status One of `SIGNAL_REALTIME_STATES`.
 * @param {number} [props.watching] How many channels the page holds, i.e. `channels.length`.
 */
export function SignalConnectionIndicator({ status, watching = 0 }) {
  const presentation = signalConnectionPresentation(status);
  const note =
    presentation.note ??
    (watching > 0 ? UNAVAILABLE_REASONS.NOT_AUTHENTICATED : UNAVAILABLE_REASONS.NOTHING_TO_WATCH);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="signal-trace-connection"
      data-status={status ?? "unreadable"}
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 8,
        flexWrap: "wrap",
        marginBottom: 16,
        padding: "8px 12px",
        borderRadius: 6,
        border: presentation.border,
        background: presentation.background,
        fontSize: 10,
        fontFamily: "monospace",
      }}
    >
      <span aria-hidden="true" style={{ color: presentation.color }}>
        {presentation.glyph}
      </span>
      <span style={{ color: presentation.color, fontWeight: 900, letterSpacing: 1 }}>
        {presentation.word}
      </span>
      <span style={{ color: C.t3 }}>{note}</span>
    </div>
  );
}

export default function SignalTrace() {
  const navigate = useNavigate();
  const { signalId } = useParams();
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
  const [showLivePipeline, setShowLivePipeline] = useState(false);

  const [searchParams] = useSearchParams();

  // Pre-populate filters from URL query params (e.g., strategy_id from Strategies page 'Trace' button)
  useEffect(() => {
    const urlStrategyId = searchParams.get("strategy_id");
    if (urlStrategyId) {
      setFilters(prev => ({ ...prev, strategy_id: urlStrategyId }));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount only

  useEffect(() => {
    if (signalId) return; // detail route: skip the list fetch, see below
    loadSignals();
  }, [filters, pagination.offset, signalId]);

  // Detail route (/app/signal-trace/:signalId): load just that one signal.
  useEffect(() => {
    if (!signalId) return;
    loadSignalDetail(signalId);
  }, [signalId]);

  const loadSignals = async () => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
      params.append("limit", pagination.limit);
      params.append("offset", pagination.offset);

      const data = await get(`/api/signal-trace/signals?${params}`);
      setSignals(data?.signals || []);
      setPagination(prev => ({ ...prev, total: data?.total || 0 }));
    } catch (err) {
      console.error("Error loading signals:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const loadSignalDetail = async (signalId) => {
    try {
      const data = await get(`/api/signal-trace/signals/${signalId}`);
      setSelectedSignal(data?.signal || null);
      setTimeline(data?.timeline || []);
    } catch (err) {
      console.error("Error loading signal detail:", err);
    }
  };

  /*
    ── Realtime (tasks 19.1, 19.2) ────────────────────────────────────────────────────────────

    The deployments watched come from two places, unioned by the hook. The strategy's own
    deployment listing is the primary source and is what makes Requirement 17.4's
    strategy-filtered entry live without further user action; the `deployment_id`s the
    loaded signals name are the second, because that listing is served from an in-process
    registry that does not contain a deployment started through `deploy_version`, and
    because an unfiltered page has no strategy to ask about at all.

    `onSnapshot` is Requirement 18.6's "request a snapshot rather than assuming no updates
    were missed": on a reconnect the whole current page is re-read, since the frames
    between the drop and the reconnect were not delivered to anyone.

    ── Task 19.2: why this read is NOT `?deployment_id=...&since=<last_known_seq>` ──
    Two findings, both about the server rather than about this page:

    1. `GET /api/signal-trace/signals` HAS NO `since` PARAMETER. `signal_trace.list_signals`
       declares thirteen filter categories plus `limit`/`offset`, and no cursor of any kind;
       FastAPI ignores a query parameter a handler does not declare, so sending `since`
       would narrow nothing while making this page's code claim it had. A read that says it
       is incremental and is not is worse than one that plainly re-reads everything.
    2. `seq` IS NOT A CURSOR INTO HISTORY even if the parameter existed. It is a per-channel
       in-process counter (`ws_channels.next_signal_sequence`) that is deliberately
       RESTARTED at 1 for a channel's first subscriber, precisely because the client's own
       `expectedSequence` restarts at 1 on the new connection. A pre-drop `seq` therefore
       names no position the server could resolve after a reconnect.

    So the snapshot is the page's own filtered read, unnarrowed — the closest thing the
    endpoint supports that cannot omit a signal (Requirement 17.1). Narrowing it by
    `deployment_id` to the subscribed deployments WAS considered and rejected: a signal row
    with no `deployment_id`, and any row outside the watched set, would silently vanish from
    a page that had been showing it, and `total`/`offset` would shift under the pagination.

    What makes the unnarrowed re-read safe is the other half of this task: the promise is
    returned to the hook, so the reducer drops the pushed state the read supersedes, and the
    content key stops the server's replay from re-applying a state change already on screen.
  */
  const signalDeploymentIds = useMemo(() => deploymentIdsFromSignals(signals), [signals]);

  const {
    signals: pushedSignals,
    refusals,
    deploymentsError,
    // Task 19.3, Requirement 18.9. The hook already reports the state; before this task the
    // page destructured neither, so a dropped connection was silent — the rows simply
    // stopped changing, which looks exactly like a quiet deployment.
    status: connectionStatus,
    channels: heldChannels,
  } = useSignalTraceRealtime({
    strategyId: filters.strategy_id,
    deploymentIds: signalDeploymentIds,
    // Returned, not fired and forgotten: the hook clears the superseded overlay only once
    // this read is actually on screen (task 19.2).
    onSnapshot: () => (signalId ? loadSignalDetail(signalId) : loadSignals()),
  });

  /*
    The rendered rows are the server's page with every pushed update applied IN PLACE, so
    the descending generation-time order the backend sorted by is never re-derived here.
    A pushed signal that is not on this page is reported rather than inserted — the client
    cannot decide whether it satisfies a thirteen-category server-side query — and the
    affordance offered for it is the same snapshot read.
  */
  const rows = useMemo(() => mergeRealtimeSignals(signals, pushedSignals), [signals, pushedSignals]);
  const newSignalIds = useMemo(
    () => unlistedSignalIds(signals, pushedSignals),
    [signals, pushedSignals],
  );
  const detailSignal = useMemo(
    () => (selectedSignal ? { ...selectedSignal, ...(pushedSignals?.[selectedSignal.id] || {}) } : null),
    [selectedSignal, pushedSignals],
  );

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

      const token = sessionStorage.getItem("token");
      const API_BASE = CONFIG.apiBaseUrl;
      const res = await fetch(`${API_BASE}/api/signal-trace/signals/export?${params}`, {
        headers: token ? { "Authorization": `Bearer ${token}` } : {}
      });
      if (!res.ok) throw new Error(`Export failed: ${res.status}`);
      
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

  // Detail route: /app/signal-trace/:signalId — a focused single-signal view instead
  // of the filtered list. The "View" button in the list rows below links here.
  if (signalId) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <Button variant="ghost" size="sm" icon={ArrowLeft} onClick={() => navigate("/app/signal-trace")}>
            Back to Signal Trace
          </Button>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 16, margin: 0 }}>
            Signal {signalId.slice(0, 8)}…
          </h1>
        </div>

        {/* Requirement 18.9 on the detail route too: this view holds the same subscriptions
            (the hook runs before this branch), so a connection that drops here stalls this
            signal's status just as silently as it stalls the list. */}
        <SignalConnectionIndicator status={connectionStatus} watching={heldChannels.length} />

        {!selectedSignal ? (
          <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>Loading signal...</Card>
        ) : (
          <>
            <Card className="p-4 mb-4">
              <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
                <Tag2 c={getDecisionColor(detailSignal.decision)}>{detailSignal.decision}</Tag2>
                <Tag2 c={getStatusColor(detailSignal.status)}>{detailSignal.status}</Tag2>
                <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>{detailSignal.symbol}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
                <div>
                  <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Signal Information</h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                    <div>Strategy: {detailSignal.strategy_id}</div>
                    <div>Version: {detailSignal.strategy_version}</div>
                    <div>Deployment: {detailSignal.deployment_id}</div>
                    <div>Worker: {detailSignal.worker_id}</div>
                  </div>
                </div>
                <div>
                  <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Market Information</h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                    <div>Price: {detailSignal.market_info?.price || "-"}</div>
                    <div>Spread: {detailSignal.market_info?.spread || "-"}</div>
                    <div>Volume: {detailSignal.market_info?.volume || "-"}</div>
                    <div>Volatility: {detailSignal.market_info?.volatility || "-"}</div>
                  </div>
                </div>
                <div>
                  <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Risk Decision</h4>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                    <div>Passed: {detailSignal.risk_passed ? "Yes" : "No"}</div>
                    <div>Reason: {detailSignal.risk_reason || "-"}</div>
                    <div>Position Size: {detailSignal.position_size || "-"}</div>
                    <div>Exposure: {detailSignal.exposure ? `${detailSignal.exposure}%` : "-"}</div>
                  </div>
                </div>
              </div>
            </Card>

            <Card className="p-4">
              <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Execution Timeline</h4>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {timeline.length === 0 ? (
                  <div style={{ color: C.t3, fontSize: 10 }}>No timeline events recorded.</div>
                ) : timeline.map((event, idx) => (
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
            </Card>
          </>
        )}
      </div>
    );
  }

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
          <Button
            variant={showLivePipeline ? "primary" : "ghost"}
            size="sm"
            icon={Activity}
            onClick={() => setShowLivePipeline((v) => !v)}
          >
            {showLivePipeline ? "Hide Live Pipeline" : "Show Live Pipeline"}
          </Button>
          <Button variant="ghost" size="sm" icon={RefreshCw} onClick={loadSignals}>Refresh</Button>
          <Button variant="ghost" size="sm" icon={Download} onClick={() => exportSignals("json")}>Export JSON</Button>
          <Button variant="ghost" size="sm" icon={Download} onClick={() => exportSignals("csv")}>Export CSV</Button>
        </div>
      </div>

      {/* Requirement 18.9 (task 19.3). Whether frames are arriving at all, said plainly and
          in every state — see SignalConnectionIndicator for why it is not failure-only. */}
      <SignalConnectionIndicator status={connectionStatus} watching={heldChannels.length} />

      {/* Requirement 18.3: a refused subscription is SURFACED. The server keeps the
          connection and answers `subscription_refused` for the channel it would not
          authorise, and a refusal that only reached the console would be indistinguishable
          on screen from a deployment that is simply quiet. A refusal is a DIFFERENT fact
          from the connection being down (one channel of a live connection, versus no
          connection at all), so it stays its own element beside the indicator above. */}
      {refusals.length > 0 && (
        <div
          data-testid="signal-trace-refusals"
          style={{
            marginBottom: 16,
            padding: "10px 12px",
            borderRadius: 6,
            border: "1px solid rgba(248,113,113,0.5)",
            background: "rgba(248,113,113,0.08)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#fca5a5", fontSize: 11, fontWeight: 700 }}>
            <AlertTriangle size={14} />
            Live updates refused for {refusals.length} deployment{refusals.length === 1 ? "" : "s"}
          </div>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18, color: "#fca5a5", fontSize: 10, fontFamily: "monospace", lineHeight: 1.7 }}>
            {refusals.map((refusal) => (
              <li key={refusal.channel}>
                {refusal.deploymentId || refusal.channel}
                {refusal.code ? ` — ${refusal.code}` : ""}
                {refusal.reason ? `: ${refusal.reason}` : ""}
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 6, color: C.t3, fontSize: 10 }}>
            This list is not updating live for those deployments. Use Refresh to re-read it.
          </div>
        </div>
      )}

      {/* The deployment list this page composes its subscriptions from could not be read,
          so it does not know every channel to hold. Reported for the same reason as a
          refusal: watching nothing must not look like a strategy with nothing to report. */}
      {deploymentsError && (
        <div
          data-testid="signal-trace-deployments-error"
          style={{
            marginBottom: 16,
            padding: "10px 12px",
            borderRadius: 6,
            border: `1px solid ${C.border}`,
            color: C.t2,
            fontSize: 10,
          }}
        >
          This strategy&apos;s deployment list could not be read, so live updates may be incomplete.
          Signals already listed still update.
        </div>
      )}

      {/* Realtime pipeline visualization — previously imported in App.jsx but never mounted
          anywhere, so live signal-trace updates were unreachable. Shown as a collapsible
          panel here rather than unconditionally, since the component renders its own
          full-page chrome (own header/stats/background) designed for a standalone view. */}
      {showLivePipeline && (
        <div style={{ marginBottom: 16, borderRadius: 8, overflow: "hidden", border: `1px solid ${C.border}`, maxHeight: 640, overflowY: "auto" }}>
          <SignalTraceVisualization wsClient={wsClient} accountId={filters.exchange_id || undefined} />
        </div>
      )}

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

        {/* Requirement 18.1: a signal generated on a watched deployment must not be
            invisible until the next manual refresh. It is announced rather than inserted,
            because whether it belongs on this filtered, sorted, paginated page is the
            server's question to answer — and the action offered is that read. */}
        {newSignalIds.length > 0 && (
          <div
            data-testid="signal-trace-new-signals"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              marginBottom: 12,
              padding: "8px 12px",
              borderRadius: 6,
              border: `1px solid ${C.cyan}`,
              background: "rgba(34,211,238,0.08)",
            }}
          >
            <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace" }}>
              {newSignalIds.length} new signal{newSignalIds.length === 1 ? "" : "s"} arrived that this page does not show.
            </span>
            <Button variant="ghost" size="xs" icon={RefreshCw} onClick={loadSignals}>Reload</Button>
          </div>
        )}

        {isLoading ? (
          <div style={{ textAlign: "center", padding: 40, color: C.t3 }}>Loading signals...</div>
        ) : rows.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: C.t3 }}>No signals found</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {rows.map(signal => (
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

                {expandedRows[signal.id] && selectedSignal && detailSignal.id === signal.id && (
                  <div style={{ padding: 16, background: C.bg2, borderRadius: 6, marginTop: 2, border: `1px solid ${C.border}` }}>
                    {/* Signal Details */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 16 }}>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Signal Information</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Strategy: {detailSignal.strategy_id}</div>
                          <div>Version: {detailSignal.strategy_version}</div>
                          <div>Deployment: {detailSignal.deployment_id}</div>
                          <div>Worker: {detailSignal.worker_id}</div>
                        </div>
                      </div>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Market Information</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Price: {detailSignal.market_info?.price || "-"}</div>
                          <div>Spread: {detailSignal.market_info?.spread || "-"}</div>
                          <div>Volume: {detailSignal.market_info?.volume || "-"}</div>
                          <div>Volatility: {detailSignal.market_info?.volatility || "-"}</div>
                        </div>
                      </div>
                      <div>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Risk Decision</h4>
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Passed: {detailSignal.risk_passed ? "Yes" : "No"}</div>
                          <div>Reason: {detailSignal.risk_reason || "-"}</div>
                          <div>Position Size: {detailSignal.position_size || "-"}</div>
                          <div>Exposure: {detailSignal.exposure ? `${detailSignal.exposure}%` : "-"}</div>
                        </div>
                      </div>
                    </div>

                    {/* Order Information */}
                    {detailSignal.order_id && (
                      <div style={{ marginBottom: 16 }}>
                        <h4 style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 8 }}>Order Information</h4>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, fontSize: 9, fontFamily: "monospace", color: C.t2 }}>
                          <div>Order ID: {detailSignal.order_id}</div>
                          <div>Exchange Order ID: {detailSignal.exchange_order_id || "-"}</div>
                          <div>Status: {detailSignal.order_status}</div>
                          <div>Quantity: {detailSignal.quantity}</div>
                          <div>Filled: {detailSignal.filled}</div>
                          <div>Average Price: {detailSignal.average_price}</div>
                          <div>Fees: {detailSignal.fees}</div>
                          <div>Slippage: {detailSignal.slippage}%</div>
                          <div>Latency: {detailSignal.latency_ms}ms</div>
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
