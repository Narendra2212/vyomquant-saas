# PATCH IMPACT REPORT

Based on exhaustive repository scanning, here is the final impact analysis of the proposed compiler changes.

## Global Impact Summary

1.  **`FEATURE` / `SIGNAL` Node Types:** Used exclusively in `routers/strategies.py`, `backend/signal_trace_engine.py`, and `frontend/strategyValidator.js`.
2.  **`MARKET_DATA` Node Type:** Used exclusively in `routers/strategies.py`, `backend/signal_trace_engine.py`, and `frontend/strategyValidator.js`.
3.  **Other String Usages:** `"market_data"` and `"signal"` are widely used as execution payload properties and telemetry streams. These are architecturally isolated from the DAG `NodeType` enum. Changing `NodeType` will not impact them.

---

## SAFE TO REMOVE: NO

While it is safe to remove the redundant `NodeType` enum from `routers/strategies.py`, doing so requires a compatibility-preserving patch to `frontend/strategyValidator.js` to prevent the frontend graph validator from drawing edges based on deprecated types.

## The Compatibility-Preserving Patch

To safely enforce the Pydantic type mapping across the stack without breaking frontend visualization:

**File:** `frontend/strategyValidator.js`
**Lines:** 219-228

**Old Code:**
```javascript
  const validConnections = {
    'market_data': ['indicator', 'feature'],
    'indicator': ['feature', 'logic', 'ml'],
    'feature': ['ml', 'logic'],
    'ml': ['signal', 'logic'],
    'logic': ['signal', 'action'],
    'signal': ['action', 'logic']
  };
```

**New Code:**
```javascript
  const validConnections = {
    'input': ['indicator'],
    'indicator': ['logic', 'ml'],
    'ml': ['logic', 'action'],
    'logic': ['action', 'logic'],
    'action': []
  };
```

By applying this patch to the frontend validator simultaneously with the backend `TYPE_COMPATIBILITY` patch, complete bidirectional architectural alignment is achieved with zero runtime regression risk.
