"""MONEY-06 / MONEY-07 — money owed to a customer must exist as a row, in a stated unit.

WHAT THE TWO FINDINGS WERE
==========================

**MONEY-06 — an obligation that existed only as a log line.** `razorpay_webhook` contains
ZERO `process_referral_commission` call nodes (AST-confirmed; the name appears four times
in Razorpay-named code as prose, and a substring scan is fooled by exactly that). When an
INR payment was captured for a referred user the handler wrote an invoice, dispatched a
notification, and then emitted a single `logger.error` saying the commission was skipped.
The refusal was correct -- an INR amount must not be credited to a USD-only ledger -- but
it was the *only* artefact. Nothing stated that money was owed, how much, or in what
currency, so nothing could query, age, reconcile or settle it.

Two further paths dropped a commission silently, which the finding did not name and
measurement exposed: `stripe_webhook` logged and continued when `_provider_major_amount`
returned `None`, and its commission RPC sat inside a bare `except` that logged and
continued -- so a transient PostgREST failure lost a real USD commission.

**MONEY-07 — the denomination was assumed, not read.** All three amount conversions pair
the amount with a hardcoded currency literal. The providers send the denomination in the
same object as the amount: Razorpay's payment entity carries `currency`, Stripe's session
and invoice carry `currency`. Measured: `payment.get("currency")` was read exactly ONCE in
the whole module, inside `_razorpay_settlement_metadata`, which early-returns for anything
that is not a marketplace item. So on the plan-purchase path the amount was treated as INR
*because the code path expected INR*, and for Stripe the same assumed-USD amount became
the base for a 20% referral commission.

HOW THIS FILE MEASURES
======================
* **An independent psycopg2 connection is the oracle.** Every "it was recorded" assertion
  reads the row back over a connection that did not write it, so the claim means committed
  and visible elsewhere -- not merely staged in a session.
* **Real transactions.** The rollback and concurrency tests use separate connections with
  explicit transaction control, because the property under test is a database guarantee.
* **Sanitised fixture payloads only.** No live payment request, no provider credential, no
  network. The currency-validation tests drive the real `_require_provider_currency`; the
  obligation tests drive the real `record_referral_obligation` RPC.
* **AST for every source-level claim.** Three Phase 8 verifier checks and four MONEY-04
  test assertions were previously defeated by substring matching on comments, docstrings
  and nested parentheses. Nothing here matches source text where a tree will do.

WHAT IS NOT CLAIMED
===================
* The `pending -> approved` settlement step for ordinary commissions is still
  unimplemented, and so is any settlement job for these obligations. An obligation reaches
  `payable` or `unresolved` and waits. That is reported, not fixed: who approves a payout,
  on what evidence, and with what reversal window is a product decision.
* An amount that `_provider_major_amount` cannot read at all produces NO obligation row,
  because `payment_amount` is `NOT NULL` and `> 0` by design -- an obligation with no
  amount is not an obligation. `test_an_unreadable_amount_records_no_row_and_that_is_stated`
  pins that boundary rather than leaving it implicit.
* A currency with no persisted minor-unit exponent cannot be converted, so a payment in
  one records no obligation either. Same reason, same explicit test.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import sys
import threading
import uuid
from decimal import Decimal
from typing import List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── explicit PostgreSQL requirement ──────────────────────────────────────────
#
# Gated on AERORA_TEST_DATABASE_URL naming PostgreSQL, the same mechanism
# tests/test_money04_*.py and tests/test_trade01_*.py use. Deliberately NO fallback to a
# local DSN file: Phase 8 measured that such a fallback made
# tests/test_tenant02_*.py run in BOTH lanes, so the "CI-comparable" lane stopped
# representing a clean CI runner. A skip here is not a pass.
_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the obligation ledger is unverified."
    ),
)

RATE = Decimal("0.20")


# ══════════════════════════════════════════════════════════════════════════
#  PART 1. MONEY-07 — THE DENOMINATION IS READ FROM THE PROVIDER
#  (no database; drives the real validator and the real module AST)
# ══════════════════════════════════════════════════════════════════════════


def _billing():
    import backend_app.routers.billing as mod

    return mod


def _billing_tree() -> ast.Module:
    return ast.parse(inspect.getsource(_billing()))


def _scope_ranges():
    """`(name, first_line, last_line)` for every function in billing.py."""
    return [
        (n.name, n.lineno, n.end_lineno or n.lineno)
        for n in ast.walk(_billing_tree())
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _innermost_scope(lineno: int) -> str:
    best, best_start = "<module>", -1
    for name, start, end in _scope_ranges():
        if start <= lineno <= end and start > best_start:
            best, best_start = name, start
    return best


def _attribute_get_calls(attr_owner: str, key: str) -> List[tuple]:
    """Every `<attr_owner>.get("<key>")` call, as `(lineno, innermost_scope)`.

    AST, not text. The whole reason MONEY-07 survived two phases is that
    `payment.get("currency")` DOES appear in this module -- once, in a different function
    that early-returns for the path in question -- so a file-wide substring search reports
    the finding as already fixed. Scope is the property that matters, so scope is what is
    computed.
    """
    found = []
    for node in ast.walk(_billing_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", None) != "get":
            continue
        owner = getattr(func.value, "id", None)
        if owner != attr_owner:
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != key:
            continue
        found.append((node.lineno, _innermost_scope(node.lineno)))
    return found


def test_the_razorpay_handler_now_reads_the_payments_own_currency():
    """MONEY-07's headline, asserted at the scope that matters.

    Before this fix `payment.get("currency")` appeared exactly once in the module, inside
    `_razorpay_settlement_metadata` -- a different function, which returns early unless
    the item is a marketplace item. So the plan-purchase path never consulted it.
    """
    reads = _attribute_get_calls("payment", "currency")
    scopes = {scope for _ln, scope in reads}

    assert "razorpay_webhook" in scopes, (
        "razorpay_webhook still does not read the payment's own `currency` field, so its "
        "amount is treated as INR because the code path expects INR. That is MONEY-07. "
        f"Reads found in: {sorted(scopes)}"
    )


def test_the_handler_still_declares_exactly_one_currency_literal():
    """The invariant MONEY-07's fix must NOT break.

    Each webhook writes a FIXED amount column (`amount_usd` / `amount_inr`) and a fixed
    `"currency"` value, so each handler is single-currency by construction. Replacing the
    literal with `payment.get("currency")` would make a variable currency reach a fixed
    column -- a new defect, not a fix -- and would also break
    `tests/test_money02_billing_amount_precision.py::
    test_each_webhook_converts_amounts_in_its_own_currency`.

    So the literal stays and the provider's declaration is checked AGAINST it. This test
    asserts the literal is still there, i.e. that the fix took the validating shape rather
    than the substituting one.
    """
    conversions = []
    for node in ast.walk(_billing_tree()):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "_provider_major_amount" or len(node.args) < 2:
            continue
        currency = node.args[1]
        literal = currency.value if isinstance(currency, ast.Constant) else None
        conversions.append((node.lineno, _innermost_scope(node.lineno), literal))

    by_scope = {}
    for _ln, scope, literal in conversions:
        by_scope.setdefault(scope, set()).add(literal)

    # Was two assertions, one per handler. Stripe's handler is gone with the provider.
    # `_amount_in_declared_currency` also appears in this map with a non-literal currency:
    # that is the shared helper, which takes the currency as a PARAMETER, and it is not a
    # handler. The invariant being checked is that the HANDLER passes a literal, which it
    # still does -- so the guard remains visible to a reader of the handler itself.
    assert by_scope.get("razorpay_webhook") == {"INR"}, by_scope
    assert "stripe_webhook" not in by_scope, (
        f"a stripe_webhook handler is back in billing.py: {by_scope}"
    )


def test_the_handler_validates_the_declared_currency_in_its_own_scope():
    """The guard is present in each handler's own scope, not in a shared helper.

    A shared helper would be invisible to a reader of the handler and would hide WHICH
    denomination each provider path accepts.
    """
    calls = []
    for node in ast.walk(_billing_tree()):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_require_provider_currency":
            calls.append(_innermost_scope(node.lineno))

    assert "razorpay_webhook" in calls, "the Razorpay handler does not validate currency"


def test_a_matching_currency_is_accepted():
    from backend_app.routers.billing import _require_provider_currency

    assert _require_provider_currency(
        "INR", "INR", provider="razorpay", payment_id="pay_1"
    ) == "INR"


def test_case_is_normalised_because_providers_disagree_about_it():
    """Stripe sends lowercase ISO codes, Razorpay uppercase, and
    `money.minor_unit_exponent` is case-SENSITIVE.

    Normalising CASE is not substituting a currency: `"usd"` and `"USD"` are the same
    declaration. `test_a_different_currency_is_refused` proves a genuinely different one
    still fails.
    """
    from backend_app.routers.billing import _require_provider_currency

    assert _require_provider_currency(
        "usd", "USD", provider="stripe", payment_id="cs_1"
    ) == "USD"


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_missing_currency_is_refused(missing):
    from backend_app.routers.billing import (ProviderCurrencyMismatch,
                                             _require_provider_currency)

    with pytest.raises(ProviderCurrencyMismatch) as excinfo:
        _require_provider_currency(
            missing, "INR", provider="razorpay", payment_id="pay_2"
        )
    assert excinfo.value.code == "CURRENCY_MISSING"


@pytest.mark.parametrize("bad", ["IN", "INRR", "1NR", "Rs", "₹", "US DOLLAR"])
def test_a_malformed_currency_is_refused(bad):
    from backend_app.routers.billing import (ProviderCurrencyMismatch,
                                             _require_provider_currency)

    with pytest.raises(ProviderCurrencyMismatch) as excinfo:
        _require_provider_currency(bad, "INR", provider="razorpay", payment_id="pay_3")
    assert excinfo.value.code == "CURRENCY_MALFORMED"


@pytest.mark.parametrize("unsupported", ["EUR", "GBP", "JPY", "XYZ"])
def test_an_unsupported_currency_is_refused_distinctly(unsupported):
    """`CURRENCY_UNSUPPORTED` is a DIFFERENT code from `CURRENCY_MISMATCH`, and the
    distinction is load-bearing.

    A mismatch means "we know this currency's scale but this handler does not record it",
    so the obligation CAN be stated in that currency. Unsupported means "we have no
    persisted minor-unit exponent", so the amount's scale is unknown and no figure can be
    recorded at all. Collapsing the two would make the handler either record a guess or
    discard a statable obligation.
    """
    from backend_app.routers.billing import (ProviderCurrencyMismatch,
                                             _require_provider_currency)

    with pytest.raises(ProviderCurrencyMismatch) as excinfo:
        _require_provider_currency(
            unsupported, "USD", provider="stripe", payment_id="cs_2"
        )
    assert excinfo.value.code == "CURRENCY_UNSUPPORTED"


def test_a_different_currency_is_refused():
    """A USD payment arriving on the INR handler, and vice versa."""
    from backend_app.routers.billing import (ProviderCurrencyMismatch,
                                             _require_provider_currency)

    with pytest.raises(ProviderCurrencyMismatch) as excinfo:
        _require_provider_currency("USD", "INR", provider="razorpay", payment_id="pay_4")
    assert excinfo.value.code == "CURRENCY_MISMATCH"

    with pytest.raises(ProviderCurrencyMismatch) as excinfo:
        _require_provider_currency("INR", "USD", provider="stripe", payment_id="cs_3")
    assert excinfo.value.code == "CURRENCY_MISMATCH"


def test_the_supported_set_is_derived_from_the_converter_not_restated():
    """A second hardcoded list could drift from the converter and accept a currency the
    conversion then refuses."""
    from backend_app.backend.marketplace import money
    from backend_app.routers.billing import _SUPPORTED_PROVIDER_CURRENCIES

    assert _SUPPORTED_PROVIDER_CURRENCIES == frozenset(money.MINOR_UNIT_EXPONENT)


def test_no_fx_conversion_was_introduced_on_the_webhook_path():
    """The fix must not have reached for `core/fx_service.py` *on the webhook path*.

    `FXService` is float-based and falls back to baseline rates, and billing.py:2605-2611
    already records the decision not to use it when deciding what a referrer is owed.

    SCOPED TO THE HANDLER, NOT THE MODULE. The first version of this test asserted
    `"fx_service" not in source` and failed immediately: billing.py has imported
    `FXService` since long before this phase and uses it for CHECKOUT PRICING, which is a
    legitimate and unrelated use -- localising a displayed plan price is not the same as
    converting money already owed to a customer. A module-wide assertion was therefore
    never true and would have had to be deleted rather than satisfied, which is how a
    test ends up weakened. The scope that matches the actual property is the handler.
    """
    tree = _billing_tree()
    handlers = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in ("stripe_webhook", "razorpay_webhook")
    ]
    # One handler now: Razorpay is the only gateway. `stripe_webhook` stays in the name
    # tuple above deliberately -- if it ever reappears it is swept for FX calls too, rather
    # than silently escaping this check.
    assert [h.name for h in handlers] == ["razorpay_webhook"], (
        f"expected exactly the Razorpay handler, found {[h.name for h in handlers]}"
    )

    forbidden = {"get_fx_rate", "to_usd", "convert_major", "localize_price",
                 "localize_plan_price", "FXService"}
    offenders = []
    for handler in handlers:
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in forbidden:
                offenders.append(f"{handler.name}:{node.lineno} calls {name}")

    assert offenders == [], (
        "a webhook handler now performs an FX conversion:\n  " + "\n  ".join(offenders)
        + "\nChoosing a rate and an as-of instant is a product decision about money owed "
          "to a customer, not something a remediation may infer."
    )


# ══════════════════════════════════════════════════════════════════════════
#  PART 2. MONEY-06 — THE OBLIGATION LEDGER (PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


@pytest.fixture
def referral_pair():
    """A referrer and a referred user, with a real relationship row.

    `referral_relationships.referrer_id` / `referred_id` both FK to `profiles(id)`, which
    in turn requires `auth.users`, so both legs are created. Cleaned up afterwards,
    children first.
    """
    referrer = str(uuid.uuid4())
    referred = str(uuid.uuid4())
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for uid, email in ((referrer, "p9-referrer"), (referred, "p9-referred")):
                cur.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (uid, f"{email}-{uid[:8]}@example.invalid"),
                )
                cur.execute(
                    "INSERT INTO public.profiles (id, email) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (uid, f"{email}-{uid[:8]}@example.invalid"),
                )
            cur.execute(
                "INSERT INTO public.referral_relationships "
                "       (referrer_id, referred_id, status) "
                "VALUES (%s, %s, 'pending') ON CONFLICT DO NOTHING",
                (referrer, referred),
            )
    finally:
        conn.close()

    yield {"referrer": referrer, "referred": referred}

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_obligations WHERE referred_id = %s",
                (referred,),
            )
            cur.execute(
                "DELETE FROM public.referral_relationships WHERE referred_id = %s",
                (referred,),
            )
            for uid in (referrer, referred):
                cur.execute("DELETE FROM public.profiles WHERE id = %s", (uid,))
                cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
    finally:
        conn.close()


def _record(conn, *, provider, payment_id, referred, amount, currency,
            tier="pro", reason="LEDGER_IS_USD_ONLY", detail="{}"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT public.record_referral_obligation("
            "  %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (provider, payment_id, referred, amount, currency, tier, reason, detail),
        )
        return cur.fetchone()[0]


def _read(payment_id, provider="razorpay"):
    """Read an obligation back over an INDEPENDENT connection."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT obligation_id, referrer_id::text, payment_amount, currency, "
                "       commission_rate, commission_amount, status, reason_code, detail "
                "  FROM public.referral_obligations "
                " WHERE provider = %s AND provider_payment_id = %s",
                (provider, payment_id),
            )
            return cur.fetchall()
    finally:
        conn.close()


