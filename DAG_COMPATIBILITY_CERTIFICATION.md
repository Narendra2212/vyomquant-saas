# DAG COMPATIBILITY CERTIFICATION

## Objective
Verify if the frontend ReactFlow DAG can be cleanly mapped to the backend `DAGCompiler` schema.

## Certification Status: 🔴 BROKEN

### A. Exact Backend DAG Schema
The backend `DAGNode` Pydantic schema expects:
- `id` (str)
- `type` (NodeType: `indicator`, `ml`, `logic`, `action`, `input`)
- Specific configuration fields based on type (e.g., `indicator` string + `params` dict, `operator` string, `action` string).

### B. Exact ReactFlow Node Schema
The frontend produces a strictly visual schema:
- `id` (str)
- `position` (dict)
- `data` (dict with `label` only, e.g., `{"label": "RSI"}`)
- `type` (implicitly default, or `input`/`output` for visual styling)

### C. Required Backend Node Types
1. **Input**: Provides market data.
2. **Indicator**: Calculates math on market data.
3. **Logic**: Converts continuous indicators into discrete boolean signals (e.g., RSI > 70).
4. **ML**: Calculates ML predictions.
5. **Action**: Takes a boolean signal and executes trades.

### D. Node Types Existing in Frontend
The frontend palette (`NODE_CATEGORIES`) provides categories that visually represent:
- Input ("Exchange")
- Indicator ("Indicators")
- ML ("ML Models")
- Action ("Execution")
*(Note: "Risk" and "Portfolio" exist in the frontend but do not map to the backend `DAGNode` schema, which handles risk globally in `RiskParameters`.)*

### E. Node Types Missing in Frontend
- **Logic Nodes**: The frontend has absolutely no logic nodes (AND, OR, NOT, GT, LT). 

### F. Required Parameters
- **Do ReactFlow nodes contain required parameters?** NO.
- The frontend nodes are completely static text blocks. There is no configuration panel, settings modal, or properties sidebar to configure essential values like:
  - RSI `period`
  - MACD `fast`/`slow`/`signal`
  - Logic `operator` (GT, LT)
  - Action `amount` or `order_type`
  - ML `model_id`

### G. Can a valid DAG be created today?
**NO.**
Even if we wrote a translation layer right now, a valid DAG is mathematically impossible to construct in the UI. 
The backend requires an indicator's output (float) to pass through a **Logic node** (comparison) to generate a boolean signal (1 or 0), which is then passed to an **Action node**. Because the frontend lacks both Logic nodes and the ability to set comparison thresholds (parameters), no valid execution graph can be formed.
