"""Marketplace pure-logic package.

Spec: marketplace-subscriptions-paper-trading. ``design.md`` -> "Architecture" states the
layering rule this package exists to hold: the marketplace's decision logic lives in modules
that import no FastAPI and perform no I/O, so each one is importable and property-testable
standalone.

Modules
-------
money                  Minor_Units integer arithmetic and ISO 4217 currency exponents
                       (Requirements 8.12, 8.13, 9.2, 10.1, 10.2, 10.3)

IMPORTANT: no runtime imports here. The same convention ``backend_app/backend/__init__.py``
already follows - importing a submodule must not pull the rest of the package, a database
handle or an event loop behind it. Import directly from the submodule:

  from backend_app.backend.marketplace.money import split_ninety_ten

``COLUMN_CONTRACT`` (task 11.6)
-------------------------------
The per-handler column manifest. Each entry names, for one new or modified marketplace
handler, every ``(table, column)`` pair that handler reads or writes. It is a plain data
structure - not a docstring, not a comment - so that
``tests/test_marketplace_paper_schema_contract.py`` can assert two things mechanically:

  1. Every ``(table, column)`` pair the manifest names is created by the migration set
     (``migrations/`` and ``backend_app/migrations/``). A pair a handler reads that no
     migration creates is the PostgreSQL ``42703`` / PostgREST ``PGRST204`` condition recorded
     in the header of ``migrations/007_add_marketplace_pricing_columns.sql`` - caught here, at
     test time, rather than in production.
  2. Every ``.select("…")`` string literal (or a module-level constant assigned a string that a
     ``.select(...)`` call reads, e.g. ``aliases.ALIAS_SELECT`` and
     ``listing_projection.LISTING_SELECT``) names only columns the owning handler's manifest
     entry lists. A handler that starts reading a new column and forgets to widen its manifest
     fails assertion 2 before it can fail with ``42703``.

Manifest entry shape
---------------------
Each value is a mapping with:

  ``module``       the dotted module the handler lives in - the test walks that module's AST for
                   ``.select("…")`` literals and the constants they read.
  ``tables``       an ordered mapping ``{table_name: frozenset(columns)}``. A handler that
                   touches several tables (the projection reads ``library_strategies`` and
                   ``marketplace_backtest_evidence``) lists each.
  ``preexisting``  the subset of ``tables`` that this specification's migration set does NOT
                   own - platform tables such as ``profiles`` that pre-date this work. Their
                   ``(table, column)`` pairs are exempt from assertion 1 (the migration set is
                   not expected to create them) but their columns still bound assertion 2's
                   ``.select`` subset check for the module that reads them.

Only NEW or MODIFIED handlers appear here. A column the specification does not touch is not
listed, so the manifest stays a statement of what this work reads and writes, not a mirror of
the whole schema.
"""

from typing import Dict, FrozenSet, Mapping

__all__ = ["COLUMN_CONTRACT"]


# ── The columns each marketplace handler reads or writes ──────────────────
#
# Sourced column-for-column from ``design.md`` -> "Data Models" and from the explicit
# projection constants the modules already declare (``aliases.ALIAS_SELECT``,
# ``listing_projection.LISTING_SELECT``). Where a handler reads a column that a module constant
# also names, the two agree by construction: the test asserts the ``.select`` literal is a
# subset of the manifest entry, so a drift between them is a red suite, not a silent leak.

# The batched creator-alias read (``aliases.resolve_aliases``). ``profiles`` is a pre-existing
# platform table this migration set does not create, so it is declared ``preexisting``: its
# columns bound the ``ALIAS_SELECT`` subset check without demanding a migration create them.
_ALIAS_HANDLER = {
    "module": "backend_app.backend.marketplace.aliases",
    "tables": {
        "profiles": frozenset({"id", "display_name"}),
    },
    "preexisting": frozenset({"profiles"}),
}