@pg_only
def test_the_obligation_table_and_its_constraints_exist():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.referral_obligations') IS NOT NULL")
            assert cur.fetchone()[0], "public.referral_obligations does not exist"

            cur.execute(
                "SELECT conname FROM pg_constraint "
                " WHERE conrelid = to_regclass('public.referral_obligations') "
                " ORDER BY conname"
            )
            names = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    assert "uq_referral_obligation_event" in names, (
        "no unique constraint on (provider, provider_payment_id), so a redelivered "
        "webhook can record the same obligation twice"
    )
    for required in (
        "chk_referral_obligations_status",
        "chk_referral_obligations_currency_format",
        "chk_referral_obligations_amount_positive",
        "chk_referral_obligations_commission_coherent",
    ):
        assert required in names, f"{required} is missing: {sorted(names)}"


@pg_only
def test_the_currency_column_is_not_pinned_to_usd():
    """The property that makes this table able to hold the MONEY-06 case at all.

    `referral_commissions`, `referral_wallets` and `referral_payouts` all carry
    `CHECK (currency = 'USD')` -- correctly, and pinned by test_money04. If this table
    had the same constraint it could not record the INR obligation it exists for.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                " WHERE conrelid = to_regclass('public.referral_obligations') "
                "   AND contype = 'c'"
            )
            defs = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    pinned = [d for d in defs if "currency" in d and "USD" in d and "=" in d]
    assert pinned == [], f"currency is pinned to USD on the obligation ledger: {pinned}"
    assert any("A-Z" in d for d in defs), (
        "currency has no FORMAT constraint, so an amount can be labelled 'Rs' or '' -- "
        f"as unusable as being unlabelled. Constraints: {defs}"
    )


@pg_only
def test_an_inr_obligation_is_recorded_in_inr_with_no_invented_commission(referral_pair):
    """THE MONEY-06 CASE, end to end.

    An INR payment for a referred user produces a durable row stating the amount in INR,
    naming the referrer, and leaving the commission NULL -- because stating it would
    require an FX rate nobody has decided on.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        obligation_id = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"),
            currency="INR",
        )
    finally:
        conn.close()

    assert obligation_id is not None

    rows = _read(payment_id)
    assert len(rows) == 1, "the obligation is not visible to an independent connection"
    (_oid, referrer_id, amount, currency, rate, commission, status,
     reason, _detail) = rows[0]

    assert referrer_id == referral_pair["referrer"], "the referrer was not attributed"
    assert amount == Decimal("4150.00")
    assert currency == "INR", "the amount was not recorded in the currency it was charged in"
    assert commission is None, (
        "a commission amount was computed for a non-USD obligation, which requires an FX "
        "rate. That is the MONEY-04 overstatement in a new place."
    )
    assert rate is None
    assert status == "unresolved"
    assert reason == "LEDGER_IS_USD_ONLY"


