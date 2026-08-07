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
  MiniMap,
  useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  Radio, Activity, Brain, GitBranch, AlertTriangle, BarChart2,
  PlusCircle, Check, ArrowLeft, Wifi, BookOpen, WifiOff, Loader2,
  Save, Undo, Redo, Search, Play, Rocket, Target, Settings, ZoomIn,
  ZoomOut, Maximize, Copy, Trash2, Scissors, Keyboard, Layers,
  X, ChevronDown, ChevronRight, PanelLeft, PanelRight
} from "lucide-react";
import {
  C, Inp, Tag2, PanelTitle
} from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { useDataPipeline, DataPipelineProvider } from "../contexts/DataPipelineContext";
import { useIndicatorEngine, IndicatorEngineProvider } from "../contexts/IndicatorEngineContext";
import { useLogicEngine, LogicEngineProvider } from "../contexts/LogicEngineContext";
import { useStrategyEngine, StrategyEngineProvider } from "../contexts/StrategyEngineContext";
import { useUndoRedo, UndoRedoProvider } from "../contexts/UndoRedoContext";
import { useValidation, ValidationProvider } from "../contexts/ValidationContext";
import { BlockRegistry, getBlocksByCategory, getCategoryIcon, getCategoryColor, BlockCategories, StreamTypes } from "../lib/blockRegistry";
import { strategiesApi } from "../api/modules/strategies";

const ApiSyncIndicator = ({ color, text, active = true }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: "6px", marginTop: "10px", paddingTop: 6, borderTop: `1px dashed ${C.border}` }}>
    <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? color : C.t3, boxShadow: active ? `0 0 8px ${color}` : 'none', transition: 'all 0.3s' }} />
    <span className="text-micro" style={{ color: active ? C.t2 : C.t4, letterSpacing: 1, fontFamily: "monospace", textTransform: "uppercase", fontWeight: 700 }}>{text}</span>
  </div>
);

const PremiumNodeWrapper = ({ children, color, active = true, selected = false, hasError = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        minWidth: 150,
        background: C.bg3,
        border: `2px solid ${hasError ? C.red : selected ? C.cyan : isHovered ? color : color + "90"}`,
        borderRadius: 8,
        color: C.t1,
        padding: "2.5",
        boxShadow: isHovered ? `${C.glow[color === C.green ? "profit" : color === C.red ? "loss" : "accent"]}, 0 4px 12px rgba(0,0,0,0.3)` : `0 0 15px ${color}25`,
        fontFamily: "monospace",
        transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "scale(1.02)" : "scale(1)",
        cursor: "pointer",
        position: "relative"
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
      {hasError && (
        <div style={{
          position: "absolute",
          top: 6,
          right: 6,
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: C.red,
          boxShadow: `0 0 8px ${C.red}`
        }} />
      )}
    </div>
  );
};

const DynamicNode = React.memo(function DynamicNode({ data, selected }) {
  const block = BlockRegistry[data.type];
  const BlockIcon = block ? block.icon : Activity;
  const blockColor = block ? block.color : C.t2;
  const hasError = data.hasError || false;

  return (
    <PremiumNodeWrapper color={blockColor} selected={selected} hasError={hasError}>
      <Handle type="target" position={Position.Left} style={{ background: blockColor, border: `1px solid ${C.bg1}`, width: "0.625rem", height: "0.625rem" }} />
      <div style={{ color: blockColor, letterSpacing: 2, textTransform: "uppercase", marginBottom: "4px", display: "flex", alignItems: "center", gap: "4px" }}>
        <BlockIcon size={10} />
        {block ? block.category : 'Block'}
      </div>
      <div className="text-body-lg" style={{ fontWeight: 900 }}>{data.label}</div>
      {hasError && (
        <div className="text-micro" style={{ color: C.red, marginTop: "2px", fontFamily: 'monospace' }}>
          ⚠ {data.errorMessage || 'Error'}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: blockColor, border: `1px solid ${C.bg1}`, width: "0.625rem", height: "0.625rem" }} />
    </PremiumNodeWrapper>
  );
});