# The public Listing serialiser (``listing_projection.project_listing`` and the
# ``LISTING_SELECT`` projection it is fed). It reads ``library_strategies`` (the additive
# marketplace columns of 007 plus the pre-existing catalogue columns the query still selects)
# and, on the detail path, the immutable ``marketplace_backtest_evidence`` copy. Only the
# columns 007 adds are asserted to exist by the migration set; the pre-existing
# ``library_strategies`` columns the projection also reads (``id``, ``name``, ``price``, the
# ``backtest_*`` aggregate, …) are declared ``preexisting`` so ``LISTING_SELECT`` can name them
# without this task's migrations having to create them.
_LISTING_HANDLER = {
    "module": "backend_app.backend.marketplace.listing_projection",
    "tables": {
        "library_strategies": frozenset(
            {
                # Additive marketplace columns created by 007 (design.md -> Data Models).
                "price_minor",
                "source_cloning_enabled",
                "supported_timeframes",
                "market_type",
                "condition_count",
                # Pre-existing catalogue columns LISTING_SELECT still requests.
                "id",
                "name",
                "description",
                "category",
                "difficulty",
                "tags",
                "symbol",
                "timeframe",
                "exchange_id",
                "price",
                "currency",
                "subscriber_count",
                "avg_rating",
                "rating_count",
                "published_at",
                "verification_status",
                "author_id",
                "backtest_total_return_pct",
                "backtest_sharpe_ratio",
                "backtest_max_drawdown_pct",
                "backtest_win_rate_pct",
                "backtest_profit_factor",
                "backtest_total_trades",
            }
        ),
        "marketplace_backtest_evidence": frozenset(
            {
                "submission_id",
                "condition_index",
                "total_return_pct",
                "sharpe_ratio",
                "sortino_ratio",
                "max_drawdown_pct",
                "win_rate_pct",
                "profit_factor",
                "total_trades",
            }
        ),
    },
    # ``library_strategies`` is pre-existing (created by the archived
    # ``001_create_library_strategies.sql`` / production reconciliation); only its additive
    # columns are this work's to create, and those are proven present by 007. The whole table is
    # marked pre-existing so the many catalogue columns LISTING_SELECT names do not each require
    # a migration in this set. The additive-column coverage is asserted independently by
    # ``tests/test_library_schema_contract.py``.
    "preexisting": frozenset({"library_strategies"}),
}

# The submission lifecycle handler - reads and writes ``marketplace_submissions`` and appends to
# ``marketplace_submission_transitions`` (design.md -> "marketplace_submissions" and
# "marketplace_submission_transitions"). Both tables are created by 007.
_SUBMISSION_HANDLER = {
    "module": "backend_app.backend.marketplace.submission_state",
    "tables": {
        "marketplace_submissions": frozenset(
            {
                "id",
                "listing_id",
                "source_strategy_id",
                "owner_id",
                "version_id",
                "submission_state",
                "eligibility_outcomes",
                "evaluator_version",
                "rejection_reason",
                "reviewed_by",
                "submitted_at",
                "reviewed_at",
                "published_at",
            }
        ),
        "marketplace_submission_transitions": frozenset(
            {
                "id",
                "submission_id",
                "owner_id",
                "from_state",
                "to_state",
                "actor_id",
                "reason",
                "transitioned_at",
            }
        ),
    },
    "preexisting": frozenset(),
}

# The evidence validator's persisted read - the immutable ``marketplace_backtest_evidence``
# copy 007 creates (design.md -> "marketplace_backtest_evidence"), plus the
# ``strategy_backtests`` reproducibility columns 006 reconciles that it validates against.
_EVIDENCE_HANDLER = {
    "module": "backend_app.backend.marketplace.evidence_validator",
    "tables": {
        "marketplace_backtest_evidence": frozenset(
            {
                "id",
                "submission_id",
                "owner_id",
                "source_backtest_id",
                "condition_index",
                "dataset",
                "start_date",
                "end_date",
                "initial_capital",
                "commission",
                "slippage",
                "dataset_checksum",
                "dag_hash",
                "engine_version",
                "executed_bar_count",
                "version_id",
                "total_return_pct",
                "sharpe_ratio",
                "sortino_ratio",
                "max_drawdown_pct",
                "win_rate_pct",
                "profit_factor",
                "total_trades",
                "final_capital",
            }
        ),
        "strategy_backtests": frozenset(
            {
                "version_id",
                "executed_bar_count",
                "dataset_checksum",
                "dag_hash",
                "engine_version",
                "final_capital",
                "completed_at",
            }
        ),
    },
    # ``strategy_backtests`` is pre-existing; 006 reconciles the reproducibility columns onto it
    # (proven by ``tests/test_backtest_evidence_columns_regression.py``), so its pairs are exempt
    # from the migration-creates-it assertion here.
    "preexisting": frozenset({"strategy_backtests"}),
}

