"""MONEY-02 / MONEY-04 regression: provider amounts are exact, and never mis-denominated.

MONEY-02 -- FLOAT DIVISION ON MONEY
-----------------------------------
Three webhook branches in `backend_app/routers/billing.py` converted a provider's
minor-unit integer to a major-unit amount with binary float division::

    payment_amount = session.get("amount_total", 0) / 100.0   # Stripe checkout, cents
    renewal_amount = invoice_obj.get("amount_paid", 0) / 100.0 # Stripe renewal
    payment_amount = payment.get("amount", 0) / 100.0          # Razorpay, paise

Those values fed `billing_invoices.amount_usd` / `amount_inr` and -- the part that
matters -- the referral commission base handed to `process_referral_commission`, which
computes a flat 20% of it.

For typical cent amounts IEEE-754 division by `100.0` usually lands on the right value,
so this was not visibly corrupting most invoices. It was still the wrong type for money,
and it was inconsistent with the discipline the rest of this codebase already enforces:
`marketplace/money.py` refuses a `float` outright, `paper/paper_accounting.py` raises on
one, `fee_engine.py` quantizes with `ROUND_HALF_EVEN`.

THE FIX: `_provider_major_amount(value, currency)`, composed from the two primitives that
already existed rather than a fourth representation of money --
`_exact_minor_units` (returns `None` for a float rather than truncating) and
`marketplace_money.to_major` (Minor_Units -> `Decimal`, quantized to the currency's ISO
4217 exponent). It returns `None`, never a guess and never `0`, when the amount cannot be
read exactly; callers then decline to record or credit.

MONEY-04 -- INR CREDITED AS USD (found during this remediation)
---------------------------------------------------------------
The Razorpay branch passed the INR amount into the `p_payment_amount_usd` argument,
carrying its own annotation::

    "p_payment_amount_usd": payment_amount  # Note: This is INR, not USD

`referral_commissions` has no currency column -- only `payment_amount_usd` and
`commission_amount_usd` -- and the function takes a flat 20%. So an INR payment credited a
commission roughly 83x larger than its real USD value and recorded it as dollars. The
defect was known, annotated, and never fixed.

THE FIX: refuse. Converting would require choosing an FX policy -- which rate, as of
when, who absorbs the accrual-to-payout drift -- and that is a product decision about
money owed to a referrer, not something to infer in a webhook handler. No commission is
recorded, the entitlement is still granted, the INR invoice is still written, and the
omission is logged with everything needed to settle it.
"""

from __future__ import annotations

import inspect
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.routers.billing import (  # noqa: E402
    _exact_minor_units,
    _provider_major_amount,
)


# ── 1. the converter is exact ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "minor,currency,expected",
    [
        (0, "USD", "0.00"),
        (1, "USD", "0.01"),
        (99, "USD", "0.99"),
        (100, "USD", "1.00"),
        (999, "USD", "9.99"),
        (1999, "USD", "19.99"),
        (2999, "USD", "29.99"),
        (100_000, "USD", "1000.00"),
        (1, "INR", "0.01"),
        (8300, "INR", "83.00"),
        (249900, "INR", "2499.00"),
    ],
)
def test_minor_units_convert_exactly(minor, currency, expected):
    got = _provider_major_amount(minor, currency)
    assert got == Decimal(expected), f"{minor} {currency} -> {got}, expected {expected}"
    # The quantum must match the currency exponent, so 100 renders "1.00" not "1".
    assert str(got) == expected


def test_a_string_amount_is_read_exactly():
    """A JSON decoder may hand the integer back as text; that must still be exact."""
    assert _provider_major_amount("2999", "USD") == Decimal("29.99")


@pytest.mark.parametrize("bad", [29.99, 0.1, 1e3, True, False])
def test_an_inexact_or_wrong_typed_amount_is_refused(bad):
    """A float is refused, NOT truncated or rounded.

    `True`/`False` are included because `isinstance(True, int)` is True in Python and a
    bool silently reading as 1 minor unit would be a money bug.
    """
    assert _provider_major_amount(bad, "USD") is None