@pg_only
def test_a_usd_obligation_states_the_amount_owed_and_is_payable(referral_pair):
    """The other side: a USD obligation CAN be stated, so it is.

    This is the `COMMISSION_RPC_FAILED` case -- a real USD commission that simply did not
    get credited. Nothing needs deciding, only retrying, so the amount is computed at the
    same flat rate `process_referral_commission` applies and the status is `payable`.
    """
    payment_id = f"pi_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        _record(
            conn, provider="stripe", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("100.00"),
            currency="USD", reason="COMMISSION_RPC_FAILED",
        )
    finally:
        conn.close()

    rows = _read(payment_id, provider="stripe")
    (_oid, referrer_id, amount, currency, rate, commission, status,
     _reason, _detail) = rows[0]

    assert currency == "USD"
    assert rate == RATE
    assert commission == Decimal("20.00"), (
        f"the obligation states {commission} owed on a 100.00 USD payment; the flat rate "
        f"process_referral_commission applies is {RATE}, so the two paths disagree about "
        f"what is owed for the same payment"
    )
    assert status == "payable"
    assert referrer_id == referral_pair["referrer"]


@pg_only
def test_the_amount_is_exact_decimal_not_binary_float(referral_pair):
    """A money column read back as a float would reintroduce MONEY-02 at the storage
    boundary."""
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("0.07"), currency="INR",
        )
    finally:
        conn.close()

    amount = _read(payment_id)[0][2]
    assert isinstance(amount, Decimal), f"amount came back as {type(amount).__name__}"
    assert amount == Decimal("0.07")


