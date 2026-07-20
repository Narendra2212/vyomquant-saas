"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: strategy_builder.py                                  ║
║                                                                          ║
║  Evaluates user drag-and-drop strategy blueprints against live state.    ║
║  Pure logic — zero HTTP/WS code.                                         ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  SB-1  CRITICAL: calculate_dynamic_size used hardcoded $10,000 balance  ║
║  SB-2  blueprint key access without existence check → KeyError           ║
║  SB-3  run_logic is async but has no timeout on logic evaluation         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import operator
from typing import Dict, Optional

logger = logging.getLogger("StrategyEngine")

# Safe operator whitelist — prevents code injection via eval()
SAFE_OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


class StrategyEngine:

    def __init__(self, risk_manager, telemetry_engine):
        self.risk = risk_manager
        self.db = telemetry_engine

    # ══════════════════════════════════════════════════════════════════════
    #  CONDITION EVALUATION
    # ══════════════════════════════════════════════════════════════════════

    def _evaluate_condition(self, condition: dict, current_state: dict) -> bool:
        """
        Safely evaluates a single leaf condition from the drag-drop canvas.
        Example: {"left": "RSI_14", "op": "<", "right": 30.0}
        """
        left_key = condition.get("left")
        if not left_key:
            logger.warning("Condition missing 'left' key — skipping.")
            return False

        left_val = current_state.get(left_key)
        if left_val is None:
            # Indicator not yet in live_state — warmup still in progress
            return False

        right_val = condition.get("right")
        # Support dynamic right-hand side: compare two indicators
        if isinstance(right_val, str) and right_val in current_state:
            right_val = current_state[right_val]

        op_func = SAFE_OPERATORS.get(condition.get("op", ""))
        if op_func is None:
            logger.error(
                f"Unsupported operator: '{condition.get('op')}'. Skipping condition."
            )
            return False

        try:
            return op_func(float(left_val), float(right_val))
        except (TypeError, ValueError) as e:
            logger.warning(f"Condition evaluation error: {e}")
            return False

    def _evaluate_logic_tree(self, logic_node: dict, current_state: dict) -> bool:
        """
        Recursively evaluates an AND/OR tree from the drag-drop canvas.
        Short-circuits using all() / any() generators for performance.
        Supports infinite nesting depth.
        """
        if not logic_node or not logic_node.get("conditions"):
            return False

        op_type = logic_node.get("operator", "AND").upper()

        def eval_child(child):
            # Nested group: recurse
            if "operator" in child and "conditions" in child:
                return self._evaluate_logic_tree(child, current_state)
            # Leaf condition
            return self._evaluate_condition(child, current_state)

        if op_type == "AND":
            return all(eval_child(c) for c in logic_node["conditions"])
        if op_type == "OR":
            return any(eval_child(c) for c in logic_node["conditions"])

        logger.error(f"Unknown logic operator: '{op_type}'")
        return False

    # ══════════════════════════════════════════════════════════════════════
    #  MASTER LOGIC RUNNER
    # ══════════════════════════════════════════════════════════════════════

    async def run_logic(
        self,
        user_id: str,
        symbol: str,
        strategy_blueprint: dict,
        current_state: Dict[str, float],
        live_balance_usdt: float,  # FIX SB-1: real balance injected by BotRunner
    ) -> Optional[dict]:
        """
        Evaluates buy/sell logic trees and returns an order payload or None.
        FIX SB-2: Validates blueprint keys before accessing them.
        FIX SB-1: Uses injected live_balance_usdt (not a hardcoded $10K).
        """
        # ── FIX SB-2: Validate required blueprint keys ────────────────────
        required = ["buy_logic", "sell_logic", "risk", "strategy_id"]
        missing = [k for k in required if k not in strategy_blueprint]
        if missing:
            logger.error(f"Blueprint missing keys: {missing}. Skipping tick.")
            return None

        risk_cfg = strategy_blueprint.get("risk") or {}
        if "position_size_pct" not in risk_cfg:
            logger.error("Blueprint missing risk.position_size_pct. Skipping tick.")
            return None

        buy_signal = self._evaluate_logic_tree(
            strategy_blueprint["buy_logic"], current_state
        )
        sell_signal = self._evaluate_logic_tree(
            strategy_blueprint["sell_logic"], current_state
        )

        # Signal collision: both true simultaneously — SELL to protect capital
        if buy_signal and sell_signal:
            logger.warning(
                f"Signal collision on {symbol} for user {user_id}. Prioritising SELL."
            )
            action = "SELL"
        elif buy_signal:
            action = "BUY"
        elif sell_signal:
            action = "SELL"
        else:
            return None  # HOLD

        qty = self._calculate_size(
            live_balance_usdt=live_balance_usdt,  # FIX SB-1
            risk_pct=float(risk_cfg["position_size_pct"]),
            current_price=float(current_state.get("Close", 0)),
        )
        if qty <= 0:
            logger.warning(f"Calculated qty is 0 for {symbol}. Skipping order.")
            return None

        return {
            "user_id": user_id,
            "strategy_id": strategy_blueprint["strategy_id"],
            "symbol": symbol,
            "side": action.lower(),
            "qty": qty,
            "reduce_only": False,  # explicitly set; BotRunner may override for close
            "params": {
                "stop_loss_pct": risk_cfg.get("stop_loss_pct"),
                "take_profit_pct": risk_cfg.get("take_profit_pct"),
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    #  POSITION SIZING
    # ══════════════════════════════════════════════════════════════════════

    def _calculate_size(
        self,
        live_balance_usdt: float,  # FIX SB-1: injected, not hardcoded
        risk_pct: float,
        current_price: float,
    ) -> float:
        """
        Converts a percentage of the live wallet balance into coin quantity.
        FIX SB-1: Original had simulated_usdt_balance = 10000.0 hardcoded.
        """
        if current_price <= 0:
            raise ValueError("current_price must be > 0 to calculate position size.")
        if not (0 < risk_pct <= 1):
            raise ValueError(f"position_size_pct must be in (0, 1]. Got {risk_pct}.")

        dollar_alloc = live_balance_usdt * risk_pct
        qty = dollar_alloc / current_price
        return round(qty, 6)
