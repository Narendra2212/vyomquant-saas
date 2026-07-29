# EDGE COMPATIBILITY MATRIX

This matrix defines the exact allowed edge connections mapped from `TYPE_COMPATIBILITY` in `routers/strategies.py`.

> [!WARNING]
> The backend defines `TYPE_COMPATIBILITY` using an Enum (`NodeType.MARKET_DATA`, etc.) that contradicts the `VALID_NODE_TYPES` (`input`, etc.). This matrix translates the *intended* architecture, but note that the backend will currently crash on validation due to this string mismatch.

| Source Node Type | Allowed Target Node Types |
| :--- | :--- |
| **`input`** *(market_data)* | `indicator`, `ml` *(feature)* |
| **`indicator`** | `ml` *(feature)*, `logic`, `ml` |
| **`ml`** | `logic`, `action` *(signal)* |
| **`logic`** | `logic`, `action` *(signal)* |
| **`action`** | *(Terminal node - no outgoing edges allowed)* |

### Backend Dictionary Equivalent
This is how the backend attempts to enforce it (with the mismatched string values in parentheses):

*   `input` (market_data) → `indicator`, `feature`
*   `indicator` → `feature`, `logic`, `ml`
*   `feature` → `ml`, `logic`
*   `ml` → `signal`, `logic`
*   `logic` → `signal`, `action`
*   `signal` → `action`, `logic`
*   `action` → `[]` (None)