# The publication Eligibility_Gate (``eligibility_gate.evaluate`` and its four owner-scoped
# reads, task 14.1). It is the one marketplace module that reads the Persistence_Layer, so its
# four ``.select`` projections are bound here the same way ``aliases`` and
# ``listing_projection`` are - a ``.select`` naming a column absent from this entry (the
# ``marketplace_submissions.state`` typo the gate carried before task 14.4's verification found
# it) fails ``test_select_literals_are_within_manifest`` before it can be a runtime 42703.
#
# Four tables, one per read (design.md -> "eligibility_gate.py", the four round trips):
#   * ``strategies``          - read 1's ``id, user_id, tenant_id, archived_at``. Pre-existing
#                               (001); this spec creates none of it.
#   * ``strategy_versions``   - read 2's ``id, strategy_id, version, is_draft,
#                               validation_state, blueprint, graph_json``. ``blueprint`` and
#                               ``version`` and ``is_draft`` are the 001 base columns;
#                               ``validation_state`` and ``graph_json`` are 004's canonical
#                               columns. Pre-existing.
#   * ``strategy_backtests``  - read 3's twenty-plus evidence columns. Pre-existing; 006
#                               reconciles the reproducibility columns onto it.
#   * ``marketplace_submissions`` - read 4's ``id, submission_state``. Created by 007, so its
#                               two columns ARE this spec's to prove exist - and
#                               ``submission_state`` (not ``state``) is the migration's column
#                               name, which assertion 1 now confirms for this reader too.
#   * ``library_strategies``  - read 4's ``id, moderation_status, is_active,
#                               source_strategy_id``. Pre-existing catalogue table.
#
# Every table but ``marketplace_submissions`` is ``preexisting``: their columns bound the
# ``.select`` subset check without demanding this spec's migrations create them, exactly as the
# ``profiles`` / ``strategy_backtests`` / ``library_strategies`` handlers above already do.
_ELIGIBILITY_HANDLER = {
    "module": "backend_app.backend.marketplace.eligibility_gate",
    "tables": {
        "strategies": frozenset(
            {"id", "user_id", "tenant_id", "archived_at"}
        ),
        "strategy_versions": frozenset(
            {
                "id",
                "strategy_id",
                "version",
                "is_draft",
                "validation_state",
                "blueprint",
                "graph_json",
            }
        ),
        "strategy_backtests": frozenset(
            {
                "id",
                "user_id",
                "strategy_id",
                "version_id",
                "status",
                "completed_at",
                "error_message",
                "dataset",
                "start_date",
                "end_date",
                "initial_capital",
                "commission",
                "slippage",
                "dataset_checksum",
                "dag_hash",
                "total_trades",
                "executed_bar_count",
                "total_return_pct",
                "sharpe_ratio",
                "max_drawdown",
                "win_rate",
                "profit_factor",
                "final_capital",
            }
        ),
        "marketplace_submissions": frozenset({"id", "submission_state"}),
        "library_strategies": frozenset(
            {"id", "moderation_status", "is_active", "source_strategy_id"}
        ),
    },
    "preexisting": frozenset(
        {
            "strategies",
            "strategy_versions",
            "strategy_backtests",
            "library_strategies",
        }
    ),
}

# The pricing evaluator and its enforcement endpoints (task 15.1). The evaluator itself
# is pure and persists nothing; the two ``POST /submissions/{id}/price-range`` and
# ``.../price`` routes in ``backend_app/routers/library.py`` are the request-context half
# that reads the evidence, persists the range and enforces it. This entry binds every
# column that half touches:
#   * ``marketplace_price_evaluations`` (007) - the persisted Price_Range record it writes
#     and reads back by ``(submission_id, currency, inputs_digest)``.
#   * ``marketplace_backtest_evidence`` (007) - the immutable Backtest_Evidence copy the
#     range is computed from (``pricing_evaluator``'s ``PRICING_INPUTS`` / ``DIGEST_INPUTS``
#     on their canonical spellings), read owner-scoped.
#   * ``marketplace_submissions`` (007) - the owner-scoped ``listing_id`` / ``owner_id``
#     lookup that resolves the caller's own Submission and its Listing.
#   * ``library_strategies`` - the pre-existing catalogue table whose ``price_minor`` the
#     single enforcement point sets on accept (the section-7 trigger mirrors ``price`` from
#     it); marked pre-existing, so its columns bind the manifest without demanding this
#     spec's migrations create them (``price_minor`` and ``currency`` come from
#     migrations/007_add_marketplace_pricing_columns.sql, the rest from the base table).
_PRICING_HANDLER = {
    "module": "backend_app.backend.marketplace.pricing_evaluator",
    "tables": {
        "marketplace_price_evaluations": frozenset(
            {
                "id",
                "submission_id",
                "owner_id",
                "currency",
                "inputs_digest",
                "minimum_price_minor",
                "recommended_price_minor",
                "maximum_price_minor",
                "evaluator_version",
                "created_at",
            }
        ),
        "marketplace_backtest_evidence": frozenset(
            {
                "submission_id",
                "owner_id",
                "source_backtest_id",
                "condition_index",
                "start_date",
                "end_date",
                "dataset_checksum",
                "dag_hash",
                "executed_bar_count",
                "total_return_pct",
                "sharpe_ratio",
                "sortino_ratio",
                "max_drawdown_pct",
                "win_rate_pct",
                "profit_factor",
                "total_trades",
                "final_capital",
            }
        ),
        "marketplace_submissions": frozenset({"id", "listing_id", "owner_id"}),
        "library_strategies": frozenset(
            {"id", "price_minor", "currency", "updated_at"}
        ),
    },
    "preexisting": frozenset({"library_strategies"}),
}

