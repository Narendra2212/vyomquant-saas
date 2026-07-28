import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlowProvider,
  applyNodeChanges,
  applyEdgeChanges,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  Radio, Activity, Brain, GitBranch, AlertTriangle, BarChart2,
  PlusCircle, Check, ArrowLeft, Wifi, BookOpen, WifiOff, Loader2
} from "lucide-react";
import { endpoints } from "../api";
import {
  C, Btn, Inp, Card, Tag2, PanelTitle
} from "../components/ui-legacy/primitives";
import { useDataPipeline, DataPipelineProvider } from "../contexts/DataPipelineContext";
import { useIndicatorEngine, IndicatorEngineProvider } from "../contexts/IndicatorEngineContext";
import { useLogicEngine, LogicEngineProvider } from "../contexts/LogicEngineContext";
import { useStrategyEngine, StrategyEngineProvider } from "../contexts/StrategyEngineContext";

const STRATEGY_TOOLBOX = [
  {
    g: "Data Sources", items: [
      { label: "CCXT Asset Feed", type: "source" },
      { label: "Orderbook Imbalance", type: "orderbook" },
      { label: "Live Ticker", type: "liveticker" }
    ]
  },
  {
    g: "Indicators", type: "indicator", items: [
      "SMA", "EMA", "WMA", "HMA", "RSI", "MACD", "ATR", "Bollinger Bands", "Stochastic", "CCI",
      "Williams %R", "OBV", "MFI", "ADX", "Supertrend", "TRIX", "Vortex", "Choppiness", "Awesome Oscillator",
      "Fisher Transform", "Z-Score", "Historical Volatility", "VWAP", "Momentum", "ROC", "Donchian",
      "Keltner Channels", "Ichimoku", "CMF", "PSAR", "Fibonacci", "Pivot Standard", "Pivot Camarilla",
    ]
  },
  { g: "ML Models", type: "mlmodel", items: ["XGBoost", "LightGBM", "RandomForest", "CatBoost", "LSTM", "GRU", "Transformer", "Autoencoder"] },
  { g: "Logic", type: "logic", items: ["Signal Logic", "And Gate", "Or Gate", "Condition Builder"] },
  { g: "Operators", type: "operator", items: ["Constant", "Compare", "Math", "Crosses"] },
  { g: "Execution", type: "action", items: ["Buy Market", "Sell Market", "Close Position", "Trailing Stop"] },
];

const ApiSyncIndicator = ({ color, text, active = true }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, paddingTop: 6, borderTop: `1px dashed ${C.border}` }}>
    <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? color : C.t3, boxShadow: active ? `0 0 8px ${color}` : 'none', transition: 'all 0.3s' }} />
    <span style={{ fontSize: 8, color: active ? C.t2 : C.t4, letterSpacing: 1, fontFamily: "monospace", textTransform: "uppercase", fontWeight: 700 }}>{text}</span>
  </div>
);

const PremiumNodeWrapper = ({ children, color, active = true }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        minWidth: 150,
        background: C.bg3,
        border: `1.5px solid ${isHovered ? color : color + "90"}`,
        borderRadius: 8,
        color: C.t1,
        padding: "10px",
        boxShadow: isHovered ? `${C.glow[color === C.green ? "profit" : color === C.red ? "loss" : "accent"]}, 0 4px 12px rgba(0,0,0,0.3)` : `0 0 15px ${color}25`,
        fontFamily: "monospace",
        transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "scale(1.02)" : "scale(1)",
        cursor: "pointer"
      }}
    >
      {children}
      {active && (
        <div style={{
          position: "absolute",
          top: 6,
          right: 6,
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          animation: "nodePulse 2s ease-in-out infinite",
          boxShadow: `0 0 8px ${color}`
        }}>
          <style>{`@keyframes nodePulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.6; transform: scale(0.8); } }`}</style>
        </div>
      )}
    </div>
  );
};

