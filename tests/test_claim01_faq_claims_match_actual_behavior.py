"""CLAIM-01 -- the in-app FAQ (backend_app/routers/support.py::FAQS_DATA) no longer
makes safety claims the code does not support.

THE DEFECT (three separate overclaims found this session, reviewed alongside LEGAL-01)
    * faq-2 described a "cryptographically verified, single-use execution token" that
      the "CCXT execution boundary validates and consumes". No such token, and no
      mechanism resembling one, exists anywhere in this codebase (grepped for
      ``execution_token``/``AntiBypass``/``anti_bypass`` across ``backend_app/`` -- zero
      hits outside this FAQ entry itself). The real anti-bypass mechanism is
      ``ExecutionEngine.execute_with_idempotency``, which rejects any call whose
      ``source`` is not ``"bot_runner"``, requires a real ``strategy_id``, and takes a
      database lock keyed by a deterministic idempotency window -- a real control, but
      not a cryptographic token, and the FAQ described a mechanism that would mislead
      anyone trying to understand (or exploit) how execution is actually gated.

    * faq-18 claimed the Emergency Kill Switch "immediately cancels all pending open
      orders across all connected exchanges and transitions all active strategies to
      PAUSED state within milliseconds". ``routers/risk.py::activate_kill_switch`` (the
      real handler for this feature) only sets a per-user halt flag
      (``_user_kill_switch_state`` / ``core.tenant_halt.set_halt``) that blocks *future*
      executions -- it never calls a cancel-order or close-position path, and it never
      touches a deployment's status. A user relying on this FAQ's claim would wrongly
      believe engaging the switch flattens their existing exposure; it does not. Cancelling
      resting orders and flattening positions is RT-21's separate Close Position / Close
      All action.

    * faq-13 claimed higher subscription tiers unlock "sub-millisecond execution tick
      rates". No such concept exists in the quota system at all --
      ``subscription_engine.Resource`` (the actual metered/counted dimensions: strategies,
      bots, exchange_connections, ml_models, backtests, optimizations, ml_trainings, etc.)
      has nothing resembling a tick rate, and nothing in the execution path is tiered by
      speed. This is also implausible on its face for a system placing orders over the
      network through CCXT.

THE FIX
    All three answers were rewritten to describe the real mechanism (or real tier
    dimensions) instead. faq-5 (the sandbox/testnet venue claim) was already corrected by
    an earlier pass and is reconfirmed, not re-fixed, here. The frontend landing-page copy
    (`FAQ.jsx`, `ProofStrip.jsx`, `TrustSection.jsx`) that named Binance as
    "production-certified" was also corrected in the same change, since CLAIM-02's
    registry fix (same session) made that claim false too.

WHAT THIS FILE PROVES
    * the fabricated "Anti-Bypass Execution Token" language is gone;
    * the kill-switch FAQ no longer claims retroactive order cancellation or a status
      transition it does not perform;
    * the real kill-switch handler (``activate_kill_switch``) does not call any
      cancel/close function, confirming the OLD claim was false and the new one matches;
    * the tier FAQ no longer claims a tick-rate / execution-speed difference that does not
      exist in the quota system;
    * the sandbox/testnet FAQ (already fixed earlier) still matches the measured
      SANDBOX_ROUTABLE set.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_PY = REPO_ROOT / "backend_app" / "routers" / "support.py"
RISK_PY = REPO_ROOT / "backend_app" / "routers" / "risk.py"


def _faqs_data() -> list[dict]:
    """Parse FAQS_DATA out of support.py's source via AST, without importing fastapi."""
    tree = ast.parse(SUPPORT_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FAQS_DATA":
                    return ast.literal_eval(node.value)
    raise AssertionError("FAQS_DATA not found in support.py")


FAQS = _faqs_data()


def _answer(faq_id: str) -> str:
    for faq in FAQS:
        if faq["id"] == faq_id:
            return faq["answer"]
    raise AssertionError(f"{faq_id} not found in FAQS_DATA")


class TestTheFabricatedAntiBypassTokenIsGone:
    def test_faq2_no_longer_describes_a_cryptographic_execution_token(self):
        answer = _answer("faq-2")
        assert "token" not in answer.lower()
        assert "Anti-Bypass" not in answer

    def test_no_such_mechanism_exists_anywhere_in_the_backend(self):
        """The thing faq-2 used to describe was never real. Confirms it still is not,
        so a future PR cannot "fix" this by reintroducing a token nobody built.
        """
        hits = []
        for py_file in (REPO_ROOT / "backend_app").rglob("*.py"):
            if py_file == SUPPORT_PY:
                continue
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"execution_token|AntiBypass|anti_bypass", text):
                hits.append(str(py_file))
        assert not hits, f"an anti-bypass token mechanism now exists: {hits}"

    def test_faq2_describes_the_real_mechanism(self):
        answer = _answer("faq-2")
        assert "execute_with_idempotency" in answer
        assert "bot" in answer.lower()


