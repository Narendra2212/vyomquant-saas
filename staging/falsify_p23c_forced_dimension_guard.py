"""Phase 10 / P-23C: does the extended forced-dimension guard actually BITE?

``test_the_forced_dimensions_are_pinned_not_drawn`` makes four claims. A guard that cannot
fail is worse than no guard, so each claim is driven against a deliberately broken version of
what it guards and must raise AssertionError.

The AST claims read the module's own ``__file__``, so they are falsified by pointing
``__file__`` at a mutated copy of the source - the guard then genuinely re-parses the broken
text rather than this probe re-implementing its logic.

Read-only with respect to the real test file: the mutations are made on a temporary copy and
``__file__`` is restored in a ``finally``.

Usage: python staging/falsify_p23c_forced_dimension_guard.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from typing import Any, Callable, List, Tuple

sys.path.insert(0, ".")

from tests.property import test_paper_confluence as m  # noqa: E402

GUARD: Callable[[], None] = m.test_the_forced_dimensions_are_pinned_not_drawn
REAL_FILE = pathlib.Path(m.__file__)
SOURCE = REAL_FILE.read_text(encoding="utf-8-sig")

RESULTS: List[Tuple[str, bool, str]] = []


def _run(name: str, mutate: Callable[[], Callable[[], None]], expect_failure: bool) -> None:
    restore = mutate()
    try:
        GUARD()
    except AssertionError as exc:
        ok = expect_failure
        RESULTS.append((name, ok, ("fired: " if ok else "UNEXPECTED: ")
                        + str(exc).splitlines()[0][:120]))
        return
    except Exception as exc:  # pragma: no cover
        RESULTS.append((name, False, f"wrong exception {type(exc).__name__}: {exc}"))
        return
    finally:
        restore()
    RESULTS.append((name, not expect_failure,
                    "passed" if not expect_failure else "GUARD DID NOT FIRE - vacuous"))


def source_mutation(old: str, new: str) -> Callable[[], Callable[[], None]]:
    """Point the module's ``__file__`` at a copy of its source with ``old`` -> ``new``."""
    def mutate() -> Callable[[], None]:
        assert old in SOURCE, f"mutation anchor not present in source: {old!r}"
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="p23c_falsify_")) / "mutated.py"
        tmp.write_text(SOURCE.replace(old, new, 1), encoding="utf-8")
        saved = m.__file__
        m.__file__ = str(tmp)

        def restore() -> None:
            m.__file__ = saved
        return restore
    return mutate


def attribute_mutation(target: str, value: Any) -> Callable[[], Callable[[], None]]:
    def mutate() -> Callable[[], None]:
        saved = getattr(m._ConfluenceCase, target)
        setattr(m._ConfluenceCase, target, value)

        def restore() -> None:
            setattr(m._ConfluenceCase, target, saved)
        return restore
    return mutate


def tuple_mutation(name: str, value: Any) -> Callable[[], Callable[[], None]]:
    def mutate() -> Callable[[], None]:
        saved = getattr(m, name)
        setattr(m, name, value)

        def restore() -> None:
            setattr(m, name, saved)
        return restore
    return mutate


# ── baseline ──────────────────────────────────────────────────────────────────────────────
_run("guard passes unmutated", lambda: (lambda: None), expect_failure=False)

# ── claim 1: the forced values must produce different intents ─────────────────────────────
_run(
    "with_limit2_order_type made an identity (values collapse)",
    attribute_mutation("with_limit2_order_type", lambda self, order_type: self),
    expect_failure=True,
)

# ── claim 2: every forced variant must be ONE-SIDED across all six intents ────────────────
_run(
    "with_side made an identity (variant is not the requested side)",
    attribute_mutation("with_side", lambda self, side: self),
    expect_failure=True,
)


def _two_sided_with_side(self: Any, side: str) -> Any:
    """A with_side that leaves the rejected intent on the opposite side."""
    other = "sell" if side == "buy" else "buy"
    rejected_name, overrides = self.rejected_intent
    poisoned = dict(overrides)
    poisoned["side"] = other
    out = m._ConfluenceCase(
        key_suffix=self.key_suffix, side=side, quantities=self.quantities,
        limit_prices=self.limit_prices, close=self.close, fee_rate=self.fee_rate,
        slippage_rate=self.slippage_rate, rejected_intent=(rejected_name, poisoned),
        limit2_order_type=self.limit2_order_type, switch_at=self.switch_at,
        nested_switch_at=self.nested_switch_at,
    )
    return out


_run(
    "a variant that is two-sided (oracle premise broken)",
    attribute_mutation("with_side", _two_sided_with_side),
    expect_failure=True,
)

# ── claim 1/2 support: the dimension tuples themselves ────────────────────────────────────
_run("SIDES reduced to one value", tuple_mutation("SIDES", ("buy",)), expect_failure=True)
_run("LIMIT2_ORDER_TYPES reduced to one value",
     tuple_mutation("LIMIT2_ORDER_TYPES", ("limit",)), expect_failure=True)

# ── claim 3: the body must force both dimensions and read neither generated field ─────────
_run(
    "body no longer iterates SIDES",
    source_mutation("        for side in SIDES:", "        for side in (SIDES[0],):"),
    expect_failure=True,
)
_run(
    "body no longer iterates LIMIT2_ORDER_TYPES",
    source_mutation("            for limit2_order_type in LIMIT2_ORDER_TYPES:",
                    "            for limit2_order_type in (LIMIT2_ORDER_TYPES[0],):"),
    expect_failure=True,
)
_run(
    "body reads case.side again",
    source_mutation('            recorder.mark(f"side_{side}")',
                    '            recorder.mark(f"side_{case.side}")'),
    expect_failure=True,
)

# ── claim 4: both generator draws must stay assigned-and-unread pins ──────────────────────
_run(
    "the _pinned_side draw is deleted",
    source_mutation('    _pinned_side = draw(st.sampled_from(("buy", "sell")))\n', ""),
    expect_failure=True,
)
_run(
    "the _pinned_limit2_order_type draw is deleted",
    source_mutation('    _pinned_limit2_order_type = draw(st.sampled_from(("limit", "market")))\n',
                    ""),
    expect_failure=True,
)
_run(
    "the _pinned_side draw is READ again",
    source_mutation("        side=SIDES[0],", "        side=_pinned_side,"),
    expect_failure=True,
)
_run(
    "the _pinned_limit2_order_type draw is READ again",
    source_mutation("        limit2_order_type=LIMIT2_ORDER_TYPES[0],",
                    "        limit2_order_type=_pinned_limit2_order_type,"),
    expect_failure=True,
)


if __name__ == "__main__":
    width = max(len(n) for n, _, _ in RESULTS)
    ok = all(passed for _, passed, _ in RESULTS)
    for name, passed, detail in RESULTS:
        print(f"[{'OK  ' if passed else 'FAIL'}] {name.ljust(width)}  {detail}")
    print(f"\ncases: {len(RESULTS)}")
    print(f"ALL GUARD FALSIFICATIONS BEHAVED AS REQUIRED: {ok}")
    raise SystemExit(0 if ok else 1)