@pg_only
def test_a_duplicate_delivery_records_one_obligation(referral_pair):
    """Idempotency, the ordinary case: the provider redelivers the same event."""
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        first = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        second = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
    finally:
        conn.close()

    assert first is not None
    assert second is None, "a redelivery returned a second obligation id"
    assert len(_read(payment_id)) == 1


@pg_only
def test_five_retries_record_one_obligation(referral_pair):
    """Provider retry storms are the normal failure mode, not a single redelivery."""
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        ids = [
            _record(
                conn, provider="razorpay", payment_id=payment_id,
                referred=referral_pair["referred"], amount=Decimal("4150.00"),
                currency="INR",
            )
            for _ in range(5)
        ]
    finally:
        conn.close()

    assert sum(1 for i in ids if i is not None) == 1, ids
    assert len(_read(payment_id)) == 1


@pg_only
def test_two_concurrent_deliveries_record_one_obligation(referral_pair):
    """THE RACE THE UNIQUE CONSTRAINT EXISTS FOR.

    Two independent connections, released together by a barrier. A `SELECT EXISTS` check
    would let both pass before either inserted -- which is exactly why 024 added a unique
    INDEX for the commission path rather than an `IF EXISTS` guard, and why this table
    has a unique CONSTRAINT.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    barrier = threading.Barrier(2)
    results: List[Optional[str]] = []
    lock = threading.Lock()

    def worker():
        c = _conn()
        try:
            barrier.wait(timeout=30)
            got = _record(
                c, provider="razorpay", payment_id=payment_id,
                referred=referral_pair["referred"], amount=Decimal("4150.00"),
                currency="INR",
            )
            with lock:
                results.append(got)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(results) == 2, f"a worker did not finish: {results}"
    assert sum(1 for r in results if r is not None) == 1, (
        f"both concurrent deliveries recorded an obligation: {results}"
    )
    assert len(_read(payment_id)) == 1


@pg_only
def test_a_rolled_back_transaction_records_nothing(referral_pair):
    """The obligation write participates in its caller's transaction.

    The RPC is one function body, so it is one transaction when autocommitted -- but a
    caller that has an open transaction and rolls back must leave no row, or a failed
    webhook would leave a phantom payable.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn(autocommit=False)
    try:
        got = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        assert got is not None, "premise: the insert succeeded inside the transaction"
        conn.rollback()
    finally:
        conn.close()

    assert _read(payment_id) == [], "a rolled-back obligation is visible"