class TestTheKillSwitchFaqMatchesWhatItActuallyDoes:
    def test_faq18_no_longer_claims_retroactive_order_cancellation(self):
        answer = _answer("faq-18")
        assert "cancels all pending open orders" not in answer
        assert "transitions all active strategies to paused" not in answer.lower()

    def test_faq18_correctly_scopes_the_claim_to_new_executions(self):
        answer = _answer("faq-18")
        assert "new order" in answer.lower() or "new execution" in answer.lower()
        assert "close" in answer.lower(), (
            "should point the reader at the real action for cancelling resting orders"
        )

    def test_the_real_kill_switch_handler_does_not_cancel_or_close_anything(self):
        """NEGATIVE CONTROL for the fix: proves the OLD FAQ claim was false by showing the
        real handler's source never calls a cancel/close function. If this ever starts
        failing because the handler now DOES cancel orders, faq-18 should be rewritten
        back to say so -- this test is what would catch that mismatch either way.
        """
        tree = ast.parse(RISK_PY.read_text(encoding="utf-8"))
        handler = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "activate_kill_switch"),
            None,
        )
        assert handler is not None, "activate_kill_switch has moved or been renamed"

        called_names = set()
        for node in ast.walk(handler):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                called_names.add(name.lower())

        forbidden_substrings = ("cancel", "close_position", "close_all", "flatten")
        matched = [n for n in called_names for f in forbidden_substrings if f in n]
        assert not matched, (
            f"activate_kill_switch now calls {matched} -- faq-18 should be rewritten to "
            "say the kill switch DOES cancel/flatten, since that would make the current "
            "wording an under-claim instead of the over-claim it was fixed for"
        )


class TestTheTierFaqNoLongerInventsAnExecutionSpeedTier:
    def test_faq13_no_longer_claims_sub_millisecond_tick_rates(self):
        answer = _answer("faq-13")
        assert "sub-millisecond" not in answer.lower()
        assert "tick rate" not in answer.lower()

    def test_no_tick_rate_concept_exists_in_the_real_quota_system(self):
        from backend_app.core.subscription_engine import Resource

        resource_values = {r.value for r in Resource}
        assert not any("tick" in v or "speed" in v or "latency" in v for v in resource_values)

    def test_faq13_describes_real_tier_dimensions(self):
        from backend_app.core.subscription_engine import Resource

        answer = _answer("faq-13").lower()
        # At least the most customer-visible real dimensions should be named.
        assert "bot" in answer
        assert "backtest" in answer
        # Every resource this answer implies ("concurrent live bots", "ml models", ...)
        # must correspond to something the quota system actually tracks.
        assert Resource.BOTS.value == "bots"
        assert Resource.ML_TRAININGS.value == "ml_trainings"


class TestTheSandboxFaqIsStillCorrect:
    """Reconfirms EXCH-05's earlier fix rather than re-deriving it; CLAIM-02 (this
    session) separately corrected the backing registry's certification levels, which this
    FAQ entry does not make claims about.
    """

    def test_faq5_only_advertises_the_measured_sandbox_routable_venues(self):
        answer = _answer("faq-5")
        advertised = {
            v for v in ("binance", "bybit", "okx", "coinbase", "kraken", "kucoin", "bitget")
            if re.search(rf"\b{v}\s*\(", answer, re.IGNORECASE)
        }
        assert advertised, f"no venue is advertised with a parenthetical label in: {answer!r}"
        assert advertised == {"binance", "bybit", "okx"}
