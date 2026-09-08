"""
backend_app/backend/marketplace/listing_projection.py - the one public Listing serialiser.

Spec: marketplace-subscriptions-paper-trading task 8.1. ``design.md`` ->
"``marketplace/listing_projection.py`` - the one public serialiser".
Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.9, 7.1.

Exposes
-------
PUBLIC_LISTING_FIELDS              the allow-list - the *only* keys a public response may carry
LISTING_SELECT                     the explicit column list that replaces ``select("*")``
DENIED_LISTING_COLUMNS             the columns that must never reach a non-owner (Req 6.4)
CONDITION_OUTCOME_METRICS          the per-condition metric keys, outcomes only (Req 6.3)
SUBSCRIPTION_PERIOD_NOMINAL_DAYS   the catalogue label for "one calendar month"
PROTECTED_LOGIC_TOKEN_MIN_LENGTH   the ``len(text) >= 3`` floor of the token set
STRUCTURAL_RESPONSE_KEYS           the response's own key vocabulary - schema, not data
project_listing(...)               row -> public dict, by explicit assignment (Reqs 6.1 … 6.9)
protected_logic_tokens(...)        every Protected_Logic substring worth searching for (Req 6.8)
deep_scalars(...)                  every key and every leaf value, at any nesting depth
assert_contains_no_protected_logic(...)  substring containment check (Requirements 6.8, 7.1)

WHY THIS MODULE IS PURE
-----------------------
Same layering rule ``money.py``, ``subscription_period.py`` and ``evidence_validator.py``
already follow: import pulls only the standard library and ``money`` - no FastAPI, no database
handle, no HTTP client, no clock read. The row and its evidence summaries always arrive as
arguments, so the projection is decidable with no fixture and no connection: that is what
makes Requirement 6's allow-list property-testable (task 8.3, P-46 … P-48) rather than merely
reviewable.

WHY THE OUTPUT IS BUILT, NOT FILTERED
-------------------------------------
The shape this replaces is ``select("*")`` followed by ``row.pop("author_id")``. That shape is
default-open: a column added to ``library_strategies`` by a future migration is exposed the
moment it exists, and the only thing standing between a new column and a public response is
somebody remembering to add another ``pop``. :func:`project_listing` inverts the default. It
starts from an empty dict and performs one explicit assignment per allow-listed field, so a
new column reaches a non-owner only if someone writes a line here that puts it there
(Requirement 6.1). The row is never copied, never mutated and never deleted from - the caller's
row object is left exactly as the driver returned it, which also means the owner-facing paths
that share the same read are unaffected.

The closing ``assert set(out) <= PUBLIC_LISTING_FIELDS`` is a real runtime assertion and not a
comment. It costs one set comparison per Listing and it is the cheapest possible guard against
a future edit in this file adding a key the allow-list does not name.

WHERE EACH FIGURE COMES FROM, AND WHY THERE IS EXACTLY ONE SOURCE PER FIELD
--------------------------------------------------------------------------
* The aggregate figures (``performance_summary``, ``risk_metrics``, ``max_drawdown_pct``) are
  the publish-time ``backtest_*`` aggregate persisted on the Listing row. The catalogue read is
  two round trips - the Listing page and the batched alias read (``design.md`` -> "Round trips
  per screen") - so it holds no evidence rows, and an aggregate that could come from either the
  row or the evidence set would have two sources that can disagree. One source per field, read
  and not recomputed: nothing here invents a statistic the persisted data does not contain
  (Requirement 28.3's prohibition applied to the catalogue).
* The per-condition figures (``condition_summaries``) come from the immutable
  ``marketplace_backtest_evidence`` copy, which only the detail read fetches. ``None`` for
  ``evidence_summaries`` means "not read" and leaves ``condition_count`` to the row's stored
  column; an empty sequence means "read, and empty".
* ``creator_alias`` is resolved by ``marketplace/aliases.py`` before the call and passed in.
  This module cannot read ``profiles``, so it cannot accidentally return an ``author_id``
  (Requirement 6.5), and it refuses an alias that *is* the row's ``author_id``.
* Money is ``price_minor`` (the exact integer) plus ``price_display`` (a string produced at
  ``money.to_major``'s presentation boundary). No ``float`` is constructed here, and the legacy
  ``price NUMERIC(10,2)`` column is never read back as the amount (Requirement 8.12).

WHY ``author_id`` IS IN ``LISTING_SELECT`` AND IN ``DENIED_LISTING_COLUMNS``
---------------------------------------------------------------------------
They answer different questions. ``LISTING_SELECT`` is what the *query* asks the database for:
the handler needs ``author_id`` to resolve the creator alias in the batched read and to decide
whether the caller is the owner. ``DENIED_LISTING_COLUMNS`` is what the *response* may never
carry. Selecting a column is not exposing it - the projection is what exposes, and it has no
assignment for ``author_id`` (Requirement 6.4). The import-time check below states the invariant
that actually matters: the two sets that describe the response, ``PUBLIC_LISTING_FIELDS`` and
``DENIED_LISTING_COLUMNS``, are disjoint.

``LISTING_SELECT`` additionally names ``supported_timeframes``, ``market_type``,
``condition_count`` and the six ``backtest_*`` aggregate columns beyond the list written in
``design.md``: every one of them is read by an assignment below, and a column the projection
reads but the query does not request would arrive as ``None`` and silently blank a public field.

WHY ``avg_rating`` AND ``rating_count`` ARE OMITTED RATHER THAN ZEROED
---------------------------------------------------------------------
Requirement 6.9 is about the difference between "no one has rated this" and "everyone rated it
zero". A ``0`` rating count with a ``0.0`` average is a figure the rating infrastructure never
recorded, and a client cannot tell it apart from a real one. So when ``rating_count`` is 0 or
``avg_rating`` is ``NULL`` the two keys are never assigned - the response has no rating fields
at all, and the absence is the honest answer. The allow-list is an upper bound on the key set,
not a required key set, which is what makes the omission representable.

WHAT ``condition_summaries`` DELIBERATELY DOES NOT CARRY
-------------------------------------------------------
``label`` and outcome metrics only. No ``dataset``, no ``start_date``/``end_date``, no
``dag_hash``, no ``dataset_checksum``, no ``blueprint``, no ``version_id``, no
``source_backtest_id``, no ``engine_version``, no ``executed_bar_count``, and no node,
indicator, threshold or model identifier (Requirement 6.3). The metric list is a frozen
tuple, :data:`CONDITION_OUTCOME_METRICS`, so a column added to the evidence table cannot join
the summary by default. ``dataset`` and the window are excluded even though neither is a graph
detail: a dataset name plus a date range is enough to begin reconstructing what the strategy
trades, which is the reconstruction Requirement 6.3 exists to prevent. ``executed_bar_count``
is excluded for the same reason - it is the window length restated in bars.

Metric values are passed through exactly as the evidence row holds them. No coercion, no
rescaling and no rounding happens here: ``max_drawdown_pct`` and ``win_rate_pct`` are
percentages because those columns say so, and re-deriving them would be guessing at a unit
that is already recorded.

WHY THE TOKEN SET LIVES IN THIS FILE
------------------------------------
``protected_logic_tokens`` and ``assert_contains_no_protected_logic`` sit beside the projection
on purpose. If the containment check owned its own idea of what Protected_Logic is, the check
and the projection could drift apart and the suite would go green while a leak was live. One
module, one definition (Requirement 6.8).

The ``len(text) >= 3`` floor excludes tokens so short that they collide with ordinary prose and
identifiers - ``"1"``, ``"id"``, ``"up"``. Without it the assertion is vacuously red for every
response, which is indistinguishable from having no assertion at all. The property tests seed
node ids, indicator names, parameter names, threshold values and model path segments that are
all at least three characters, so the floor excludes nothing real (``design.md`` -> "Mechanical
Protected_Logic containment").

``STRUCTURAL_RESPONSE_KEYS`` is the other side of that same coin. The check searches every
*value* in a body at every depth, and it searches the mapping keys too - a key is where a
``select("*")`` leak surfaces first. But the keys this API emits are enumerated in advance by
Requirement 6.2 and asserted against the projection's output on every call, so they are
identical for every Listing and for a caller who asked about no Listing at all. A strategy that
happens to name an indicator ``details`` does not turn the error envelope's ``details`` key into
a disclosure. The key vocabulary is schema, not data, and it is exempt by name; no value ever
is, including the values stored under those keys. See the constant for the full argument.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The alias read. ``marketplace/aliases.py`` (task 8.2) owns the batched
  ``profiles.select("id,display_name")`` and the refusal when it fails; this module takes the
  resolved string.
* The publication-state predicate. Whether a Listing is visible at all is the query's business
  (Requirement 6.10 - a non-published Listing must 404 identically to an unknown one), and a
  projection that ran on a row that should never have been read would already be too late.
* The rate limits of Requirement 6.7 and the HTTP status of Requirement 7.1. Those are the
  router's, and ``library.py`` is repointed at this module by task 16.
* Any ``select("*")``-shaped convenience helper. The absence is the point.
"""

