export const getMinDataLength = (indicator, params = {}) => {
  const minLengths = {
    rsi: (params.period || 14) + 1,
    macd: Math.max(params.fast || 12, params.slow || 26, params.signal || 9) + 1,
    ema: (params.period || 14) + 1,
    sma: (params.period || 14) + 1,
    bollinger: (params.period || 20) + 1,
    bb: (params.period || 20) + 1,
    atr: (params.period || 14) + 1,
    stochastic: (params.period || 14) + 1,
    obv: 2,
    vwap: 2
  };
  return minLengths[indicator.toLowerCase()] || 10;
};

export const validateConditions = (conditions, indicatorsData) => {
  const errors = [];

  conditions.forEach((condition, index) => {
    // Check required fields
    if (!condition.type) {
      errors.push({ index, field: 'type', message: 'Condition type is required' });
    }
    if (!condition.operator) {
      errors.push({ index, field: 'operator', message: 'Operator is required' });
    }
    if (!condition.left) {
      errors.push({ index, field: 'left', message: 'Left operand is required' });
    }
    if (!condition.right) {
      errors.push({ index, field: 'right', message: 'Right operand is required' });
    }

    // Validate indicator references
    if (condition.left?.source === 'indicator' && condition.left?.name) {
      if (!indicatorsData[condition.left.name]) {
        errors.push({ index, field: 'left.name', message: `Indicator '${condition.left.name}' not found` });
      }
    }
    if (condition.right?.source === 'indicator' && condition.right?.name) {
      if (!indicatorsData[condition.right.name]) {
        errors.push({ index, field: 'right.name', message: `Indicator '${condition.right.name}' not found` });
      }
    }

    // Validate crossover has enough data points
    if (condition.type === 'crossover') {
      const leftData = condition.left?.source === 'indicator' ? indicatorsData[condition.left.name] : null;
      if (leftData && leftData.length < 2) {
        errors.push({ index, field: 'left', message: 'Crossover requires at least 2 data points' });
      }
    }
  });

  return errors;
};

export const getTimeframeMs = (tf) => {
  const multipliers = { 'm': 60 * 1000, 'h': 60 * 60 * 1000, 'd': 24 * 60 * 60 * 1000 };
  const unit = tf.slice(-1);
  const value = parseInt(tf);
  return value * (multipliers[unit] || multipliers['m']);
};

export const getMaxHistoryForTimeframe = (tf) => {
  // Approximate max history available from exchanges
  const limits = { '1m': 7, '5m': 30, '15m': 90, '1h': 180, '4h': 365, '1d': 1000 };
  return limits[tf] || 30;
};