const SourceNode = React.memo(function SourceNode({ data }) {
  return (
    <PremiumNodeWrapper color={C.t2}>
      <Handle type="source" position={Position.Right} style={{ background: C.t2, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.t2, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Radio size={10} />
        Data Ingestion
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <ApiSyncIndicator color={C.accent} text="WebSocket Active" active={true} />
    </PremiumNodeWrapper>
  );
});

const IndicatorNode = React.memo(function IndicatorNode({ data }) {
  const outputKey = data?.params?.output;
  const isMultiOutput = data?.label === 'MACD' || data?.label === 'Bollinger Bands';

  return (
    <PremiumNodeWrapper color={C.accent}>
      <Handle type="target" position={Position.Left} style={{ background: C.accent, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.accent, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Activity size={10} />
        Transformation
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      {isMultiOutput && outputKey && (
        <div style={{ fontSize: 9, color: C.t2, marginTop: 2, fontFamily: 'monospace' }}>
          output: {outputKey}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: C.accent, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
});

const MlModelNode = React.memo(function MlModelNode({ data }) {
  return (
    <PremiumNodeWrapper color={C.purple}>
      <Handle type="target" position={Position.Left} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.purple, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Brain size={10} />
        Prediction
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <Handle type="source" position={Position.Right} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <ApiSyncIndicator color={C.purple} text="Model Synced" active={true} />
    </PremiumNodeWrapper>
  );
});

const OperatorNode = React.memo(function OperatorNode({ data }) {
  return (
    <PremiumNodeWrapper color={C.gold}>
      <Handle type="target" position={Position.Left} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.gold, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <GitBranch size={10} />
        Logic Gate
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <Handle type="source" position={Position.Right} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
});

const LogicNode = React.memo(function LogicNode({ data }) {
  const lastSignal = data?.lastSignal;
  const signalColor = lastSignal === 'BUY' ? C.profit : lastSignal === 'SELL' ? C.loss : C.t2;
  const confidence = data?.confidence || 0;

  return (
    <PremiumNodeWrapper color={C.gold}>
      <Handle type="target" position={Position.Left} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.gold, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <GitBranch size={10} />
        Signal Logic
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      {lastSignal && (
        <div style={{
          fontSize: 9,
          color: signalColor,
          marginTop: 2,
          fontFamily: 'monospace',
          fontWeight: 700,
          background: `${signalColor}20`,
          padding: '2px 6px',
          borderRadius: 3,
          display: 'inline-block'
        }}>
          {lastSignal} {confidence > 0 && `(${Math.round(confidence * 100)}%)`}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
});

const ActionNode = React.memo(function ActionNode({ data }) {
  const isBuy = data.label.includes("Buy");
  const isSell = data.label.includes("Sell") || data.label.includes("Close") || data.label.includes("Stop");
  const color = isBuy ? C.profit : isSell ? C.loss : C.warning;

  return (
    <PremiumNodeWrapper color={color}>
      <Handle type="target" position={Position.Left} style={{ background: color, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: color, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Activity size={10} />
        Execution Route
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <ApiSyncIndicator color={color} text="Router Armed" active={true} />
    </PremiumNodeWrapper>
  );
});

const OrderbookImbalanceNode = React.memo(function OrderbookImbalanceNode({ data }) {
  const imbalance = data?.params?.imbalance || 0;
  const isBullish = imbalance > 0;

  return (
    <PremiumNodeWrapper color={C.purple}>
      <Handle type="target" position={Position.Left} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.purple, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <BookOpen size={10} />
        Orderbook Flow
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <div style={{
        fontSize: 10,
        color: isBullish ? C.profit : C.loss,
        marginTop: 4,
        fontFamily: "monospace"
      }}>
        Imbalance: {imbalance > 0 ? "+" : ""}{imbalance.toFixed(3)}
      </div>
      <ApiSyncIndicator color={C.purple} text={isBullish ? "Bid Dominant" : "Ask Dominant"} active={true} />
      <Handle type="source" position={Position.Right} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
});

const LiveTickerNode = React.memo(function LiveTickerNode({ data }) {
  const isLive = data?.params?.mode === 'live';

  return (
    <PremiumNodeWrapper color={isLive ? C.accent : C.t3} active={isLive}>
      <Handle type="source" position={Position.Right} style={{ background: isLive ? C.accent : C.t3, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: isLive ? C.accent : C.t3, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        {isLive ? <Wifi size={10} /> : <WifiOff size={10} />}
        Live Stream
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <div style={{ fontSize: 9, color: C.t2, marginTop: 4 }}>
        {isLive ? "Real-time ticks" : "Backtest mode - disabled"}
      </div>
      <ApiSyncIndicator color={isLive ? C.accent : C.t3} text={isLive ? "Connected" : "Standby"} active={isLive} />
    </PremiumNodeWrapper>
  );
});

function StrategyStructuredView({ nodes, setNodes, edges, setEdges, toolbox, getNodeParamSchema, renderDynamicField, defaultDates }) {
  const handleAddNode = (type, label) => {
    const newNodeId = `n-${Date.now()}`;
    const defaultParams = {};
    if (type === "source") {
      defaultParams.symbol = "BTC/USDT";
      defaultParams.timeframe = "15m";
      defaultParams.start_date = defaultDates.start_date;
      defaultParams.end_date = defaultDates.end_date;
    } else if (type === "indicator") {
      defaultParams.window = 14;
    }

    const newNode = {
      id: newNodeId,
      type: type || "indicator",
      position: { x: 250, y: nodes.length * 150 + 100 },
      data: { label, params: defaultParams }
    };
    setNodes((nds) => [...nds, newNode]);
  };

  const handleDeleteNode = (nodeId) => {
    setNodes((nds) => nds.filter((node) => node.id !== nodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
  };

  const handleAddEdge = (sourceId, targetId) => {
    if (!targetId) return;
    const edgeId = `e-${sourceId}-${targetId}`;
    if (edges.some(e => e.id === edgeId)) return;
    setEdges((eds) => [...eds, { id: edgeId, source: sourceId, target: targetId, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }]);
  };

  const handleRemoveEdge = (edgeId) => {
    setEdges((eds) => eds.filter(e => e.id !== edgeId));
  };

  const [selectedToolboxItem, setSelectedToolboxItem] = useState("");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", height: "100%", paddingBottom: 100, flex: 1 }}>
      {/* Node List */}
      {nodes.map(node => (
        <Card key={node.id} cls="p-5" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Header */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: `1px solid ${C.border}`, paddingBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: C.t1, fontSize: 14, fontWeight: 900 }}>{node.data.label}</span>
              <Tag2 c="purple">{node.type}</Tag2>
            </div>
            <Btn v="danger" sz="sm" onClick={() => handleDeleteNode(node.id)}>Delete Block</Btn>
          </div>

          {/* Config */}
          <div>
            <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Configuration</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {getNodeParamSchema(node.data.label, node.type).map(field => renderDynamicField(field, node))}
              {getNodeParamSchema(node.data.label, node.type).length === 0 && (
                <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>No configuration required.</div>
              )}
            </div>
          </div>

          {/* Connections */}
          <div style={{ marginTop: 8 }}>
            <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Outgoing Connections (Outputs)</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {edges.filter(e => e.source === node.id).map(edge => {
                const targetNode = nodes.find(n => n.id === edge.target);
                return (
                  <div key={edge.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.bg3, border: `1px solid ${C.border}`, padding: "6px 12px", borderRadius: 6 }}>
                    <span style={{ color: C.t1, fontSize: 12, fontFamily: "monospace" }}>â†’ {targetNode ? targetNode.data.label : edge.target}</span>
                    <button onClick={() => handleRemoveEdge(edge.id)} style={{ color: C.red, background: "transparent", border: "none", cursor: "pointer", fontSize: 11, fontFamily: "monospace" }}>Remove</button>
                  </div>
                );
              })}
              {edges.filter(e => e.source === node.id).length === 0 && (
                 <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>No outgoing connections.</div>
              )}
            </div>
            
            <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "flex-end" }}>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                <label htmlFor={`connect-${node.id}`} style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase" }}>Connect to</label>
                <select
                  id={`connect-${node.id}`}
                  style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", outline: "none" }}
                  defaultValue=""
                >
                  <option value="" disabled>Select a block to connect to...</option>
                  {nodes.filter(n => n.id !== node.id).map(n => (
                    <option key={n.id} value={n.id}>{n.data.label} (ID: {n.id})</option>
                  ))}
                </select>
              </div>
              <Btn v="outline" sz="sm" onClick={() => {
                const select = document.getElementById(`connect-${node.id}`);
                handleAddEdge(node.id, select.value);
                select.value = "";
              }}>Add Connection</Btn>
            </div>
          </div>
        </Card>
      ))}

      {/* Add New Node */}
      <Card cls="p-5">
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
            <label htmlFor="add-block-select" style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase" }}>Add New Block</label>
            <select
              id="add-block-select"
              value={selectedToolboxItem}
              onChange={(e) => setSelectedToolboxItem(e.target.value)}
              style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", outline: "none" }}
            >
              <option value="" disabled>Select a block type to add...</option>
              {toolbox.map(group => (
                <optgroup key={group.g} label={group.g}>
                  {group.items.map(item => {
                    const label = typeof item === "string" ? item : item.label;
                    const type = typeof item === "string" ? group.type : item.type;
                    return <option key={label} value={`${type}::${label}`}>{label}</option>
                  })}
                </optgroup>
              ))}
            </select>
          </div>
          <Btn v="primary" sz="sm" onClick={() => {
            if (!selectedToolboxItem) return;
            const [type, label] = selectedToolboxItem.split("::");
            handleAddNode(type, label);
            setSelectedToolboxItem("");
          }}>Add Block</Btn>
        </div>
      </Card>
    </div>
  );
}

function StrategyBuilderInner({ onBack: onBackProp, strategy: strategyProp, initialStrategy, onBacktest: onBacktestProp }) {
  const navigate = useNavigate();
  const location = useLocation();
  // When used as a standalone /app/builder route, props are optional;
  // fall back to route state (from navigate call) and navigate() for navigation.
  const strategy = strategyProp ?? location.state?.strategy ?? null;
  const onBack = onBackProp ?? (() => navigate("/app/strategies"));
  const onBacktest = onBacktestProp ?? ((payload) => navigate("/app/backtest", { state: { strategy: payload } }));
  const {
    mode: pipelineMode, setMode: setPipelineMode, activeExchange, isLoadingExchange,
    availableTimeframes, availableSymbols, isLoadingSymbols, loadMarkets, fetchOHLCV,
    fetchOrderbookImbalance, connectLiveData, isFetchingData, fetchProgress, isLiveConnected,
    validateInputs, validationErrors
  } = useDataPipeline();

  const {
    computeIndicator, computeIndicatorsBatch, indicatorCache, clearIndicatorCache,
    isComputing, computationError, lastComputation, getAvailableOutputs, validateIndicatorParams
  } = useIndicatorEngine();

  const {
    evaluateLogic, evaluateLogicBatch, logicCache, clearLogicCache, isEvaluating,
    evaluationError, lastSignal, getAvailableOperators, getConditionTemplate
  } = useLogicEngine();

  const {
    parseGraphToExecutionPlan, executeStrategy, validateStrategy, executionCache,
    clearExecutionCache, isExecuting, executionError, lastExecution, generateExecutionId
  } = useStrategyEngine();

  const [builderMode, setBuilderMode] = useState('backtest');
  const [viewMode, setViewMode] = useState('canvas');

  useEffect(() => {
    setPipelineMode(builderMode);
  }, [builderMode, setPipelineMode]);

  useEffect(() => {
    if (activeExchange && !isLoadingExchange) {
      loadMarkets();
    }
  }, [activeExchange, isLoadingExchange, loadMarkets]);

  const strategyNodeTypes = useMemo(() => ({
    source: SourceNode,
    indicator: IndicatorNode,
    mlmodel: MlModelNode,
    operator: OperatorNode,
    logic: LogicNode,
    action: ActionNode,
    orderbook: OrderbookImbalanceNode,
    liveticker: LiveTickerNode,
  }), []);

  const [strategyName, setStrategyName] = useState("Untitled Strategy");
  const [isSavingStrategy, setIsSavingStrategy] = useState(false);
  const [saveState, setSaveState] = useState("");
  const [ccxtMarkets, setCcxtMarkets] = useState(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]);

  const reactFlowWrapper = useRef(null);
  const nodeSeq = useRef(4);
  const loadedStrategy = initialStrategy || strategy || null;
  const [strategyIdState, setStrategyIdState] = useState(loadedStrategy?.id || null);

  const getDefaultDateRange = () => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 30);
    return {
      start_date: start.toISOString().split('T')[0],
      end_date: end.toISOString().split('T')[0]
    };
  };

  const defaultDates = getDefaultDateRange();

  const initialNodes = [
    { id: "n-1", type: "source", position: { x: 50, y: 250 }, data: { label: "CCXT Asset Feed", params: { symbol: "BTC/USDT", timeframe: "15m", start_date: defaultDates.start_date, end_date: defaultDates.end_date } } },
    { id: "n-2", type: "indicator", position: { x: 300, y: 150 }, data: { label: "RSI", params: { window: 14 } } },
    { id: "n-3", type: "mlmodel", position: { x: 550, y: 150 }, data: { label: "XGBoost", params: { confidence: 0.8 } } },
    { id: "n-4", type: "action", position: { x: 800, y: 250 }, data: { label: "Buy Market", params: {} } },
  ];

  const initialEdges = [
    { id: "e1-2", source: "n-1", target: "n-2", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
    { id: "e2-3", source: "n-2", target: "n-3", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
    { id: "e3-4", source: "n-3", target: "n-4", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
  ];

  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  useEffect(() => {
    if (!loadedStrategy) return;
    if (loadedStrategy.name) setStrategyName(loadedStrategy.name);
    if (Array.isArray(loadedStrategy.nodes) && loadedStrategy.nodes.length > 0) {
      setNodes(loadedStrategy.nodes);
    }
    if (Array.isArray(loadedStrategy.edges) && loadedStrategy.edges.length > 0) {
      setEdges(loadedStrategy.edges);
    }
  }, [loadedStrategy]);

  const onNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), []);
  const onEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), []);
  const onConnect = useCallback((params) => setEdges((eds) => [...eds, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }]), []);

  const selectedNode = useMemo(() => nodes.find(n => n.id === selectedNodeId) || null, [nodes, selectedNodeId]);

  const handleDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const handleDrop = useCallback((event) => {
    event.preventDefault();
    const reactFlowBounds = reactFlowWrapper.current?.getBoundingClientRect();
    const type = event.dataTransfer.getData('application/reactflow-type');
    const label = event.dataTransfer.getData('application/reactflow-label');

    if (!type || !label || !reactFlowBounds) return;

    const position = {
      x: event.clientX - reactFlowBounds.left - 75,
      y: event.clientY - reactFlowBounds.top - 25,
    };

    nodeSeq.current += 1;
    const newNodeId = `n-${nodeSeq.current}`;

    const defaultParams = {};
    if (type === "source") {
      const dates = getDefaultDateRange();
      defaultParams.symbol = "BTC/USDT";
      defaultParams.timeframe = "15m";
      defaultParams.start_date = dates.start_date;
      defaultParams.end_date = dates.end_date;
    } else if (type === "indicator") {
      defaultParams.window = 14;
    }

    const newNode = {
      id: newNodeId,
      type: type || "indicator",
      position,
      data: { label, params: defaultParams }
    };

    setNodes((nds) => [...nds, newNode]);
    setSelectedNodeId(newNodeId);
  }, []);

  const handleParamChange = useCallback((nodeId, key, value) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              params: {
                ...(node.data.params || {}),
                [key]: value
              }
            }
          };
        }
        return node;
      })
    );
  }, []);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((nds) => nds.filter((node) => node.id !== selectedNodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId(null);
  }, [selectedNodeId]);

  const renderDynamicField = (field, node) => {
    const val = node?.data?.params?.[field.key] ?? field.default;

    if (field.type === "select") {
      return (
        <div key={field.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor={`field-${node.id}-${field.key}`} style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>
            {field.label}
          </label>
          <select
            id={`field-${node.id}-${field.key}`}
            value={val}
            onChange={(e) => handleParamChange(node.id, field.key, e.target.value)}
            style={{
              width: "100%", background: C.bg3, border: `1px solid ${C.border}`,
              color: C.t1, borderRadius: 8, padding: "8px 10px", fontSize: 11,
              fontFamily: "monospace", outline: "none"
            }}
          >
            {field.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>
      );
    }

    if (field.type === "date") {
      return (
        <div key={field.key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor={`field-${node.id}-${field.key}`} style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>
            {field.label}
          </label>
          <input
            id={`field-${node.id}-${field.key}`}
            type="date"
            value={val || ""}
            onChange={(e) => handleParamChange(node.id, field.key, e.target.value)}
            style={{
              width: "100%", background: C.bg3, border: `1px solid ${C.border}`,
              color: C.t1, borderRadius: 8, padding: "8px 10px", fontSize: 11,
              fontFamily: "monospace", outline: "none"
            }}
          />
        </div>
      );
    }

    return (
      <Inp
        key={field.key}
        lbl={field.label}
        type={field.type || "text"}
        val={val}
        onChange={(e) => handleParamChange(node.id, field.key, field.type === "number" ? Number(e.target.value) : e.target.value)}
      />
    );
  };

  const getNodeParamSchema = (label, type) => {
    if (type === "source" || label === "CCXT Asset Feed") {
      return [
        { key: "symbol", label: "Asset Pair", type: "select", options: availableSymbols.length > 0 ? availableSymbols : ccxtMarkets, default: "BTC/USDT" },
        { key: "timeframe", label: "Timeframe", type: "select", options: availableTimeframes.length > 0 ? availableTimeframes : ["1m", "5m", "15m", "1h", "4h", "1d"], default: "15m" },
        { key: "start_date", label: "Start Date", type: "date", default: defaultDates.start_date },
        { key: "end_date", label: "End Date", type: "date", default: defaultDates.end_date }
      ];
    }
    if (type === "indicator") {
      if (label === "RSI") {
        return [
          { key: "window", label: "Period (Window)", type: "number", default: 14 },
          { key: "overbought", label: "Overbought", type: "number", default: 70 },
          { key: "oversold", label: "Oversold", type: "number", default: 30 }
        ];
      }
      if (label === "SMA" || label === "EMA" || label === "WMA") {
        return [{ key: "window", label: "Period (Window)", type: "number", default: 14 }];
      }
      if (label === "MACD") {
        return [
          { key: "fast_period", label: "Fast Period", type: "number", default: 12 },
          { key: "slow_period", label: "Slow Period", type: "number", default: 26 },
          { key: "signal_period", label: "Signal Period", type: "number", default: 9 },
          { key: "output", label: "Output Key", type: "select", options: ["macd", "signal", "histogram"], default: "macd" }
        ];
      }
      if (label === "Bollinger Bands") {
        return [
          { key: "window", label: "Period (Window)", type: "number", default: 20 },
          { key: "num_std", label: "Std Dev Multiplier", type: "number", default: 2 },
          { key: "output", label: "Output Key", type: "select", options: ["upper", "middle", "lower"], default: "middle" }
        ];
      }
      return [{ key: "window", label: "Lookback Period", type: "number", default: 14 }];
    }
    if (type === "logic" || label === "Signal Logic" || label === "Condition Builder") {
      return [
        { key: "left_indicator", label: "Left Indicator", type: "select", options: ["RSI", "SMA", "EMA", "MACD", "Close"], default: "RSI" },
        { key: "operator", label: "Operator", type: "select", options: [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"], default: ">" },
        { key: "right_indicator", label: "Right Indicator / Constant", type: "select", options: ["RSI", "SMA", "EMA", "MACD", "Close", "Constant"], default: "Constant" },
        { key: "right_value", label: "Right Value (if Constant)", type: "number", default: 70 }
      ];
    }
    if (type === "mlmodel") {
      return [
        { key: "confidence", label: "Confidence Threshold", type: "number", default: 0.8 },
        { key: "lookback", label: "Lookback Windows", type: "number", default: 50 }
      ];
    }
    return [];
  };

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

  const handleSaveStrategy = async () => {
    const trimmedName = strategyName.trim() || "Untitled Strategy";
    setIsSavingStrategy(true);
    setSaveState("Saving...");

    try {
      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(nodes, edges);
      const sourceNode = serNodes.find((n) => n.type === "source");
      const pair = sourceNode?.params?.symbol || "BTC/USDT";
      const timeframe = sourceNode?.params?.timeframe || "15m";

      const payload = {
        name: trimmedName,
        pair,
        timeframe,
        status: loadedStrategy?.status || "stopped",
        pnl: Number(loadedStrategy?.pnl || 0),
        win_rate: Number(loadedStrategy?.wr || 0),
        nodes: serNodes,
        edges: serEdges,
      };

      let response;
      if (strategyIdState) {
        response = await endpoints.strategies.update(strategyIdState, payload);
      } else {
        response = await endpoints.strategies.save(payload);
      }

      if (response && response.id) {
        setStrategyIdState(response.id);
      }
      setSaveState("Saved");
    } catch (err) {
      console.error("Save strategy error:", err);
      setSaveState("Save Failed");
    } finally {
      setIsSavingStrategy(false);
      setTimeout(() => setSaveState(""), 3000);
    }
  };

  return (
    <div style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Top Controls */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Btn v="ghost" sz="sm" Icon={ArrowLeft} onClick={onBack}>Back</Btn>
          <span style={{ color: C.t1, fontWeight: 900, fontSize: 16, letterSpacing: -0.5 }}>Algorithm Studio</span>
          <Tag2 c="purple">DAG VectorBT Canvas</Tag2>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {saveState && <span style={{ color: saveState === "Saved" ? C.green : C.t3, fontSize: 10, fontFamily: "monospace", marginRight: 8 }}>{saveState}</span>}

          <div style={{ display: "flex", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 2, marginRight: 8 }}>
            <button
              onClick={() => setViewMode('canvas')}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 10,
                fontFamily: "monospace",
                fontWeight: 700,
                border: "none",
                background: viewMode === 'canvas' ? C.cyan : "transparent",
                color: viewMode === 'canvas' ? "#000" : C.t2,
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              Canvas View
            </button>
            <button
              onClick={() => setViewMode('structured')}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 10,
                fontFamily: "monospace",
                fontWeight: 700,
                border: "none",
                background: viewMode === 'structured' ? C.purple : "transparent",
                color: viewMode === 'structured' ? "#fff" : C.t2,
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              Structured View
            </button>
          </div>

          <div style={{ display: "flex", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: 2, marginRight: 8 }}>
            <button
              onClick={() => setBuilderMode('backtest')}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 10,
                fontFamily: "monospace",
                fontWeight: 700,
                border: "none",
                background: builderMode === 'backtest' ? C.cyan : "transparent",
                color: builderMode === 'backtest' ? "#000" : C.t2,
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              Backtest Mode
            </button>
            <button
              onClick={() => setBuilderMode('live')}
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 10,
                fontFamily: "monospace",
                fontWeight: 700,
                border: "none",
                background: builderMode === 'live' ? C.purple : "transparent",
                color: builderMode === 'live' ? "#fff" : C.t2,
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              Live Execution
            </button>
          </div>

          <Btn v="outline" sz="sm" Icon={Check} onClick={handleSaveStrategy} disabled={isSavingStrategy}>
            {isSavingStrategy ? "Saving..." : "Save Strategy"}
          </Btn>

          {onBacktest && (
            <Btn
              v="primary"
              sz="sm"
              Icon={BarChart2}
              onClick={() => {
                onBacktest({
                  id: strategyIdState,
                  name: strategyName,
                  nodes,
                  edges
                });
              }}
            >
              Run VectorBT
            </Btn>
          )}
        </div>
      </div>

      <div style={{ flex: 1, display: viewMode === "canvas" ? "grid" : "flex", gridTemplateColumns: viewMode === "canvas" ? "220px 1fr 280px" : "1fr", gap: 12, minHeight: 0 }}>
        {viewMode === "canvas" ? (
          <>
            {/* Left Toolbar */}
            <Card cls="p-3 flex flex-col" style={{ overflowY: "auto" }}>
              <PanelTitle title="Block Library" sub="Drag blocks onto the canvas" />
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {STRATEGY_TOOLBOX.map((group) => (
                  <div key={group.g}>
                    <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>
                      {group.g}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {group.items.map((item) => {
                        const label = typeof item === "string" ? item : item.label;
                        const type = typeof item === "string" ? group.type : item.type;
                        return (
                          <div
                            key={label}
                            draggable
                            onDragStart={(e) => {
                              e.dataTransfer.setData("application/reactflow-type", type);
                              e.dataTransfer.setData("application/reactflow-label", label);
                              e.dataTransfer.effectAllowed = "move";
                            }}
                            style={{
                              background: C.bg3,
                              border: `1px solid ${C.border}`,
                              borderRadius: 6,
                              padding: "6px 8px",
                              fontSize: 10,
                              fontFamily: "monospace",
                              color: C.t1,
                              cursor: "grab",
                              display: "flex",
                              alignItems: "center",
                              gap: 6
                            }}
                            className="hover:border-cyan-500/40 hover:text-cyan-400 transition-all"
                          >
                            <PlusCircle size={10} style={{ color: C.cyan }} />
                            <span>{label}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {/* Center Canvas */}
            <Card cls="relative flex-1" style={{ minHeight: 0 }}>
              <div ref={reactFlowWrapper} style={{ width: "100%", height: "100%" }} onDragOver={handleDragOver} onDrop={handleDrop}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  nodeTypes={strategyNodeTypes}
                  onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                  fitView
                  attributionPosition="bottom-left"
                >
                  <Background color={C.border} gap={16} />
                  <Controls style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 6, button: { background: C.bg3, color: C.t1 } }} />
                </ReactFlow>
              </div>
            </Card>

            {/* Right Inspector */}
            <Card cls="p-4 flex flex-col" style={{ overflowY: "auto" }}>
              <PanelTitle
                title="Block Inspector"
                sub={selectedNode ? `${selectedNode.data.label} Config` : "Select a block"}
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Inp lbl="Strategy Name" ph="Untitled Strategy" val={strategyName} onChange={e => setStrategyName(e.target.value)} />
                {selectedNode ? (
                  getNodeParamSchema(selectedNode?.data?.label, selectedNode?.type).map((field) =>
                    renderDynamicField(field, selectedNode)
                  )
                ) : (
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Select a node to configure parameters.</div>
                )}
                {selectedNode && (
                  <button
                    onClick={handleDeleteNode}
                    style={{
                      width: "100%",
                      background: `${C.red}20`,
                      color: C.red,
                      fontWeight: 900,
                      borderRadius: 8,
                      padding: "10px 0",
                      border: `1px solid ${C.red}40`,
                      cursor: "pointer",
                      fontSize: 11,
                      fontFamily: "monospace",
                      transition: "all 0.2s"
                    }}
                    className="hover:bg-red-500/30"
                  >
                    Delete Block
                  </button>
                )}
              </div>
            </Card>
          </>
        ) : (
          <StrategyStructuredView
            nodes={nodes}
            setNodes={setNodes}
            edges={edges}
            setEdges={setEdges}
            toolbox={STRATEGY_TOOLBOX}
            getNodeParamSchema={getNodeParamSchema}
            renderDynamicField={renderDynamicField}
            defaultDates={defaultDates}
          />
        )}
      </div>
    </div>
  );
}

export default function StrategyBuilder(props) {
  return (
    <DataPipelineProvider>
      <IndicatorEngineProvider>
        <LogicEngineProvider>
          <StrategyEngineProvider>
            <ReactFlowProvider>
              <StrategyBuilderInner {...props} />
            </ReactFlowProvider>
          </StrategyEngineProvider>
        </LogicEngineProvider>
      </IndicatorEngineProvider>
    </DataPipelineProvider>
  );
}