# The settlement / subscription-period handler - ``marketplace_settlements`` and the additive
# ``library_subscriptions`` columns 008 creates, plus the append-only
# ``library_subscription_transitions`` history (design.md -> "marketplace_settlements",
# "library_subscription_transitions", and the "Additive columns" table).
_SETTLEMENT_HANDLER = {
    "module": "backend_app.backend.marketplace.subscription_period",
    "tables": {
        "marketplace_settlements": frozenset(
            {
                "id",
                "subscription_id",
                "listing_id",
                "owner_id",
                "purchaser_id",
                "amount_minor",
                "owner_share_minor",
                "platform_fee_minor",
                "currency",
                "provider",
                "provider_reference",
                "is_reversal",
                "reverses_reference",
                "settled_at",
            }
        ),
        "library_subscription_transitions": frozenset(
            {
                "id",
                "subscription_id",
                "user_id",
                "from_state",
                "to_state",
                "cause",
                "actor_id",
                "prior_period_expiry",
                "new_period_expiry",
                "transitioned_at",
            }
        ),
        "library_subscriptions": frozenset(
            {
                "owner_id",
                "price_minor",
                "owner_share_minor",
                "platform_fee_minor",
                "period_start",
                "period_expiry",
                "provider",
                "provider_reference",
                "provider_session_at",
                "failure_cause",
                "failed_at",
                "renewal_enabled",
            }
        ),
    },
    # ``library_subscriptions`` pre-dates this work; 008 only adds the columns above. The whole
    # table is marked pre-existing, and the additive columns are proven present by the
    # constraint/column assertions in the schema-contract test itself.
    "preexisting": frozenset({"library_subscriptions"}),
}


# The Entitlement_Resolver (``entitlement_resolver.resolve`` and its two reads, task 17.1). It
# is the single admission decision for both deployment and Paper_Session start, and the second
# marketplace module (after the Eligibility_Gate) that reads the Persistence_Layer, so its two
# ``.select`` projections are bound here the same way.
#
# The one embedded round trip reads three tables at once off ``library_strategies``:
#   * ``library_strategies``       - ``id, author_id, source_strategy_id,
#                                     source_cloning_enabled`` (the Listing and its owner).
#                                     Pre-existing catalogue table; ``source_strategy_id`` and
#                                     ``source_cloning_enabled`` are 007's additive columns, the
#                                     rest are base columns.
#   * ``marketplace_submissions``  - the embedded ``submission_state`` (is the Listing
#                                     PUBLISHED / SUSPENDED / UNPUBLISHED). Created by 007, so
#                                     ``submission_state`` IS this spec's to prove exists.
#   * ``library_subscriptions``    - the embedded ``id, user_id, status, period_expiry`` for the
#                                     caller's own row. ``period_expiry`` is 008's additive
#                                     column; ``id, user_id, status`` are base columns.
# and the version-resolution read touches:
#   * ``strategy_versions``        - ``id, strategy_id, version, is_draft``, the current live
#                                     version behind ``source_strategy_id`` (Req 7.5, 7.11).
#                                     Pre-existing (004's canonical version table).
#
# Because the ``.select`` string embeds resources as ``rel(cols)``, the schema-contract test's
# projection splitter emits the two embed *relation names* (``marketplace_submissions``,
# ``library_subscriptions``) and the embed's inner columns (``user_id``, ``status``) as bare
# tokens too; every one of those tokens is present in the union of this entry's tables, so
# ``test_select_literals_are_within_manifest`` stays green. The relation-name tokens are listed
# on ``library_strategies`` (the table they are embedded off), which is where the reader names
# them.
_ENTITLEMENT_HANDLER = {
    "module": "backend_app.backend.marketplace.entitlement_resolver",
    "tables": {
        "library_strategies": frozenset(
            {
                "id",
                "author_id",
                "source_strategy_id",
                "source_cloning_enabled",
                # The two embedded relations named in the one-round-trip ``.select``.
                "marketplace_submissions",
                "library_subscriptions",
            }
        ),
        "marketplace_submissions": frozenset({"submission_state"}),
        "library_subscriptions": frozenset(
            {"id", "user_id", "status", "period_expiry"}
        ),
        "strategy_versions": frozenset(
            {"id", "strategy_id", "version", "is_draft"}
        ),
    },
    # ``library_strategies``, ``library_subscriptions`` and ``strategy_versions`` all pre-date
    # this work; only their additive columns are this spec's to create, proven present by the
    # 007/008 column assertions. ``marketplace_submissions`` is created by 007, so its
    # ``submission_state`` column IS asserted to exist by the migration set here.
    "preexisting": frozenset(
        {"library_strategies", "library_subscriptions", "strategy_versions"}
    ),
}


