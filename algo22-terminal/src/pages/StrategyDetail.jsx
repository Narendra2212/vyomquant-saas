import React, { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, Activity, BarChart2, Zap, Shield,
  Layers, Settings, Globe, Server, Play, Pause, Trash2,
  Copy, ExternalLink, Download, RefreshCw, CheckCircle,
  AlertTriangle, FileText, TrendingUp, Target, PieChart, Edit2
} from "lucide-react";
import { C, Tag2, StatusDot, ProgressBar } from "../components/ui-legacy/primitives";
import { CONFIG } from "../config";
import { get, post } from "../apiClient";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
// Imported from the modules directly rather than through `components/ds/index.js` — this
// page charts nothing, and the barrel is what would otherwise put `ds/Chart`'s recharts
// dependency in its import graph. Same reason `pages/Strategies.jsx` gives.
import { Alert } from "../components/ds/Alert";
import { ConfirmDialog } from "../components/ds/ConfirmDialog";
import { deployPresentation } from "../lib/deployFlow";
import ResearchConsole from "../components/ResearchConsole";
import DeploymentConsole from "../components/DeploymentConsole";

/**
 * PHASE 8: Strategy Detail Page
 * 
 * Comprehensive Strategy detail view with all tabs:
 * - Overview
 * - Deployments
 * - Backtests
 * - Executions
 * - Signals
 * - Orders
 * - Positions
 * - Logs
 * - Metrics
 * - Risk
 * - Configuration
 * - Versions
 * - Marketplace
 * - Subscribers
 * - Revenue
 * - Realtime Status
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * TASK 10.4 — the four native dialogs, and the placeholder tab
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 10.4. `design.md` §1.8, §1.9, §7.3, §8.3, §8.4.
 * Requirements 7.6, 18.3, 19.4. Property P13.
 *
 * §1.8 recorded four `window.confirm` calls here — deploy, delete, restore version,
 * deploy version. None of them could be focus-trapped, given an accessible name, or
 * given the review grid Requirement 8.1 asks for, so all four now render through
 * `ds/ConfirmDialog`. Every one of the four handlers was split in two: the control's
 * handler now *only* opens a dialog, and the request lives in a `handleConfirm*`
 * function that nothing but `ConfirmDialog`'s `onConfirm` reaches. That split is what
 * makes P13 ("a destructive action reaches the backend only after explicit
 * confirmation") a statement about which code paths exist rather than about a return
 * value someone remembered to check.
 *
 * **No request changed.** Same four endpoints, same bodies, same query strings, same
 * `isProcessing` / `busy` optimistic flags, same `setActionError` / `setError` failure
 * handling, same `navigate` and same re-reads. This task replaced confirmation
 * surfaces and nothing else.
 *
 * §1.9's `Audit history …` placeholder panel and the tab that reached it are gone
 * (Requirement 19.4). §7.3 decided between wiring it to
 * `GET /api/signal-trace/signals?strategy_id=` and removing it, and chose removal:
 * Signal Trace owns that data. The header now carries a `Signal Trace` action to
 * `/app/signal-trace?strategy_id=…` — the same route `SignalsTab` and
 * `pages/Strategies.jsx` already link to. `activeTab` is keyed by string id and every
 * tab is rendered from `tabs.map` with `key={tab.id}`, so there is no positional
 * index to go stale, and the default (`"overview"`) was never the removed tab.
 */

/**
 * The deployment target both of this page's deploy requests carry.
 *
 * ═══ WHERE THIS PAGE'S DEPLOY TARGET COMES FROM ═══
 *
 * From here, and from nowhere else. Both requests hard-code it — `handleConfirmDeploy`
 * sends `{environment: "paper"}` in its body and `handleConfirmDeployVersion` sends
 * `?environment=paper` in its query string — and this page offers no control that
 * changes either one. There is no exchange-account selector, no risk-configuration
 * selector and no sizing form on this page; §8.3's step 1 lives on
 * `pages/Strategies.jsx`, which is where a trader picks Live, Paper or Backtest and
 * where the preflight gate is polled.
 *
 * So the constant is declared once and read by both the request and the dialog. That
 * is the point of it: §1.8's complaint was that this page's confirmations *asserted* a
 * destination in prose ("Deploy \"X\" to paper trading?") while `Strategies.jsx`
 * deployed through the real gate — the two pages disagreed about what deploy means.
 * The prose claim is gone. The target is now **shown**, from this one value, as
 * `ConfirmDialog`'s environment badge (Requirement 8.5) and as a review row, and the
 * dialog and the request cannot drift apart because there is only one string.
 *
 * @see PAGE_DEPLOY_PRESENTATION
 */
const PAGE_DEPLOY_ENVIRONMENT = "paper";

/**
 * Title, confirm intent, confirm label and resolved environment id for that target.
 *
 * Read from `lib/deployFlow.js` rather than authored here, so this page's deploy
 * confirmation is titled and coloured by the same table that titles and colours
 * §8.3's flow. `deployPresentation` resolves the target through
 * `design/semantic.js`'s vocabulary and is total: it answers
 * `UNCONFIRMED_PRESENTATION` for anything outside `LIVE`/`PAPER`/`BACKTEST` rather
 * than guessing, which is also why the resolved id — not the raw `"paper"` — is what
 * is handed to `ConfirmDialog`'s `environment` prop.
 *
 * `confirmIntent` is `neutral` for Paper, which is `ConfirmDialog`'s brand treatment.
 * There is no acknowledgement checkbox and no real-funds statement anywhere in this
 * file: `deployFlow.js` constructs those only on the `LIVE` branch (Requirement 8.4,
 * P14), and no path from this page resolves to Live.
 */
const PAGE_DEPLOY_PRESENTATION = deployPresentation(PAGE_DEPLOY_ENVIRONMENT);

/**
 * Why the deploy dialogs carry a row naming a target the trader cannot change.
 *
 * Stating it is the honest form of a surface that does not offer the choice. Omitting
 * it would leave the trader to infer the destination, and inferring is what the old
 * confirmations made them do.
 */
const DEPLOY_TARGET_NOTE = "Fixed by this page — not selectable here";
const DEPLOY_ELSEWHERE_NOTE = "Choose Live or Backtest from the Strategies page";