from __future__ import annotations

from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from backend_app.backend.marketplace import money

__all__ = [
    "PUBLIC_LISTING_FIELDS",
    "LISTING_SELECT",
    "DENIED_LISTING_COLUMNS",
    "CONDITION_OUTCOME_METRICS",
    "SUBSCRIPTION_PERIOD_NOMINAL_DAYS",
    "PROTECTED_LOGIC_TOKEN_MIN_LENGTH",
    "STRUCTURAL_RESPONSE_KEYS",
    "STRATEGY_PROTECTED_LOGIC_COLUMNS",
    "VERSION_PROTECTED_LOGIC_COLUMNS",
    "BACKTEST_PROTECTED_LOGIC_COLUMNS",
    "project_listing",
    "protected_logic_tokens",
    "deep_scalars",
    "assert_contains_no_protected_logic",
]

# ══════════════════════════════════════════════════════════════════════════
# THE ALLOW-LIST (Requirements 6.1, 6.2)
# ══════════════════════════════════════════════════════════════════════════

#: Every key a public Listing response may carry, and no others (Requirements 6.1, 6.2). This
#: is an upper bound on the key set, not a required key set: Requirement 6.9's rating omission
#: and an unread evidence set both produce a strict subset.
PUBLIC_LISTING_FIELDS: FrozenSet[str] = frozenset(
    {
        "listing_id",
        "name",
        "description",
        "category",
        "difficulty",
        "tags",
        "supported_timeframes",
        "symbol",
        "exchange_id",
        "market_type",
        "performance_summary",
        "condition_summaries",
        "risk_metrics",
        "max_drawdown_pct",
        "condition_count",
        "validation_status",
        "price_minor",
        "price_display",
        "currency",
        "subscription_period_days",
        "creator_alias",
        "published_at",
        "subscriber_count",
        "avg_rating",
        "rating_count",
        "source_cloning_enabled",
    }
)

