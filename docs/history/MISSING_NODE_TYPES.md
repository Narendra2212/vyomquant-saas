# MISSING NODE TYPES IN FRONTEND

To make the DAG Builder functional and compatible with the backend `dag_engine.py`, the following node types MUST be added to the frontend `NODE_CATEGORIES` palette.

## 1. Logic Operators (Combinatorial)
These nodes take two boolean signals and combine them.
*   **AND**: Both inputs must be true.
*   **OR**: At least one input must be true.

## 2. Logic Operators (Inversion)
*   **NOT**: Inverts a boolean signal.

## 3. Logic Operators (Comparison)
These are **CRITICAL**. The backend indicators output continuous float values (e.g., an RSI of 75.4). Action nodes require boolean signals (1 or 0). Without comparison nodes, the DAG is broken.
*   **GT (Greater Than)**: Compares an indicator to a threshold or another indicator.
*   **LT (Less Than)**: Compares an indicator to a threshold or another indicator.
*   **GTE (Greater Than or Equal)**
*   **LTE (Less Than or Equal)**
*   **EQ (Equal)**

## 4. Scalar Value Nodes / Constants
To use a comparison node (like GT), the user needs to provide a threshold (e.g., RSI > **70**). 
*   **Constant Float**: A node that outputs a static number (e.g., 70.0). Alternatively, this can be embedded as a property/parameter inside the Logic node itself.

## Summary of Palette Updates Required
The `NODE_CATEGORIES` in `StrategyBuilder.jsx` must be updated to include a `'Logic'` category containing:
`['AND', 'OR', 'NOT', 'Greater Than (GT)', 'Less Than (LT)', 'Equal (EQ)']`
