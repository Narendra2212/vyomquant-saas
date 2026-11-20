"""RISK-03 -- a user's own risk settings may TIGHTEN a limit, never loosen one.

THE DEFECT
----------
``core/risk_manager.validate_trade_request`` loads the account holder's own
``risk_settings`` row and derived its effective limits from it. ``max_leverage`` was
safe, because section 5 computes
``min(self.thresholds.drawdown_tier_N_max_leverage, effective_max_leverage)`` -- a user
value can only ever pull that ceiling down. ``max_daily_loss`` and ``max_positions`` were
taken VERBATIM and compared directly:

    if request.daily_pnl_pct < -effective_max_daily_loss:   -> REJECT_DAILY_LOSS
    if effective_open >= effective_max_positions:           -> REJECT_MAX_OPEN_TRADES

So an account holder could set ``max_positions`` to 100 (the request model's own upper
bound) against a platform ``max_open_trades`` of 5 and make REJECT_MAX_OPEN_TRADES
UNREACHABLE, and set ``max_daily_loss`` large relative to their capital to do the same to
REJECT_DAILY_LOSS. Those are the two circuit breakers that exist to cap concurrent
exposure and to stop an account bleeding out over a single day, and the user they protect
could switch both off.

WHAT THE FIX IS, AND WHERE IT LIVES
------------------------------------
``min(platform_threshold, requested)`` applied to all three limits at the point the
effective value is derived -- not at each use site. One place to read, one place to get
wrong, and a future check that reads ``effective_max_positions`` inherits the clamp
instead of having to remember it.

Separately, ``routers/risk.py`` now CLAMPS a submitted limit to the same ceiling as it
stores it. That is not a second safety layer -- the order-path clamp is sufficient on its
own -- it is honesty: without it a trader saves ``max_positions = 100``, reads it back
unchanged, and believes their account permits a hundred concurrent positions while the
engine quietly enforces 5. A stored limit nothing honours is worse than no limit at all,
which is this finding's own closing observation about ``max_position_size_usd``. Clamped
rather than refused because a refusal would reject this endpoint's own default of 10
positions; the response reports what it lowered and to what, so the clamp is not silent.

WHICH CEILING, AND WHY NOT THE PLAN TIER
-----------------------------------------
The finding proposed the account's plan tier. ``TenantQuota.for_plan`` does carry one --
FREE 5 positions / 1.0x leverage, BASIC 20 / 1.0x, PROFESSIONAL 100 / 3.0x, ENTERPRISE
500 / 10.0x -- and it is the wrong ceiling. It is a fair-use resource table whose only
enforcer (``core/hard_quota_enforcer``) is deprecated and carried by no route, and the free
tier's 1.0x means no margin at all while this endpoint offers 3x as its default. Every
account on the platform is on the free tier, so clamping to the plan quota would have
withdrawn margin trading from the whole user base under the heading of a safety fix.

``RiskThresholds`` is the ceiling already in force: it is exactly what the order path
applies to an account with no ``risk_settings`` row. Enforcing it means configuring your
settings can tighten what the platform gives you by default, but never loosen it.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from backend_app.core.risk_manager import RiskThresholds


# ══════════════════════════════════════════════════════════════════════════════
#  The clamp itself
# ══════════════════════════════════════════════════════════════════════════════


def _thresholds() -> RiskThresholds:
    return RiskThresholds()


def test_the_platform_ceilings_are_well_below_what_the_api_accepts():
    """The premise of the whole finding, asserted so it cannot quietly stop being true.

    If the platform thresholds were raised to meet the request model's bounds, the clamp
    would still be correct but these tests would be proving nothing -- so the gap they
    exercise is pinned here rather than assumed.
    """
    t = _thresholds()
    assert t.max_open_trades < 100, (
        "the request model admits max_positions up to 100; if the platform ceiling were "
        "also 100 there would be nothing for the clamp to do"
    )
    assert t.base_max_leverage < 50, (
        "the request model admits max_leverage up to 50"
    )
    assert 0 < t.max_daily_loss_pct < 1, (
        "max_daily_loss_pct is compared as a fraction of capital"
    )


@pytest.mark.parametrize("requested", [6, 10, 100, 10_000])
def test_a_user_cannot_raise_their_own_max_positions(requested):
    """The core defect. A requested value above the platform ceiling must not survive."""
    t = _thresholds()
    assert requested > t.max_open_trades, "this case must actually exceed the ceiling"
    effective = min(t.max_open_trades, int(requested))
    assert effective == t.max_open_trades, (
        f"a user asking for {requested} concurrent positions must still be held to "
        f"{t.max_open_trades}; otherwise REJECT_MAX_OPEN_TRADES is unreachable"
    )


@pytest.mark.parametrize("requested", [1, 2, 3])
def test_a_user_can_still_lower_their_own_max_positions(requested):
    """The clamp must not become a floor. Tightening is the whole point of the setting --
    a trader who wants to run one position at a time must still be able to say so."""
    t = _thresholds()
    assert requested < t.max_open_trades
    assert min(t.max_open_trades, requested) == requested


@pytest.mark.parametrize("requested_usd,capital", [(1_000_000.0, 10_000.0),
                                                  (500.0, 1_000.0),
                                                  (999_999.0, 100_000.0)])
def test_a_user_cannot_raise_their_own_daily_loss_limit(requested_usd, capital):
    """``max_daily_loss`` is stored in ACCOUNT CURRENCY and compared as a FRACTION of
    capital, so the conversion happens before the clamp. Each case here converts to more
    than the platform fraction and must come back as the platform fraction."""
    t = _thresholds()
    requested_pct = requested_usd / capital
    assert requested_pct > t.max_daily_loss_pct, "this case must exceed the ceiling"
    effective = min(t.max_daily_loss_pct, requested_pct)
    assert effective == t.max_daily_loss_pct, (
        "a daily-loss limit larger than the platform's would make REJECT_DAILY_LOSS "
        "unreachable, which is the second circuit breaker this finding is about"
    )


def test_a_user_can_still_tighten_their_daily_loss_limit():
    t = _thresholds()
    # 1% of capital, well inside a 5% platform ceiling.
    requested_pct = 100.0 / 10_000.0
    assert requested_pct < t.max_daily_loss_pct
    assert min(t.max_daily_loss_pct, requested_pct) == requested_pct


def test_zero_capital_leaves_the_platform_threshold_standing():
    """A non-positive ``total_capital`` gives the conversion no denominator, so the
    platform threshold stands alone. That was the pre-existing behaviour and this change
    does not alter it -- asserted so a later refactor does not turn it into a division by
    zero or an unbounded limit."""
    t = _thresholds()
    total_capital = 0.0
    effective = (
        min(t.max_daily_loss_pct, 1_000_000.0 / total_capital)
        if total_capital > 0
        else t.max_daily_loss_pct
    )
    assert effective == t.max_daily_loss_pct


# ══════════════════════════════════════════════════════════════════════════════
#  The clamp is actually wired into validate_trade_request
# ══════════════════════════════════════════════════════════════════════════════


# Each effective limit, and the ONE platform ceiling that is allowed to bound it.
# Mapped rather than pooled, because a clamp against the wrong threshold -- say
# `max_positions` held to `base_max_leverage` -- would satisfy any check that merely
# looked for "a min against some threshold", and would be nonsense.
_EFFECTIVE_LIMIT_CEILINGS = {
    "effective_max_daily_loss": "max_daily_loss_pct",
    "effective_max_positions": "max_open_trades",
    "effective_max_leverage": "base_max_leverage",
}


def _user_settings_branch() -> ast.If:
    """The ``if user_settings:`` block of ``validate_trade_request``, as a syntax tree.

    Read through ``ast`` rather than by slicing the source text. Two reasons, both found
    the hard way by an earlier version of this file that did slice it: the branch contains
    a nested ``if self.total_capital > 0: ... else: ...``, so splitting on ``"else:"``
    silently truncates it to the first few lines; and the explanatory comment inside the
    branch itself contains the text ``min(``, so counting that substring counts prose. A
    tree sees the code and only the code.
    """
    from backend_app.core import risk_manager

    source = textwrap.dedent(
        inspect.getsource(risk_manager.RiskManager.validate_trade_request)
    )
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "user_settings"
        ):
            return node
    raise AssertionError(
        "validate_trade_request no longer has an `if user_settings:` branch -- the "
        "derivation site this finding is about has moved, and this test can no longer "
        "see whether the limits are clamped. Re-point it before assuming it passes."
    )


def _assignments(branch: ast.If) -> list[ast.Assign]:
    """Single-target name assignments in the branch, in source order.

    Source order matters: the alias resolution below is one hop, so it needs to meet
    ``platform_max_daily_loss = self.thresholds.max_daily_loss_pct`` before it meets the
    ``min`` that uses it. ``ast.walk`` is breadth-first and would not guarantee that.
    """
    found = [
        node
        for node in ast.walk(branch)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    return sorted(found, key=lambda n: (n.lineno, n.col_offset))


def _ceilings_reachable(expr: ast.AST, aliases: dict[str, set[str]]) -> set[str]:
    """Which ``self.thresholds.*`` ceilings this expression is bounded by."""
    found: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Attribute):
            inner = node.value
            if isinstance(inner, ast.Attribute) and inner.attr == "thresholds":
                found.add(node.attr)
        elif isinstance(node, ast.Name):
            found |= aliases.get(node.id, set())
    return found


def _reads_user_input(expr: ast.AST, aliases: set[str]) -> bool:
    """Whether this expression carries a value the account holder controls."""
    return any(
        isinstance(node, ast.Name) and (node.id == "user_settings" or node.id in aliases)
        for node in ast.walk(expr)
    )


def _alias_maps(branch: ast.If) -> tuple[dict[str, set[str]], set[str]]:
    """Locals that stand for a platform ceiling, and locals that carry user input."""
    ceiling_aliases: dict[str, set[str]] = {}
    user_aliases: set[str] = set()
    for node in _assignments(branch):
        name = node.targets[0].id
        reached = _ceilings_reachable(node.value, ceiling_aliases)
        if reached:
            ceiling_aliases[name] = reached
        if _reads_user_input(node.value, user_aliases):
            user_aliases.add(name)
    return ceiling_aliases, user_aliases


def test_validate_trade_request_clamps_every_limit_against_its_own_platform_ceiling():
    """Behavioural tests above prove the arithmetic; this proves the engine uses it.

    A source check, and it is the right shape here: the alternative is booting the full
    risk manager with a fake ``risk_settings`` row, a Redis cache and an event loop to
    observe one ``min``. What matters is that no limit can be derived from user input
    without passing through the clamp, and that is a property of the derivation site, not
    of any one trade.

    The rule enforced is: every assignment to an effective limit inside the user-settings
    branch is either a ``min(platform_ceiling, user_value)``, or a platform ceiling with
    no user input in it at all (the zero-capital fallback). Anything else -- a user value
    taken verbatim, a user value bounded by the wrong ceiling, a fourth branch added later
    that forgets -- fails here.
    """
    branch = _user_settings_branch()
    ceiling_aliases, user_aliases = _alias_maps(branch)

    for limit, ceiling in _EFFECTIVE_LIMIT_CEILINGS.items():
        writes = [n for n in _assignments(branch) if n.targets[0].id == limit]
        assert writes, (
            f"{limit} is no longer assigned in the user-settings branch; if it moved, "
            f"this test is no longer watching the place the clamp has to be"
        )
        for node in writes:
            reached = _ceilings_reachable(node.value, ceiling_aliases)
            where = f"{limit} (line {node.lineno} of validate_trade_request)"

            if not _reads_user_input(node.value, user_aliases):
                # The fallback path. No user value in it, so no clamp is needed -- but it
                # must still land on the platform ceiling rather than on something
                # unbounded.
                assert ceiling in reached, (
                    f"{where} is derived without user input but also without "
                    f"self.thresholds.{ceiling}; a limit that is neither clamped nor the "
                    f"platform default is unbounded"
                )
                continue

            assert isinstance(node.value, ast.Call) and (
                getattr(node.value.func, "id", None) == "min"
            ), (
                f"{where} is derived from user-controlled input WITHOUT a min(): a user "
                f"could then raise it, and the circuit breaker it guards becomes "
                f"unreachable. This is exactly the RISK-03 defect."
            )
            assert ceiling in reached, (
                f"{where} is clamped, but not against self.thresholds.{ceiling}. It is "
                f"bounded by {sorted(reached) or 'nothing'} instead, which is the wrong "
                f"ceiling for this limit."
            )
            assert any(
                _ceilings_reachable(arg, ceiling_aliases) for arg in node.value.args
            ) and any(
                _reads_user_input(arg, user_aliases) for arg in node.value.args
            ), (
                f"{where} calls min(), but not between the platform ceiling and the "
                f"user's value -- a min() of two user values, or of two platform values, "
                f"clamps nothing"
            )


def test_the_clamp_still_reads_the_users_own_settings():
    """The counterpart to the clamp, and the reason this is not fixed by deletion.

    Ignoring ``risk_settings`` entirely would also make the circuit breakers unreachable-
    proof, and would also break the legitimate half of the feature: a trader who wants to
    run one position at a time, or stop at 1% a day, must still be able to say so. All
    three keys have to actually be read.
    """
    branch = _user_settings_branch()
    keys = {
        node.slice.value
        for node in ast.walk(branch)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "user_settings"
        and isinstance(node.slice, ast.Constant)
    }
    assert {"max_daily_loss", "max_positions", "max_leverage"} <= keys, (
        "the user-settings branch no longer reads all three of the trader's own limits "
        f"(found {sorted(keys)}); tightening must stay possible, only loosening is barred"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  The API refuses a limit it would not honour
# ══════════════════════════════════════════════════════════════════════════════


def test_the_api_ceilings_are_the_ones_the_engine_enforces():
    """The API boundary and the order path must not drift apart.

    Read from ``RiskThresholds`` in both places rather than restated in either, so moving
    a threshold moves both. A hardcoded copy here is how an API comes to promise a limit
    the engine does not keep.
    """
    from backend_app.routers.risk import platform_risk_ceilings

    thresholds = _thresholds()
    ceilings = platform_risk_ceilings()

    assert ceilings["max_positions"] == int(thresholds.max_open_trades)
    assert ceilings["max_leverage"] == int(thresholds.base_max_leverage)


def test_the_api_does_not_clamp_against_the_plan_quota():
    """The ceiling is deliberately NOT ``TenantQuota.for_plan``, and this pins the reason.

    The finding suggested the plan tier. The free tier caps leverage at 1.0x -- no margin
    at all -- while the risk-settings endpoint offers 3x as its own default, and every
    account on the platform is on the free tier. Clamping to the plan quota would have
    withdrawn margin trading from the entire user base under the heading of a safety fix,
    so if someone later wires the plan quota in here, this test is where that shows up.
    """
    from backend_app.core.tenant import TenantPlan, TenantQuota
    from backend_app.routers.risk import platform_risk_ceilings

    free = TenantQuota.for_plan(TenantPlan.FREE)
    assert free.max_leverage <= 1.0, (
        "if the free tier's quota gained real leverage headroom, revisit this choice"
    )
    assert platform_risk_ceilings()["max_leverage"] > free.max_leverage, (
        "the API ceiling has been lowered onto the free plan quota; that caps every "
        "account on the platform at 1x and is a product regression, not a safety fix"
    )


@pytest.mark.parametrize("field,requested", [("max_positions", 100),
                                             ("max_positions", 20),
                                             ("max_leverage", 50)])
def test_an_over_ceiling_value_is_stored_as_the_value_in_force(field, requested):
    """The defect: what was stored was not what was enforced."""
    from backend_app.routers.risk import clamp_to_platform_ceilings, platform_risk_ceilings

    ceiling = platform_risk_ceilings()[field]
    assert requested > ceiling, "this case must actually exceed the ceiling"

    record = {"max_positions": 3, "max_leverage": 2, field: requested}
    adjustments = clamp_to_platform_ceilings(record)

    assert record[field] == ceiling, (
        f"{field} was stored as {record[field]}, which the order path will not honour"
    )
    assert [a["field"] for a in adjustments] == [field]
    assert adjustments[0]["requested"] == requested
    assert adjustments[0]["applied"] == ceiling
    assert str(ceiling) in adjustments[0]["reason"], (
        "the explanation must name the ceiling, or the trader is left guessing what "
        "value would have been kept"
    )


def test_a_value_within_the_ceiling_is_stored_exactly_and_reported_as_unchanged():
    """The clamp must not become a floor, and must not cry wolf on an ordinary save."""
    from backend_app.routers.risk import clamp_to_platform_ceilings

    record = {"max_positions": 2, "max_leverage": 1, "max_daily_loss": 250.0}
    assert clamp_to_platform_ceilings(record) == []
    assert record == {"max_positions": 2, "max_leverage": 1, "max_daily_loss": 250.0}


def test_max_daily_loss_is_left_alone_at_the_api_boundary():
    """Deliberate: it is stored in account currency and enforced as a fraction of capital,
    and capital is not known here. Clamping it against a fraction would compare a dollar
    amount to a percentage. The order path does it, after the conversion."""
    from backend_app.routers.risk import clamp_to_platform_ceilings, platform_risk_ceilings

    assert "max_daily_loss" not in platform_risk_ceilings()

    record = {"max_daily_loss": 1_000_000.0}
    assert clamp_to_platform_ceilings(record) == []
    assert record["max_daily_loss"] == 1_000_000.0


def test_a_record_that_omits_a_limit_is_not_given_one():
    """A partial save must not acquire a field it never mentioned."""
    from backend_app.routers.risk import clamp_to_platform_ceilings

    record = {"max_positions": 999}
    clamp_to_platform_ceilings(record)
    assert "max_leverage" not in record


def test_an_unreadable_ceiling_does_not_break_the_response():
    """The settings are already saved by the time the ceilings are reported, so an
    unreadable threshold must not turn a successful save into a 500."""
    from backend_app.routers import risk as risk_router

    original = risk_router.platform_risk_ceilings
    try:
        risk_router.platform_risk_ceilings = lambda: (_ for _ in ()).throw(
            RuntimeError("thresholds unavailable")
        )
        assert risk_router._safe_platform_ceilings() == {}
    finally:
        risk_router.platform_risk_ceilings = original


def test_the_clamp_is_applied_to_the_record_every_consumer_reads():
    """Where the clamp sits decides whether six things agree or five of them lie.

    ``updated_record`` is what goes to the in-memory store, the ``risk_settings`` upsert,
    the WebSocket broadcast, the notification and the response. Clamping it at the point it
    is assembled is what makes those agree -- clamping ``body`` instead would leave the
    ``current_settings`` fall-through unclamped, so a value already stored above the
    ceiling would survive a save that did not mention it.
    """
    import inspect

    from backend_app.routers import risk as risk_router

    source = inspect.getsource(risk_router.update_risk_settings)
    assert "clamp_to_platform_ceilings(updated_record)" in source, (
        "the clamp must be applied to the assembled record, not to the request body"
    )

    before, _, after = source.partition("clamp_to_platform_ceilings(updated_record)")
    assert "_user_risk_settings[uid] = updated_record" in after, (
        "the clamp must run BEFORE the record reaches the in-memory store, or the store "
        "and the database disagree with each other"
    )
    assert "upsert" in after, "the clamp must run before the row is persisted"
    assert "logger.warning" in source, (
        "a ceiling that could not be read must say so, or it stops checking silently"
    )

# ══════════════════════════════════════════════════════════════════════════════
#  End to end, through the real route
# ══════════════════════════════════════════════════════════════════════════════


def test_the_endpoint_returns_the_limit_it_will_actually_enforce():
    """The user-visible behaviour, driven through the real PUT.

    Every test above this one checks a piece: the arithmetic, the helper, where the helper
    is called. None of them would catch the call being inside a branch that never runs, or
    the response being assembled from ``body`` instead of the clamped record. This drives
    the endpoint the way the UI does and reads what comes back.
    """
    from unittest.mock import AsyncMock, Mock, patch

    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.main import app
    from backend_app.routers.risk import platform_risk_ceilings

    ceilings = platform_risk_ceilings()
    client = TestClient(app)

    with patch("backend_app.routers.risk._sb") as mock_sb:
        sb_instance = Mock()
        sb_instance.table.return_value.upsert.return_value.execute.return_value = Mock(
            data=[{"status": "ok"}]
        )
        mock_sb.side_effect = AsyncMock(return_value=sb_instance)

        app.dependency_overrides[get_current_user] = lambda: {
            "id": "risk03_e2e_user",
            "access_token": "valid_token",
        }
        try:
            response = client.put(
                "/api/risk/settings",
                json={
                    # Both at the request model's own upper bounds, both far above the
                    # ceilings the order path enforces.
                    "max_positions": 100,
                    "max_leverage": 50,
                    "max_daily_loss": 2000,
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()

    # The saved values, read back the way the UI reads them.
    assert payload["max_positions"] == ceilings["max_positions"]
    assert payload["max_leverage"] == ceilings["max_leverage"]
    assert payload["data"]["max_positions"] == ceilings["max_positions"], (
        "the nested copy must agree with the flattened one, or a client sees two "
        "different limits in one response"
    )

    # And the response SAYS it lowered them, naming both fields. A clamp the caller
    # cannot see is the same lie in the other direction.
    adjusted = {a["field"] for a in payload["limit_adjustments"]}
    assert adjusted == {"max_positions", "max_leverage"}, (
        f"expected both limits reported as adjusted, got {sorted(adjusted)}"
    )
    assert str(ceilings["max_positions"]) in payload["message"]
    assert payload["platform_ceilings"] == ceilings

    # max_daily_loss is enforced in the order path, against capital, so it is stored as
    # submitted here. Pinned so a later change does not start clamping a dollar amount
    # against a percentage.
    assert payload["max_daily_loss"] == 2000.0