# The Marketplace checkout path (``checkout_service.create_checkout``, task 18.1) - the third
# marketplace module that reads the Persistence_Layer, and the only one that writes
# ``library_subscriptions``. Its two ``.select`` projections
# (``CHECKOUT_LISTING_SELECT``, ``CHECKOUT_SUBSCRIPTION_SELECT``) are bound here the same way
# the Eligibility_Gate's and the Entitlement_Resolver's are, and its WRITE payload is bound too:
# the ``PENDING`` insert is where a column name that no migration created becomes a PostgREST
# ``PGRST204`` on a payment path.
#
# Two tables, one per statement (design.md -> "Root-cause fix 1"):
#   * ``library_strategies``     - the Listing read: ``id, name, author_id, price_minor,
#                                  currency, is_active``, plus the embedded relation token
#                                  ``marketplace_submissions`` that the projection splitter emits
#                                  for ``marketplace_submissions(submission_state)``.
#                                  Pre-existing catalogue table; ``price_minor`` is 007's
#                                  additive column.
#   * ``marketplace_submissions`` - the embedded ``submission_state`` that decides
#                                  purchasability. Created by 007, so this column IS this
#                                  spec's to prove exists.
#   * ``library_subscriptions``  - the row this path writes. ``library_id``, ``user_id``,
#                                  ``status`` and ``started_at`` are base columns; ``owner_id``,
#                                  ``price_minor``, ``currency``, ``owner_share_minor``,
#                                  ``platform_fee_minor``, ``provider``, ``provider_reference``,
#                                  ``provider_session_at``, ``failure_cause`` and ``failed_at``
#                                  are 008's additive columns.
#
#   ``expires_at``, ``period_start`` and ``period_expiry`` are DELIBERATELY ABSENT from this
#   entry. A ``PENDING`` row has no period, and this path omits those columns rather than
#   writing ``NULL`` into them (Requirement 9.3) - so naming them here would assert a read or
#   write this handler must not make. ``checkout_service.OMITTED_ON_PENDING`` is the runtime
#   half of the same statement.
_CHECKOUT_HANDLER = {
    "module": "backend_app.backend.marketplace.checkout_service",
    "tables": {
        "library_strategies": frozenset(
            {
                "id",
                "name",
                "author_id",
                "price_minor",
                "currency",
                "is_active",
                # The embedded relation named in CHECKOUT_LISTING_SELECT.
                "marketplace_submissions",
            }
        ),
        "marketplace_submissions": frozenset({"submission_state"}),
        "library_subscriptions": frozenset(
            {
                "id",
                "library_id",
                "user_id",
                "status",
                "started_at",
                "owner_id",
                "price_minor",
                "currency",
                "owner_share_minor",
                "platform_fee_minor",
                "provider",
                "provider_reference",
                "provider_session_at",
                "failure_cause",
                "failed_at",
            }
        ),
    },
    # ``library_strategies`` and ``library_subscriptions`` both pre-date this work; only their
    # additive columns are this spec's to create, proven present by the 007/008 column
    # assertions. ``marketplace_submissions`` is created by 007, so its ``submission_state``
    # column IS asserted to exist by the migration set here.
    "preexisting": frozenset({"library_strategies", "library_subscriptions"}),
}