@pytest.mark.parametrize("bad", [None, "", "abc", "12.5", "1,000", object()])
def test_an_unreadable_amount_is_refused(bad):
    assert _provider_major_amount(bad, "USD") is None


def test_refusal_is_none_and_never_zero():
    """`0` would be recorded as a real £0 charge; `None` means "do not record"."""
    assert _provider_major_amount(None, "USD") is None
    assert _provider_major_amount(29.99, "USD") is None
    # and a genuine zero is still distinguishable from a refusal
    assert _provider_major_amount(0, "USD") == Decimal("0.00")


@pytest.mark.parametrize("currency", ["GBP", "EUR", "XYZ", "", None])
def test_an_unsupported_currency_is_refused(currency):
    """No persisted minor-unit exponent means the amount has no defined meaning."""
    assert _provider_major_amount(100, currency) is None


def test_an_out_of_range_amount_is_refused():
    assert _provider_major_amount(10**15, "USD") is None


def test_the_result_is_a_decimal_not_a_float():
    got = _provider_major_amount(2999, "USD")
    assert isinstance(got, Decimal)
    assert not isinstance(got, float)


def test_no_float_round_trip_in_the_converter():
    """The classic failure this replaces: 0.1 + 0.2 != 0.3 in binary float.

    Summed as Decimals from minor units the arithmetic is exact, which is the property
    the invoice and commission columns depend on.
    """
    parts = [_provider_major_amount(m, "USD") for m in (10, 20)]
    assert sum(parts) == Decimal("0.30")
    assert str(sum(parts)) == "0.30"


def test_converter_reuses_the_existing_strict_reader():
    """Composition, not a fourth money representation.

    If `_exact_minor_units` is ever loosened to accept a float, this catches that the
    converter inherited the weakening.
    """
    assert _exact_minor_units(29.99) is None
    assert _exact_minor_units("2999") == 2999
    assert _exact_minor_units(True) is None


# ── 2. the float division is gone from the webhook branches ──────────────────


def _billing_source() -> str:
    import backend_app.routers.billing as mod

    return inspect.getsource(mod)


def _float_divisions_in_billing(source: str | None = None):
    """Every executable `x / <float>` in billing.py, as (lineno, divisor) pairs.

    Parsed with `ast` rather than scanned as text, and that is deliberate. A substring
    scan cannot distinguish code from a comment or a docstring, and the MONEY-02 fix's
    own documentation quotes the three defective expressions verbatim so a reader can see
    what was replaced. A line-based filter missed them because a continuation line inside
    a docstring neither starts with `#` nor with a quote -- this test failed exactly that
    way on its first run, which is the second time a text scan has produced a false
    positive in this remediation (see
    tests/test_api01_strategies_update_allowlist.py).

    `ast` sees only real code, so documentation can quote the defect freely.

    ``source`` is overridable so section 4 can point the same detector at a synthetic
    copy of the pre-fix code and prove it still fires.
    """
    import ast

    tree = ast.parse(source if source is not None else _billing_source())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        right = node.right
        if isinstance(right, ast.Constant) and isinstance(right.value, float):
            found.append((getattr(node, "lineno", -1), right.value))
    return found


def test_no_executable_float_division_remains_in_billing():
    """MONEY-02, as a class rather than three instances.

    Any division by a float literal anywhere in this module is reported, so a fourth
    branch added later with `/ 100.0` fails here too.
    """
    offenders = _float_divisions_in_billing()
    assert offenders == [], (
        f"billing.py still performs float division in executable code at "
        f"(line, divisor): {offenders}. Money must not be divided as a binary float."
    )