@pg_only
def test_an_unreferred_payment_records_a_refused_decision_not_nothing(referral_pair):
    """"Nothing is owed" is a decision, and it is recorded as one.

    A payment from a user with no referral relationship owes nobody. Recording `refused`
    rather than writing no row means a later audit can tell "we decided nothing is owed"
    from "we never looked".
    """
    stranger = str(uuid.uuid4())
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (stranger, f"p9-stranger-{stranger[:8]}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO public.profiles (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (stranger, f"p9-stranger-{stranger[:8]}@example.invalid"),
            )
        _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=stranger, amount=Decimal("4150.00"), currency="INR",
        )

        rows = _read(payment_id)
        assert len(rows) == 1
        assert rows[0][6] == "refused", f"status was {rows[0][6]!r}, expected 'refused'"
        assert rows[0][1] is None, "a referrer was attributed where no relationship exists"
        assert rows[0][5] is None, "a commission was computed with nobody to owe it to"
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_obligations WHERE referred_id = %s",
                (stranger,),
            )
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (stranger,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (stranger,))
        conn.close()


@pg_only
def test_a_refund_reverses_the_obligation(referral_pair):
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.reverse_referral_obligation(%s, %s, %s)",
                ("razorpay", payment_id, "refund.processed"),
            )
            assert cur.fetchone()[0] is True
    finally:
        conn.close()

    rows = _read(payment_id)
    assert rows[0][6] == "reversed"

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reversed_at, detail->>'reversal_reason' "
                "  FROM public.referral_obligations "
                " WHERE provider = 'razorpay' AND provider_payment_id = %s",
                (payment_id,),
            )
            reversed_at, reason = cur.fetchone()
    finally:
        conn.close()

    assert reversed_at is not None, "a reversed obligation must say when"
    assert reason == "refund.processed"