#: The explicit ``library_strategies`` column list every public read selects, replacing
#: ``select("*")``. Ordered as ``design.md`` writes it, then the columns the assignments below
#: read: the two array/enum columns of Requirement 6.2, the stored ``condition_count``, and the
#: publish-time ``backtest_*`` aggregate that is the sole source of the aggregate figures.
#:
#: ``author_id`` is requested for the batched alias read and the ownership decision, never for
#: the response - see the module docstring. ``price`` (the legacy ``NUMERIC(10,2)`` mirror) is
#: requested because the existing catalogue readers still consult it during the task 16
#: migration; :func:`project_listing` does not read it.
LISTING_SELECT: str = (
    "id,name,description,category,difficulty,tags,symbol,timeframe,exchange_id,"
    "price,price_minor,currency,subscriber_count,avg_rating,rating_count,published_at,"
    "verification_status,source_cloning_enabled,author_id,"
    "supported_timeframes,market_type,condition_count,"
    "backtest_total_return_pct,backtest_sharpe_ratio,backtest_max_drawdown_pct,"
    "backtest_win_rate_pct,backtest_profit_factor,backtest_total_trades"
)

#: The columns that must never reach a non-owner (Requirement 6.4), named so that a test can
#: populate every one of them and assert none appears in the projection's output. Membership
#: here is a statement about the *response*, not about the query - ``author_id`` is selected and
#: still denied.
DENIED_LISTING_COLUMNS: FrozenSet[str] = frozenset(
    {
        "author_id",
        "source_strategy_id",
        "moderated_by",
        "moderation_notes",
        "moderated_at",
        "deployment_requirements",
        "version_history",
        "evaluation_score",
        "equity_curve_snapshot",
        "has_ml_model",
        "node_count",
        "risk_stop_loss_pct",
        "risk_take_profit_pct",
        "risk_max_position_size",
        "risk_max_drawdown_pct",
        "is_active",
        "moderation_status",
        "updated_at",
    }
)