# The subscriber-safe marketplace deployment (``routers/library.deploy_marketplace_strategy``,
# task 17.3) - the only handler in this manifest that writes ``strategy_deployments``, and the
# reason ``011_marketplace_deployment_source.sql`` exists.
#
# WHAT CHANGED, AND WHY THE MANIFEST IS THE PLACE IT IS RECORDED
#   Before 17.3 this handler answered a subscriber by INSERTing a ``strategies`` row carrying the
#   owner's ``buy_logic``, ``sell_logic``, ``risk``, ``indicators`` and ``ml_model_path`` - a full
#   Protected_Logic transfer (Requirement 7.1). It now writes a ``strategy_deployments`` row
#   instead: ``user_id`` = the SUBSCRIBER, ``strategy_id`` / ``version_id`` = the OWNER's, and
#   ``marketplace_listing_id`` = the Listing that entitled it. None of the five logic columns is
#   read or written anywhere on the path, so none of them appears here - the manifest is a
#   statement of what this handler touches, and its silence about ``strategies`` is part of the
#   statement.
#
# Two tables:
#   * ``strategy_deployments``  - the row this path WRITES. ``user_id``, ``strategy_id``,
#                                 ``version_id``, ``version``, ``environment``, ``status``,
#                                 ``exchange_symbol``, ``initial_capital``, ``created_at`` and
#                                 ``updated_at`` are base columns (001 section 2 / 003 section 3).
#                                 ``marketplace_listing_id`` is task 17.3's own additive column,
#                                 created by ``011_marketplace_deployment_source.sql``.
#   * ``strategy_versions``     - the one further read this path makes: the owner's version LABEL
#                                 (``version``, e.g. "v1.2") for the ``NOT NULL VARCHAR(20)``
#                                 ``strategy_deployments.version`` column. The Entitlement_Resolver
#                                 returns the version *id*; the human-readable label is not an
#                                 identifier and is deliberately not on :class:`Entitlement`, so
#                                 it is read here. The projection names ``id`` and ``version``
#                                 only - no ``blueprint`` and no ``execution_graph``, the two
#                                 columns on that table that carry Protected_Logic.
#
# ``strategy_deployments`` is NOT marked ``preexisting``, unlike ``strategy_versions``. The table
# pre-dates this work, but ``marketplace_listing_id`` does not: marking the table pre-existing
# would exempt the one column at risk from assertion 1 and leave the PGRST204 this manifest
# exists to catch uncaught. Every other column listed for it is created by 001/003, so the
# stricter setting costs nothing and buys the assertion that matters.
_DEPLOYMENT_HANDLER = {
    "module": "backend_app.routers.library",
    "tables": {
        "strategy_deployments": frozenset(
            {
                # Task 17.3's additive column (011_marketplace_deployment_source.sql).
                "marketplace_listing_id",
                # Base columns of the existing deployment shape (001 section 2 / 003 section 3).
                "user_id",
                "strategy_id",
                "version_id",
                "version",
                "environment",
                "status",
                "exchange_symbol",
                "initial_capital",
                "created_at",
                "updated_at",
            }
        ),
        "strategy_versions": frozenset({"id", "version"}),
    },
    # ``strategy_versions`` pre-dates this work (001's canonical version table), so its columns
    # are not this migration set's to create. ``strategy_deployments`` is deliberately absent
    # from this set - see the note above.
    "preexisting": frozenset({"strategy_versions"}),
}


# The Subscription expiry sweep (``expiry_sweep.sweep``, task 20.1) - the housekeeping worker
# that relabels a lapsed Subscription and stops what it was running (Requirements 11.8, 11.12,
# 11.15, 24.4).
#
# It touches four tables, and the reason each column is here is worth stating because two of
# them are *writes on other people's tables*:
#   * ``library_subscriptions``  - the ONE update statement per pass. It reads ``id``,
#                                  ``user_id``, ``library_id``, ``status`` and ``period_expiry``
#                                  (``SWEEP_CANDIDATE_SELECT``) and writes ``status`` and
#                                  ``updated_at``. ``period_expiry``, ``period_start`` and
#                                  ``expires_at`` are read-only to this handler and are named
#                                  here as reads only - ``expiry_sweep._FORBIDDEN_UPDATE_COLUMNS``
#                                  is the runtime half of that statement. Pre-existing table;
#                                  ``period_expiry`` is 008's additive column.
#   * ``library_subscription_transitions`` - Requirement 11.12's append-only history, one row
#                                  per expiry. Created by 008, so every column named here IS
#                                  this spec's to prove exists.
#   * ``strategy_deployments``    - Requirement 11.15's enforcement: a running deployment of the
#                                  expired Listing owned by that purchaser is moved to
#                                  ``'stopped'``. ``marketplace_listing_id`` is task 17.3's
#                                  additive column (011) and is the join back to the Listing;
#                                  ``user_id`` scopes it to the purchaser, so another
#                                  subscriber's still-paid deployment is a different row. NOT
#                                  marked pre-existing, for the same reason the deployment
#                                  handler above is not: that would exempt
#                                  ``marketplace_listing_id`` - the one column at risk - from
#                                  the migration-creates-it assertion.
#   * ``paper_sessions``          - the persisted half of Requirement 11.15's Paper_Session stop.
#                                  Created by 009, so its columns ARE this spec's to prove
#                                  exist. The *runtime* teardown is task 27.1's and writes no
#                                  column, so nothing further appears here.
_EXPIRY_SWEEP_HANDLER = {
    "module": "backend_app.backend.marketplace.expiry_sweep",
    "tables": {
        "library_subscriptions": frozenset(
            {
                "id",
                "user_id",
                "library_id",
                "status",
                "period_expiry",
                "updated_at",
            }
        ),
        "library_subscription_transitions": frozenset(
            {
                "subscription_id",
                "user_id",
                "from_state",
                "to_state",
                "cause",
                "transitioned_at",
            }
        ),
        "strategy_deployments": frozenset(
            {
                "marketplace_listing_id",
                "user_id",
                "status",
                "stopped_at",
                "updated_at",
            }
        ),
        "paper_sessions": frozenset(
            {
                "user_id",
                "listing_id",
                "session_state",
                "stopped_at",
                "updated_at",
            }
        ),
    },
    # ``library_subscriptions`` pre-dates this work (008 only adds columns to it), so its pairs
    # are exempt from the migration-creates-it assertion, exactly as the settlement and checkout
    # handlers above declare. The other three are NOT exempt: 008 creates the transitions table,
    # 009 creates ``paper_sessions``, and 011 creates the one ``strategy_deployments`` column
    # this handler depends on.
    "preexisting": frozenset({"library_subscriptions"}),
}


