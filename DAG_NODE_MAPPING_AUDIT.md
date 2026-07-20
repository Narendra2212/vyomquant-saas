# DAG Node Mapping Audit

This document maps the ReactFlow node types from `algo22-terminal/src/App.jsx` to the backend `DAGNode` types in `core/models/pydantic_models.py`.

## 1. Source Node
- **Frontend ReactFlow Type**: `source`
- **Actual UI Configuration Fields**: `symbol`, `timeframe`, `start_date`, `end_date` (in `data.params`)
- **Backend DAGNode Equivalent**: `input`
- **Required Serializer Transformation**: Map `data.params.symbol` to `symbol`, `data.params.timeframe` to `timeframe`.

## 2. Indicator Node
- **Frontend ReactFlow Type**: `indicator`
- **Actual UI Configuration Fields**: `window`, `output` (in `data.params`)
- **Backend DAGNode Equivalent**: `indicator`
- **Required Serializer Transformation**: Map `data.label` (lowercase) to `indicator`, and pass `data.params` into `params`.

## 3. Operator Node
- **Frontend ReactFlow Type**: `operator`
- **Actual UI Configuration Fields**: `operation`, `gate` (in `data.params`)
- **Backend DAGNode Equivalent**: `logic`
- **Required Serializer Transformation**: Map `data.params.gate` or `data.params.operation` to `operator`.

## 4. Logic Node
- **Frontend ReactFlow Type**: `logic`
- **Actual UI Configuration Fields**: Usually just connections/gates in `data.params`
- **Backend DAGNode Equivalent**: `logic`
- **Required Serializer Transformation**: Map `data.params.gate` to `operator`.

## 5. ML Model Node
- **Frontend ReactFlow Type**: `mlmodel`
- **Actual UI Configuration Fields**: `confidence` (in `data.params`)
- **Backend DAGNode Equivalent**: `ml`
- **Required Serializer Transformation**: Map `data.label` to `model_id` (or similar), and `data.params.confidence` to `confidence_threshold`.

## 6. Action Node
- **Frontend ReactFlow Type**: `action`
- **Actual UI Configuration Fields**: `size_pct`, `order_type` (in `data.params`)
- **Backend DAGNode Equivalent**: `action`
- **Required Serializer Transformation**: Extract action from label (e.g., "Buy Market" -> "buy"), map `data.params.size_pct` to `amount` (as decimal), map `data.params.order_type` (lowercase) to `order_type`.

## 7. Unsupported Nodes
- **Frontend ReactFlow Type**: `orderbook`
  - **Reason**: There is no specific `orderbook` node type or mapping in backend `DAGNode`.
- **Frontend ReactFlow Type**: `liveticker`
  - **Reason**: `liveticker` is a UI-specific node (streaming data) that lacks a clear equivalent in the backtesting/deployment DAG model.