def test_the_three_original_defect_sites_no_longer_divide():
    """The specific instances, located by the provider field each one read."""
    import ast

    tree = ast.parse(_billing_source())
    # Collect the source segment of every float division, if any survived.
    segments = []
    source = _billing_source()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, float):
                segments.append(ast.get_source_segment(source, node) or "")
    for field in ("amount_total", "amount_paid", '"amount"'):
        assert not any(field in seg for seg in segments), (
            f"a float division still reads {field}. MONEY-02 is open for that branch."
        )


def test_all_three_branches_use_the_exact_converter():
    source = _billing_source()
    assert source.count("_provider_major_amount(") >= 4, (
        "expected the converter's definition plus three call sites; found "
        f"{source.count('_provider_major_amount(')}"
    )
    for arg in ('session.get("amount_total")',
                'invoice_obj.get("amount_paid")',
                'payment.get("amount")'):
        assert arg in source, f"{arg} is no longer read; a branch may have been dropped"


def test_amounts_are_sent_as_decimal_strings_not_floats():
    """Serialising a Decimal through float would reintroduce the defect in transport."""
    source = _billing_source()
    assert 'str(payment_amount) if payment_amount is not None else None' in source
    # `renewal_amount` was Stripe's `invoice.payment_succeeded` branch -- a RECURRING
    # subscription renewal, which Razorpay one-time orders do not produce. The branch went
    # with the provider, so there is no longer a renewal amount to serialise. Both
    # round() guards stay: they cost nothing and they are the assertions that would catch
    # float rounding being reintroduced under either name.
    assert "round(payment_amount, 2)" not in source, (
        "round() on the amount is back; that is float rounding on money."
    )
    assert "round(renewal_amount, 2)" not in source


# ── 3. MONEY-04: the Razorpay commission is not credited in the wrong currency ──


