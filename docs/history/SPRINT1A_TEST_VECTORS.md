# SPRINT 1A TEST VECTORS

## Vector 1: Standard Pipeline (Golden Path)

*   **Structure:** `INPUT → RSI → GT(70) → SELL`
*   **Payload Extract:**
    ```json
    {
      "nodes": [
        {"id": "n1", "type": "input", "symbol": "BTCUSDT"},
        {"id": "n2", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
        {"id": "n3", "type": "logic", "operator": "GT", "params": {"threshold": 70}},
        {"id": "n4", "type": "action", "action": "sell"}
      ],
      "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"}
      ]
    }
    ```
*   **Expected Validation:** PASS.
*   **Expected Execution:** SUCCESS. `LogicExecutor` reads threshold 70, outputs boolean series, triggers SELL action node.

## Vector 2: Inverse Logic Pipeline

*   **Structure:** `INPUT → RSI → LT(30) → BUY`
*   **Payload Extract:**
    ```json
    {
      "nodes": [
        {"id": "n1", "type": "input", "symbol": "BTCUSDT"},
        {"id": "n2", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
        {"id": "n3", "type": "logic", "operator": "LT", "params": {"threshold": 30}},
        {"id": "n4", "type": "action", "action": "buy"}
      ],
      "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"}
      ]
    }
    ```
*   **Expected Validation:** PASS.
*   **Expected Execution:** SUCCESS. `LogicExecutor` applies `LT` operator against static threshold `30`, triggering BUY.

## Vector 3: Invalid Type Compatibility (Orphan Logic)

*   **Structure:** `INPUT → RSI → SELL`
*   **Payload Extract:**
    ```json
    {
      "nodes": [
        {"id": "n1", "type": "input", "symbol": "BTCUSDT"},
        {"id": "n2", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
        {"id": "n3", "type": "action", "action": "sell"}
      ],
      "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"}
      ]
    }
    ```
*   **Expected Validation:** FAIL.
*   **Expected Execution:** BLOCKED. `DAGCompiler` should throw `DAGCompilationError` because `INDICATOR → ACTION` is not a permitted edge in `TYPE_COMPATIBILITY`. It must pass through `LOGIC` or `ML`.

## Vector 4: Illegal Cycle

*   **Structure:** `INPUT → RSI → GT → SELL → RSI`
*   **Payload Extract:**
    ```json
    {
      "nodes": [
        {"id": "n1", "type": "input", "symbol": "BTCUSDT"},
        {"id": "n2", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
        {"id": "n3", "type": "logic", "operator": "GT", "params": {"threshold": 70}},
        {"id": "n4", "type": "action", "action": "sell"}
      ],
      "edges": [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
        {"source": "n4", "target": "n2"}
      ]
    }
    ```
*   **Expected Validation:** FAIL.
*   **Expected Execution:** BLOCKED. `DAGCompiler` should throw `DAGCompilationError` during `_detect_cycles` execution. Additionally, `ACTION → INDICATOR` violates type compatibility.