#: The per-condition metrics a public summary carries: outcomes, nothing else (Requirement 6.3).
#: Frozen and explicit, so a column added to ``marketplace_backtest_evidence`` cannot join the
#: summary by default. ``final_capital``, ``initial_capital``, ``commission``, ``slippage``,
#: ``dataset``, ``start_date``, ``end_date``, ``dataset_checksum``, ``dag_hash``,
#: ``engine_version``, ``executed_bar_count``, ``version_id`` and ``source_backtest_id`` are all
#: absent deliberately - see the module docstring.
CONDITION_OUTCOME_METRICS: Tuple[str, ...] = (
    "total_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown_pct",
    "win_rate_pct",
    "profit_factor",
    "total_trades",
)

#: The Listing-row columns the aggregate performance figures are read from, as
#: ``(public key, column)`` pairs. The Listing carries the publish-time aggregate; the
#: projection reads it rather than recomputing one.
_PERFORMANCE_SUMMARY_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("total_return_pct", "backtest_total_return_pct"),
    ("win_rate_pct", "backtest_win_rate_pct"),
    ("profit_factor", "backtest_profit_factor"),
    ("total_trades", "backtest_total_trades"),
)

#: The risk figures, same rule. ``max_drawdown_pct`` also appears as a top-level field because
#: Requirement 6.2 names maximum drawdown in its own right; both readings are the one column.
_RISK_METRIC_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("sharpe_ratio", "backtest_sharpe_ratio"),
    ("max_drawdown_pct", "backtest_max_drawdown_pct"),
)

#: The catalogue label for the Subscription_Period length Requirement 6.2 asks for. The
#: Subscription_Period *is* one calendar month (Requirement 11.4), whose true length is 28 to 31
#: days and is not knowable from a Listing row, so this figure labels the offering and nothing
#: more. No arithmetic consumes it: the authoritative period start and expiry are computed by
#: ``subscription_period.period_for_activation`` from the payment confirmation instant, and that
#: is the only thing that decides when access ends.
SUBSCRIPTION_PERIOD_NOMINAL_DAYS: int = 30

# The two sets that describe the response must not overlap. Stated at import so that an edit
# adding a denied column to the allow-list fails immediately and loudly, rather than at whatever
# later moment a reviewer happens to read both lists.
assert not (PUBLIC_LISTING_FIELDS & DENIED_LISTING_COLUMNS), (
    "a column cannot be both allow-listed and denied: "
    f"{sorted(PUBLIC_LISTING_FIELDS & DENIED_LISTING_COLUMNS)}"
)


# ══════════════════════════════════════════════════════════════════════════
# THE PROJECTION (Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.9)
# ══════════════════════════════════════════════════════════════════════════


