import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip
} from "recharts";
import { endpoints } from "../api";
import { mapBacktestExecutionToUI } from "../api/modules/strategies";
import { C, Inp, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { CONFIG } from "../config";

/**
 * The message a refused or failed run is reported with (task 17.1).
 *
 * The extended execute endpoint refuses with a structured `detail` — `{error, message, …}` —
 * naming the version, the window or the market that could not be resolved (Requirements
 * 5.4, 7.4). `apiClient`'s `ApiError` only lifts `detail` into its own message when the
 * backend sent a bare string, so the object form is read here rather than dropped in favour
 * of a generic "request failed".
 */
const runFailureMessage = (error) => {
  const detail = error?.data?.detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message;
  }
  if (typeof detail === "string") return detail;
  return error?.message || "The backtest could not be run.";
};

export default function Backtester({ strategy: strategyProp, onBack: onBackProp }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const strategyIdParam = searchParams.get("strategy_id");

  // Accept strategy from a direct prop (inline use), route state (navigate call from
  // Strategies/Builder), or a `?strategy_id=` query param (deep link / sidebar entry).
  // The query param is fetched from the backend below; until it resolves, `strategy`
  // falls back to whatever was passed synchronously.
  const [fetchedStrategy, setFetchedStrategy] = useState(null);
  const [strategyLoadError, setStrategyLoadError] = useState(null);
  const [isLoadingStrategy, setIsLoadingStrategy] = useState(false);
  const strategy = strategyProp ?? location.state?.strategy ?? fetchedStrategy ?? null;
  const onBack = onBackProp ?? (() => navigate("/app/strategies"));

  useEffect(() => {
    // Only fetch when the strategy wasn't already supplied via prop/route-state and a
    // strategy_id is present in the URL (Path B / deep link from the sidebar or a bookmark).
    if (strategyProp || location.state?.strategy || !strategyIdParam) return;
    let cancelled = false;
    const loadById = async () => {
      setIsLoadingStrategy(true);
      setStrategyLoadError(null);
      try {
        const data = await endpoints.strategies.getById(strategyIdParam);
        const record = data?.strategy ?? data;
        if (!cancelled) {
          setFetchedStrategy({
            id: record.id ?? strategyIdParam,
            name: record.name ?? record.strategy_name ?? "Untitled Strategy",
            nodes: record.nodes ?? record.graph_json?.nodes ?? [],
            edges: record.edges ?? record.graph_json?.edges ?? [],
          });
        }
      } catch (err) {
        console.error("Failed to load strategy for backtest:", err);
        if (!cancelled) setStrategyLoadError(err.message || "Failed to load strategy");
      } finally {
        if (!cancelled) setIsLoadingStrategy(false);
      }
    };
    loadById();
    return () => { cancelled = true; };
  }, [strategyIdParam, strategyProp, location.state]);

  // The selected strategy's current version, resolved from the backend's own version
  // history (task 17.1). Requirement 4.7: this runs identically whichever entry path put a
  // strategy on the page — preselection from the Strategies_Page, the `?strategy_id=` deep
  // link, or the selector below — because all three end at the same `strategy.id`.
  //
  // Only the version the backend marks `is_current` is used. A strategy with no current
  // version leaves this null, and the run is sent without a `version_id`; the endpoint then
  // answers 422 naming the version as unavailable rather than this page guessing which of
  // several persisted versions the user meant.
  useEffect(() => {
    const id = strategy?.id;
    if (!id) {
      setVersionId(null);
      return undefined;
    }
    let cancelled = false;
    const loadVersion = async () => {
      try {
        const payload = await endpoints.strategies.versions(id);
        const rows = Array.isArray(payload) ? payload : payload?.versions || [];
        const current = rows.find((row) => row?.is_current) || null;
        if (!cancelled) setVersionId(current?.id ?? null);
      } catch (err) {
        console.debug("Failed to resolve current version for backtest:", err);
        if (!cancelled) setVersionId(null);
      }
    };
    loadVersion();
    return () => { cancelled = true; };
  }, [strategy?.id]);

  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("15m");
  const [lookbackDays, setLookbackDays] = useState("30");
  const [initialCapital, setInitialCapital] = useState("10000");
  const [tradeSizePct, setTradeSizePct] = useState("10");
  const [mlThreshold, setMlThreshold] = useState(0.7);
  const [stopLossPct, setStopLossPct] = useState("2");
  const [takeProfitPct, setTakeProfitPct] = useState("4");
  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [runError, setRunError] = useState(null);
  const [supportedSymbols, setSupportedSymbols] = useState([]);
  const [dataValidationError, setDataValidationError] = useState(null);
  const [isValidatingData, setIsValidatingData] = useState(false);
  const [tradePage, setTradePage] = useState(0);
  const [tradesPerPage] = useState(20);
  // The immutable version a run executes (task 17.1). `POST .../backtests/execute` takes a
  // `version_id` and loads that version's own persisted compiled plan, so this is the whole
  // of what identifies the artifact to be backtested — no graph travels from here
  // (Requirements 5.2, 5.7).
  const [versionId, setVersionId] = useState(null);
  // `strategyName` used to live here. Its only reader was `handleSaveBacktest`, which named
  // the client-assembled blueprint it saved; task 17.2 removed that flow, and the row the
  // backend persists carries the version's own blueprint instead.

  const [availableStrategies, setAvailableStrategies] = useState([]);
  const [savedRuns, setSavedRuns] = useState([]);
  const [isLoadingSaved, setIsLoadingSaved] = useState(false);

  /**
   * Re-read "Saved Backtest History" from the database (task 17.2).
   *
   * One loader, called on mount and again after a run completes, because both need the
   * same thing: the rows the backend holds. `POST .../backtests/execute` creates the
   * `strategy_backtests` row, runs, and writes the metrics back before it answers, so a
   * completed run is *already* saved (Requirement 10.1) — this reads it rather than
   * appending a row assembled from what the page happens to have on screen.
   *
   * What used to live here instead was `handleSaveBacktest`: a "Save Backtest Run" button
   * that POSTed a second `strategy_backtests` row from the browser (with `version: 1`
   * hardcoded, this page's `symbol` as the dataset, and the canvas as the blueprint) and
   * then PUT the on-screen metrics into it. Against the extended endpoint that is a
   * duplicate row describing the same run in the client's words, so it is gone along with
   * its `saveSuccessMsg` plumbing.
   *
   * A failed read leaves the previous list alone and says so in the console; it never
   * fabricates an entry, and it is deliberately not surfaced as a run failure — the run
   * itself succeeded and is persisted whether or not this list could be refreshed.
   */
  const loadSavedBacktests = useCallback(async () => {
    setIsLoadingSaved(true);
    try {
      const data = await endpoints.strategies.listBacktests({ limit: 20 });
      setSavedRuns(Array.isArray(data?.backtests) ? data.backtests : []);
    } catch (err) {
      console.error("Failed to load saved backtests:", err);
    } finally {
      setIsLoadingSaved(false);
    }
  }, []);

  useEffect(() => {
    loadSavedBacktests();
  }, [loadSavedBacktests]);

  const handleLoadSavedRun = (run) => {
    if (!run?.blueprint) return;
    // Load the strategy from the saved backtest blueprint
    setFetchedStrategy({
      id: run.strategy_id,
      name: run.blueprint.name || "Saved Strategy",
      nodes: run.blueprint.nodes || [],
      edges: run.blueprint.edges || [],
    });
    // Load results if available
    if (run.status === "completed" && run.equity_curve) {
      setResults({
        equity: run.equity_curve,
        total_return_pct: run.total_return_pct,
        win_rate_pct: run.win_rate,
        max_drawdown_pct: run.max_drawdown,
        sharpe_ratio: run.sharpe_ratio,
        sortino_ratio: run.sortino_ratio,
        profit_factor: run.profit_factor,
        total_trades: run.total_trades
      });
    }
    // Load parameters
    if (run.dataset) setSymbol(run.dataset);
    if (run.initial_capital) setInitialCapital(String(run.initial_capital));
  };

  useEffect(() => {
    const fetchStrategies = async () => {
      try {
        const payload = await endpoints.strategies.list();
        const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
        if (Array.isArray(rows)) setAvailableStrategies(rows);
      } catch (err) {
        console.debug("Failed to list strategies for selector:", err);
      }
    };
    fetchStrategies();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const fetchSymbols = async () => {
      try {
        const data = await endpoints.exchange.getSupported();
        const symbols = Array.isArray(data) ? data : data?.supported || [];
        if (symbols.length > 0) {
          setSupportedSymbols([
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT", "AVAX/USDT",
            "MATIC/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT", "XLM/USDT", "NEAR/USDT",
            "APT/USDT", "OP/USDT", "ARB/USDT", "INJ/USDT", "RENDER/USDT", "SUI/USDT", "SEI/USDT", "FET/USDT", "PEPE/USDT",
            "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "FTM/USDT", "TIA/USDT", "STX/USDT", "RNDR/USDT", "GALA/USDT", "ORDI/USDT"
          ].sort());
        }
      } catch (err) {
        setSupportedSymbols([
          "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "SHIB/USDT",
          "DOT/USDT", "LINK/USDT", "MATIC/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT", "XLM/USDT", "NEAR/USDT",
          "APT/USDT", "OP/USDT", "ARB/USDT", "INJ/USDT", "RENDER/USDT", "SUI/USDT", "SEI/USDT", "FET/USDT", "PEPE/USDT",
          "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "FTM/USDT", "TIA/USDT", "STX/USDT", "RNDR/USDT", "GALA/USDT", "ORDI/USDT"
        ].sort());
      }
    };
    fetchSymbols();
    return () => controller.abort();
  }, []);

  // `extractStrategiesFromNodes` and `extractSymbolsFromNodes` used to live here (task 17.1
  // removed both). They existed to rebuild the legacy job-queue endpoint's bespoke payload —
  // a list of strategy nicknames mapped from node labels, and a symbol guessed from a
  // `source` node with `BTCUSDT` as its fallback. The canonical endpoint takes neither: the
  // strategy is the version's own compiled plan, and the market is the version's own DATA
  // block, which is where SB-06 says it belongs. Nothing here substitutes a market any more.

  const runBacktest = async () => {
    if (!strategy) return;

    // One window, computed once, so the range the data check validates is the range the run
    // is asked for rather than a second computation of "now".
    const endDate = new Date().toISOString().split('T')[0];
    const startDate = new Date(Date.now() - parseInt(lookbackDays) * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    // First validate historical data
    setIsValidatingData(true);
    setDataValidationError(null);
    setRunError(null);

    try {
      const API_BASE = CONFIG.apiBaseUrl;
      const token = sessionStorage.getItem("token");

      // ⚠️ PATH CORRECTION ⚠️ This read `/api/strategy-operations/backtests/validate-data`,
      // which no router declares. `strategy_operations.router` is mounted at `/api` in
      // `main.py` and `validate_historical_data` carries the single decorator
      // `@router.post("/backtests/validate-data")` with no `strategy-operations` alias, so
      // the declared address is the one below. The `if (validationRes.ok)` below meant the
      // 404 was swallowed and every run skipped its data check silently — the same shape as
      // the `listBacktests` defect the api-paths guard was originally written for.
      const validationRes = await fetch(`${API_BASE}/api/backtests/validate-data`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          symbol: symbol,
          timeframe: timeframe,
          start_date: startDate,
          end_date: endDate
        })
      });
      
      if (validationRes.ok) {
        const validationResult = await validationRes.json();
        if (!validationResult.valid) {
          setDataValidationError(validationResult);
          setIsValidatingData(false);
          return;
        }
        
        // Show warnings if any
        if (validationResult.warnings && validationResult.warnings.length > 0) {
          console.warn("Data validation warnings:", validationResult.warnings);
        }
      }
    } catch (err) {
      console.error("Data validation failed:", err);
      // Continue with backtest even if validation fails
    } finally {
      setIsValidatingData(false);
    }
    
    setIsRunning(true);
    try {
      // Task 17.1: the run is one POST to the canonical execute endpoint, which loads the
      // named version's own persisted compiled plan and runs the same DAG_Engine →
      // BacktestEngine (VectorBT) → RiskEngine pipeline the live runtime uses (Requirements
      // 5.1, 5.2). What used to be here — a `toCanonical()` serialization of the canvas, a
      // list of strategy nicknames guessed from node labels, and a `dag` block posted to the
      // legacy job-queue endpoint — is gone: a graph assembled in the browser is by
      // definition not the artifact the live runtime consumes (Requirement 5.7).
      //
      // The body carries only what the request model accepts, and it forbids unknown fields.
      // Symbol and timeframe are the version's own DATA block, not this page's selectors, so
      // they are deliberately not sent (SB-06); the selectors above still scope the
      // historical-data check, which is unchanged.
      const payload = {
        ...(versionId ? { version_id: versionId } : {}),
        start_date: startDate,
        end_date: endDate,
        initial_capital: Number(initialCapital),
      };

      // Synchronous: the endpoint creates the `strategy_backtests` row, runs, writes the
      // metrics back, and answers with the persisted result. There is no job id to poll.
      const data = await endpoints.strategies.executeBacktest(strategy.id, payload);
      setResults(mapBacktestExecutionToUI(data));

      // Task 17.2: the response's `backtest_id` names a row the endpoint has already
      // created and completed, so the run appears in "Saved Backtest History" by re-reading
      // the list — not by a second save request from here (Requirement 10.1).
      await loadSavedBacktests();
    } catch (err) {
      // The endpoint's own refusal is what the user is told — the unavailable version, the
      // window that holds too little data, the market the version does not name — rather
      // than an empty panel.
      console.error("Backtest run failed:", err?.message);
      setRunError(runFailureMessage(err));
      setResults(null);
    } finally {
      setIsRunning(false);
    }
  };

  // Memoised – prevents recharts reconciliation unless results actually change
  const equityData = useMemo(
    () => (results?.equity || []).map((row, i) => ({
      timestamp: row.timestamp ?? row.time ?? i,
      equity: Number(row.equity ?? row.value ?? 0),
    })),
    [results]
  );

  // Stable reference – statItems never changes between renders
  const statItems = useMemo(() => [
    { key: "total_return_pct", label: "Total Return %" },
    { key: "win_rate_pct", label: "Win Rate %" },
    { key: "max_drawdown_pct", label: "Max Drawdown %" },
    { key: "total_trades", label: "Total Trades" },
    { key: "profit_factor", label: "Profit Factor" },
    { key: "sharpe_ratio", label: "Sharpe Ratio" },
    { key: "sortino_ratio", label: "Sortino Ratio" },
    { key: "calmar_ratio", label: "Calmar Ratio" },
  ], []);

  return (
    <div style={{ padding: 16, overflowY: "auto", flex: 1, display: "grid", gridTemplateColumns: "260px 1fr", gap: 12 }}>
      <Card className="p-4" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Button variant="ghost" size="sm" icon={ArrowLeft} onClick={onBack}>Back</Button>
        <div style={{ color: C.t1, fontWeight: 900, fontSize: 15 }}>VectorBT Engine</div>
        
        <div>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
            Select Strategy
          </label>
          <select
            value={strategy?.id || ""}
            onChange={async (e) => {
              const selectedId = e.target.value;
              if (!selectedId) {
                setFetchedStrategy(null);
                return;
              }
              setIsLoadingStrategy(true);
              setStrategyLoadError(null);
              try {
                const data = await endpoints.strategies.getById(selectedId);
                const record = data?.strategy ?? data;
                setFetchedStrategy({
                  id: record.id ?? selectedId,
                  name: record.name ?? record.strategy_name ?? "Untitled Strategy",
                  nodes: record.nodes ?? record.graph_json?.nodes ?? [],
                  edges: record.edges ?? record.graph_json?.edges ?? [],
                });
              } catch (err) {
                setStrategyLoadError(err.message || "Failed to load strategy");
              } finally {
                setIsLoadingStrategy(false);
              }
            }}
            style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: C.t1, outline: "none" }}
            className="focus:border-cyan-500/50 transition-all"
          >
            <option value="">{strategy?.name ? `${strategy.name}` : "-- Choose Strategy --"}</option>
            {availableStrategies.map(s => (
              <option key={s.id} value={s.id}>{s.name || `Strategy #${s.id}`}</option>
            ))}
          </select>
        </div>

        {strategyLoadError && (
          <p role="alert" style={{ color: C.red, fontSize: 10, fontFamily: "monospace", margin: 0 }}>
            Could not load strategy {strategyIdParam}: {strategyLoadError}
          </p>
        )}

        <div>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Asset Pair</label>
          <input
            list="crypto-symbols"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Search asset..."
            style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: C.t1, outline: "none" }}
            className="focus:border-cyan-500/50 transition-all"
          />
          <datalist id="crypto-symbols">
            {supportedSymbols.map(s => <option key={s} value={s} />)}
          </datalist>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: C.t1, outline: "none" }}>
              {["1m", "5m", "15m", "1h", "4h", "1d"].map(tf => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <Inp lbl="Days" type="number" val={lookbackDays} onChange={(e) => setLookbackDays(e.target.value)} />
        </div>

        <Inp lbl="Capital ($)" type="number" val={initialCapital} onChange={(e) => setInitialCapital(e.target.value)} />
        <Inp lbl="Trade Size %" type="number" val={tradeSizePct} onChange={(e) => setTradeSizePct(e.target.value)} />

        <div>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>ML Confidence: {Number(mlThreshold).toFixed(2)}</label>
          <input type="range" min={0.5} max={0.99} step={0.01} value={mlThreshold} onChange={(e) => setMlThreshold(e.target.value)} style={{ width: "100%", accentColor: C.cyan }} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Inp lbl="Stop Loss %" type="number" val={stopLossPct} onChange={(e) => setStopLossPct(e.target.value)} />
          <Inp lbl="Take Profit %" type="number" val={takeProfitPct} onChange={(e) => setTakeProfitPct(e.target.value)} />
        </div>

        <button onClick={runBacktest} disabled={isRunning || isLoadingStrategy || isValidatingData || !strategy || (dataValidationError && !dataValidationError.valid)} style={{ marginTop: 8, background: C.cyan, color: "#000", borderRadius: 10, padding: "14px 0", fontSize: 12, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, cursor: (isRunning || isLoadingStrategy || isValidatingData) ? "wait" : "pointer", border: "none", opacity: (isRunning || isLoadingStrategy || isValidatingData) ? 0.75 : 1, transition: "all 0.2s" }}>
          {isValidatingData ? "Validating Data..." : isRunning ? "Running VectorBT..." : "RUN BACKTEST"}
        </button>

        {runError && (
          <p role="alert" data-testid="backtester-run-error" style={{ color: C.red, fontSize: 10, fontFamily: "monospace", margin: 0 }}>
            {runError}
          </p>
        )}
        
        {dataValidationError && (
          <div role="alert" style={{ background: `${C.red}12`, border: `1px solid ${C.red}`, borderRadius: 8, padding: 12, marginTop: 8 }}>
            <div style={{ color: C.red, fontSize: 10, fontFamily: "monospace", fontWeight: 700, marginBottom: 8 }}>
              ⚠️ Historical Data Validation Failed
            </div>
            {dataValidationError.issues && dataValidationError.issues.map((issue, idx) => (
              <div key={idx} style={{ color: C.t1, fontSize: 9, fontFamily: "monospace", marginBottom: 4 }}>
                • {issue.message}
              </div>
            ))}
            {dataValidationError.warnings && dataValidationError.warnings.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ color: C.gold, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4 }}>
                  Warnings:
                </div>
                {dataValidationError.warnings.map((warning, idx) => (
                  <div key={idx} style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", marginBottom: 2 }}>
                    • {warning.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
          {statItems.map((s) => (
            <Card key={s.key} cls="p-3">
              <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{s.label}</div>
              <div style={{ color: C.t1, fontSize: 18, fontWeight: 900, fontFamily: "monospace" }}>
                {results?.[s.key] === null || results?.[s.key] === undefined ? "-" : String(results[s.key])}
              </div>
            </Card>
          ))}
        </div>
        <Card className="p-4 flex-1 flex flex-col">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <PanelTitle title="Equity Curve" sub={results ? `Simulated performance over ${lookbackDays} days` : "Awaiting backtest execution..."} />
            {/*
              The "Save Backtest Run" button stood here until task 17.2. There is nothing left
              for it to do: `POST .../backtests/execute` persists the run itself, so a
              completed result is saved before this panel renders it, and the run shows up in
              "Saved Backtest History" below (Requirement 10.1). A button offering to save it
              again would write a second row for the same run.
            */}
            {results?.backtest_id && (
              <span
                data-testid="backtester-persisted-run"
                style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", letterSpacing: 1 }}
              >
                SAVED · {String(results.backtest_id).slice(0, 8)}
              </span>
            )}
          </div>
          <div style={{ width: "100%", height: 280, minHeight: 280, position: "relative" }}>
            <ResponsiveContainer width="100%" height={280} minWidth={100} minHeight={280}>
              <AreaChart data={equityData}>
                <defs>
                  <linearGradient id="btEq" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.green} stopOpacity={0.22} />
                    <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="timestamp" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="equity" stroke={C.green} strokeWidth={2} fill="url(#btEq)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
            {!results && !isRunning && (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
                Configure parameters and click Run Backtest to visualize data.
              </div>
            )}
            {isRunning && (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.cyan, fontFamily: "monospace", fontSize: 12, background: "rgba(1,6,8,0.5)", backdropFilter: "blur(2px)" }}>
                VectorBT Engine is crunching historical data...
              </div>
            )}
          </div>
        </Card>

        {/* Saved Backtests History */}
        {savedRuns.length > 0 && (
          <Card className="p-4">
            <PanelTitle
              title="Saved Backtest History"
              sub={isLoadingSaved ? "Refreshing from the database..." : "Reproducible backtest simulation runs"}
            />
            <div style={{ overflowX: "auto", marginTop: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${C.border}`, color: C.t3, textAlign: "left" }}>
                    <th style={{ padding: "6px 8px" }}>Created At</th>
                    <th style={{ padding: "6px 8px" }}>Strategy</th>
                    <th style={{ padding: "6px 8px" }}>Version</th>
                    <th style={{ padding: "6px 8px" }}>Pair</th>
                    <th style={{ padding: "6px 8px" }}>Status</th>
                    <th style={{ padding: "6px 8px" }}>Return %</th>
                    <th style={{ padding: "6px 8px" }}>Win Rate</th>
                    <th style={{ padding: "6px 8px" }}>Max DD</th>
                    <th style={{ padding: "6px 8px", textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {savedRuns.map(run => (
                    <tr key={run.id} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }} className="hover:bg-white/[0.02]">
                      <td style={{ padding: "6px 8px", color: C.t2 }}>{new Date(run.created_at).toLocaleDateString()}</td>
                      <td style={{ padding: "6px 8px", color: C.t1, fontWeight: 700 }}>
                        {run.blueprint?.name || `Strategy ${run.strategy_id?.slice(0, 8)}`}
                      </td>
                      <td style={{ padding: "6px 8px", color: C.t2 }}>{run.version}</td>
                      <td style={{ padding: "6px 8px", color: C.cyan }}>{run.dataset}</td>
                      <td style={{ padding: "6px 8px", color: run.status === "completed" ? C.green : C.t2 }}>
                        {run.status}
                      </td>
                      <td style={{ padding: "6px 8px", color: Number(run.total_return_pct ?? 0) >= 0 ? C.green : C.red }}>
                        {run.total_return_pct ? `${run.total_return_pct.toFixed(2)}%` : "-"}
                      </td>
                      <td style={{ padding: "6px 8px", color: C.t1 }}>{run.win_rate ? `${(run.win_rate * 100).toFixed(1)}%` : "-"}</td>
                      <td style={{ padding: "6px 8px", color: C.red }}>{run.max_drawdown ? `${(run.max_drawdown * 100).toFixed(2)}%` : "-"}</td>
                      <td style={{ padding: "6px 8px", textAlign: "right" }}>
                        <Button variant="ghost" size="xs" onClick={() => handleLoadSavedRun(run)} disabled={run.status !== "completed"}>
                          Inspect
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* Trade Table */}
        {results && results.total_trades > 0 && (
          <Card className="p-4">
            <PanelTitle title="Trade History" sub={`${results.total_trades} simulated trades`} />
            {results.trades && results.trades.length > 0 ? (
              <>
                <div style={{ overflowX: "auto", marginTop: 8 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${C.border}`, color: C.t3, textAlign: "left" }}>
                        <th style={{ padding: "6px 8px" }}>Trade #</th>
                        <th style={{ padding: "6px 8px" }}>Entry Time</th>
                        <th style={{ padding: "6px 8px" }}>Entry Price</th>
                        <th style={{ padding: "6px 8px" }}>Exit Time</th>
                        <th style={{ padding: "6px 8px" }}>Exit Price</th>
                        <th style={{ padding: "6px 8px" }}>Side</th>
                        <th style={{ padding: "6px 8px" }}>Quantity</th>
                        <th style={{ padding: "6px 8px" }}>Gross P&L</th>
                        <th style={{ padding: "6px 8px" }}>Fees</th>
                        <th style={{ padding: "6px 8px" }}>Net P&L</th>
                        <th style={{ padding: "6px 8px" }}>Return %</th>
                        <th style={{ padding: "6px 8px" }}>Duration</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.trades
                        .slice(tradePage * tradesPerPage, (tradePage + 1) * tradesPerPage)
                        .map((trade, idx) => (
                        <tr key={idx} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }} className="hover:bg-white/[0.02]">
                          <td style={{ padding: "6px 8px", color: C.t2 }}>#{trade.trade_id}</td>
                          <td style={{ padding: "6px 8px", color: C.t2 }}>
                            {trade.entry_time ? new Date(trade.entry_time).toLocaleString() : "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.t1 }}>
                            ${trade.entry_price?.toFixed(2) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.t2 }}>
                            {trade.exit_time ? new Date(trade.exit_time).toLocaleString() : "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.t1 }}>
                            ${trade.exit_price?.toFixed(2) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: trade.side === "BUY" ? C.green : C.red }}>
                            {trade.side || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.t1 }}>
                            {trade.quantity?.toFixed(4) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: trade.gross_pnl >= 0 ? C.green : C.red }}>
                            ${trade.gross_pnl?.toFixed(2) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.red }}>
                            ${trade.fees?.toFixed(2) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: trade.net_pnl >= 0 ? C.green : C.red }}>
                            ${trade.net_pnl?.toFixed(2) || "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: trade.return_pct >= 0 ? C.green : C.red }}>
                            {trade.return_pct ? `${trade.return_pct.toFixed(2)}%` : "-"}
                          </td>
                          <td style={{ padding: "6px 8px", color: C.t2 }}>
                            {trade.duration ? `${trade.duration}s` : "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                
                {/* Pagination */}
                {results.trades.length > tradesPerPage && (
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
                    <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                      Showing {tradePage * tradesPerPage + 1} to {Math.min((tradePage + 1) * tradesPerPage, results.trades.length)} of {results.trades.length} trades
                    </span>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Button 
                        variant="ghost" 
                        size="xs" 
                        onClick={() => setTradePage(Math.max(0, tradePage - 1))}
                        disabled={tradePage === 0}
                      >
                        Previous
                      </Button>
                      <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace", alignSelf: "center" }}>
                        Page {tradePage + 1} of {Math.ceil(results.trades.length / tradesPerPage)}
                      </span>
                      <Button 
                        variant="ghost" 
                        size="xs" 
                        onClick={() => setTradePage(Math.min(Math.ceil(results.trades.length / tradesPerPage) - 1, tradePage + 1))}
                        disabled={tradePage >= Math.ceil(results.trades.length / tradesPerPage) - 1}
                      >
                        Next
                      </Button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginTop: 8 }}>
                Trade details not available for this backtest (VectorBT trade extraction may require configuration)
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}
