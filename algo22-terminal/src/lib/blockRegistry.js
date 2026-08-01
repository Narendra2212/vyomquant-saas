/**
 * Block Registry - Central block definitions for Strategy Builder
 * 
 * PHASE B: Dynamic block loading system
 * Builder must NEVER hardcode blocks - all blocks loaded from registry
 */

import { Radio, Activity, Brain, GitBranch, Check, AlertTriangle, Database, Cpu, TrendingUp, Zap, Target, Settings } from 'lucide-react';
import { C } from '../components/ui-legacy/primitives';

// Stream types for strongly typed connections
export const StreamTypes = {
  MARKET_DATA: 'market_data',
  OHLCV: 'ohlcv',
  INDICATOR: 'indicator',
  FEATURE: 'feature',
  PREDICTION: 'prediction',
  BOOLEAN: 'boolean',
  NUMBER: 'number',
  SIGNAL: 'signal',
  TRADING_INTENT: 'trading_intent'
};

// Block categories
export const BlockCategories = {
  DATA: 'data',
  INDICATORS: 'indicators',
  FEATURE_ENGINEERING: 'feature_engineering',
  MATH: 'math',
  LOGIC: 'logic',
  ML: 'ml',
  DL: 'dl',
  ACTION: 'action'
};

// Block registry
export const BlockRegistry = {
  // ══════════════════════════════════════════════════════════════════════════
  // DATA BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'ccxt_asset_feed': {
    category: BlockCategories.DATA,
    name: 'CCXT Asset Feed',
    description: 'Fetch historical OHLCV data from exchange',
    icon: Database,
    color: C.t2,
    inputs: [],
    outputs: [StreamTypes.OHLCV],
    parameters: [
      { key: 'symbol', label: 'Symbol', type: 'select', options: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT'], default: 'BTC/USDT' },
      { key: 'timeframe', label: 'Timeframe', type: 'select', options: ['1m', '5m', '15m', '1h', '4h', '1d'], default: '15m' },
      { key: 'start_date', label: 'Start Date', type: 'date', default: null },
      { key: 'end_date', label: 'End Date', type: 'date', default: null }
    ],
    backendType: 'source',
    validation: (params) => {
      if (!params.symbol) return { valid: false, error: 'Symbol is required' };
      if (!params.timeframe) return { valid: false, error: 'Timeframe is required' };
      return { valid: true };
    }
  },
  
  'orderbook_imbalance': {
    category: BlockCategories.DATA,
    name: 'Orderbook Imbalance',
    description: 'Calculate orderbook flow imbalance',
    icon: Activity,
    color: C.purple,
    inputs: [StreamTypes.MARKET_DATA],
    outputs: [StreamTypes.NUMBER],
    parameters: [
      { key: 'depth', label: 'Depth Levels', type: 'number', default: 10, min: 1, max: 50 }
    ],
    backendType: 'orderbook',
    validation: (params) => {
      if (params.depth < 1 || params.depth > 50) return { valid: false, error: 'Depth must be between 1 and 50' };
      return { valid: true };
    }
  },
  
  'live_ticker': {
    category: BlockCategories.DATA,
    name: 'Live Ticker',
    description: 'Real-time price ticker stream',
    icon: Zap,
    color: C.accent,
    inputs: [],
    outputs: [StreamTypes.MARKET_DATA],
    parameters: [
      { key: 'symbol', label: 'Symbol', type: 'select', options: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'], default: 'BTC/USDT' },
      { key: 'mode', label: 'Mode', type: 'select', options: ['live', 'backtest'], default: 'backtest' }
    ],
    backendType: 'liveticker',
    validation: (params) => {
      if (!params.symbol) return { valid: false, error: 'Symbol is required' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // INDICATOR BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'sma': {
    category: BlockCategories.INDICATORS,
    name: 'SMA',
    description: 'Simple Moving Average',
    icon: TrendingUp,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'window', label: 'Period', type: 'number', default: 20, min: 1, max: 500 }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.window < 1 || params.window > 500) return { valid: false, error: 'Period must be between 1 and 500' };
      return { valid: true };
    }
  },
  
  'ema': {
    category: BlockCategories.INDICATORS,
    name: 'EMA',
    description: 'Exponential Moving Average',
    icon: TrendingUp,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'window', label: 'Period', type: 'number', default: 20, min: 1, max: 500 }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.window < 1 || params.window > 500) return { valid: false, error: 'Period must be between 1 and 500' };
      return { valid: true };
    }
  },
  
  'rsi': {
    category: BlockCategories.INDICATORS,
    name: 'RSI',
    description: 'Relative Strength Index',
    icon: Activity,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'window', label: 'Period', type: 'number', default: 14, min: 2, max: 100 }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.window < 2 || params.window > 100) return { valid: false, error: 'Period must be between 2 and 100' };
      return { valid: true };
    }
  },
  
  'macd': {
    category: BlockCategories.INDICATORS,
    name: 'MACD',
    description: 'Moving Average Convergence Divergence',
    icon: Activity,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'fast_period', label: 'Fast Period', type: 'number', default: 12, min: 1, max: 200 },
      { key: 'slow_period', label: 'Slow Period', type: 'number', default: 26, min: 1, max: 200 },
      { key: 'signal_period', label: 'Signal Period', type: 'number', default: 9, min: 1, max: 50 },
      { key: 'output', label: 'Output', type: 'select', options: ['macd', 'signal', 'histogram'], default: 'macd' }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.fast_period >= params.slow_period) return { valid: false, error: 'Fast period must be less than slow period' };
      return { valid: true };
    }
  },
  
  'bollinger_bands': {
    category: BlockCategories.INDICATORS,
    name: 'Bollinger Bands',
    description: 'Bollinger Bands volatility indicator',
    icon: Activity,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'window', label: 'Period', type: 'number', default: 20, min: 1, max: 200 },
      { key: 'std_dev', label: 'Std Dev Multiplier', type: 'number', default: 2, min: 0.5, max: 4 },
      { key: 'output', label: 'Output', type: 'select', options: ['upper', 'middle', 'lower'], default: 'middle' }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.std_dev < 0.5 || params.std_dev > 4) return { valid: false, error: 'Std dev must be between 0.5 and 4' };
      return { valid: true };
    }
  },
  
  'atr': {
    category: BlockCategories.INDICATORS,
    name: 'ATR',
    description: 'Average True Range',
    icon: Activity,
    color: C.accent,
    inputs: [StreamTypes.OHLCV],
    outputs: [StreamTypes.INDICATOR],
    parameters: [
      { key: 'window', label: 'Period', type: 'number', default: 14, min: 1, max: 100 }
    ],
    backendType: 'indicator',
    validation: (params) => {
      if (params.window < 1 || params.window > 100) return { valid: false, error: 'Period must be between 1 and 100' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // MATH BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'constant': {
    category: BlockCategories.MATH,
    name: 'Constant',
    description: 'Constant value',
    icon: Target,
    color: C.gold,
    inputs: [],
    outputs: [StreamTypes.NUMBER],
    parameters: [
      { key: 'value', label: 'Value', type: 'number', default: 0 }
    ],
    backendType: 'operator',
    validation: (params) => {
      if (params.value === null || params.value === undefined) return { valid: false, error: 'Value is required' };
      return { valid: true };
    }
  },
  
  'compare': {
    category: BlockCategories.MATH,
    name: 'Compare',
    description: 'Compare two values',
    icon: GitBranch,
    color: C.gold,
    inputs: [StreamTypes.NUMBER, StreamTypes.NUMBER],
    outputs: [StreamTypes.BOOLEAN],
    parameters: [
      { key: 'operator', label: 'Operator', type: 'select', options: ['>', '<', '>=', '<=', '==', '!='], default: '>' }
    ],
    backendType: 'operator',
    validation: (params) => {
      if (!params.operator) return { valid: false, error: 'Operator is required' };
      return { valid: true };
    }
  },
  
  'math': {
    category: BlockCategories.MATH,
    name: 'Math Operation',
    description: 'Mathematical operation',
    icon: Cpu,
    color: C.gold,
    inputs: [StreamTypes.NUMBER, StreamTypes.NUMBER],
    outputs: [StreamTypes.NUMBER],
    parameters: [
      { key: 'operation', label: 'Operation', type: 'select', options: ['add', 'subtract', 'multiply', 'divide', 'mod'], default: 'add' }
    ],
    backendType: 'operator',
    validation: (params) => {
      if (!params.operation) return { valid: false, error: 'Operation is required' };
      return { valid: true };
    }
  },
  
  'crosses': {
    category: BlockCategories.MATH,
    name: 'Crosses',
    description: 'Detect value crossing',
    icon: Activity,
    color: C.gold,
    inputs: [StreamTypes.INDICATOR, StreamTypes.NUMBER],
    outputs: [StreamTypes.BOOLEAN],
    parameters: [
      { key: 'direction', label: 'Direction', type: 'select', options: ['above', 'below', 'either'], default: 'above' }
    ],
    backendType: 'operator',
    validation: (params) => {
      if (!params.direction) return { valid: false, error: 'Direction is required' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // LOGIC BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'signal_logic': {
    category: BlockCategories.LOGIC,
    name: 'Signal Logic',
    description: 'Generate trading signal from conditions',
    icon: GitBranch,
    color: C.gold,
    inputs: [StreamTypes.BOOLEAN],
    outputs: [StreamTypes.SIGNAL],
    parameters: [
      { key: 'signal_on_true', label: 'Signal if True', type: 'select', options: ['BUY', 'SELL', 'HOLD'], default: 'BUY' },
      { key: 'signal_on_false', label: 'Signal if False', type: 'select', options: ['BUY', 'SELL', 'HOLD'], default: 'HOLD' },
      { key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1 }
    ],
    backendType: 'logic',
    validation: (params) => {
      if (params.min_confidence < 0 || params.min_confidence > 1) return { valid: false, error: 'Confidence must be between 0 and 1' };
      return { valid: true };
    }
  },
  
  'and_gate': {
    category: BlockCategories.LOGIC,
    name: 'AND Gate',
    description: 'Logical AND operation',
    icon: GitBranch,
    color: C.gold,
    inputs: [StreamTypes.BOOLEAN, StreamTypes.BOOLEAN],
    outputs: [StreamTypes.BOOLEAN],
    parameters: [],
    backendType: 'logic',
    validation: () => ({ valid: true })
  },
  
  'or_gate': {
    category: BlockCategories.LOGIC,
    name: 'OR Gate',
    description: 'Logical OR operation',
    icon: GitBranch,
    color: C.gold,
    inputs: [StreamTypes.BOOLEAN, StreamTypes.BOOLEAN],
    outputs: [StreamTypes.BOOLEAN],
    parameters: [],
    backendType: 'logic',
    validation: () => ({ valid: true })
  },
  
  'condition_builder': {
    category: BlockCategories.LOGIC,
    name: 'Condition Builder',
    description: 'Build complex conditions',
    icon: Settings,
    color: C.gold,
    inputs: [StreamTypes.INDICATOR, StreamTypes.NUMBER],
    outputs: [StreamTypes.BOOLEAN],
    parameters: [
      { key: 'left_indicator', label: 'Left Indicator', type: 'select', options: ['RSI', 'SMA', 'EMA', 'MACD', 'Close'], default: 'RSI' },
      { key: 'operator', label: 'Operator', type: 'select', options: ['>', '<', '>=', '<=', '==', 'crosses_above', 'crosses_below'], default: '>' },
      { key: 'right_type', label: 'Right Type', type: 'select', options: ['indicator', 'constant'], default: 'constant' },
      { key: 'right_indicator', label: 'Right Indicator', type: 'select', options: ['RSI', 'SMA', 'EMA', 'MACD', 'Close'], default: 'SMA' },
      { key: 'right_value', label: 'Right Value', type: 'number', default: 70 }
    ],
    backendType: 'logic',
    validation: (params) => {
      if (params.right_type === 'indicator' && !params.right_indicator) return { valid: false, error: 'Right indicator required when type is indicator' };
      if (params.right_type === 'constant' && params.right_value === null) return { valid: false, error: 'Right value required when type is constant' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // ML BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'xgboost': {
    category: BlockCategories.ML,
    name: 'XGBoost',
    description: 'XGBoost gradient boosting model',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 },
      { key: 'lookback', label: 'Lookback Windows', type: 'number', default: 50, min: 10, max: 500 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      if (params.confidence_threshold < 0 || params.confidence_threshold > 1) return { valid: false, error: 'Confidence must be between 0 and 1' };
      return { valid: true };
    }
  },
  
  'lightgbm': {
    category: BlockCategories.ML,
    name: 'LightGBM',
    description: 'LightGBM gradient boosting model',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      return { valid: true };
    }
  },
  
  'random_forest': {
    category: BlockCategories.ML,
    name: 'Random Forest',
    description: 'Random Forest ensemble model',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // DL BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'lstm': {
    category: BlockCategories.DL,
    name: 'LSTM',
    description: 'Long Short-Term Memory network',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 },
      { key: 'sequence_length', label: 'Sequence Length', type: 'number', default: 60, min: 10, max: 500 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      return { valid: true };
    }
  },
  
  'gru': {
    category: BlockCategories.DL,
    name: 'GRU',
    description: 'Gated Recurrent Unit network',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      return { valid: true };
    }
  },
  
  'transformer': {
    category: BlockCategories.DL,
    name: 'Transformer',
    description: 'Transformer attention model',
    icon: Brain,
    color: C.purple,
    inputs: [StreamTypes.FEATURE],
    outputs: [StreamTypes.PREDICTION],
    parameters: [
      { key: 'model_id', label: 'Model ID', type: 'text', default: '' },
      { key: 'confidence_threshold', label: 'Confidence Threshold', type: 'number', default: 0.7, min: 0, max: 1 }
    ],
    backendType: 'mlmodel',
    validation: (params) => {
      if (!params.model_id) return { valid: false, error: 'Model ID is required' };
      return { valid: true };
    }
  },

  // ══════════════════════════════════════════════════════════════════════════
  // ACTION BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  'buy_market': {
    category: BlockCategories.ACTION,
    name: 'Buy Market',
    description: 'Execute market buy order',
    icon: Check,
    color: C.green,
    inputs: [StreamTypes.SIGNAL],
    outputs: [StreamTypes.TRADING_INTENT],
    parameters: [
      { key: 'size_pct', label: 'Size %', type: 'number', default: 10, min: 0.1, max: 100 },
      { key: 'order_type', label: 'Order Type', type: 'select', options: ['MARKET', 'LIMIT'], default: 'MARKET' }
    ],
    backendType: 'action',
    validation: (params) => {
      if (params.size_pct < 0.1 || params.size_pct > 100) return { valid: false, error: 'Size must be between 0.1 and 100' };
      return { valid: true };
    }
  },
  
  'sell_market': {
    category: BlockCategories.ACTION,
    name: 'Sell Market',
    description: 'Execute market sell order',
    icon: Check,
    color: C.red,
    inputs: [StreamTypes.SIGNAL],
    outputs: [StreamTypes.TRADING_INTENT],
    parameters: [
      { key: 'size_pct', label: 'Size %', type: 'number', default: 10, min: 0.1, max: 100 },
      { key: 'order_type', label: 'Order Type', type: 'select', options: ['MARKET', 'LIMIT'], default: 'MARKET' }
    ],
    backendType: 'action',
    validation: (params) => {
      if (params.size_pct < 0.1 || params.size_pct > 100) return { valid: false, error: 'Size must be between 0.1 and 100' };
      return { valid: true };
    }
  },
  
  'close_position': {
    category: BlockCategories.ACTION,
    name: 'Close Position',
    description: 'Close current position',
    icon: Target,
    color: C.warning,
    inputs: [StreamTypes.SIGNAL],
    outputs: [StreamTypes.TRADING_INTENT],
    parameters: [
      { key: 'side', label: 'Side', type: 'select', options: ['LONG', 'SHORT', 'BOTH'], default: 'BOTH' }
    ],
    backendType: 'action',
    validation: () => ({ valid: true })
  },
  
  'trailing_stop': {
    category: BlockCategories.ACTION,
    name: 'Trailing Stop',
    description: 'Set trailing stop loss',
    icon: AlertTriangle,
    color: C.warning,
    inputs: [StreamTypes.TRADING_INTENT],
    outputs: [StreamTypes.TRADING_INTENT],
    parameters: [
      { key: 'trail_pct', label: 'Trail %', type: 'number', default: 2, min: 0.1, max: 20 }
    ],
    backendType: 'action',
    validation: (params) => {
      if (params.trail_pct < 0.1 || params.trail_pct > 20) return { valid: false, error: 'Trail must be between 0.1 and 20' };
      return { valid: true };
    }
  }
};