# The Settlement_Record writer (``settlement_service.settle``, task 19.1) - the one path that
# writes ``marketplace_settlements`` and the only path that may set
# ``library_subscriptions.status = 'active'``.
#
# It is a SEPARATE entry from ``settlement_and_subscription`` above on purpose. That entry's
# ``module`` is ``subscription_period``, which is pure arithmetic and issues no ``.select`` at
# all, so it binds no projection: a mistyped column in ``SETTLEMENT_SUBSCRIPTION_SELECT`` would
# have gone unnoticed by ``test_select_literals_are_within_manifest`` and surfaced as a PGRST204
# on a *payment confirmation*. This entry names the module that actually talks to the
# Persistence_Layer, so the projection is bound the same way the checkout path's is.
#
# Four tables:
#   * ``library_subscriptions``   - read through ``SETTLEMENT_SUBSCRIPTION_SELECT`` (``id``,
#                                   ``library_id``, ``user_id``, ``owner_id``, ``status``,
#                                   ``price_minor``, ``currency``, ``period_start``,
#                                   ``period_expiry``, ``provider``, ``provider_reference``) and
#                                   written by the one transition UPDATE (``status``,
#                                   ``period_start``, ``period_expiry``, and the retained
#                                   ``started_at`` / ``expires_at`` mirrors plus
#                                   ``cancelled_at``). Pre-existing table; the period columns
#                                   are 008's additive ones.
#   * ``marketplace_settlements`` - the ledger row. Created by 008, so every column named here
#                                   IS this spec's to prove exists.
#   * ``library_subscription_transitions`` - Requirement 11.12's append-only history row.
#                                   Created by 008.
#   * ``deployment_permissions``  - the entitlement write, carrying the period expiry in
#                                   ``expires_at`` rather than the NULL that
#                                   ``routers/library.grant_deployment_permission`` writes.
#                                   Created by ``migrations/006_reconcile_production_database.sql``
#                                   and NOT marked pre-existing: every column this handler
#                                   touches is in that CREATE TABLE, so the stricter setting
#                                   costs nothing and keeps ``expires_at`` - the one column the
#                                   privilege fix depends on - inside assertion 1.
_SETTLEMENT_SERVICE_HANDLER = {
    "module": "backend_app.backend.marketplace.settlement_service",
    "tables": {
        "library_subscriptions": frozenset(
            {
                "id",
                "library_id",
                "user_id",
                "owner_id",
                "status",
                "price_minor",
                "currency",
                "period_start",
                "period_expiry",
                "provider",
                "provider_reference",
                "started_at",
                "expires_at",
                "cancelled_at",
            }
        ),
        "marketplace_settlements": frozenset(
            {
                "id",
                "subscription_id",
                "listing_id",
                "owner_id",
                "purchaser_id",
                "amount_minor",
                "owner_share_minor",
                "platform_fee_minor",
                "currency",
                "provider",
                "provider_reference",
                "is_reversal",
                "reverses_reference",
                "settled_at",
            }
        ),
        "library_subscription_transitions": frozenset(
            {
                "subscription_id",
                "user_id",
                "from_state",
                "to_state",
                "cause",
                "actor_id",
                "prior_period_expiry",
                "new_period_expiry",
                "transitioned_at",
            }
        ),
        "deployment_permissions": frozenset(
            {
                "user_id",
                "library_id",
                "granted_via",
                "subscription_id",
                "is_active",
                "granted_at",
                "expires_at",
            }
        ),
    },
    # ``library_subscriptions`` pre-dates this work; 008 only adds columns to it, and those are
    # proven present by the schema-contract test's own column assertions.
    "preexisting": frozenset({"library_subscriptions"}),
}


