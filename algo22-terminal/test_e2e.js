import { serializeReactFlowToDAG } from './src/utils/dagSerializer.js';

// Golden Strategy Topology
const nodes = [
  { id: '1', type: 'source', data: { label: 'Data', params: { symbol: 'BTCUSDT', timeframe: '1h' } } },
  { id: '2', type: 'indicator', data: { label: 'RSI(14)', indicator: 'rsi', params: { period: 14 } } },
  { id: '3', type: 'operator', data: { label: 'GT(70)', operator: 'GT', params: { threshold: 70 } } },
  { id: '4', type: 'action', data: { label: 'SELL', action: 'sell', params: { amount: 1.0, order_type: 'market' } } }
];

const edges = [
  { source: '1', target: '2' },
  { source: '2', target: '3' },
  { source: '3', target: '4' }
];

const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(nodes, edges);

async function runTest() {
  const token = "mock-token"; // Assuming auth is mocked or we can grab a token
  // Let's just output the payloads to see what the frontend WOULD send.
  
  console.log("=== PHASE 2: VALIDATE ===");
  const validatePayload = { dag: { nodes: serNodes, edges: serEdges } };
  console.log("POST /api/strategies/validate");
  console.log(JSON.stringify(validatePayload, null, 2));

  console.log("\n=== PHASE 3: SAVE ===");
  const savePayload = {
    name: "Golden Strategy",
    nodes: serNodes,
    edges: serEdges,
    buy_logic: { operator: "AND", conditions: [] },
    sell_logic: { operator: "AND", conditions: [] },
    risk: { position_size_pct: 0.1, stop_loss_pct: 0.05, take_profit_pct: 0.1 },
    symbol: "BTCUSDT",
    timeframe: "1h"
  };
  console.log("POST /api/strategies");
  console.log(JSON.stringify(savePayload, null, 2));

  console.log("\n=== PHASE 4: UPDATE ===");
  const updatePayload = { ...savePayload, name: "Golden Strategy (Updated)" };
  console.log("PUT /api/strategies/{id}");
  console.log(JSON.stringify(updatePayload, null, 2));

  console.log("\n=== PHASE 5: BACKTEST ===");
  const backtestPayload = {
    strategies: ["Golden Strategy"],
    symbols: ["BTCUSDT"],
    timeframe: "1h",
    initial_capital: 1000,
    trade_size_pct: 0.1,
    stop_loss_pct: 0.02,
    take_profit_pct: 0.04,
    ml_threshold: 0.75,
    params: {},
    dag: {
      nodes: serNodes,
      edges: serEdges,
      symbols: ["BTCUSDT"],
      timeframe: "1h",
      strategy_name: "Golden Strategy"
    }
  };
  console.log("POST /api/strategies/backtest");
  console.log(JSON.stringify(backtestPayload, null, 2));

  console.log("\n=== PHASE 6: DEPLOY ===");
  const deployPayload = { exchange_id: "binance" };
  console.log("POST /api/strategies/{id}/deploy");
  console.log(JSON.stringify(deployPayload, null, 2));
}

runTest();
