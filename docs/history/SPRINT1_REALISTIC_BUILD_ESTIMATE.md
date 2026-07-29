# SPRINT 1 REALISTIC BUILD ESTIMATE

## Overview
Achieving a **truly functional** DAG Builder requires significantly more engineering than simply wiring up the API buttons. The frontend is currently a static visual mockup and lacks the configuration engine, logic nodes, and schema translation required to drive the backend.

## Epic 1: Node Configuration Engine (High Effort)
**Goal:** Allow users to set specific parameters (e.g., RSI period, Model IDs) on nodes.
*   **Tasks:**
    *   Build a dynamic "Node Properties" sidebar that updates based on the selected node type.
    *   Build custom ReactFlow node components to visually display selected parameters on the canvas.
    *   Synchronize local React state with the ReactFlow `node.data` object.
*   **Estimate:** 2-3 Days

## Epic 2: Logic & Math Nodes (Medium Effort)
**Goal:** Bridge the gap between continuous indicators and discrete execution signals.
*   **Tasks:**
    *   Add Logic Categories (AND, OR, NOT) to the drag-and-drop palette.
    *   Add Comparison Categories (GT, LT, EQ) and constant float scalar inputs to the palette.
    *   Implement connection rules (e.g., preventing an Action node from connecting directly to an Indicator node without passing through a Logic node).
*   **Estimate:** 1-2 Days

## Epic 3: Schema Translation Layer (Medium Effort)
**Goal:** Convert ReactFlow state into backend `DAGConfig` and `StrategyBlueprint` schemas.
*   **Tasks:**
    *   Write a serializer that loops through ReactFlow `nodes` and `edges`, mapping them to the strict Pydantic models.
    *   Implement frontend-side topological validation (preventing cycles, ensuring disconnected nodes are flagged) before hitting the `/validate` endpoint.
*   **Estimate:** 1 Day

## Epic 4: API Wiring & Lifecycle Management (Medium Effort)
**Goal:** Hook up the builder to the hardened Sprint 0 API routes.
*   **Tasks:**
    *   Wire Save (`POST /api/strategies`) and Load (`GET /api/strategies/{id}`).
    *   Wire the "Validate" button to hit `POST /api/strategies/validate` and render real API errors on the canvas.
    *   Wire the Backtest/Simulation workflow to hit `POST /api/strategies/backtest`.
    *   Refactor `EventDagRunner.jsx` to process the synchronous `BacktestResponse` instead of listening to the non-existent websocket endpoint.
    *   Wire the Deploy workflow.
*   **Estimate:** 2 Days

## Total Realistic Estimate
**6 to 8 Engineering Days** to build a fully functional, production-ready DAG Strategy Builder. This accounts for building the missing form state management and logic blocks required for mathematical execution.