export default function StrategyDetail() {
  const navigate = useNavigate();
  const { strategyId } = useParams();
  const [activeTab, setActiveTab] = useState("overview");
  const [strategy, setStrategy] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState({});
  const [actionError, setActionError] = useState(null);
  /*
   * Task 10.4. Each holds a *snapshot* of the strategy as it read when the control was
   * activated, or `null` when the dialog is closed, so the dialog keeps describing what
   * the trader clicked even if `loadStrategyDetail` re-reads underneath it. Nothing is
   * defaulted into existence — an absent field renders `ConfirmDialog`'s not-available
   * marker (Requirements 14.5, 19.3).
   *
   * Two states rather than one discriminated union: the two dialogs are opened from
   * separate handlers and neither clears the other, so a bug that opened both would be
   * caught by `ConfirmDialog`'s single-overlay registry (Requirement 17.3) rather than
   * silently confirming the wrong action.
   */
  const [deployDialog, setDeployDialog] = useState(null);
  const [deleteDialog, setDeleteDialog] = useState(null);

  const API_BASE = CONFIG.apiBaseUrl;
  const token = sessionStorage.getItem("token");

  useEffect(() => {
    loadStrategyDetail();
  }, [strategyId]);

  const loadStrategyDetail = async () => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/strategies/${strategyId}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (!res.ok) {
        throw new Error(`Strategy load failed (HTTP ${res.status})`);
      }
      const data = await res.json();
      setStrategy(data);
    } catch (err) {
      console.error("Error loading strategy detail:", err);
      setStrategy(null);
    } finally {
      setIsLoading(false);
    }
  };

  const setBusy = (key, value) => setIsProcessing((prev) => ({ ...prev, [key]: value }));

  const authedFetch = (path, options = {}) =>
    fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });

  const handleEdit = () => {
    // StrategyDetail has no inline builder; hand off to the Strategies page,
    // which already knows how to resume the builder for a given strategy.
    navigate("/app/strategies", { state: { resumeBuilderStrategy: strategy?.strategy || strategy } });
  };

  const handleClone = async () => {
    if (isProcessing.clone) return;
    setBusy("clone", true);
    setActionError(null);
    const stratName = strategy?.strategy?.name || strategy?.name || "Strategy";
    try {
      const res = await authedFetch(`/api/strategies/${strategyId}/clone`, {
        method: "POST",
        body: JSON.stringify({ new_name: `${stratName} (Copy)` }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Clone failed (HTTP ${res.status})`);
      const newId = data.id || data.strategy_id || data.strategy?.id;
      if (newId) navigate(`/app/strategies/${newId}`);
      else await loadStrategyDetail();
    } catch (err) {
      console.error("Clone error:", err);
      setActionError(err.message);
    } finally {
      setBusy("clone", false);
    }
  };

  /**
   * Opens the deploy confirmation. **Issues nothing** (Requirement 7.6, P13).
   *
   * The `isProcessing.deploy` guard stays where it was, so a second activation while a
   * deploy is in flight does not even open a dialog.
   */
  const handleDeploy = () => {
    if (isProcessing.deploy) return;
    const strat = strategy?.strategy || strategy || {};
    setDeployDialog({
      name: strat.name || "Strategy",
      id: strategyId,
      version: strat.current_version ?? null,
      symbol: strat.symbol ?? null,
      timeframe: strat.timeframe ?? null,
    });
  };

  /**
   * The deploy request, reachable only from `ConfirmDialog`'s explicit confirm.
   *
   * Byte-for-byte the request the `window.confirm` version issued: same path, same
   * method, same body, same `isProcessing` flag, same `setActionError`, same re-read.
   * The only change is what has to happen before it runs.
   */
  const handleConfirmDeploy = async () => {
    setDeployDialog(null);
    if (isProcessing.deploy) return;
    setBusy("deploy", true);
    setActionError(null);
    try {
      const res = await authedFetch(`/api/strategies/${strategyId}/deploy`, {
        method: "POST",
        body: JSON.stringify({ environment: PAGE_DEPLOY_ENVIRONMENT }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Deploy failed (HTTP ${res.status})`);
      await loadStrategyDetail();
    } catch (err) {
      console.error("Deploy error:", err);
      setActionError(err.message);
    } finally {
      setBusy("deploy", false);
    }
  };

  const handlePause = async () => {
    if (isProcessing.pause) return;
    setBusy("pause", true);
    setActionError(null);
    try {
      const res = await authedFetch(`/api/strategies/${strategyId}/pause`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Pause failed (HTTP ${res.status})`);
      await loadStrategyDetail();
    } catch (err) {
      console.error("Pause error:", err);
      setActionError(err.message);
    } finally {
      setBusy("pause", false);
    }
  };

  /** Opens the delete confirmation. **Issues nothing** (Requirement 7.6, P13). */
  const handleDelete = () => {
    if (isProcessing.delete) return;
    const strat = strategy?.strategy || strategy || {};
    setDeleteDialog({
      name: strat.name || "Strategy",
      id: strategyId,
      status: strat.status ?? null,
      version: strat.current_version ?? null,
      environment: strat.environment ?? null,
    });
  };

  /**
   * The delete request, reachable only from `ConfirmDialog`'s explicit confirm.
   *
   * Unchanged: same `DELETE /api/strategies/{id}`, same navigation to the library on
   * success, and the same asymmetric `setBusy` — cleared only on failure, because the
   * success path leaves this page and clearing it would set state on an unmounted tree.
   */
  const handleConfirmDelete = async () => {
    setDeleteDialog(null);
    if (isProcessing.delete) return;
    setBusy("delete", true);
    setActionError(null);
    try {
      const res = await authedFetch(`/api/strategies/${strategyId}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Delete failed (HTTP ${res.status})`);
      navigate("/app/strategies");
    } catch (err) {
      console.error("Delete error:", err);
      setActionError(err.message);
      setBusy("delete", false);
    }
  };

  const tabs = [
    { id: "overview", label: "Overview", Icon: Activity },
    { id: "research", label: "Research", Icon: Target },
    { id: "deployments", label: "Deployments", Icon: Server },
    { id: "backtests", label: "Backtests", Icon: BarChart2 },
    { id: "executions", label: "Executions", Icon: Zap },
    { id: "signals", label: "Signals", Icon: TrendingUp },
    { id: "orders", label: "Orders", Icon: ExternalLink },
    { id: "positions", label: "Positions", Icon: Layers },
    { id: "logs", label: "Logs", Icon: FileText },
    { id: "metrics", label: "Metrics", Icon: Target },
    { id: "risk", label: "Risk", Icon: Shield },
    { id: "configuration", label: "Configuration", Icon: Settings },
    { id: "versions", label: "Versions", Icon: Copy },
    { id: "marketplace", label: "Marketplace", Icon: Globe },
    { id: "subscribers", label: "Subscribers", Icon: PieChart },
    { id: "revenue", label: "Revenue", Icon: TrendingUp },
    // Task 10.4: the `audit` entry is gone with `AuditTab` (§1.9, Requirement 19.4). A
    // tab is a control, and one that promises audit history the product does not serve
    // here is an inert control. Signal Trace owns that data and the header links to it.
  ];

  if (isLoading) {
    return (
      <div style={{ padding: 20, display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", color: C.t3 }}>
        Loading strategy detail...
      </div>
    );
  }

  if (!strategy) {
    return (
      <div style={{ padding: 20 }}>
        <Button variant="ghost" size="sm" icon={ArrowLeft} onClick={() => navigate("/app/strategies")}>
          Back to Strategies
        </Button>
        <div style={{ marginTop: 20, color: C.t3 }}>Strategy not found</div>
      </div>
    );
  }

  const strat = strategy.strategy || {};
  const version = strategy.version || {};
  const deployments = strategy.deployments || [];
  const backtests = strategy.backtests || [];

  return (
    // TEMPORARY page-level mono (task 3.3). The shell no longer sets a font family. This
    // page declares mono on its LABELS but not on its VALUES — the Overview and
    // Performance metric grids, and the health grids inside DeploymentConsole and
    // ResearchConsole, all render a mono caption above a figure that inherited mono from
    // the shell. Without this, every one of those pairs would change family mid-readout.
    //
    // REMOVE in task 27.1, which retokens this page (and the two consoles) and puts mono
    // on the figures themselves.
    <div className="font-mono" style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Button variant="ghost" size="sm" icon={ArrowLeft} onClick={() => navigate("/app/strategies")}>
            Back
          </Button>
          <div>
            <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20, margin: 0 }}>{strat.name}</h1>
            <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", margin: "4px 0 0 0" }}>
              {strat.description || "No description"}
            </p>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="ghost" size="sm" icon={RefreshCw} onClick={loadStrategyDetail}>Refresh</Button>
          {/* Task 10.4 — what replaced the `Audit History` tab (§7.3, Requirement 19.4).
              The tab rendered a placeholder line; Signal Trace is the page that
              owns the per-strategy signal, order and execution record, and this is the
              same route `SignalsTab` below and `pages/Strategies.jsx` already link to,
              with the same `strategy_id` filter `SignalTrace.jsx` reads. */}
          <Button
            variant="ghost"
            size="sm"
            icon={Activity}
            onClick={() => navigate(`/app/signal-trace?strategy_id=${strategyId}`)}
          >
            Signal Trace
          </Button>
          <Button variant="ghost" size="sm" icon={Copy} onClick={handleClone} disabled={!!isProcessing.clone}>
            {isProcessing.clone ? "Cloning…" : "Clone"}
          </Button>
          <Button variant="ghost" size="sm" icon={Edit2} onClick={handleEdit}>Edit</Button>
          {strat.status === "running" ? (
            <Button variant="ghost" size="sm" icon={Pause} onClick={handlePause} disabled={!!isProcessing.pause}>
              {isProcessing.pause ? "Pausing…" : "Pause"}
            </Button>
          ) : (
            <Button variant="success" size="sm" icon={Play} onClick={handleDeploy} disabled={!!isProcessing.deploy}>
              {isProcessing.deploy ? "Deploying…" : "Deploy"}
            </Button>
          )}
          <Button variant="danger" size="sm" icon={Trash2} onClick={handleDelete} disabled={!!isProcessing.delete}>
            {isProcessing.delete ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>

      {actionError && (
        <div style={{
          marginBottom: 16, padding: "10px 12px", borderRadius: 6,
          background: C.red + "15", border: `1px solid ${C.red}30`,
          color: C.red, fontSize: 11, fontFamily: "monospace",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} style={{ background: "none", border: "none", color: C.red, cursor: "pointer", fontSize: 12 }}>✕</button>
        </div>
      )}

      {/* Status Bar */}
      <Card className="p-4 mb-4">
        <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <StatusDot status={strat.status} />
            <span style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>{strat.status}</span>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <Tag2 c="cyan">{strat.symbol}</Tag2>
            <Tag2 c="purple">{strat.timeframe}</Tag2>
            <Tag2 c={strat.environment === "live" ? "red" : "green"}>{strat.environment || "paper"}</Tag2>
            <Tag2 c="gray">v{strat.current_version || "1.0"}</Tag2>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 20, fontSize: 10, fontFamily: "monospace", color: C.t3 }}>
            <div>Created: {strat.created_at ? new Date(strat.created_at).toLocaleDateString() : "N/A"}</div>
            <div>Updated: {strat.updated_at ? new Date(strat.updated_at).toLocaleDateString() : "N/A"}</div>
          </div>
        </div>
      </Card>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 2, marginBottom: 20, borderBottom: `1px solid ${C.border}`, paddingBottom: 12 }}>
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "8px 12px",
              background: activeTab === tab.id ? C.cyan + "15" : "transparent",
              color: activeTab === tab.id ? C.cyan : C.t3,
              border: activeTab === tab.id ? `1px solid ${C.cyan + "30"}` : "1px solid transparent",
              borderRadius: 6,
              fontSize: 11,
              fontFamily: "monospace",
              cursor: "pointer",
              transition: "all 0.2s"
            }}
          >
            <tab.Icon size={12} />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === "overview" && <OverviewTab strategy={strategy} />}
        {activeTab === "research" && <ResearchConsole strategyId={strategyId} versionId={version.id} executionGraph={strat.blueprint} />}
        {activeTab === "deployments" && <DeploymentConsole strategyId={strategyId} versionId={version.id} executionGraph={strat.blueprint} />}
        {activeTab === "backtests" && <BacktestsTab backtests={backtests} strategyId={strategyId} />}
        {activeTab === "versions" && <VersionsTab strategyId={strategyId} />}
        {activeTab === "metrics" && <MetricsTab strategyId={strategyId} />}
        {activeTab === "risk" && <RiskTab strategyId={strategyId} />}
        {activeTab === "configuration" && <ConfigurationTab strategy={strategy} />}
        {activeTab === "executions" && <ExecutionsTab strategyId={strategyId} />}
        {activeTab === "signals" && <SignalsTab strategyId={strategyId} />}
        {activeTab === "orders" && <OrdersTab strategyId={strategyId} />}
        {activeTab === "positions" && <PositionsTab strategyId={strategyId} />}
        {activeTab === "logs" && <LogsTab strategyId={strategyId} />}
        {activeTab === "marketplace" && <MarketplaceTab strategy={strategy} />}
        {activeTab === "subscribers" && <SubscribersTab strategyId={strategyId} />}
        {activeTab === "revenue" && <RevenueTab strategyId={strategyId} />}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════════════════
          Task 10.4 — the two page-level confirmations that replaced `window.confirm`
          (design.md §1.8, §8.4; Requirements 7.6, 18.3, 19.4; Property P13).

          Both render through `ds/ConfirmDialog`, so both get the focus trap with initial
          focus on **cancel**, `Escape`, `role="dialog"` / `aria-modal` / `aria-labelledby`
          and the single-overlay claim (Requirements 18.3, 17.3) that `window.confirm`
          could not be given. Neither dialog issues anything: the request lives in the
          `onConfirm` target and in no other reachable path.
          ══════════════════════════════════════════════════════════════════════════════════ */}

      {/* ── Deploy. Titled, coloured and badged from `lib/deployFlow.js` ──────────────────
          `PAGE_DEPLOY_PRESENTATION` supplies the title ("Start paper session", §8.3's own
          words), the confirm intent and the resolved environment id, so this page and
          §8.3's flow describe a paper deployment identically. What is gone is the prose
          claim: the old copy asked "Deploy \"X\" to paper trading?" as though the trader
          had picked the destination, when the request hard-codes it. The destination is
          now shown — badge plus two review rows — and named as not selectable here.

          No acknowledgement checkbox, and no real-funds statement: §8.4 reserves those
          for the Live path and for bulk irreversible actions, and `deployFlow.js`
          constructs them only inside its `LIVE` branch. Nothing on this page resolves to
          Live. */}
      <ConfirmDialog
        open={deployDialog !== null}
        onCancel={() => setDeployDialog(null)}
        onConfirm={handleConfirmDeploy}
        title={PAGE_DEPLOY_PRESENTATION.title}
        intent={PAGE_DEPLOY_PRESENTATION.confirmIntent}
        environment={PAGE_DEPLOY_PRESENTATION.environment}
        description={
          "A paper session runs this strategy's current version against the live feed with "
          + "simulated money. No real order is placed and no real funds are committed."
        }
        review={[
          { label: "Strategy", value: deployDialog?.name ?? null },
          { label: "Identifier", value: deployDialog?.id ?? null },
          { label: "Version", value: deployDialog?.version ?? null },
          { label: "Market", value: deployDialog?.symbol ?? null },
          { label: "Timeframe", value: deployDialog?.timeframe ?? null },
          { label: "Target", value: DEPLOY_TARGET_NOTE },
          { label: "Other targets", value: DEPLOY_ELSEWHERE_NOTE },
        ]}
        confirmLabel={PAGE_DEPLOY_PRESENTATION.confirmLabel}
        cancelLabel="Cancel"
      />

      {/* ── Delete. `intent="destructive"`, and NO acknowledgement checkbox ──────────────
          §8.4's inventory: "Delete / archive strategy … Acknowledgement: No — reversible
          via archive". `DELETE /api/strategies/{id}` performs `archive_strategy` (a soft
          archive) and has done since the lifecycle work; the row and every version,
          backtest, deployment and signal behind it survive. So the old copy — "This
          cannot be undone." — was simply false, and it is the sentence this dialog most
          needed to stop saying.

          Same judgement task 10.3 made for the same route on `pages/Strategies.jsx`, in
          the same words: the checkbox is reserved for the irreversible and the live-funds
          cases, and spending it on a reversible action is how a trader learns to tick one
          without reading it. The title stays "Delete strategy" because that is the
          control the trader activated; the description is where it says what Delete
          actually does. */}
      <ConfirmDialog
        open={deleteDialog !== null}
        onCancel={() => setDeleteDialog(null)}
        onConfirm={handleConfirmDelete}
        title="Delete strategy"
        intent="destructive"
        description={
          "Deleting archives this strategy and removes it from your library list. Nothing "
          + "is destroyed: its versions, backtests, deployments and signals are all kept "
          + "and stay inspectable for history and audit. It is refused while any of its "
          + "deployments is still deploying, running or paused."
        }
        review={[
          { label: "Strategy", value: deleteDialog?.name ?? null },
          { label: "Identifier", value: deleteDialog?.id ?? null },
          { label: "Current status", value: deleteDialog?.status ?? null },
          { label: "Version", value: deleteDialog?.version ?? null },
          { label: "Environment", value: deleteDialog?.environment ?? null },
          { label: "Removed from", value: "Your strategy library list" },
          { label: "Kept", value: "Versions, backtests, deployments, signals" },
        ]}
        confirmLabel="Delete strategy"
        cancelLabel="Keep it in the list"
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// TAB COMPONENTS
// ══════════════════════════════════════════════════════════════════════════

function OverviewTab({ strategy }) {
  const strat = strategy.strategy || {};
  const perf = strategy.performance || {};
  
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Performance</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
          {[
            { l: "Total P&L", v: perf.today_pnl || 0, c: perf.today_pnl >= 0 ? C.green : C.red },
            { l: "ROI", v: perf.roi_pct || 0, c: perf.roi_pct >= 0 ? C.green : C.red },
            { l: "Win Rate", v: perf.win_rate || 0, c: C.cyan },
            { l: "Sharpe", v: perf.sharpe_ratio || 0, c: C.purple },
          ].map(m => (
            <div key={m.l} style={{ background: C.bg3, borderRadius: 6, padding: 8 }}>
              <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>{m.l}</div>
              <div style={{ color: m.c, fontSize: 14, fontWeight: 900 }}>{m.v}</div>
            </div>
          ))}
        </div>
      </Card>
      
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Strategy Info</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 10, fontFamily: "monospace", color: C.t2 }}>
          <div>Exchange: {strat.exchange}</div>
          <div>Symbol: {strat.symbol}</div>
          <div>Timeframe: {strat.timeframe}</div>
          <div>Environment: {strat.environment}</div>
          <div>Version: {strat.current_version}</div>
        </div>
      </Card>
      
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Status</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 10, fontFamily: "monospace", color: C.t2 }}>
          <div>Status: {strat.status}</div>
          <div>Health: {strategy.health || "healthy"}</div>
          <div>Worker: {strategy.worker_status || "active"}</div>
          <div>Exchange: {strategy.exchange_status || "connected"}</div>
        </div>
      </Card>
    </div>
  );
}

function DeploymentsTab({ deployments }) {
  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Active Deployments</h3>
      {deployments.length === 0 ? (
        <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
          No active deployments
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {deployments.map(dep => (
            <Card key={dep.id} cls="p-4">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>Version {dep.version}</div>
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                    {dep.environment} • {dep.worker_region} • {dep.created_at ? new Date(dep.created_at).toLocaleString() : "N/A"}
                  </div>
                </div>
                <Tag2 c={dep.status === "running" ? "green" : dep.status === "failed" ? "red" : "orange"}>
                  {dep.status}
                </Tag2>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function BacktestsTab({ backtests, strategyId }) {
  const navigate = useNavigate();
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, margin: 0 }}>Backtest History</h3>
        <Button variant="primary" size="xs" icon={BarChart2} onClick={() => navigate(`/app/backtest?strategy_id=${strategyId}`)}>
          Run New Backtest
        </Button>
      </div>
      {backtests.length === 0 ? (
        <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
          No backtests recorded yet.
          <div style={{ marginTop: 12 }}>
            <Button variant="primary" size="sm" icon={BarChart2} onClick={() => navigate(`/app/backtest?strategy_id=${strategyId}`)}>
              Launch Backtester
            </Button>
          </div>
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {backtests.map(bt => (
            <Card key={bt.id} cls="p-4 hover:border-cyan-500/30 transition-all cursor-pointer" onClick={() => navigate(`/app/backtest?strategy_id=${strategyId}`)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>Version {bt.version}</div>
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                    {bt.dataset} • {bt.start_date} to {bt.end_date} • {bt.created_at ? new Date(bt.created_at).toLocaleString() : "N/A"}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <div style={{ color: bt.total_return_pct >= 0 ? C.green : C.red, fontWeight: 900, fontSize: 12 }}>
                    {bt.total_return_pct >= 0 ? "+" : ""}{bt.total_return_pct}%
                  </div>
                  <Tag2 c={bt.status === "completed" ? "green" : "orange"}>
                    {bt.status}
                  </Tag2>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function VersionsTab({ strategyId }) {
  const [versions, setVersions] = useState([]);
  const [compareWith, setCompareWith] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [busy, setBusy] = useState({});
  const [error, setError] = useState(null);
  /*
   * Task 10.4. A snapshot of the version row as it read when the control was activated,
   * or `null` when closed — `loadVersions` re-reads after every mutation, so holding the
   * row itself would let the dialog start describing a different version.
   */
  const [restoreDialog, setRestoreDialog] = useState(null);
  const [deployDialog, setDeployDialog] = useState(null);
  const API_BASE = CONFIG.apiBaseUrl;
  const token = sessionStorage.getItem("token");

  useEffect(() => {
    loadVersions();
  }, [strategyId]);

  const authedFetch = (path, options = {}) =>
    fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });

  const loadVersions = async () => {
    try {
      const res = await authedFetch(`/api/strategies/${strategyId}/versions`);
      const data = await res.json();
      setVersions(data.versions || []);
    } catch (err) {
      console.error("Error loading versions:", err);
    }
  };

  const setVerBusy = (id, action, value) =>
    setBusy((prev) => ({ ...prev, [`${id}:${action}`]: value }));

  const handleCompare = async (ver) => {
    // First click picks the baseline; second click (on a different version) runs the compare.
    if (!compareWith) {
      setCompareWith(ver.version);
      return;
    }
    if (compareWith === ver.version) {
      setCompareWith(null);
      return;
    }
    setError(null);
    try {
      const res = await authedFetch(
        `/api/strategies/${strategyId}/versions/compare?version_a=${encodeURIComponent(compareWith)}&version_b=${encodeURIComponent(ver.version)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Compare failed (HTTP ${res.status})`);
      setComparison({ a: compareWith, b: ver.version, data });
    } catch (err) {
      console.error("Compare error:", err);
      setError(err.message);
    } finally {
      setCompareWith(null);
    }
  };

  /** Opens the restore confirmation. **Issues nothing** (Requirement 7.6, P13). */
  const handleRestore = (ver) => {
    setRestoreDialog({
      id: ver.id,
      version: ver.version ?? null,
      createdAt: ver.created_at ? new Date(ver.created_at).toLocaleString() : null,
    });
  };

  /**
   * The restore request, reachable only from `ConfirmDialog`'s explicit confirm.
   * Unchanged endpoint, method, query string, busy key and error handling.
   */
  const handleConfirmRestore = async () => {
    const ver = restoreDialog;
    setRestoreDialog(null);
    if (!ver) return;
    setVerBusy(ver.id, "restore", true);
    setError(null);
    try {
      const res = await authedFetch(
        `/api/strategies/${strategyId}/versions/restore?version=${encodeURIComponent(ver.version)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Restore failed (HTTP ${res.status})`);
      await loadVersions();
    } catch (err) {
      console.error("Restore error:", err);
      setError(err.message);
    } finally {
      setVerBusy(ver.id, "restore", false);
    }
  };

  /** Opens the deploy-version confirmation. **Issues nothing** (Requirement 7.6, P13). */
  const handleDeployVersion = (ver) => {
    setDeployDialog({
      id: ver.id,
      version: ver.version ?? null,
      isCurrent: ver.is_current === true,
      isDraft: ver.is_draft === true,
    });
  };

  /**
   * The deploy-version request, reachable only from `ConfirmDialog`'s explicit confirm.
   *
   * The query string is assembled from `PAGE_DEPLOY_ENVIRONMENT`, the same constant the
   * dialog's badge and review rows read, so the environment shown and the environment
   * sent are one value. `encodeURIComponent("paper")` is `"paper"`, so the request on the
   * wire is identical to the one the `window.confirm` version issued.
   */
  const handleConfirmDeployVersion = async () => {
    const ver = deployDialog;
    setDeployDialog(null);
    if (!ver) return;
    setVerBusy(ver.id, "deploy", true);
    setError(null);
    try {
      const res = await authedFetch(
        `/api/strategies/${strategyId}/versions/${encodeURIComponent(ver.version)}`
        + `/deploy?environment=${encodeURIComponent(PAGE_DEPLOY_ENVIRONMENT)}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || data.detail || `Deploy failed (HTTP ${res.status})`);
      await loadVersions();
    } catch (err) {
      console.error("Deploy version error:", err);
      setError(err.message);
    } finally {
      setVerBusy(ver.id, "deploy", false);
    }
  };

  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Version History</h3>

      {error && (
        <div style={{
          marginBottom: 12, padding: "8px 12px", borderRadius: 6,
          background: C.red + "15", border: `1px solid ${C.red}30`,
          color: C.red, fontSize: 11, fontFamily: "monospace",
        }}>
          {error}
        </div>
      )}

      {compareWith && (
        <div style={{
          marginBottom: 12, padding: "8px 12px", borderRadius: 6,
          background: C.cyan + "15", border: `1px solid ${C.cyan}30`,
          color: C.cyan, fontSize: 11, fontFamily: "monospace",
        }}>
          Comparing from version {compareWith} — select another version to compare against.
        </div>
      )}

      {comparison && (
        <Card className="p-4 mb-4">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <h4 style={{ color: C.t1, fontSize: 12, fontWeight: 700, margin: 0 }}>
              Comparison: {comparison.a} vs {comparison.b}
            </h4>
            <button onClick={() => setComparison(null)} style={{ background: "none", border: "none", color: C.t3, cursor: "pointer" }}>✕</button>
          </div>
          <pre style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", overflow: "auto", maxHeight: 300 }}>
            {JSON.stringify(comparison.data, null, 2)}
          </pre>
        </Card>
      )}

      {versions.length === 0 ? (
        <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
          No versions yet
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {versions.map(ver => (
            <Card key={ver.id} cls="p-4">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>{ver.version}</span>
                    {ver.is_current && <Tag2 c="green">Current</Tag2>}
                    {ver.is_draft && <Tag2 c="gray">Draft</Tag2>}
                  </div>
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                    {ver.created_at ? new Date(ver.created_at).toLocaleString() : "N/A"}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 4 }}>
                  <Button
                    variant={compareWith === ver.version ? "primary" : "ghost"}
                    size="xs"
                    onClick={() => handleCompare(ver)}
                  >
                    Compare
                  </Button>
                  {!ver.is_current && (
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => handleRestore(ver)}
                      disabled={!!busy[`${ver.id}:restore`]}
                    >
                      {busy[`${ver.id}:restore`] ? "Restoring…" : "Restore"}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => handleDeployVersion(ver)}
                    disabled={!!busy[`${ver.id}:deploy`]}
                  >
                    {busy[`${ver.id}:deploy`] ? "Deploying…" : "Deploy"}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════════════════
          Task 10.4 — the other two confirmations that replaced `window.confirm`
          (design.md §1.8, §8.4; Requirements 7.6, 18.3; Property P13).
          ══════════════════════════════════════════════════════════════════════════════════ */}

      {/* ── Restore version. `intent="neutral"`, and NO acknowledgement checkbox ──────────
          Restoring is **additive**: it writes a new version whose content is copied from
          the older one, and the older one and every version between them are left exactly
          as they are. Nothing is overwritten and nothing is removed, so `destructive` would
          be the wrong treatment — reserving that hue for the actions that actually remove
          something is what keeps it meaningful — and §8.4 reserves the acknowledgement for
          the irreversible and live-funds rows, which this is neither of.

          The old copy already said the right thing ("This creates a new version based on
          it"); it is kept, moved into the description and into a review row that names the
          outcome, because that sentence is the entire reason no acknowledgement is
          warranted. */}
      <ConfirmDialog
        open={restoreDialog !== null}
        onCancel={() => setRestoreDialog(null)}
        onConfirm={handleConfirmRestore}
        title="Restore version"
        intent="neutral"
        description={
          "Restoring copies this version's contents into a new version and makes that the "
          + "current one. This version is not modified and no version is removed, so the "
          + "history stays complete and the restore itself can be reversed by restoring "
          + "another version."
        }
        review={[
          { label: "Restoring from version", value: restoreDialog?.version ?? null },
          { label: "Saved", value: restoreDialog?.createdAt ?? null },
          { label: "Result", value: "A new version, copied from this one" },
          { label: "Kept", value: "Every existing version, unchanged" },
        ]}
        confirmLabel="Restore version"
        cancelLabel="Cancel"
      />

      {/* ── Deploy version. Same `lib/deployFlow.js` presentation as the page-level deploy,
          because it is the same target: this request hard-codes `?environment=paper` too.
          The old copy asked "Deploy version N to paper trading?"; the destination is now
          the dialog's environment badge and two review rows, and the rows say plainly that
          the target is not chosen here. No acknowledgement, for the reason on the
          page-level dialog above. */}
      <ConfirmDialog
        open={deployDialog !== null}
        onCancel={() => setDeployDialog(null)}
        onConfirm={handleConfirmDeployVersion}
        title={PAGE_DEPLOY_PRESENTATION.title}
        intent={PAGE_DEPLOY_PRESENTATION.confirmIntent}
        environment={PAGE_DEPLOY_PRESENTATION.environment}
        description={
          "A paper session runs this exact version against the live feed with simulated "
          + "money. No real order is placed and no real funds are committed. A deployment "
          + "always binds one immutable version, so later edits do not change what runs."
        }
        review={[
          { label: "Version", value: deployDialog?.version ?? null },
          { label: "Strategy", value: strategyId ?? null },
          {
            // Read off the row's own two flags. A version that is neither the current one
            // nor a draft is a saved, non-current version — stating that is a fact the row
            // carries, and it is worth stating here because deploying a non-current version
            // is the case a trader is most likely to have reached by mistake.
            label: "Version state",
            value: deployDialog === null
              ? null
              : [
                deployDialog.isCurrent ? "Current" : "Not current",
                deployDialog.isDraft ? "Draft" : null,
              ].filter(Boolean).join(" · "),
          },
          { label: "Target", value: DEPLOY_TARGET_NOTE },
          { label: "Other targets", value: DEPLOY_ELSEWHERE_NOTE },
        ]}
        confirmLabel={PAGE_DEPLOY_PRESENTATION.confirmLabel}
        cancelLabel="Cancel"
      />
    </div>
  );
}

function MetricsTab({ strategyId }) {
  const [metrics, setMetrics] = useState(null);
  const [timeRange, setTimeRange] = useState("1d");
  const API_BASE = CONFIG.apiBaseUrl;
  const token = sessionStorage.getItem("token");

  useEffect(() => {
    loadMetrics();
  }, [strategyId, timeRange]);

  const loadMetrics = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/strategies/${strategyId}/performance?time_range=${timeRange}`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      const data = await res.json();
      setMetrics(data);
    } catch (err) {
      console.error("Error loading metrics:", err);
    }
  };

  if (!metrics) return <div style={{ color: C.t3 }}>Loading metrics...</div>;

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {["1d", "1w", "1m", "3m", "all"].map(range => (
          <button
            key={range}
            onClick={() => setTimeRange(range)}
            style={{
              padding: "6px 12px",
              background: timeRange === range ? C.cyan + "15" : "transparent",
              color: timeRange === range ? C.cyan : C.t3,
              border: timeRange === range ? `1px solid ${C.cyan + "30"}` : `1px solid ${C.border}`,
              borderRadius: 6,
              fontSize: 10,
              fontFamily: "monospace",
              cursor: "pointer"
            }}
          >
            {range.toUpperCase()}
          </button>
        ))}
      </div>
      
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 12 }}>
        {[
          { l: "Total P&L", v: metrics.today_pnl || 0, c: metrics.today_pnl >= 0 ? C.green : C.red },
          { l: "ROI", v: metrics.roi_pct || 0, c: metrics.roi_pct >= 0 ? C.green : C.red },
          { l: "Win Rate", v: metrics.win_rate || 0, c: C.cyan },
          { l: "Sharpe", v: metrics.sharpe_ratio || 0, c: C.purple },
          { l: "Sortino", v: metrics.sortino_ratio || 0, c: C.purple },
          { l: "Profit Factor", v: metrics.profit_factor || 0, c: C.cyan },
          { l: "Max Drawdown", v: metrics.max_drawdown || 0, c: C.red },
          { l: "Total Trades", v: metrics.total_trades || 0, c: C.t1 },
        ].map(m => (
          <Card key={m.l} cls="p-4">
            <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginBottom: 4 }}>{m.l}</div>
            <div style={{ color: m.c, fontSize: 16, fontWeight: 900 }}>{m.v}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function RiskTab({ strategyId }) {
  const [risk, setRisk] = useState(null);
  const API_BASE = CONFIG.apiBaseUrl;
  const token = sessionStorage.getItem("token");

  useEffect(() => {
    loadRisk();
  }, [strategyId]);

  const loadRisk = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/strategies/${strategyId}/risk-metrics`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      const data = await res.json();
      setRisk(data.risk_metrics);
    } catch (err) {
      console.error("Error loading risk metrics:", err);
    }
  };

  if (!risk) return <div style={{ color: C.t3 }}>Loading risk metrics...</div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Drawdown</h3>
        <div style={{ color: C.red, fontSize: 24, fontWeight: 900 }}>{risk.max_drawdown || 0}%</div>
      </Card>
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Exposure</h3>
        <div style={{ color: C.t1, fontSize: 24, fontWeight: 900 }}>{risk.current_exposure || 0}%</div>
        <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Limit: {risk.exposure_limit || 100}%</div>
      </Card>
      <Card className="p-4">
        <h3 style={{ color: C.t1, fontSize: 12, fontWeight: 700, marginBottom: 12 }}>Circuit Breaker</h3>
        <Tag2 c={risk.kill_switch_active ? "red" : "green"}>
          {risk.kill_switch_active ? "Active" : "Inactive"}
        </Tag2>
      </Card>
    </div>
  );
}

function ConfigurationTab({ strategy }) {
  const strat = strategy.strategy || {};
  const version = strategy.version || {};
  const blueprint = version.blueprint || {};

  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Strategy Configuration</h3>
      <Card className="p-4">
        <pre style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", overflow: "auto" }}>
          {JSON.stringify(blueprint, null, 2)}
        </pre>
      </Card>
    </div>
  );
}