# The administrative reinstatement of a SUSPENDED Subscription
# (``subscription_reinstatement.reinstate``, task 22 remediation, Requirement 11.17) - the
# second writer of ``library_subscriptions.status = 'active'`` and the only one that writes it
# with NO payment of its own.
#
# Three tables, and the SILENCES are as much of the statement as the entries:
#   * ``library_subscriptions``  - read on ``REINSTATEMENT_SUBSCRIPTION_SELECT`` and written on
#                                  one column. ``period_start`` and ``period_expiry`` are READ
#                                  (they are what the payment probe anchors on and what the
#                                  result reports) but never written; ``started_at`` and
#                                  ``expires_at`` are ABSENT from this entry entirely, because
#                                  this handler must not touch the mirrors either - naming them
#                                  would assert a write it must never make.
#   * ``marketplace_settlements`` - read only, and only the four columns that answer "was the
#                                  period being resumed paid for?". No money column: this path
#                                  asks whether there was a payment, never how much.
#   * ``library_subscription_transitions`` - the Requirement 11.12 history row, with
#                                  ``cause='admin_reinstatement'``.
#
# ``deployment_permissions`` is absent: the grant the settled payment wrote already carries the
# period expiry, so a reinstatement grants nothing.
_SUBSCRIPTION_REINSTATEMENT_HANDLER = {
    "module": "backend_app.backend.marketplace.subscription_reinstatement",
    "tables": {
        "library_subscriptions": frozenset(
            {
                "id",
                "library_id",
                "user_id",
                "owner_id",
                "status",
                "period_start",
                "period_expiry",
            }
        ),
        "marketplace_settlements": frozenset(
            {"id", "subscription_id", "is_reversal", "settled_at"}
        ),
        "library_subscription_transitions": frozenset(
            {
                "subscription_id",
                "user_id",
                "from_state",
                "to_state",
                "cause",
                "actor_id",
                "prior_period_expiry",
                "new_period_expiry",
                "transitioned_at",
            }
        ),
    },
    # ``library_subscriptions`` pre-dates this work; 008 adds the period columns, proven present
    # by the schema-contract test's own column assertions.
    "preexisting": frozenset({"library_subscriptions"}),
}


#: The public manifest. Keyed by a short handler name; each value is one entry of the shape
#: documented in the module docstring. The test iterates this mapping.
COLUMN_CONTRACT: Dict[str, Mapping[str, object]] = {
    "creator_alias_read": _ALIAS_HANDLER,
    "listing_projection": _LISTING_HANDLER,
    "submission_lifecycle": _SUBMISSION_HANDLER,
    "evidence_validation": _EVIDENCE_HANDLER,
    "eligibility_gate": _ELIGIBILITY_HANDLER,
    "pricing_evaluation": _PRICING_HANDLER,
    "settlement_and_subscription": _SETTLEMENT_HANDLER,
    "settlement": _SETTLEMENT_SERVICE_HANDLER,
    "subscription_reinstatement": _SUBSCRIPTION_REINSTATEMENT_HANDLER,
    "entitlement_resolver": _ENTITLEMENT_HANDLER,
    "checkout": _CHECKOUT_HANDLER,
    "marketplace_deployment": _DEPLOYMENT_HANDLER,
    "expiry_sweep": _EXPIRY_SWEEP_HANDLER,
}


def _iter_contract_pairs(
    contract: Mapping[str, Mapping[str, object]],
) -> "FrozenSet[tuple]":
    """Every ``(table, column)`` pair the manifest names, for the test's convenience.

    Kept here rather than in the test so the one place that knows the manifest's shape is the
    manifest. The test uses it and its ``preexisting`` companion below; nothing at runtime does.
    """
    pairs = set()
    for entry in contract.values():
        tables = entry["tables"]  # type: ignore[index]
        for table, columns in tables.items():  # type: ignore[union-attr]
            for column in columns:
                pairs.add((table, column))
    return frozenset(pairs)
