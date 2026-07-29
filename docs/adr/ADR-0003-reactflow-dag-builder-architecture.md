# ADR-0003: ReactFlow DAG Builder Architecture

## Status
Accepted

## Context
The VyomQuant platform's defining feature is visual strategy building. The initial implementation utilized a static UI mockup (`StrategyBuilder.jsx`) that could not dynamically connect nodes, export a valid directed acyclic graph (DAG) structure, or map UI properties to the backend's `DAGConfig` / `pydantic` models.

## Decision
We adopted `ReactFlow` as the core engine for the visual DAG builder on the frontend. We explicitly built a translation layer (`dagSerializer.js`) to decouple the frontend's visual state (X/Y coordinates, node colors) from the mathematical and logical payload required by the backend compiler.

## Consequences
- **Positive**: ReactFlow provides a robust, heavily supported framework for node-graph interfaces, drastically reducing frontend complexity.
- **Positive**: The serializer strictly enforces the payload schema, ensuring the backend receives clean, typed data, preventing `AttributeError` exceptions during backtest compilations.
- **Negative**: ReactFlow's state management must be synchronized tightly with the application's overall state management, which adds complexity to `StrategyBuilder.jsx`.
