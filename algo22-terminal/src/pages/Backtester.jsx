import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip
} from "recharts";
import { endpoints } from "../api";
import { C, Inp, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

const serializeReactFlowToDAG = (nds, eds) => {
  const serializedNodes = (nds || []).map((node) => ({
    id: node.id,
    type: node.type || "indicator",
    label: node.data?.label || "Node",
    params: node.data?.params || {},
    position: node.position || { x: 0, y: 0 },
  }));

  const serializedEdges = (eds || []).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
  }));

  return { nodes: serializedNodes, edges: serializedEdges };
};

export default function Backtester({ strategy: strategyProp, onBack: onBackProp }) {
  const navigate = useNavigate();
  const location = useLocation();
  // Accept strategy from either route state (navigate call) or direct prop (inline use)
  const strategy = strategyProp ?? location.state?.strategy ?? null;
  const onBack = onBackProp ?? (() => navigate("/app/strategies"));
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
  const [supportedSymbols, setSupportedSymbols] = useState([]);
  const strategyName = strategy?.name || "Custom Strategy";

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

  const extractStrategiesFromNodes = (nodes) => {
    const strategyMap = {
      'RSI': 'rsi',
      'MACD': 'macd',
      'SMA': 'sma_crossover',
      'EMA': 'ema_crossover',
      'Bollinger Bands': 'bollinger',
      'Stochastic': 'stochastic',
      'ATR': 'atr',
      'CCI': 'cci',
      'Williams %R': 'williams_r',
      'ADX': 'adx',
      'Supertrend': 'supertrend',
      'VWAP': 'vwap',
      'Momentum': 'momentum',
      'ROC': 'roc',
      'XGBoost': 'xgboost',
      'LSTM': 'lstm',
      'Transformer': 'transformer',
    };

    const strategies = [];
    (nodes || []).forEach(node => {
      if (node.type === 'indicator') {
        const label = node.data?.label;
        if (strategyMap[label]) {
          strategies.push(strategyMap[label]);
        }
      }
      if (node.type === 'logic') {
        const leftInd = node.data?.params?.left_indicator;
        const rightInd = node.data?.params?.right_indicator || 'SMA';
        if (leftInd && strategyMap[leftInd]) {
          strategies.push(strategyMap[leftInd]);
        }
      }
    });

    return strategies.length > 0 ? strategies : ['rsi'];
  };

  const extractSymbolsFromNodes = (nodes) => {
    const sourceNode = (nodes || []).find(n => n.type === 'source');
    if (sourceNode?.data?.params?.symbol) {
      const sym = sourceNode.data.params.symbol;
      return [sym.replace('/', '')];
    }
    if (strategy?.dataSource?.symbol) {
      return [strategy.dataSource.symbol.replace('/', '')];
    }
    return ['BTCUSDT'];
  };

  const runBacktest = async () => {
    if (!strategy) return;
    setIsRunning(true);
    try {
      const strategies = extractStrategiesFromNodes(strategy.nodes);
      const symbols = extractSymbolsFromNodes(strategy.nodes);

      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(strategy.nodes || [], strategy.edges || []);

      const payload = {
        strategies,
        symbols,
        timeframe,
        initial_capital: Number(initialCapital),
        trade_size_pct: Number(tradeSizePct) / 100,
        stop_loss_pct: Number(stopLossPct) / 100,
        take_profit_pct: Number(takeProfitPct) / 100,
        ml_threshold: Number(mlThreshold),
        params: {},
        dag: {
          nodes: serNodes,
          edges: serEdges,
          symbols: symbols,
          timeframe: timeframe,
          strategy_name: strategyName
        }
      };

      console.log("🔬 API CALL: POST /api/strategies/backtest", payload);
      console.log("🔬 DAG → Strategies mapping:", strategies);
      console.log("🔬 Extracted symbols:", symbols);

      const data = await endpoints.strategies.backtest(payload);
      console.log("🔬 API RESPONSE:", data);
      setResults(data);
    } catch (err) {
      console.error("🔬 API ERROR: Backtest run failed:", err.message);
      setResults(null);
    } finally {
      setIsRunning(false);
    }
  };

  // Memoised – prevents recharts reconciliation unless results actually change
  const equityData = useMemo(
    () => (results?.equity || []).map((row, i) => ({
      timestamp: row.timestamp ?? i,
      equity: Number(row.equity ?? 0),
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
        <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{strategy?.name || "Untitled Strategy"}</div>

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

        <button onClick={runBacktest} disabled={isRunning || !strategy} style={{ marginTop: 8, background: C.cyan, color: "#000", borderRadius: 10, padding: "14px 0", fontSize: 12, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, cursor: isRunning ? "wait" : "pointer", border: "none", opacity: isRunning ? 0.75 : 1, transition: "all 0.2s" }}>
          {isRunning ? "Running VectorBT..." : "RUN BACKTEST"}
        </button>
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
          <PanelTitle title="Equity Curve" sub={results ? `Simulated performance over ${lookbackDays} days` : "Awaiting backtest execution..."} />
          <div style={{ flex: 1, minHeight: 260, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%" minHeight={260}>
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
      </div>
    </div>
  );
}