function ExecutionsTab({ strategyId }) {
  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let isMounted = true;
    const fetchExecutions = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await get(`/api/orders/history?strategy_id=${strategyId}&limit=50`);
        const list = Array.isArray(data) ? data : (data?.orders || data?.history || []);
        if (isMounted) {
          setExecutions(list);
        }
      } catch (err) {
        console.error("Failed to load strategy executions:", err);
        if (isMounted) {
          setError("Failed to load execution ledger.");
        }
      } finally {
        if (isMounted) setLoading(false);
      }
    };
    fetchExecutions();
    return () => { isMounted = false; };
  }, [strategyId]);

  if (loading) {
    return (
      <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, fontFamily: "monospace", fontSize: 11 }}>
          <Activity size={14} className="animate-spin" style={{ color: C.cyan }} />
          <span>Loading execution records...</span>
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="p-8" style={{ textAlign: "center", color: C.red, fontFamily: "monospace", fontSize: 11 }}>
        {error}
      </Card>
    );
  }

  if (executions.length === 0) {
    return (
      <Card className="p-8" style={{ textAlign: "center", color: C.t3, fontFamily: "monospace", fontSize: 11 }}>
        No execution records found for this strategy yet.
        <div style={{ color: C.t4, fontSize: 10, marginTop: 4 }}>
          Executions will populate here when the strategy triggers live or paper orders.
        </div>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ color: C.t1, fontSize: 13, fontWeight: 700, margin: 0 }}>Execution Ledger</h3>
        <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>{executions.length} orders recorded</span>
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
            {["Order ID", "Timestamp", "Symbol", "Side", "Price", "Filled / Size", "Status"].map(h => (
              <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "8px 14px", textAlign: "left", fontSize: 8, letterSpacing: 1.5, textTransform: "uppercase" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {executions.map(ex => (
            <tr key={ex.id || Math.random()} style={{ borderBottom: `1px solid ${C.border}15` }} className="hover:bg-white/5 transition-colors">
              <td style={{ padding: "8px 14px", color: C.cyan, fontWeight: 700 }}>
                {ex.id ? (String(ex.id).length > 12 ? `${String(ex.id).slice(0, 8)}...` : ex.id) : "—"}
              </td>
              <td style={{ padding: "8px 14px", color: C.t3 }}>
                {ex.created_at ? new Date(ex.created_at).toLocaleTimeString() : (ex.timestamp || "—")}
              </td>
              <td style={{ padding: "8px 14px", color: C.t1, fontWeight: 700 }}>{ex.symbol || "BTC/USDT"}</td>
              <td style={{ padding: "8px 14px" }}>
                <Tag2 c={String(ex.side).toUpperCase() === "BUY" ? "green" : "red"}>
                  {String(ex.side || "BUY").toUpperCase()}
                </Tag2>
              </td>
              <td style={{ padding: "8px 14px", color: C.t2 }}>
                ${parseFloat(ex.price || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </td>
              <td style={{ padding: "8px 14px", color: C.t2 }}>
                {ex.filled_quantity ?? ex.filled ?? ex.amount ?? 0} / {ex.quantity ?? ex.amount ?? 0}
              </td>
              <td style={{ padding: "8px 14px" }}>
                <Tag2 c={String(ex.status).toLowerCase() === "filled" ? "green" : String(ex.status).toLowerCase() === "rejected" ? "red" : "gray"}>
                  {String(ex.status || "FILLED").toUpperCase()}
                </Tag2>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function SignalsTab({ strategyId }) {
  const navigate = useNavigate();
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchSignals = async () => {
      setLoading(true);
      try {
        const data = await get(`/api/signal-trace/signals?strategy_id=${strategyId}&limit=20`);
        setSignals(data?.signals || []);
      } catch (err) {
        console.error("Failed to load strategy signals:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchSignals();
  }, [strategyId]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, margin: 0 }}>Recent Signals</h3>
        <Button
          variant="primary"
          size="xs"
          icon={TrendingUp}
          onClick={() => navigate(`/app/signal-trace?strategy_id=${strategyId}`)}
        >
          Open in Signal Trace
        </Button>
      </div>

      {loading ? (
        <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
          Loading signals...
        </Card>
      ) : signals.length === 0 ? (
        <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
          No signals generated yet. Deployed strategies generate signals upon processing market data.
          <div style={{ marginTop: 12 }}>
            <Button
              variant="outline"
              size="sm"
              icon={Activity}
              onClick={() => navigate(`/app/signal-trace?strategy_id=${strategyId}`)}
            >
              View Signal Trace Console
            </Button>
          </div>
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {signals.map(sig => (
            <Card
              key={sig.id}
              cls="p-4 hover:border-cyan-500/30 transition-all cursor-pointer"
              onClick={() => navigate(`/app/signal-trace/${sig.id}`)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <Tag2 c={sig.decision === "BUY" ? "green" : sig.decision === "SELL" ? "red" : "gray"}>
                    {sig.decision}
                  </Tag2>
                  <div>
                    <span style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>{sig.symbol}</span>
                    <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginLeft: 8 }}>
                      {sig.timeframe} • {sig.exchange_id}
                    </span>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                    {sig.created_at ? new Date(sig.created_at).toLocaleTimeString() : "-"}
                  </span>
                  <Tag2 c={sig.status === "executed" ? "green" : sig.status === "rejected" ? "red" : "orange"}>
                    {sig.status}
                  </Tag2>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function OrdersTab({ strategyId }) {
  return (
    <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
      Order execution history is tracked under the Deployments and Signal Trace consoles.
    </Card>
  );
}

function PositionsTab({ strategyId }) {
  return (
    <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
      Position tracking is synchronized through the Portfolio and Risk engine.
    </Card>
  );
}

function LogsTab({ strategyId }) {
  return (
    <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>
      Real-time execution logs are available in the Deployment Console tab.
    </Card>
  );
}

function MarketplaceTab({ strategy }) {
  const strat = strategy.strategy || {};
  const isPublished = strat.is_published || false;
  const [loading, setLoading] = useState(false);
  const [libraryStatus, setLibraryStatus] = useState(null);
  /*
   * Task 10.4, found by the guard rather than by §1.8's table.
   *
   * `publishToLibrary` used to report both of its outcomes with `window.alert` — one for
   * the success and one carrying `err.message`. §1.8 lists only this page's four
   * `window.confirm` calls, but `guards/native-dialogs.js` matches `alert(` as well, and
   * task 10.11's repo-wide guard allowlists exactly `pages/TwoFA.jsx` and
   * `components/NotificationCenter.jsx` — so these two had to go for this page to reach
   * zero. `window.alert` is unstyled, unfocusable, unreadable to assistive technology in
   * context and blocks the whole tab (Requirement 18.3).
   *
   * Reported inline instead, beside the control that caused it, in the same shape the
   * page's own `actionError` and `VersionsTab`'s `error` banners already use. The publish
   * request itself is untouched: same `POST /api/library`, same payload, same
   * `checkLibraryStatus` re-read, same `loading` flag.
   */
  const [notice, setNotice] = useState(null);

  const checkLibraryStatus = async () => {
    try {
      const res = await get(`/api/library/me?strategy_id=${strat.id}`);
      setLibraryStatus(res?.data || res);
    } catch (err) {
      console.debug('Failed to check library status:', err);
    }
  };

  const publishToLibrary = async () => {
    setLoading(true);
    try {
      const payload = {
        strategy_id: strat.id,
        name: strat.name,
        description: strat.description,
        category: 'custom',
        difficulty: 'intermediate',
        tags: [],
        price: null,
        currency: 'USD',
        subscription_tier: 'free'
      };
      await post('/api/library', payload);
      setNotice({ kind: 'ok', text: 'Strategy submitted for review.' });
      checkLibraryStatus();
    } catch (err) {
      console.error(err);
      setNotice({ kind: 'error', text: err?.message || 'Failed to publish' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (strat.id) {
      checkLibraryStatus();
    }
  }, [strat.id]);

  const hasLibraryEntry = libraryStatus && libraryStatus.strategies && libraryStatus.strategies.some(s => s.source_strategy_id === strat.id);

  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Marketplace Status</h3>

      {/* What replaced the two `window.alert` calls.
          `ds/Alert` rather than a hand-rolled banner: it already owns the severity token,
          the icon, the border treatment and — the part that matters where an `alert` used
          to be — the live-region role, `status` for the accepted case and `alert` for the
          refusal, so the outcome is still announced without blocking the tab (Requirement
          18.3). It also takes its hue from `statusToken` rather than from the `C` shim, so
          this notice adds no legacy token reference and no colour literal. */}
      {notice !== null && (
        <div style={{ marginBottom: 12 }} data-testid="marketplace-notice">
          <Alert
            severity={notice.kind === "error" ? "error" : "info"}
            title={notice.text}
            onDismiss={() => setNotice(null)}
            dismissLabel="Dismiss this message"
          />
        </div>
      )}

      <Card className="p-4">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ color: C.t1, fontWeight: 600, fontSize: 12 }}>Publication Status</div>
            <Tag2 c={hasLibraryEntry ? "green" : "gray"}>
              {hasLibraryEntry ? "Published to Library" : "Not Published"}
            </Tag2>
          </div>
          {!hasLibraryEntry && (
            <Button variant="primary" size="sm" onClick={publishToLibrary} disabled={loading}>
              {loading ? 'Publishing...' : 'Publish to Library'}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

function SubscribersTab({ strategy }) {
  const strat = strategy.strategy || {};
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const res = await client.get('/api/library/creator/analytics');
        setAnalytics(res.data);
      } catch (err) {
        console.error('Failed to fetch creator analytics:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchAnalytics();
  }, []);

  if (loading) {
    return <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>Loading...</Card>;
  }

  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Subscriber Analytics</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Active Subscribers</div>
          <div style={{ color: C.t1, fontSize: 24, fontWeight: 700 }}>{analytics?.active_subscribers || 0}</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Published Strategies</div>
          <div style={{ color: C.t1, fontSize: 24, fontWeight: 700 }}>{analytics?.published_strategies_count || 0}</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Average Rating</div>
          <div style={{ color: C.t1, fontSize: 24, fontWeight: 700 }}>{analytics?.rating_average || 0}/5</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Payout Schedule</div>
          <div style={{ color: C.t1, fontSize: 12, fontWeight: 600 }}>{analytics?.payout_schedule || 'N/A'}</div>
        </Card>
      </div>
    </div>
  );
}

function RevenueTab({ strategy }) {
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const res = await client.get('/api/library/creator/analytics');
        setAnalytics(res.data);
      } catch (err) {
        console.error('Failed to fetch creator analytics:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchAnalytics();
  }, []);

  if (loading) {
    return <Card className="p-8" style={{ textAlign: "center", color: C.t3 }}>Loading...</Card>;
  }

  return (
    <div>
      <h3 style={{ color: C.t1, fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Revenue Analytics</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Total Earnings (USD)</div>
          <div style={{ color: "#4ade80", fontSize: 24, fontWeight: 700 }}>${analytics?.total_earnings_usd || 0}</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Monthly Recurring Revenue</div>
          <div style={{ color: "#60a5fa", fontSize: 24, fontWeight: 700 }}>${analytics?.monthly_recurring_revenue || 0}</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Platform Fee Paid</div>
          <div style={{ color: "#f87171", fontSize: 24, fontWeight: 700 }}>${analytics?.platform_fee_paid || 0}</div>
        </Card>
        <Card className="p-4">
          <div style={{ color: C.t2, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>Net Revenue (90%)</div>
          <div style={{ color: "#4ade80", fontSize: 24, fontWeight: 700 }}>${analytics?.total_earnings_usd || 0}</div>
        </Card>
      </div>
    </div>
  );
}

/*
 * `AuditTab` was here (§1.9, Requirement 19.4). It rendered one centred line of
 * placeholder text and nothing else — a tab, in the primary tab row, promising a record
 * this page never fetched. §7.3 chose removal over wiring it up, because Signal Trace
 * already owns the per-strategy signal / order / execution record and serves it from
 * `GET /api/signal-trace/signals?strategy_id=`. The header's `Signal Trace` action is
 * where that promise is now kept.
 */