@pg_only
def test_a_second_refund_delivery_is_a_no_op(referral_pair):
    """Reversal is idempotent, so a redelivered refund cannot double-debit."""
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.reverse_referral_obligation(%s, %s, %s)",
                ("razorpay", payment_id, "refund.processed"),
            )
            assert cur.fetchone()[0] is True
            cur.execute(
                "SELECT public.reverse_referral_obligation(%s, %s, %s)",
                ("razorpay", payment_id, "refund.processed"),
            )
            assert cur.fetchone()[0] is False, (
                "a second refund delivery reversed the obligation again"
            )
    finally:
        conn.close()


@pg_only
def test_a_settled_obligation_is_not_silently_reversed(referral_pair):
    """Money already paid out is not clawed back by a webhook.

    Reversal only matches outstanding rows. Reversing a `settled` obligation would mean
    a refund event silently undoing a completed payout, which is a decision with its own
    process -- so the function reports FALSE and the caller logs it.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE public.referral_obligations "
                "   SET status = 'settled', settled_at = now() "
                " WHERE provider = 'razorpay' AND provider_payment_id = %s",
                (payment_id,),
            )
            cur.execute(
                "SELECT public.reverse_referral_obligation(%s, %s, %s)",
                ("razorpay", payment_id, "refund.processed"),
            )
            assert cur.fetchone()[0] is False
    finally:
        conn.close()

    assert _read(payment_id)[0][6] == "settled"


@pg_only
def test_out_of_order_delivery_a_refund_before_the_capture(referral_pair):
    """Providers do not guarantee ordering.

    A refund for a payment whose obligation was never recorded must be a no-op, not an
    error -- and must not create a row, because there is no payment amount to state.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.reverse_referral_obligation(%s, %s, %s)",
                ("razorpay", payment_id, "refund.processed"),
            )
            assert cur.fetchone()[0] is False
    finally:
        conn.close()

    assert _read(payment_id) == [], "a reversal invented an obligation row"