def project_listing(
    row: Any,
    evidence_summaries: Optional[Sequence[Any]] = None,
    creator_alias: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the public view of one Listing, built key by key.

    Args:
        row: One ``library_strategies`` row - a mapping, as ``supabase-py`` returns, or any
            object exposing the columns as attributes. It is read only: not copied, not
            mutated, and nothing is deleted from it.
        evidence_summaries: The Listing's ``marketplace_backtest_evidence`` rows in condition
            order, or ``None`` when the caller did not read them (the catalogue paths do not).
            ``None`` leaves ``condition_count`` to the row's stored column; an empty sequence
            means the evidence was read and is empty.
        creator_alias: The display alias resolved server-side by ``marketplace/aliases.py``
            (Requirement 6.5). Never a user identifier, an email address or an authentication
            identity.

    Returns:
        A fresh ``dict`` whose key set is a subset of :data:`PUBLIC_LISTING_FIELDS`.

    Raises:
        ValueError: ``creator_alias`` is missing, blank, or equal to the row's ``author_id`` -
            each of which would put an owner identity in a public response (Requirement 6.5).
            Also raised when ``rating_count`` is present but not a whole number.
        money.MoneyError: ``price_minor`` is present but is not an admissible integer number of
            Minor_Units, or ``currency`` has no persisted minor-unit exponent. The bad row is
            refused rather than served with a fabricated price.
    """
    alias = _checked_alias(creator_alias, row)

    out: Dict[str, Any] = {}

    # ── Identity and descriptive metadata (Requirement 6.2) ──────────────
    out["listing_id"] = _read(row, "id")
    out["name"] = _read(row, "name")
    out["description"] = _read(row, "description")
    out["category"] = _read(row, "category")
    out["difficulty"] = _read(row, "difficulty")
    # A fresh list, so a caller mutating the response cannot reach into the driver's row.
    out["tags"] = list(_read(row, "tags") or ())

    # ── Asset and market information (Requirement 6.2) ───────────────────
    out["supported_timeframes"] = _supported_timeframes(row)
    out["symbol"] = _read(row, "symbol")
    out["exchange_id"] = _read(row, "exchange_id")
    out["market_type"] = _read(row, "market_type")

    # ── Outcome figures only (Requirement 6.3) ───────────────────────────
    out["performance_summary"] = {
        key: _read(row, column) for key, column in _PERFORMANCE_SUMMARY_SOURCES
    }
    out["risk_metrics"] = {
        key: _read(row, column) for key, column in _RISK_METRIC_SOURCES
    }
    out["max_drawdown_pct"] = _read(row, "backtest_max_drawdown_pct")
    out["condition_summaries"] = _condition_summaries(evidence_summaries)
    out["condition_count"] = (
        _read(row, "condition_count")
        if evidence_summaries is None
        else len(evidence_summaries)
    )
    out["validation_status"] = _read(row, "verification_status")

    # ── Price, currency and period (Requirements 6.2, 8.12) ──────────────
    price_minor = _read(row, "price_minor")
    currency = _read(row, "currency")
    out["price_minor"] = price_minor
    out["price_display"] = _price_display(price_minor, currency)
    out["currency"] = currency
    out["subscription_period_days"] = SUBSCRIPTION_PERIOD_NOMINAL_DAYS

    # ── Creator, publication and social counters ─────────────────────────
    out["creator_alias"] = alias
    out["published_at"] = _read(row, "published_at")
    out["subscriber_count"] = _read(row, "subscriber_count")
    out["source_cloning_enabled"] = bool(_read(row, "source_cloning_enabled"))

    # ── Ratings: present only when the infrastructure holds values (Req 6.9) ──
    avg_rating = _read(row, "avg_rating")
    rating_count = _whole_number(_read(row, "rating_count"), "rating_count")
    if avg_rating is not None and rating_count:
        out["avg_rating"] = avg_rating
        out["rating_count"] = rating_count

    # The guard, not a comment. One set comparison, always on.
    assert set(out) <= PUBLIC_LISTING_FIELDS, (
        "listing projection produced fields outside the allow-list: "
        f"{sorted(set(out) - PUBLIC_LISTING_FIELDS)}"
    )
    return out


def _condition_summaries(
    evidence_summaries: Optional[Sequence[Any]],
) -> List[Dict[str, Any]]:
    """One ``{label, …outcome metrics}`` dict per Backtest_Condition (Requirement 6.3).

    The label is positional - ``"Condition 1"``, ``"Condition 2"``, … - because it is the only
    thing Requirement 6.3 permits in place of an identifier. It carries no ``source_backtest_id``
    and no ``dataset``, so it names a condition without describing it.
    """
    if not evidence_summaries:
        return []
    return [
        {
            "label": f"Condition {index}",
            **{metric: _read(summary, metric) for metric in CONDITION_OUTCOME_METRICS},
        }
        for index, summary in enumerate(evidence_summaries, start=1)
    ]


def _supported_timeframes(row: Any) -> List[str]:
    """The Listing's timeframes, falling back to the single tested ``timeframe``.

    ``supported_timeframes TEXT[]`` is an additive column, so a Listing published before that
    migration carries ``NULL`` for it while still carrying the ``timeframe`` it was backtested
    on. Reporting that one timeframe is the truthful narrower answer; inventing a wider set
    would claim support the evidence does not show.
    """
    declared = _read(row, "supported_timeframes")
    if declared:
        return list(declared)
    timeframe = _read(row, "timeframe")
    return [timeframe] if timeframe else []


def _price_display(price_minor: Any, currency: Any) -> Optional[str]:
    """The price as a major-unit string, or ``None`` for a Listing carrying no price.

    Formatting goes through :func:`money.to_major`, the sanctioned presentation boundary, and
    the result is a ``str`` - so no ``float`` is constructed here and none can be constructed
    from this field downstream (Requirements 8.12, 8.13). A ``price_minor`` that is not an
    admissible integer, or a currency with no persisted exponent, raises rather than rendering
    an approximation.
    """
    if price_minor is None or currency is None:
        return None
    return str(money.to_major(price_minor, currency))


def _checked_alias(creator_alias: Optional[str], row: Any) -> str:
    """Return the alias, refusing anything that would disclose the creator's identity.

    Requirement 6.5 is the whole reason this check exists: the alias is the *only* creator
    representation a public response carries, so an empty alias (which invites a caller-side
    fallback to some other field) and an alias that is really the ``author_id`` are both
    failures worth raising on rather than serving.
    """
    if not isinstance(creator_alias, str) or not creator_alias.strip():
        raise ValueError(
            "creator_alias must be a non-empty display alias resolved server-side; "
            "resolve it with marketplace.aliases.resolve_aliases before projecting"
        )
    author_id = _read(row, "author_id")
    if author_id is not None and creator_alias == str(author_id):
        raise ValueError(
            "creator_alias is the row's author_id; a public Listing carries a display alias "
            "only, never the creator's user identifier"
        )
    return creator_alias


def _whole_number(value: Any, what: str) -> Optional[int]:
    """``value`` as an ``int``, or ``None`` when absent. Raises on a non-numeric value."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number, got a bool: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a whole number, got {value!r}") from exc