function StrategyBuilderCanvas({ initialStrategy, strategyProp, onBackProp, onBacktestProp }) {
  const navigate = useNavigate();
  const location = useLocation();
  const strategy = strategyProp ?? location.state?.strategy ?? null;
  const onBack = onBackProp ?? (() => navigate("/app/strategies"));
  const onBacktest = onBacktestProp ?? ((payload) => navigate("/app/backtest", { state: { strategy: payload } }));
  
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { pushState, undo, redo, canUndo, canRedo } = useUndoRedo();
  const { errors, warnings, isValid, validateGraph, getErrorByNodeId } = useValidation();

  const [builderMode, setBuilderMode] = useState('backtest');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [isSavingStrategy, setIsSavingStrategy] = useState(false);
  const [saveState, setSaveState] = useState("");
  const [collapsedCategories, setCollapsedCategories] = useState({});
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [backendBlocks, setBackendBlocks] = useState(null);
  const [loadingBlocks, setLoadingBlocks] = useState(true);

  const reactFlowWrapper = useRef(null);
  const nodeSeq = useRef(4);
  const loadedStrategy = initialStrategy || strategy || null;
  const [strategyIdState, setStrategyIdState] = useState(loadedStrategy?.id || null);
  const [strategyName, setStrategyName] = useState(loadedStrategy?.name || "Untitled Strategy");

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
    { id: "n-1", type: "ccxt_asset_feed", position: { x: 50, y: 250 }, data: { label: "CCXT Asset Feed", params: { symbol: "BTC/USDT", timeframe: "15m", start_date: defaultDates.start_date, end_date: defaultDates.end_date } } },
    { id: "n-2", type: "rsi", position: { x: 300, y: 150 }, data: { label: "RSI", params: { window: 14 } } },
    { id: "n-3", type: "xgboost", position: { x: 550, y: 150 }, data: { label: "XGBoost", params: { confidence: 0.8 } } },
    { id: "n-4", type: "buy_market", position: { x: 800, y: 250 }, data: { label: "Buy Market", params: {} } },
  ];

  const initialEdges = [
    { id: "e1-2", source: "n-1", target: "n-2", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
    { id: "e2-3", source: "n-2", target: "n-3", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
    { id: "e3-4", source: "n-3", target: "n-4", animated: true, style: { stroke: C.cyan, strokeWidth: 2 } },
  ];

  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);

  const strategyNodeTypes = useMemo(() => ({
    ccxt_asset_feed: DynamicNode,
    orderbook_imbalance: DynamicNode,
    live_ticker: DynamicNode,
    sma: DynamicNode,
    ema: DynamicNode,
    rsi: DynamicNode,
    macd: DynamicNode,
    bollinger_bands: DynamicNode,
    atr: DynamicNode,
    constant: DynamicNode,
    compare: DynamicNode,
    math: DynamicNode,
    crosses: DynamicNode,
    signal_logic: DynamicNode,
    and_gate: DynamicNode,
    or_gate: DynamicNode,
    condition_builder: DynamicNode,
    xgboost: DynamicNode,
    lightgbm: DynamicNode,
    random_forest: DynamicNode,
    lstm: DynamicNode,
    gru: DynamicNode,
    transformer: DynamicNode,
    buy_market: DynamicNode,
    sell_market: DynamicNode,
    close_position: DynamicNode,
    trailing_stop: DynamicNode,
  }), []);

  // PHASE H: Continuous validation
  useEffect(() => {
    validateGraph(nodes, edges);
  }, [nodes, edges, validateGraph]);

  // Update nodes with error states
  useEffect(() => {
    setNodes(nds => nds.map(node => {
      const error = getErrorByNodeId(node.id);
      return {
        ...node,
        data: {
          ...node.data,
          hasError: !!error,
          errorMessage: error?.message
        }
      };
    }));
  }, [errors, getErrorByNodeId]);

  // Fetch backend blocks for dynamic synchronization
  useEffect(() => {
    const fetchBackendBlocks = async () => {
      try {
        setLoadingBlocks(true);
        const response = await strategiesApi.getBlocks();
        setBackendBlocks(response);
      } catch (error) {
        console.error('[StrategyBuilder] Failed to fetch backend blocks:', error);
        // Fall back to static registry if backend fails
        setBackendBlocks(null);
      } finally {
        setLoadingBlocks(false);
      }
    };

    fetchBackendBlocks();
  }, []);

  // Dynamic block getter that uses backend data if available
  const getDynamicBlocksByCategory = useCallback((category) => {
    if (backendBlocks) {
      // Use backend data
      const categoryMap = {
        [BlockCategories.INDICATORS]: backendBlocks.indicators || [],
        [BlockCategories.ML]: backendBlocks.ml_models || [],
        [BlockCategories.DL]: backendBlocks.dl_models || [],
        [BlockCategories.DATA]: getBlocksByCategory(BlockCategories.DATA),
        [BlockCategories.FEATURE_ENGINEERING]: getBlocksByCategory(BlockCategories.FEATURE_ENGINEERING),
        [BlockCategories.MATH]: getBlocksByCategory(BlockCategories.MATH),
        [BlockCategories.LOGIC]: getBlocksByCategory(BlockCategories.LOGIC),
        [BlockCategories.ACTION]: getBlocksByCategory(BlockCategories.ACTION),
      };
      
      const backendCategoryBlocks = categoryMap[category] || [];
      
      // Convert backend format to frontend format
      return backendCategoryBlocks.map(block => ({
        type: block.id,
        name: block.name,
        description: block.description,
        category: block.category,
        icon: getCategoryIcon(block.category),
        color: getCategoryColor(block.category),
        parameters: block.parameters || [],
        inputs: [StreamTypes.OHLCV],
        outputs: [StreamTypes.INDICATOR],
        backendType: block.category === 'indicators' ? 'indicator' : block.category
      }));
    }
    
    // Fall back to static registry
    return getBlocksByCategory(category);
  }, [backendBlocks, getBlocksByCategory, getCategoryIcon, getCategoryColor]);

  const onNodesChange = useCallback((changes) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    pushState({ nodes: applyNodeChanges(changes, nodes), edges });
  }, [edges, pushState]);

  const onEdgesChange = useCallback((changes) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
    pushState({ nodes, edges: applyEdgeChanges(changes, edges) });
  }, [nodes, pushState]);

  const onConnect = useCallback((params) => {
    // PHASE C: Strongly typed connections
    const sourceNode = nodes.find(n => n.id === params.source);
    const targetNode = nodes.find(n => n.id === params.target);
    
    if (!sourceNode || !targetNode) return;

    const sourceBlock = BlockRegistry[sourceNode.type];
    const targetBlock = BlockRegistry[targetNode.type];
    
    if (sourceBlock && targetBlock) {
      const sourceOutput = sourceBlock.outputs[0];
      const targetInput = targetBlock.inputs[0];
      
      // Allow NUMBER input to accept INDICATOR output
      if (targetInput === 'number' && sourceOutput === 'indicator') {
        setEdges((eds) => [...eds, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }]);
        pushState({ nodes, edges: [...edges, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }] });
        return;
      }
      
      // Allow FEATURE input to accept INDICATOR output
      if (targetInput === 'feature' && sourceOutput === 'indicator') {
        setEdges((eds) => [...eds, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }]);
        pushState({ nodes, edges: [...edges, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }] });
        return;
      }
      
      // Otherwise, types must match
      if (sourceOutput !== targetInput) {
        // Type mismatch - silently reject connection
        return;
      }
    }

    setEdges((eds) => [...eds, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }]);
    pushState({ nodes, edges: [...edges, { ...params, id: `e-${params.source}-${params.target}`, animated: true, style: { stroke: C.cyan, strokeWidth: 2 } }] });
  }, [nodes, edges, pushState]);

  const onNodeClick = useCallback((_, node) => {
    setSelectedNodeId(node.id);
  }, []);

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

    const block = BlockRegistry[type];
    const defaultParams = {};
    
    if (block && block.parameters) {
      block.parameters.forEach(param => {
        defaultParams[param.key] = param.default;
      });
    }

    const newNode = {
      id: newNodeId,
      type: type,
      position,
      data: { label, params: defaultParams }
    };

    setNodes((nds) => [...nds, newNode]);
    setSelectedNodeId(newNodeId);
    pushState({ nodes: [...nodes, newNode], edges });
  }, [nodes, edges, pushState]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((nds) => nds.filter((node) => node.id !== selectedNodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId(null);
    pushState({ nodes: nodes.filter(n => n.id !== selectedNodeId), edges: edges.filter(e => e.source !== selectedNodeId && e.target !== selectedNodeId) });
  }, [selectedNodeId, nodes, edges, pushState]);

  const handleUndo = useCallback(() => {
    const previousState = undo();
    if (previousState) {
      setNodes(previousState.nodes);
      setEdges(previousState.edges);
    }
  }, [undo]);

  const handleRedo = useCallback(() => {
    const nextState = redo();
    if (nextState) {
      setNodes(nextState.nodes);
      setEdges(nextState.edges);
    }
  }, [redo]);

  const handleFitView = useCallback(() => {
    fitView({ duration: 800 });
  }, [fitView]);

  // PHASE M: Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Prevent default for builder shortcuts
      if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
          case 's':
            e.preventDefault();
            handleSaveStrategy();
            break;
          case 'z':
            e.preventDefault();
            if (e.shiftKey) {
              handleRedo();
            } else {
              handleUndo();
            }
            break;
          case 'c':
            e.preventDefault();
            // Copy selected node
            break;
          case 'v':
            e.preventDefault();
            // Paste node
            break;
          case 'd':
            e.preventDefault();
            // Duplicate node
            break;
        }
      } else {
        switch (e.key) {
          case 'Delete':
          case 'Backspace':
            if (selectedNodeId) {
              e.preventDefault();
              handleDeleteNode();
            }
            break;
          case 'Escape':
            setSelectedNodeId(null);
            break;
          case 'f':
            e.preventDefault();
            setLibraryOpen(!libraryOpen);
            break;
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNodeId, handleDeleteNode, handleUndo, handleRedo, libraryOpen]);

  const handleSaveStrategy = async () => {
    const trimmedName = strategyName.trim() || "Untitled Strategy";
    setIsSavingStrategy(true);
    setSaveState("Compiling...");

    try {
      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(nodes, edges);
      const sourceNode = serNodes.find((n) => n.type === "ccxt_asset_feed");
      const pair = sourceNode?.params?.symbol || "BTC/USDT";
      const timeframe = sourceNode?.params?.timeframe || "15m";

      // PHASE G: Compiler-based workflow
      const blueprint = {
        nodes: serNodes,
        edges: serEdges,
        symbols: [pair],
        timeframe: timeframe
      };

      setSaveState("Compiling...");
      const compileResponse = await post('/api/strategies/compile', {
        blueprint: blueprint,
        version: "v1.0",
        metadata: { name: trimmedName }
      });

      if (!compileResponse || compileResponse.error) {
        throw new Error(compileResponse?.error || "Compilation failed");
      }

      setSaveState("Saving...");
      const payload = {
        name: trimmedName,
        description: `Strategy created with ${serNodes.length} nodes`,
        blueprint: blueprint,
        execution_graph: compileResponse.execution_graph,
        exchange: "binance",
        symbol: pair,
        timeframe: timeframe,
        tags: []
      };

      let response;
      if (strategyIdState) {
        response = await post('/api/strategies/' + strategyIdState, payload);
      } else {
        response = await post('/api/strategies', payload);
      }

      if (response && response.strategy) {
        setStrategyIdState(response.strategy.id);
      }
      setSaveState("Saved");
    } catch (err) {
      console.error("Save strategy error:", err);
      setSaveState("Save Failed: " + (err.message || "Unknown error"));
    } finally {
      setIsSavingStrategy(false);
      setTimeout(() => setSaveState(""), 3000);
    }
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

  const toggleCategory = useCallback((category) => {
    setCollapsedCategories(prev => ({
      ...prev,
      [category]: !prev[category]
    }));
  }, []);

  const filteredBlocks = useMemo(() => {
    if (!searchQuery) return null;
    
    // Search both static registry and backend blocks
    const searchLower = searchQuery.toLowerCase();
    const results = [];
    
    // Search static registry
    Object.entries(BlockRegistry).forEach(([type, block]) => {
      if (block.name.toLowerCase().includes(searchLower) ||
          block.description.toLowerCase().includes(searchLower) ||
          block.category.toLowerCase().includes(searchLower)) {
        results.push([type, block]);
      }
    });
    
    // Search backend blocks if available
    if (backendBlocks) {
      backendBlocks.indicators?.forEach(block => {
        if (block.name.toLowerCase().includes(searchLower) ||
            block.description.toLowerCase().includes(searchLower) ||
            block.category.toLowerCase().includes(searchLower)) {
          results.push([block.id, {
            ...block,
            icon: getCategoryIcon(block.category),
            color: getCategoryColor(block.category),
            inputs: [StreamTypes.OHLCV],
            outputs: [StreamTypes.INDICATOR],
            backendType: 'indicator'
          }]);
        }
      });
      
      backendBlocks.ml_models?.forEach(block => {
        if (block.name.toLowerCase().includes(searchLower) ||
            block.description.toLowerCase().includes(searchLower) ||
            block.category.toLowerCase().includes(searchLower)) {
          results.push([block.id, {
            ...block,
            icon: getCategoryIcon(block.category),
            color: getCategoryColor(block.category),
            inputs: [StreamTypes.OHLCV],
            outputs: [StreamTypes.INDICATOR],
            backendType: 'ml'
          }]);
        }
      });
      
      backendBlocks.dl_models?.forEach(block => {
        if (block.name.toLowerCase().includes(searchLower) ||
            block.description.toLowerCase().includes(searchLower) ||
            block.category.toLowerCase().includes(searchLower)) {
          results.push([block.id, {
            ...block,
            icon: getCategoryIcon(block.category),
            color: getCategoryColor(block.category),
            inputs: [StreamTypes.OHLCV],
            outputs: [StreamTypes.INDICATOR],
            backendType: 'dl'
          }]);
        }
      });
    }
    
    return results;
  }, [searchQuery, backendBlocks, getCategoryIcon, getCategoryColor]);

  // Load strategy on mount
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

  // PHASE G: Autosave every 30 seconds
  useEffect(() => {
    const autosaveInterval = setInterval(() => {
      // Save builder layout to localStorage
      const layout = { nodes, edges, strategyName };
      localStorage.setItem('builder_autosave', JSON.stringify(layout));
    }, 30000);

    return () => clearInterval(autosaveInterval);
  }, [nodes, edges, strategyName]);

  // Recover draft on mount
  useEffect(() => {
    const savedLayout = localStorage.getItem('builder_autosave');
    if (savedLayout && !loadedStrategy) {
      try {
        const layout = JSON.parse(savedLayout);
        setNodes(layout.nodes || initialNodes);
        setEdges(layout.edges || initialEdges);
        setStrategyName(layout.strategyName || "Untitled Strategy");
      } catch (e) {
        console.error("Failed to recover autosave:", e);
      }
    }
  }, [loadedStrategy]);

  const selectedNode = useMemo(() => nodes.find(n => n.id === selectedNodeId) || null, [nodes, selectedNodeId]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: C.bg1 }}>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", background: C.bg2, borderBottom: `1px solid ${C.border}`, gap: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Button variant="ghost" size="sm" Icon={ArrowLeft} onClick={onBack}>Back</Button>
          <Inp
            value={strategyName}
            onChange={(e) => setStrategyName(e.target.value)}
            placeholder="Strategy Name"
            className="text-body" style={{ width: 200, fontFamily: "monospace" }}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {/* PHASE I: Professional toolbar */}
          <Button variant="ghost" size="sm" Icon={Undo} onClick={handleUndo} disabled={!canUndo} title="Undo (Ctrl+Z)" />
          <Button variant="ghost" size="sm" Icon={Redo} onClick={handleRedo} disabled={!canRedo} title="Redo (Ctrl+Y)" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="ghost" size="sm" Icon={ZoomOut} onClick={() => zoomOut()} title="Zoom Out" />
          <Button variant="ghost" size="sm" Icon={ZoomIn} onClick={() => zoomIn()} title="Zoom In" />
          <Button variant="ghost" size="sm" Icon={Maximize} onClick={handleFitView} title="Fit View" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="ghost" size="sm" Icon={PanelLeft} onClick={() => setLibraryOpen(!libraryOpen)} title="Toggle Library (F)" />
          <Button variant="ghost" size="sm" Icon={PanelRight} onClick={() => setInspectorOpen(!inspectorOpen)} title="Toggle Inspector" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="outline" size="sm" Icon={Save} onClick={handleSaveStrategy} disabled={isSavingStrategy || !isValid}>
            {isSavingStrategy ? "Saving..." : "Save"}
          </Button>
          <Button variant="primary" size="sm" Icon={Play} onClick={handleSaveStrategy} disabled={!isValid} title="Compile & Save">
            Compile
          </Button>
          {onBacktest && (
            <Button variant="primary" size="sm" Icon={BarChart2} onClick={() => onBacktest({ id: strategyIdState, name: strategyName, nodes, edges })}>
              Backtest
            </Button>
          )}
        </div>
      </div>

      {/* Validation Bar */}
      {!isValid && (
        <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 16px", background: `${C.red}20`, borderBottom: `1px solid ${C.red}` }}>
          <AlertTriangle size={16} style={{ color: C.red }} />
          <span className="text-body-sm" style={{ color: C.red, fontFamily: "monospace" }}>
            {errors.length} error{errors.length !== 1 ? 's' : ''}: {errors[0]?.message}
          </span>
        </div>
      )}

      {/* Main Content */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Block Library */}
        {libraryOpen && (
          <div style={{ width: 260, background: C.bg2, borderRight: `1px solid ${C.border}`, display: "flex", flexDirection: "column" }}>
            {/* PHASE J: Professional Search */}
            <div style={{ padding: "12px", borderBottom: `1px solid ${C.border}` }}>
              <div style={{ position: "relative" }}>
                <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: C.t3 }} />
                <input
                  type="text"
                  placeholder="Search blocks..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="text-body-sm"
                  style={{
                    width: "100%",
                    background: C.bg3,
                    border: `1px solid ${C.border}`,
                    borderRadius: "0.375rem",
                    padding: "8px 12px 8px 32",
                    color: C.t1,
                    outline: "none"
                  }}
                />
              </div>
            </div>

            <div style={{ flex: 1, overflowY: "auto", padding: "12px" }}>
              {filteredBlocks ? (
                // Search results
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  {filteredBlocks.map(([type, block]) => {
                    const BlockIcon = block.icon;
                    return (
                      <div
                        key={type}
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData("application/reactflow-type", type);
                          e.dataTransfer.setData("application/reactflow-label", block.name);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        style={{
                          background: C.bg3,
                          border: `1px solid ${C.border}`,
                          borderRadius: "0.375rem",
                          padding: "10px",
                          cursor: "grab",
                          display: "flex",
                          alignItems: "center",
                          gap: "8px"
                        }}
                      >
                        <BlockIcon size={16} style={{ color: block.color }} />
                        <div style={{ flex: 1 }}>
                          <div className="text-body-sm" style={{ fontWeight: 600, color: C.t1 }}>{block.name}</div>
                          <div className="text-caption-sm" style={{ color: C.t3 }}>{block.description}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                // Categories
                Object.values(BlockCategories).map(category => {
                  const categoryBlocks = getDynamicBlocksByCategory(category);
                  const CategoryIcon = getCategoryIcon(category);
                  const isCollapsed = collapsedCategories[category];
                  
                  return (
                    <div key={category} style={{ marginBottom: "16px" }}>
                      <div
                        onClick={() => toggleCategory(category)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                          padding: "8px",
                          cursor: "pointer",
                          userSelect: "none"
                        }}
                      >
                        {isCollapsed ? <ChevronRight size={14} style={{ color: C.t3 }} /> : <ChevronDown size={14} style={{ color: C.t3 }} />}
                        <CategoryIcon size={14} style={{ color: getCategoryColor(category) }} />
                        <span className="text-caption" style={{ fontWeight: 700, color: C.t2, textTransform: "uppercase", letterSpacing: 1 }}>
                          {category.replace('_', ' ')}
                        </span>
                        {loadingBlocks && (category === BlockCategories.INDICATORS || category === BlockCategories.ML || category === BlockCategories.DL) ? (
                          <span className="text-caption-sm" style={{ marginLeft: "auto", color: C.t3 }}>Loading...</span>
                        ) : (
                          <span className="text-caption-sm" style={{ marginLeft: "auto", color: C.t3 }}>{categoryBlocks.length}</span>
                        )}
                      </div>
                      
                      {!isCollapsed && (
                        <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "4px" }}>
                          {categoryBlocks.map(block => {
                            const BlockIcon = block.icon;
                            return (
                              <div
                                key={block.type}
                                draggable
                                onDragStart={(e) => {
                                  e.dataTransfer.setData("application/reactflow-type", block.type);
                                  e.dataTransfer.setData("application/reactflow-label", block.name);
                                  e.dataTransfer.effectAllowed = "move";
                                }}
                                style={{
                                  background: C.bg3,
                                  border: `1px solid ${C.border}`,
                                  borderRadius: "0.375rem",
                                  padding: "8px 10px",
                                  cursor: "grab",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "8px",
                                  transition: "all 0.15s"
                                }}
                                onMouseEnter={(e) => e.currentTarget.style.background = C.bg4}
                                onMouseLeave={(e) => e.currentTarget.style.background = C.bg3}
                              >
                                <BlockIcon size={14} style={{ color: block.color }} />
                                <span className="text-caption" style={{ color: C.t1 }}>{block.name}</span>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* Canvas */}
        <div style={{ flex: 1, position: "relative" }} ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            nodeTypes={strategyNodeTypes}
            fitView
            deleteKeyCode={null}
          >
            <Background color={C.bg3} gap={16} />
            <Controls />
            <MiniMap 
              nodeColor={C.cyan}
              nodeStrokeWidth={3}
              zoomable
              pannable
            />
          </ReactFlow>
          
          {/* Drag drop overlay */}
          <div
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 10
            }}
          />
        </div>

        {/* Inspector */}
        {inspectorOpen && (
          <div style={{ width: 300, background: C.bg2, borderLeft: `1px solid ${C.border}`, display: "flex", flexDirection: "column" }}>
            <div style={{ padding: "12px", borderBottom: `1px solid ${C.border}` }}>
              <PanelTitle title="Inspector" sub={selectedNode ? selectedNode.data.label : "Select a node"} />
            </div>
            
            <div style={{ flex: 1, overflowY: "auto", padding: "12px" }}>
              {selectedNode ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {/* Node Info */}
                  <div>
                    <div className="text-caption-sm" style={{ color: C.t3, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase", marginBottom: "8px" }}>
                      Block Type
                    </div>
                    <Tag2 c={getCategoryColor(BlockRegistry[selectedNode.type]?.category)}>
                      {BlockRegistry[selectedNode.type]?.name || selectedNode.type}
                    </Tag2>
                  </div>

                  {/* Parameters */}
                  {BlockRegistry[selectedNode.type]?.parameters && (
                    <div>
                      <div className="text-caption-sm" style={{ color: C.t3, fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase", marginBottom: "8px" }}>
                        Parameters
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        {BlockRegistry[selectedNode.type].parameters.map(param => (
                          <div key={param.key}>
                            <label className="text-caption-sm" style={{ color: C.t2, fontFamily: "monospace", marginBottom: "4px", display: "block" }}>
                              {param.label}
                            </label>
                            {param.type === "select" ? (
                              <select
                                value={selectedNode.data?.params?.[param.key] ?? param.default}
                                onChange={(e) => {
                                  setNodes(nds => nds.map(n => n.id === selectedNodeId ? {
                                    ...n,
                                    data: { ...n.data, params: { ...n.data.params, [param.key]: e.target.value } }
                                  } : n));
                                  pushState({ nodes: nds.map(n => n.id === selectedNodeId ? {
                                    ...n,
                                    data: { ...n.data, params: { ...n.data.params, [param.key]: e.target.value } }
                                  } : n), edges });
                                }}
                                style={{
                                  width: "100%",
                                  background: C.bg3,
                                  border: `1px solid ${C.border}`,
                                  borderRadius: "0.375rem",
                                  padding: "8px",
                                  color: C.t1,
                                  outline: "none"
                                }}
                                className="text-body-sm"
                              >
                                {param.options.map(opt => (
                                  <option key={opt} value={opt}>{opt}</option>
                                ))}
                              </select>
                            ) : (
                              <input
                                type={param.type === "number" ? "number" : "text"}
                                value={selectedNode.data?.params?.[param.key] ?? param.default}
                                onChange={(e) => {
                                  setNodes(nds => nds.map(n => n.id === selectedNodeId ? {
                                    ...n,
                                    data: { ...n.data, params: { ...n.data.params, [param.key]: e.target.value } }
                                  } : n));
                                  pushState({ nodes: nds.map(n => n.id === selectedNodeId ? {
                                    ...n,
                                    data: { ...n.data, params: { ...n.data.params, [param.key]: e.target.value } }
                                  } : n), edges });
                                }}
                                style={{
                                  width: "100%",
                                  background: C.bg3,
                                  border: `1px solid ${C.border}`,
                                  borderRadius: "0.375rem",
                                  padding: "8px",
                                  color: C.t1,
                                  outline: "none"
                                }}
                                className="text-body-sm"
                              />
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Actions */}
                  <div>
                    <Button variant="danger" size="sm" Icon={Trash2} onClick={handleDeleteNode} style={{ width: "100%" }}>
                      Delete Node
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="text-body-sm" style={{ color: C.t3, fontFamily: "monospace", textAlign: "center", padding: "20px" }}>
                  Click a node to inspect
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Status Bar */}
      <div className="text-caption" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 16px", background: C.bg2, borderTop: `1px solid ${C.border}`, fontFamily: "monospace", color: C.t3 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span>{nodes.length} nodes</span>
          <span>{edges.length} connections</span>
          {saveState && <span style={{ color: isValid ? C.green : C.red }}>{saveState}</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span>Ctrl+S Save</span>
          <span>Ctrl+Z Undo</span>
          <span>Ctrl+Y Redo</span>
          <span>F Toggle Library</span>
        </div>
      </div>
    </div>
  );
}

function StrategyBuilderWrapper(props) {
  return (
    <ReactFlowProvider>
      <DataPipelineProvider mode="backtest">
        <IndicatorEngineProvider>
          <LogicEngineProvider>
            <StrategyEngineProvider>
              <UndoRedoProvider>
                <ValidationProvider>
                  <StrategyBuilderCanvas {...props} />
                </ValidationProvider>
              </UndoRedoProvider>
            </StrategyEngineProvider>
          </LogicEngineProvider>
        </IndicatorEngineProvider>
      </DataPipelineProvider>
    </ReactFlowProvider>
  );
}

export default StrategyBuilderWrapper;