// Helper functions
export const getBlockByType = (type) => BlockRegistry[type];

export const getBlocksByCategory = (category) => {
  return Object.entries(BlockRegistry)
    .filter(([_, block]) => block.category === category)
    .map(([type, block]) => ({ type, ...block }));
};

export const getAllBlocks = () => {
  return Object.entries(BlockRegistry).map(([type, block]) => ({ type, ...block }));
};

export const validateConnection = (sourceType, targetType, sourceOutput, targetInput) => {
  // PHASE C: Strongly typed connections
  // Validate that source output stream type matches target input stream type
  const sourceBlock = getBlockByType(sourceType);
  const targetBlock = getBlockByType(targetType);
  
  if (!sourceBlock || !targetBlock) {
    return { valid: false, error: 'Invalid block type' };
  }
  
  const sourceOutputs = sourceBlock.outputs;
  const targetInputs = targetBlock.inputs;
  
  // If target has no inputs, reject
  if (targetInputs.length === 0) {
    return { valid: false, error: 'Target block has no inputs' };
  }
  
  // If source has no outputs, reject
  if (sourceOutputs.length === 0) {
    return { valid: false, error: 'Source block has no outputs' };
  }
  
  // Check if source output type is compatible with target input type
  const outputType = sourceOutputs[0];
  const inputType = targetInputs[0];
  
  // Allow NUMBER input to accept INDICATOR output
  if (inputType === StreamTypes.NUMBER && outputType === StreamTypes.INDICATOR) {
    return { valid: true };
  }
  
  // Allow FEATURE input to accept INDICATOR output
  if (inputType === StreamTypes.FEATURE && outputType === StreamTypes.INDICATOR) {
    return { valid: true };
  }
  
  // Otherwise, types must match
  if (outputType !== inputType) {
    return { valid: false, error: `Type mismatch: ${outputType} cannot connect to ${inputType}` };
  }
  
  return { valid: true };
};

export const getCategoryIcon = (category) => {
  const icons = {
    [BlockCategories.DATA]: Database,
    [BlockCategories.INDICATORS]: Activity,
    [BlockCategories.FEATURE_ENGINEERING]: Cpu,
    [BlockCategories.MATH]: GitBranch,
    [BlockCategories.LOGIC]: Settings,
    [BlockCategories.ML]: Brain,
    [BlockCategories.DL]: Brain,
    [BlockCategories.ACTION]: Check
  };
  return icons[category] || Activity;
};

export const getCategoryColor = (category) => {
  const colors = {
    [BlockCategories.DATA]: C.t2,
    [BlockCategories.INDICATORS]: C.accent,
    [BlockCategories.FEATURE_ENGINEERING]: C.cyan,
    [BlockCategories.MATH]: C.gold,
    [BlockCategories.LOGIC]: C.gold,
    [BlockCategories.ML]: C.purple,
    [BlockCategories.DL]: C.purple,
    [BlockCategories.ACTION]: C.green
  };
  return colors[category] || C.t2;
};