# ══════════════════════════════════════════════════════════════════════════
# PROTECTED_LOGIC CONTAINMENT (Requirements 6.3, 6.8, 7.1)
# ══════════════════════════════════════════════════════════════════════════

#: The floor on token length. Below it a token collides with ordinary prose - see the module
#: docstring on why a vacuously failing assertion is worse than none.
PROTECTED_LOGIC_TOKEN_MIN_LENGTH: int = 3

#: The ``strategies`` columns that express how a strategy decides (Glossary: Protected_Logic).
STRATEGY_PROTECTED_LOGIC_COLUMNS: Tuple[str, ...] = (
    "buy_logic",
    "sell_logic",
    "indicators",
    "risk",
    "ml_model_path",
)

#: The ``strategy_versions`` graph documents.
VERSION_PROTECTED_LOGIC_COLUMNS: Tuple[str, ...] = (
    "blueprint",
    "graph_json",
    "execution_graph",
)

#: The ``strategy_backtests`` columns that fingerprint the graph and the data behind it.
BACKTEST_PROTECTED_LOGIC_COLUMNS: Tuple[str, ...] = (
    "blueprint",
    "dag_hash",
    "dataset_checksum",
)

#: Every key a Marketplace response emits from its own fixed vocabulary, irrespective of which
#: Listing - or whether any Listing - is being described. One place, so a reader can see the
#: whole set at once and a new response key has one obvious home.
#:
#: WHY A KEY THE API EMITS UNCONDITIONALLY CANNOT BE A DISCLOSURE
#: -------------------------------------------------------------
#: Requirement 6.8 is written about what a *field* contains: "no field of any Marketplace_API
#: … response … contains any part of that Protected_Logic". Requirement 7.1 uses the same
#: wording ("from every field of every response"). The *names* of those fields are not response
#: data at all - Requirement 6.2 enumerates them in advance, and :data:`PUBLIC_LISTING_FIELDS`
#: is that enumeration, checked against the projection's output on every call. A name from that
#: enumeration appears in the body of every Listing response, including a Listing whose strategy
#: has no indicator, no node and no model: it is therefore constant across strategies and
#: carries no information about any of them. If a strategy happens to name an indicator
#: ``details`` or a node ``avg_rating``, the response's ``details`` and ``avg_rating`` keys still
#: say nothing the caller did not already know from the published schema, so treating them as a
#: leak would report a spelling coincidence as a disclosure - and, worse, would train a reader
#: to discount the check.
#:
#: The exemption is for *key positions only*. Every value, at every depth, is searched
#: unconditionally - including the values stored under these keys. A node identifier returned as
#: the value of ``name``, or inside ``details``, or embedded in ``message``, is exactly the leak
#: this check exists to find, and none of them is exempt.
STRUCTURAL_RESPONSE_KEYS: FrozenSet[str] = frozenset(
    PUBLIC_LISTING_FIELDS
    # The nested keys ``project_listing`` builds inside its own fields: the positional
    # Backtest_Condition label of Requirement 6.3, the per-condition outcome metrics, and the
    # aggregate performance and risk metric names.
    | {"label"}
    | set(CONDITION_OUTCOME_METRICS)
    | {key for key, _ in _PERFORMANCE_SUMMARY_SOURCES}
    | {key for key, _ in _RISK_METRIC_SOURCES}
    # The structured error envelope - ``errors.structured_error_body`` and
    # ``StructuredError.to_error_object``, the shape Requirement 7.1's bodies take on the wire.
    | {"error", "code", "message", "details", "request_id"}
)