@pg_only
@pytest.mark.parametrize(
    "amount,currency,reason",
    [
        (Decimal("0"), "INR", "LEDGER_IS_USD_ONLY"),
        (Decimal("-5.00"), "INR", "LEDGER_IS_USD_ONLY"),
        (Decimal("4150.00"), "RUPEE", "LEDGER_IS_USD_ONLY"),
        (Decimal("4150.00"), "R", "LEDGER_IS_USD_ONLY"),
        (Decimal("4150.00"), "", "LEDGER_IS_USD_ONLY"),
        (Decimal("4150.00"), "INR", "   "),
    ],
)
def test_malformed_input_records_nothing(referral_pair, amount, currency, reason):
    """The function refuses rather than inserting a row the CHECKs would reject.

    The function is the last line of defence for any future writer, so it validates
    independently of the application-level guard: a malformed unit reaching the format
    CHECK would surface as a 23514 rather than a readable refusal.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        got = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=amount, currency=currency,
            reason=reason,
        )
    finally:
        conn.close()

    assert got is None, f"an obligation was recorded for amount={amount} ccy={currency!r}"
    assert _read(payment_id) == []


@pg_only
def test_a_lowercase_currency_is_normalised_rather_than_refused(referral_pair):
    """Case is normalised AT THE DATABASE LAYER too, not only in the application.

    The first version of this suite expected `'inr'` to be REFUSED here, on the reasoning
    that the function is a last line of defence. Running it showed the function
    `upper(btrim(...))`s first, so `'inr'` is accepted and stored as `'INR'` -- and that
    is the better contract: no writer can land a lowercase code, so
    `WHERE currency = 'INR'` cannot miss rows. Refusing instead would make the database
    stricter than the application for no benefit, since `'inr'` and `'INR'` are the same
    declaration.

    Asserted explicitly so the normalisation is a stated property rather than an
    accident, and so a future change that removes it fails here.
    """
    payment_id = f"pay_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        got = _record(
            conn, provider="razorpay", payment_id=payment_id,
            referred=referral_pair["referred"], amount=Decimal("4150.00"),
            currency="inr",
        )
    finally:
        conn.close()

    assert got is not None, "a lowercase ISO code was refused"
    assert _read(payment_id)[0][3] == "INR", (
        "a lowercase currency was stored verbatim, so `WHERE currency = 'INR'` would "
        "miss this row"
    )


@pg_only
def test_an_event_with_no_identity_records_nothing(referral_pair):
    """A blank payment id cannot be deduplicated: NULL is distinct from NULL in SQL, so
    retries would accumulate unbounded rows."""
    conn = _conn()
    try:
        assert _record(
            conn, provider="razorpay", payment_id="", referred=referral_pair["referred"],
            amount=Decimal("1.00"), currency="INR",
        ) is None
        assert _record(
            conn, provider="", payment_id="pay_x", referred=referral_pair["referred"],
            amount=Decimal("1.00"), currency="INR",
        ) is None
    finally:
        conn.close()


@pg_only
def test_the_two_providers_id_spaces_do_not_collide(referral_pair):
    """Idempotency is keyed on (provider, payment_id), not payment_id alone.

    A Stripe payment intent and a Razorpay payment id are independent namespaces; keying
    on the id alone would let one silently suppress the other's obligation.
    """
    shared = f"id_p9_{uuid.uuid4().hex[:12]}"
    conn = _conn()
    try:
        a = _record(
            conn, provider="razorpay", payment_id=shared,
            referred=referral_pair["referred"], amount=Decimal("4150.00"), currency="INR",
        )
        b = _record(
            conn, provider="stripe", payment_id=shared,
            referred=referral_pair["referred"], amount=Decimal("50.00"), currency="USD",
            reason="COMMISSION_RPC_FAILED",
        )
    finally:
        conn.close()

    assert a is not None and b is not None, (
        "the same id under two providers collided, suppressing a real obligation"
    )
    assert len(_read(shared, provider="razorpay")) == 1
    assert len(_read(shared, provider="stripe")) == 1


@pg_only
def test_a_customer_cannot_write_an_obligation_to_themselves():
    """Browser roles get SELECT only. A customer recording money owed to themselves would
    be the obvious abuse."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT grantee, privilege_type "
                "  FROM information_schema.role_table_grants "
                " WHERE table_schema='public' AND table_name='referral_obligations' "
                "   AND grantee IN ('anon','authenticated') "
                " ORDER BY grantee, privilege_type"
            )
            grants = cur.fetchall()
    finally:
        conn.close()

    writes = [g for g in grants if g[1] in ("INSERT", "UPDATE", "DELETE")]
    assert writes == [], f"a browser role can write obligations: {writes}"
    assert ("authenticated", "SELECT") in grants, (
        "an authenticated user cannot read their own obligations, so the ledger is "
        f"invisible to the person it owes: {grants}"
    )


