# DAG COMPILER REPAIR PLAN

## Overview
This document serves as the master index for the backend DAG compiler audit and repair strategy. The goal of this repair is to produce the minimum backend patch required to successfully execute the following target DAG:

```
INPUT(BTCUSDT) → RSI(period=14) → GT(threshold=70) → SELL(amount=0.1)
```

## Audit Findings & Strategy
Through a rigorous audit of the `pydantic_models.py` schema, `dag_engine.py` execution engine, and `strategies.py` compiler, two critical blockers were identified. I have generated specific reports and design reviews addressing each component.

### 1. The Enum Mismatch
The API contract uses the `"input"` string to define market data nodes, but the backend compiler validates connectivity using the string `"market_data"`. This instantly crashes any graph submission.
**Details:** See `ENUM_MISMATCH_REPORT.md`

### 2. The Logic Node Threshold Blocker
The compiler evaluates mathematical operators (like `>`) by comparing two Pandas Series. However, there is no node type that allows users to pipe a static threshold (like `70`) into the graph.
**Details:** See `LOGIC_EXECUTION_AUDIT.md`

### 3. Architecture Resolution
To resolve the threshold issue without breaking the Pydantic API contract or introducing unnecessary node types into the DAG schema, a design decision was made to embed the threshold parameter directly inside the Logic node itself.
**Details:** See `THRESHOLD_NODE_DESIGN_REVIEW.md`

### 4. The Execution Plan
I have identified the exact, minimal code changes needed in the backend Python files to implement these fixes without refactoring the engine. The patch is extremely lightweight (one deleted class and a 4-line modification).
**Details:** See `COMPILER_PATCH_PLAN.md`