def protected_logic_tokens(
    strategy_row: Any = None,
    version_row: Any = None,
    backtest_rows: Optional[Sequence[Any]] = None,
) -> FrozenSet[str]:
    """Every substring of a strategy's Protected_Logic worth searching a response for.

    Walks each Protected_Logic document to its leaves and keeps every key and every value whose
    ``str()`` form is at least :data:`PROTECTED_LOGIC_TOKEN_MIN_LENGTH` characters long: node
    identifiers, node types, connection endpoints, indicator names, parameter names, threshold
    values, model path segments, and the graph and dataset fingerprints. Keys are kept as well
    as values because a key is itself a structural fact about the graph.

    Args:
        strategy_row: The ``strategies`` row, or ``None``.
        version_row: The ``strategy_versions`` row, or ``None``.
        backtest_rows: The ``strategy_backtests`` rows, or ``None``.

    Returns:
        The token set. Absent rows and ``NULL`` columns contribute nothing, so a caller can
        pass whichever documents it holds.
    """
    tokens: Set[str] = set()

    documents: List[Any] = []
    for column in STRATEGY_PROTECTED_LOGIC_COLUMNS:
        documents.append(_read(strategy_row, column))
    for column in VERSION_PROTECTED_LOGIC_COLUMNS:
        documents.append(_read(version_row, column))
    for backtest_row in backtest_rows or ():
        for column in BACKTEST_PROTECTED_LOGIC_COLUMNS:
            documents.append(_read(backtest_row, column))

    for document in documents:
        if document is None:
            continue
        for scalar in deep_scalars(document):
            text = str(scalar)
            if len(text) >= PROTECTED_LOGIC_TOKEN_MIN_LENGTH:
                tokens.add(text)

    return frozenset(tokens)


def deep_scalars(value: Any) -> Iterator[Any]:
    """Yield every key and every leaf value of ``value``, at any nesting depth.

    Mappings contribute their keys as well as their values. ``str`` and ``bytes`` are leaves
    rather than sequences of characters, which is what makes a free-text field one token
    instead of a stream of letters. Other iterables - lists, tuples, sets, frozensets - are
    walked element by element, so a token buried in a collection is still reached
    (Requirements 6.8, 7.1).

    A container that reaches itself is walked once: recursion is bounded by object identity, so
    a cyclic response body yields a finite stream instead of exhausting the stack.
    """
    yield from _walk(value, set())