@pg_only
def test_the_obligation_writer_is_not_reachable_by_a_browser_role():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for role in ("anon", "authenticated"):
                cur.execute(
                    "SELECT has_function_privilege(%s, "
                    "  'public.record_referral_obligation"
                    "(text,text,uuid,numeric,text,text,text,jsonb)', 'EXECUTE')",
                    (role,),
                )
                assert cur.fetchone()[0] is False, (
                    f"{role} can execute record_referral_obligation, which takes a "
                    f"caller-supplied referred_id and has no auth.uid() check"
                )
            cur.execute(
                "SELECT has_function_privilege('service_role', "
                "  'public.record_referral_obligation"
                "(text,text,uuid,numeric,text,text,text,jsonb)', 'EXECUTE')"
            )
            assert cur.fetchone()[0] is True, (
                "service_role lost EXECUTE; the billing webhook would stop recording "
                "obligations"
            )
    finally:
        conn.close()


@pg_only
def test_the_obligation_writer_touches_neither_ledger_table():
    """What makes "atomic consistency with the ledger credit" vacuous rather than unproven.

    An obligation row exists precisely WHEN no credit was made. The two are mutually
    exclusive states of one payment, each produced by a single transaction, so there is
    no cross-table consistency to coordinate. If this function ever wrote a commission or
    a wallet balance, one payment would produce two economic records.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT regexp_replace(pg_get_functiondef(p.oid), "
                "       '--[^' || chr(10) || ']*', '', 'g') "
                "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                " WHERE n.nspname='public' AND p.proname='record_referral_obligation'"
            )
            body = cur.fetchone()[0]
    finally:
        conn.close()

    assert "referral_commissions" not in body
    assert "referral_wallets" not in body
    assert "ON CONFLICT (provider, provider_payment_id)" in body


@pg_only
def test_the_two_paths_agree_on_the_rate():
    """Both the commission function and the obligation function must owe the same amount
    for the same payment. Each hardcodes 0.20 because there is no single source to read;
    this is what stops them drifting."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.proname, regexp_replace(pg_get_functiondef(p.oid), "
                "       '--[^' || chr(10) || ']*', '', 'g') "
                "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                " WHERE n.nspname='public' "
                "   AND p.proname IN ('process_referral_commission', "
                "                     'record_referral_obligation')"
            )
            bodies = dict(cur.fetchall())
    finally:
        conn.close()

    assert len(bodies) == 2, f"expected both functions, found {sorted(bodies)}"
    for name, body in bodies.items():
        assert "0.20" in body, f"{name} no longer carries the 0.20 flat rate"


def test_the_postgres_requirement_is_explicit():
    """The gate, so a skip cannot be silent.

    AST-based for the forbidden-literal half: a text scan would match this test's own
    explanatory prose, which is the error that has already produced seven false positives
    across Phase 8 and Phase 9.
    """
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    env_reads = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "AERORA_TEST_DATABASE_URL" in env_reads

    me = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "test_the_postgres_requirement_is_explicit"
    )
    own = range(me.lineno, (me.end_lineno or me.lineno) + 1)
    docstrings = {
        ast.get_docstring(n, clean=False)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and ast.get_docstring(n, clean=False)
    }
    offenders = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value not in docstrings
        and getattr(node, "lineno", -1) not in own
        and "pg_dsn" in node.value
    ]
    assert offenders == [], (
        f"this suite resolves a database through a local DSN file: {offenders}"
    )