def _enclosing_scopes(source: str | None = None):
    """``(name, first_line, last_line)`` for every function in billing.py."""
    import ast

    tree = ast.parse(source if source is not None else _billing_source())
    return [
        (n.name, n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _scope_of(lineno: int, source: str | None = None) -> str:
    """The innermost function containing ``lineno`` -- innermost, so a nested
    helper is reported rather than the handler that happens to wrap it."""
    best_name, best_start = "<module>", -1
    for name, start, end in _enclosing_scopes(source):
        if start <= lineno <= end and start > best_start:
            best_name, best_start = name, start
    return best_name


def _referral_commission_rpc_calls(source: str | None = None):
    """Every executable ``*.rpc("process_referral_commission", {...})`` in billing.py.

    Returns ``(lineno, enclosing_function, argument_source)`` per call site.

    Parsed with `ast`, for the same reason as :func:`_float_divisions_in_billing`: the
    MONEY-04 fix documents the call it deleted verbatim, including that call's original
    ``# Note: This is INR, not USD`` annotation, so a text scan for that annotation
    matches the very comment that explains the removal. That is the third false positive
    a substring scan has produced during this remediation, so MONEY-04 is asserted
    structurally instead -- which is the stronger statement anyway: not "a particular
    comment is absent" but "exactly one commission credit exists and it is denominated
    in the currency its column claims".
    """
    import ast

    source = source if source is not None else _billing_source()
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "rpc":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "process_referral_commission"):
            continue
        payload = ""
        if len(node.args) > 1:
            payload = ast.get_source_segment(source, node.args[1]) or ""
        calls.append((node.lineno, _scope_of(node.lineno, source), payload))
    return calls


def _amount_conversions(source: str | None = None):
    """Every ``_provider_major_amount(value, CURRENCY)`` call site.

    Returns ``(lineno, enclosing_function, currency_literal)``; the currency is ``None``
    when it is not a plain string literal, which would itself be worth failing on.
    """
    import ast

    source = source if source is not None else _billing_source()
    conversions = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "_provider_major_amount" or len(node.args) < 2:
            continue
        currency = node.args[1]
        literal = currency.value if isinstance(currency, ast.Constant) else None
        conversions.append((node.lineno, _scope_of(node.lineno, source), literal))
    return conversions


def _usd_commission_argument_sites(source: str | None = None):
    """Every EXECUTABLE place billing.py supplies a ``p_payment_amount_usd`` argument.

    A dict-key / keyword-argument question asked structurally, not a substring scan.
    billing.py's MONEY-04 note explains the deleted credit call verbatim and names
    ``p_payment_amount_usd`` in prose while doing so, so `"p_payment_amount_usd" in source`
    matches the comment that documents the fix -- the same false positive that made
    :func:`_referral_commission_rpc_calls` parse with `ast` in the first place.
    """
    import ast

    source = source if source is not None else _billing_source()
    sites = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "p_payment_amount_usd":
                    sites.append((node.lineno, _scope_of(node.lineno, source)))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "p_payment_amount_usd":
                    sites.append((node.lineno, _scope_of(node.lineno, source)))
    return sites


def test_no_referral_commission_is_credited_anywhere_any_more():
    """MONEY-04 is now satisfied by construction: there is no credit site to mis-denominate.

    This was test_exactly_one_referral_commission_credit_remains (plus
    test_the_surviving_credit_is_the_stripe_one, which asserted that the one survivor sat in
    stripe_webhook). The asymmetry those two described was real: `referral_commissions` has
    a USD-only `p_payment_amount_usd` and no currency column, so the USD/Stripe path could
    credit and the INR/Razorpay path could not. Stripe's handler is deleted, so the credit
    site went with it and nothing credits a commission at all.

    THIS IS A PRODUCT CONSEQUENCE, NOT ONLY A TEST CHANGE. No referrer is paid automatically
    on any payment this platform can now take. What a referral earns is recorded as an
    OBLIGATION instead (the next assertion), which is a settleable queue rather than silent
    loss. Crediting in INR would require `referral_commissions` to carry a currency -- doing
    it without one would reintroduce the exact MONEY-04 defect, an INR figure in a column
    that means dollars.
    """
    calls = _referral_commission_rpc_calls()
    assert calls == [], (
        "a referral commission credit is back in billing.py at "
        f"{[(ln, scope) for ln, scope, _ in calls]}. `referral_commissions` still has no "
        "currency column, so any credit on the INR path is the MONEY-04 defect."
    )

    # Nor may anything SUPPLY the USD-only argument: with no credit site there is no
    # legitimate reason to pass it, and passing it is how one creeps back. Asked of the
    # syntax tree, because billing.py's own MONEY-04 note names the argument in prose
    # while explaining the call it deleted.
    sites = _usd_commission_argument_sites()
    assert sites == [], (
        f"p_payment_amount_usd is supplied again at {sites}; the only path that could "
        "honestly supply it was the USD one, and that provider is gone."
    )


def test_what_a_referral_is_owed_is_recorded_as_an_obligation_instead():
    """MONEY-06: the surviving path states the debt rather than dropping it.

    With no credit site left, this is the ONLY thing standing between a referrer and
    silent non-payment, so it is asserted directly rather than assumed from the absence
    above. The obligation is written from the handler's own scope, so a reader of the
    webhook can see that the money owed is recorded.
    """
    import ast

    tree = ast.parse(_billing_source())
    recorded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_record_referral_obligation":
            recorded.append((node.lineno, _scope_of(node.lineno)))

    assert recorded, (
        "nothing calls _record_referral_obligation, so a commission that cannot be "
        "credited is now owed to a referrer and recorded nowhere at all."
    )
    assert {scope for _ln, scope in recorded} == {"razorpay_webhook"}, (
        f"obligations are recorded outside the webhook handler: {recorded}"
    )


def test_each_webhook_converts_amounts_in_its_own_currency():
    """The precondition the next assertion rests on."""
    by_scope: dict = {}
    for _lineno, scope, currency in _amount_conversions():
        by_scope.setdefault(scope, set()).add(currency)
    # Was one assertion per handler. Razorpay is the only gateway, so there is one.
    assert by_scope.get("razorpay_webhook") == {"INR"}, by_scope
    assert "stripe_webhook" not in by_scope, (
        f"a stripe_webhook handler is back in billing.py: {by_scope}"
    )


def test_no_amount_in_any_currency_can_reach_the_usd_commission_column():
    """The same property as before, now provable without a credit site to inspect.

    This used to read the one credit site, resolve its enclosing scope, and assert that
    scope converted amounts exclusively in USD -- "a correct INR number placed in a column
    that means dollars" was the original defect, so the assertion was about provenance.

    With no credit site at all there is no provenance to check, and the stronger statement
    is available directly: billing.py converts amounts in INR only, and names the USD
    column nowhere, so no amount in any currency can reach it. Asserted as the conjunction
    rather than as the absence alone, because the absence by itself would also hold on a
    file that had quietly stopped converting anything.
    """
    currencies = {c for _ln, _s, c in _amount_conversions() if c is not None}
    assert currencies == {"INR"}, (
        f"billing.py converts provider amounts as {sorted(map(str, currencies))}; INR is "
        "the only currency the one remaining gateway charges in."
    )
    assert _usd_commission_argument_sites() == []


def test_razorpay_branch_does_not_call_the_commission_rpc():
    """Refusal is the fix, so the Razorpay branch must not invoke the RPC at all.

    Scoped by AST enclosing-function, NOT by slicing the source between text markers.
    The marker version of this test used `source.index("MONEY-04")` as the start of the
    Razorpay section, and broke the moment a comment elsewhere in the module referred to
    MONEY-04 by name -- which the MONEY-05 fix does, when it explains why an unknown
    *tier* is credited where an unknown *currency* is refused. The slice then began in
    the Stripe branch and swept up Stripe's legitimate call.

    That is the fourth false positive text matching has produced in this remediation.
    Enclosing scope is the property actually being asserted, so assert that.
    """
    offenders = [
        (lineno, scope)
        for lineno, scope, _payload in _referral_commission_rpc_calls()
        if scope == "razorpay_webhook"
    ]
    assert offenders == [], (
        f"the Razorpay branch calls process_referral_commission at {offenders}. Until "
        "referral_commissions carries a currency, an INR payment must not credit a "
        "USD-denominated commission."
    )


def test_razorpay_skip_is_logged_loudly_with_settlement_facts():
    """A silent omission of money owed would be its own defect.

    Scoped to `razorpay_webhook`'s own source range via AST line numbers rather than by
    slicing on a text marker -- see the note in
    `test_razorpay_branch_does_not_call_the_commission_rpc`.
    """
    source = _billing_source()
    lines = source.splitlines()
    scope = [s for s in _enclosing_scopes() if s[0] == "razorpay_webhook"]
    assert scope, "razorpay_webhook no longer exists; repoint this guard"
    _name, start, end = scope[0]
    section = "\n".join(lines[start - 1:end])

    assert "MONEY-04: referral commission SKIPPED" in section
    assert "logger.error" in section, "the skip must be an error, not a debug line"
    for fact in ("payment_id", "payment_amount", "user_id", "item_key"):
        assert fact in section, (
            f"the skip log omits {fact}; the omission must be settleable from the log."
        )


def test_an_unreadable_amount_blocks_the_obligation_but_not_the_entitlement():
    """`None` must gate the money record without discarding the purchase.

    This was asserted against the Stripe credit's guard
    (`if payment_amount is not None and payment_amount > 0:`) alongside
    test_stripe_commission_is_still_credited, which proved the USD credit kept working.
    Both referred to a handler that no longer exists.

    The property itself is unchanged and still load-bearing, because the surviving path
    writes an OBLIGATION from the same unreliable input: a provider amount that may be
    absent or unparseable must not become a `None > 0` TypeError inside a webhook that has
    already taken the customer's money, and must not silently record a zero or a guess as
    the sum owed. So it is asserted where the money is now recorded.
    """
    source = _billing_source()
    assert "if _declared_amount is not None and _declared_amount > 0:" in source, (
        "the referral obligation is no longer guarded against an unreadable amount; "
        "`None > 0` raises TypeError in Python 3, inside a handler that has already "
        "granted the entitlement for a captured payment."
    )
    # And the skip is still loud, so an unrecordable obligation is not a silent one.
    assert "MONEY-04: referral commission SKIPPED" in source


# ── 4. negative control: the detectors actually fire ────────────────────────
#
# Every assertion above is of the form "the defect is absent". That shape of test is
# worthless if the detector cannot see the defect in the first place -- it passes on a
# vulnerable file just as happily as on a fixed one. This remediation has already
# produced three false positives from text scanning (here, and in
# tests/test_api01_strategies_update_allowlist.py), so the detectors are themselves
# put under test against a synthetic reconstruction of the pre-fix code.
#
# It is the same principle the unwired-control sweep applies to the product: a check
# that is never exercised is not a check.

_PRE_FIX_SOURCE = '''
async def stripe_webhook(request):
    if event_type == "checkout.session.completed":
        payment_amount = session.get("amount_total", 0) / 100.0
        sb.rpc("process_referral_commission", {
            "p_referred_id": user_id,
            "p_payment_id": payment_id,
            "p_subscription_tier": item_key,
            "p_payment_amount_usd": payment_amount,
        }).execute()
    elif event_type == "invoice.payment_succeeded":
        renewal_amount = invoice_obj.get("amount_paid", 0) / 100.0


async def razorpay_webhook(request):
    payment_amount = payment.get("amount", 0) / 100.0
    sb.rpc("process_referral_commission", {
        "p_referred_id": user_id,
        "p_payment_id": payment_id,
        "p_subscription_tier": item_key,
        "p_payment_amount_usd": payment_amount,  # Note: This is INR, not USD
    }).execute()
'''


def test_the_float_division_detector_fires_on_the_pre_fix_code():
    """MONEY-02's detector must find all three original divisions."""
    offenders = _float_divisions_in_billing(_PRE_FIX_SOURCE)
    assert len(offenders) == 3, (
        f"the detector found {len(offenders)} of the 3 known pre-fix float divisions: "
        f"{offenders}. It cannot be trusted to prove the fix held."
    )
    assert {divisor for _lineno, divisor in offenders} == {100.0}


def test_the_duplicate_credit_detector_fires_on_the_pre_fix_code():
    """MONEY-04's detector must see two credit sites, one per provider."""
    calls = _referral_commission_rpc_calls(_PRE_FIX_SOURCE)
    scopes = sorted(scope for _lineno, scope, _payload in calls)
    assert scopes == ["razorpay_webhook", "stripe_webhook"], (
        f"the detector resolved the pre-fix credit sites to {scopes}; it must see both "
        "providers crediting, which is the condition MONEY-04 describes."
    )


def test_the_currency_detector_reports_an_unconverted_pre_fix_module():
    """Pre-fix there was no exact converter at all, so the USD claim was unprovable."""
    assert _amount_conversions(_PRE_FIX_SOURCE) == []


def test_the_scope_resolver_distinguishes_the_two_webhooks():
    """The MONEY-04 assertion rests entirely on attributing a call to the right handler.

    If `_scope_of` collapsed both webhooks into one name, "the surviving credit is the
    Stripe one" would be vacuous.
    """
    scopes = {name for name, _start, _end in _enclosing_scopes(_PRE_FIX_SOURCE)}
    assert {"stripe_webhook", "razorpay_webhook"} <= scopes
    calls = _referral_commission_rpc_calls(_PRE_FIX_SOURCE)
    assert len({scope for _lineno, scope, _payload in calls}) == 2