def _walk(value: Any, seen: Set[int]) -> Iterator[Any]:
    """The recursion behind :func:`deep_scalars`, carrying the identity set."""
    if isinstance(value, (str, bytes, bytearray)):
        yield value
        return

    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        for key, item in value.items():
            yield from _walk(key, seen)
            yield from _walk(item, seen)
        seen.discard(marker)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        for item in value:
            yield from _walk(item, seen)
        seen.discard(marker)
        return

    yield value


def assert_contains_no_protected_logic(
    response: Any,
    tokens: Any,
    structural_keys: Optional[Any] = None,
) -> None:
    """Assert that no field of ``response`` contains any Protected_Logic token.

    The check is *substring* containment, not equality, and it runs at every nesting depth
    including free-text and collection fields: a node identifier embedded in a description, a
    message or a list element is a leak just as much as one returned in its own field
    (Requirements 6.8, 7.1).

    Every value is searched. A mapping *key* is searched too unless it is part of the response's
    own fixed vocabulary - see :data:`STRUCTURAL_RESPONSE_KEYS` for why a key the API emits for
    every Listing, including one whose strategy has no indicators at all, cannot be a
    disclosure. The exemption never extends to a value: the value stored *under* a structural
    key is searched like any other.

    Args:
        response: Any response body - a dict, a list, a model dump, a string.
        tokens: The token set from :func:`protected_logic_tokens`.
        structural_keys: The response's fixed key vocabulary, defaulting to
            :data:`STRUCTURAL_RESPONSE_KEYS`. A caller asserting over a body this module does
            not build - the route's authenticated enrichment, a ``paper_events`` envelope - passes
            that set unioned with its own declared keys. Pass ``frozenset()`` to search keys as
            well, which is the right thing for a body whose key set is caller-controlled.

    Raises:
        AssertionError: A token appears somewhere in ``response``. The message names the token
            and the path it was found at, because a failure that says only "a leak exists" is a
            failure nobody can act on. It is raised explicitly rather than via a bare ``assert``
            so that ``python -O`` cannot strip the check.
    """
    candidates = [token for token in tokens if token]
    if not candidates:
        return

    exempt: FrozenSet[str] = (
        STRUCTURAL_RESPONSE_KEYS
        if structural_keys is None
        else frozenset(str(key) for key in structural_keys)
    )

    for path, scalar in _walk_with_path(response, "$", set(), exempt):
        text = str(scalar)
        for token in candidates:
            if token in text:
                raise AssertionError(
                    f"Protected_Logic token {token!r} appears in the response at {path}"
                )


def _walk_with_path(
    value: Any,
    path: str,
    seen: Set[int],
    structural_keys: FrozenSet[str] = frozenset(),
) -> Iterator[Tuple[str, Any]]:
    """:func:`deep_scalars` with a path per scalar, for an actionable failure message.

    A mapping key whose text is in ``structural_keys`` is not yielded - it is part of the
    response schema rather than response data. Its value is still walked and yielded.
    """
    if isinstance(value, (str, bytes, bytearray)):
        yield path, value
        return

    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        for key, item in value.items():
            if str(key) not in structural_keys:
                yield from _walk_with_path(key, f"{path}.<key>", seen, structural_keys)
            yield from _walk_with_path(item, f"{path}.{key}", seen, structural_keys)
        seen.discard(marker)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        for index, item in enumerate(value):
            yield from _walk_with_path(item, f"{path}[{index}]", seen, structural_keys)
        seen.discard(marker)
        return

    yield path, value


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════════════════════════════════════════


def _read(row: Any, name: str) -> Any:
    """Read one column from a row mapping or a row object, without mutating either.

    Returns ``None`` for an absent key or attribute and for a ``None`` row, so a projection of a
    row that predates an additive migration produces a ``None`` field rather than a
    ``KeyError`` - and never a substituted value.
    """
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)
