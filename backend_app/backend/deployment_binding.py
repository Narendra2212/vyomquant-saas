"""
backend_app/backend/deployment_binding.py

The deploy gate and the Deployment_Binding, in one place.

Spec: strategy-builder task 8.2. ``design.md`` -> "Exchange-agnostic deployment
binding" (``STRUCTURE DeploymentBinding`` and ``PROCEDURE deploy``).
Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.9, and 12.1/SB-06.

WHY THIS IS A MODULE AND NOT MORE OF ``strategy_service``
---------------------------------------------------------
This is the one place in the codebase where a venue legitimately enters. Everything
upstream of it - the graph, the compiled plan, the version row - is exchange-agnostic
by construction (SB-06: ``strategy_dag.schema.FORBIDDEN_PARAM_FIELDS`` strips
``exchange``/``exchange_id`` from every node's params at parse time). The binding is
where the author says "run *this* immutable version, on *that* account, under *these*
risk and execution limits, in *this* mode". Keeping the gate here rather than inline in
the router means the runtime (tasks 8.3/8.4) can read the same refusals without
importing FastAPI, and means a test can exercise the gate without a TestClient.

WHAT THIS MODULE REFUSES, AND WITH WHICH STATUS
-----------------------------------------------
Every refusal is a :class:`DeployRejected` carrying a stable ``code`` and an
``http_status``, so the two routers map it without matching on prose:

  ``VERSION_NOT_READY``            409  lifecycle_state is not READY (13.3). Names the
                                        state AND the outstanding prerequisite.
  ``PLAN_UNREADABLE``              409  compiled_plan present but not a CompiledPlan.
  ``MARKET_UNRESOLVED``            422  the plan's DATA nodes declare no symbol and
                                        timeframe, so there is no market to check.
  ``MARKET_AMBIGUOUS``             422  the plan reads more than one market.
  ``MODE_UNRECOGNISED``            422  mode outside ``chk_sd_mode``'s vocabulary.
  ``LIVE_REQUIRES_EXCHANGE_ACCOUNT`` 422 mode=live with no account (13.6).
  ``EXCHANGE_ACCOUNT_NOT_FOUND``   404  no such account belongs to this user (13.2).
  ``RISK_CONFIG_NOT_FOUND``        404  no such risk config belongs to this user (13.2).
  ``SYMBOL_NOT_AVAILABLE``         422  the account's venue lists no such market (13.4).
                                        Names the symbol and the account.
  ``TIMEFRAME_NOT_SUPPORTED``      422  that venue/pipeline serves no data at that
                                        interval (13.5). Names both.
  ``MARKET_UNIVERSE_UNAVAILABLE``  503  the tradeable universe is not loaded, so market
                                        compatibility cannot be *confirmed*. Refused
                                        rather than assumed - see "ERR TOWARD REFUSING".
  ``BINDING_NOT_STORABLE``         503  ``mode = 'live'`` while migration 004e is
                                        unapplied, so the account reference has nowhere
                                        to live. Refused rather than reported as bound.
  ``GAP_*`` / ``SYNTHETIC_FILL_*`` 422  re-raised from
                                        :func:`market_data_contract.resolve_gap_policy`,
                                        which owns the whole gap rule (13.7).
  ``MODEL_AWAITING_ARTIFACT``      409  a model node's artifact does not match, or cannot
                                        be verified against, the checksum its model
                                        version records (17.6). The node holds
                                        ``AWAITING_MODEL`` and the deployment is held out
                                        of ``RUNNING``.
  ``FEATURE_SCHEMA_DRIFT``         409  a model version's recorded feature schema differs
                                        from what this version's graph now produces
                                        (17.7). Names expected AND actual columns.
  ``MODEL_NODE_NOT_BOUND``         409  the plan declares a model node with no active
                                        model version. 17.5 says such a version is not
                                        ``READY``; reaching here means the row says
                                        otherwise.
  ``MODEL_VERSIONS_UNAVAILABLE``   503  migration 004d is unapplied, so no model version
                                        can be read and neither 17.6 nor 17.7 can be
                                        checked. Refused, not assumed.

Task 8.6 added the last four. They are here rather than in a parallel error path for the
reason this module exists: the runtime (task 8.4) has to reach the same disposition
without importing FastAPI, and the codes have to be decided in one file so the two deploy
routes cannot disagree. The *verdicts* those four codes report come from
``model_readiness``, which raises nothing - see that module's docstring for why a runtime
gate and a deploy refusal cannot be the same exception.

**404, not 403, for another tenant's resource.** Tasks 6.3/6.5/6.6/7.1/7.4 established
that convention on this surface: telling a caller "403" about an account id confirms the
account exists. Ownership violation and non-existence are one answer here.

ERR TOWARD REFUSING
-------------------
This task creates a path to real order routing, so every "cannot tell" resolves to a
refusal rather than to a permission:

* An unreadable plan, an ambiguous market, an unrecognised mode -> refused.
* An asset universe that has not loaded -> refused (503, retryable), for **paper as well
  as live**. Requirement 13.4 says refuse when the account lists no market for the
  symbol; "we could not check" is not "it is listed". A paper deployment whose symbol
  the venue does not list produces fills at prices from a feed the author will not have
  when they go live, which is exactly the figure they would use to decide to go live.
* A venue whose own timeframe vocabulary is unreadable -> the pipeline intersection is
  used and the answer says so (``timeframe_source``), never invented.

REQUIREMENT 13.9: THE BINDING IS A REFERENCE, NOT A CREDENTIAL
--------------------------------------------------------------
There is **no call to** ``load_decrypted_keys`` anywhere in this module or in either
deploy handler, and a test asserts that structurally. The binding stores
``exchange_account_id``; the venue slug is resolved from the account row so the
execution process can find the keys, and the keys themselves are read in
``master_executor`` - the live loop - by ``vault.load_decrypted_keys(user_id,
exchange_id)``. :func:`public_binding` builds its result from named keys rather than
copying a row, so no key, secret or passphrase can travel out on a response (21.7), and
:func:`normalise_execution_config` refuses a request that carries one.

"IN ONE TRANSACTION" (Requirement 13.2) - THE DECISION, STATED
--------------------------------------------------------------
Requirement 13.2 wants the three ownership assertions and the binding creation to be
one atomic act. **PostgREST exposes no multi-statement transaction to this client** -
task 6.5 hit the same wall for ``model_versions`` and recorded a compensating sequence.
Here the shape is better than a compensating sequence, because the three assertions are
*reads*:

    read+assert version owner -> read+assert account owner -> read+assert risk owner
    -> ONE INSERT

The INSERT is a single statement, so the binding row cannot come into existence unless
all three assertions have already passed. A crash anywhere before the INSERT leaves
**nothing at all** - no row, no partially bound deployment - because nothing has been
written yet. That is the safe direction: the failure mode is "no deployment", never "a
deployment bound to an account it was never checked against".

What the sequence does *not* eliminate is a time-of-check/time-of-use window: an account
could in principle be deleted or change hands between its check and the INSERT. Two
independent controls cover the consequence rather than the window. The INSERT is made
with the caller's RLS-scoped client and carries ``user_id`` explicitly, so
``sd_owner_insert`` re-checks the deployment's own tenancy at write time; and the
credentials are resolved at execution time through the vault by ``(user_id,
exchange_id)`` under ``exchange_keys``' own owner policy, so a stale reference yields
*no keys*, never another tenant's keys.

MIGRATION 004e IS UNAPPLIED
---------------------------
``backend_app/migrations/004e_deployment_bindings.sql`` adds ``exchange_account_id``,
``risk_config_id``, ``execution_config``, ``mode`` and ``dag_hash``. It is applied by
hand (``.github/workflows/03-deploy.yml`` has no migration step), so code reaches
production before the DDL. Task 8.1 left two obligations, both discharged here:

1. Every path touching those columns **degrades with a warning naming that file** and
   never a 500 - the same probe-and-cache shape ``strategy_service.
   canonical_columns_supported`` uses for part 1.
2. A degraded write **never reports a deployment as bound**. ``binding_stored`` is
   ``False``, the response says so, and ``mode = 'live'`` is refused outright (503)
   rather than stored as a paper row - because with the columns absent
   ``chk_sd_live_needs_account`` does not exist either, and a live deployment whose
   account reference was dropped is a deployment nothing can route.

And task 8.1's capitalised note - ``chk_sd_live_needs_account`` guards ``mode``, not the
legacy ``environment`` column, and the pre-existing writer set only ``environment`` - is
why :func:`binding_row_columns` sets ``mode`` on **every** deployment this codebase
creates. Without that the database-level Requirement 13.6 guarantee covers nothing.
13.6 is *also* enforced here in the handler, so a refusal is a 4xx naming the missing
account rather than a 23514 surfacing as a 500.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from backend_app.backend.market_data_contract import (
    MODE_LIVE,
    MODE_PAPER,
    MarketDataContractError,
    resolve_gap_policy,
)
from backend_app.backend.market_data_validation import GapHandlingStrategy
from backend_app.backend.strategy_builder import LIFECYCLE_READY, LIFECYCLE_STATES

logger = logging.getLogger("DeploymentBinding")


# ══════════════════════════════════════════════════════════════════════════
# VOCABULARY - shared with the database and with the pipeline, never re-spelled
# ══════════════════════════════════════════════════════════════════════════

#: ``chk_sd_mode``'s vocabulary. Imported from ``market_data_contract`` rather than
#: re-declared: task 7.5 already owns ``MODE_PAPER``/``MODE_LIVE`` and its comment on
#: ``MODE_PAPER`` names ``chk_sd_mode`` as the reader. ``MODE_BACKTEST`` is deliberately
#: absent - a backtest is a ``strategy_backtests`` row, and ``chk_sd_live_needs_account``
#: constrains only ``'live'``, so admitting it here would let a historical read pose as a
#: running deployment with no account binding at all.
BINDING_MODES: Tuple[str, ...] = (MODE_PAPER, MODE_LIVE)

#: Named in every degradation warning so an operator never has to guess which file to
#: apply.
DEPLOYMENT_BINDING_MIGRATION = "backend_app/migrations/004e_deployment_bindings.sql"

#: The five columns 004e adds. Probed as a set, because PostgREST reports the first
#: missing one and a partially applied migration must degrade rather than half-write.
BINDING_COLUMNS: Tuple[str, ...] = (
    "exchange_account_id",
    "risk_config_id",
    "execution_config",
    "mode",
    "dag_hash",
)

#: Requirement 13.10's states, verbatim. ``DEPLOYING`` is what a fresh binding is; the
#: rest are task 8.3's transitions. Published here because the binding is what carries
#: them.
BINDING_STATES: Tuple[str, ...] = (
    "DEPLOYING",
    "RUNNING",
    "PAUSED",
    "STOPPED",
    "FAILED",
)

#: The tables that can hold an exchange account, in the order they are consulted.
#: ``exchange_keys`` first because it is the table the vault actually reads
#: (``api_key_vault.load_decrypted_keys``), so an account found there is one the
#: execution process can resolve credentials for; ``exchange_connections`` second,
#: because it records connection state for the same ``(user_id, exchange_id)`` pair.
#: Both declare ``id TEXT PRIMARY KEY`` and ``user_id TEXT``, which is precisely why
#: 004e could express no foreign key and why ownership is asserted here.
EXCHANGE_ACCOUNT_TABLES: Tuple[str, ...] = ("exchange_keys", "exchange_connections")

#: Per-user risk configuration. There is no ``risk_configs`` table in this repository;
#: ``design.md``'s ``risk_config_id`` resolves against this one
#: (``migrations/006_reconcile_production_database.sql``).
RISK_CONFIG_TABLE = "risk_settings"

#: ``design.md``'s ``execution_config`` fields, and only these. A request key outside
#: this set is refused rather than stored, because ``execution_config`` is read by the
#: execution path and a key nothing reads is a limit the author believes is in force.
EXECUTION_CONFIG_FIELDS: Tuple[str, ...] = (
    "max_order_notional",
    "max_open_positions",
    "slippage_tolerance_bps",
    "order_timeout_seconds",
    "retry_policy",
)

#: How long a "columns are absent" verdict is trusted before it is re-probed, so applying
#: 004e to a running fleet takes effect without a redeploy.
BINDING_COLUMN_RECHECK_SECONDS = 300.0

#: PostgreSQL's ``undefined_column`` and PostgREST's schema-cache equivalent.
#: ``PGRST205``/``42P01`` (missing *table*) are deliberately absent: ``strategy_deployments``
#: is created by migration 001 and a missing table there is a real problem, not something
#: to degrade around.
_MISSING_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

_binding_columns_supported: Optional[bool] = None
_binding_columns_checked_at: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class DeployRejected(Exception):
    """One classified deploy refusal.

    ``design.md``'s ``RAISE DeployRejected("SYMBOL_NOT_AVAILABLE", symbol,
    account.exchange_id)``, generalised: ``code`` is stable, ``details`` carries the
    named quantities the requirement asks for, and ``http_status`` is decided here rather
    than by each router, so the two deploy surfaces cannot disagree about whether a
    non-READY version is a 409 or a 422.

    Deliberately not a ``ValueError``: both existing deploy endpoints already read a bare
    ``ValueError`` as "not found" or "quota/subscription", and this is neither.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})
        self.http_status = int(http_status)

    def to_detail(self) -> Dict[str, Any]:
        """The FastAPI ``detail`` body. ``error`` first, matching this router's shape."""
        return {"error": self.code, "message": self.message, **self.details}


# ══════════════════════════════════════════════════════════════════════════
# 004e AVAILABILITY - detected, never assumed
# ══════════════════════════════════════════════════════════════════════════


def reset_binding_column_support() -> None:
    """Forget the cached 004e verdict. For tests, and for an operator who just applied it."""
    global _binding_columns_supported, _binding_columns_checked_at
    _binding_columns_supported = None
    _binding_columns_checked_at = 0.0


def binding_column_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False`` or ``None`` for "not yet determined"."""
    return _binding_columns_supported


def _remember_binding_support(supported: bool) -> None:
    global _binding_columns_supported, _binding_columns_checked_at
    _binding_columns_supported = supported
    _binding_columns_checked_at = time.monotonic()


def remember_binding_columns_absent() -> None:
    """Record that 004e is not applied, so the next write degrades without re-probing.

    Public because the write path learns this the hard way: a process that cached a
    positive verdict and then meets ``42703`` at the INSERT has newer information than
    the probe did, and the caller must be able to record it without reaching into this
    module's globals. The negative verdict still expires after
    :data:`BINDING_COLUMN_RECHECK_SECONDS`.
    """
    _remember_binding_support(False)


def _cached_binding_support() -> Optional[bool]:
    if _binding_columns_supported is None:
        return None
    if _binding_columns_supported:
        return True
    if time.monotonic() - _binding_columns_checked_at >= BINDING_COLUMN_RECHECK_SECONDS:
        return None
    return False


def is_missing_binding_column_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says a 004e column does not exist.

    Narrow on purpose. Anything this returns ``False`` for is re-raised: the one outcome
    worse than a deploy that fails loudly is a deploy that silently drops the account
    reference and starts anyway.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text:  # a missing TABLE, which degrading cannot help with
        return False
    if any(code in text for code in _MISSING_COLUMN_CODES):
        return True
    if not any(column in text for column in BINDING_COLUMNS):
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def warn_binding_columns_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says exactly what is not stored."""
    logger.warning(
        "strategy_deployments is missing the deployment binding columns (%s). "
        "Apply %s, then restart or wait %.0fs for the re-probe. Until then a deployment "
        "row is written in the legacy shape: exchange_account_id, risk_config_id, "
        "execution_config, mode and dag_hash are NOT persisted, so the binding is NOT "
        "stored, chk_sd_mode and chk_sd_live_needs_account do not exist, and Requirement "
        "13.1 is not met. mode='live' is refused outright rather than stored as paper. "
        "Detail: %s",
        ", ".join(BINDING_COLUMNS),
        DEPLOYMENT_BINDING_MIGRATION,
        BINDING_COLUMN_RECHECK_SECONDS,
        detail,
    )


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


async def binding_columns_supported(sb: Any) -> bool:
    """Whether ``strategy_deployments`` carries 004e's five columns.

    Read-only and cached: one ``SELECT <five columns> ... LIMIT 1`` per process through
    the caller's own RLS-scoped client, so the probe sees what the write will see. An
    *indeterminate* answer resolves to ``True`` so the binding write is attempted and any
    real failure surfaces at the INSERT rather than being pre-emptively downgraded - the
    same disposition ``strategy_service.canonical_columns_supported`` takes.
    """
    cached = _cached_binding_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        result = await _execute(
            sb.table("strategy_deployments")
            .select(",".join(BINDING_COLUMNS))
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_binding_column_error(exc):
            _remember_binding_support(False)
            warn_binding_columns_absent(str(exc))
            return False
        logger.warning(
            "Deployment binding column probe was inconclusive (%s); attempting the "
            "binding write and letting a real error surface.",
            exc,
        )
        return True

    error = getattr(result, "error", None)
    if error is not None and is_missing_binding_column_error(Exception(str(error))):
        _remember_binding_support(False)
        warn_binding_columns_absent(str(error))
        return False

    _remember_binding_support(True)
    return True


# ══════════════════════════════════════════════════════════════════════════
# THE REQUEST, NORMALISED
# ══════════════════════════════════════════════════════════════════════════


def normalise_mode(mode: Any, *, environment: Optional[str] = None) -> str:
    """``mode`` as one of :data:`BINDING_MODES`.

    ``environment`` is the fallback, and only for the two values that mean the same thing:
    the legacy column's vocabulary is paper/live/cloud/local, and ``cloud``/``local``
    describe *where a worker runs*, not whether fills are real. Mapping either of them
    onto ``live`` would make a deployment route real orders because of a hosting choice,
    so they resolve to ``paper`` - the value that reaches no real order router.

    Raises
        :class:`DeployRejected` (``MODE_UNRECOGNISED``, 422) for anything else. Fail
        closed: the safe default and the permissive one are one string apart.
    """
    raw = mode if mode is not None else environment
    normalised = str(raw or MODE_PAPER).strip().lower()
    if normalised in BINDING_MODES:
        return normalised
    if mode is None and normalised in ("cloud", "local", "sandbox", ""):
        # A legacy `environment` value that says nothing about fills.
        return MODE_PAPER
    raise DeployRejected(
        "MODE_UNRECOGNISED",
        f"'{raw}' is not a deployment mode. A Deployment_Binding mode is one of "
        f"{', '.join(BINDING_MODES)} (chk_sd_mode).",
        {"mode": raw, "recognised_modes": list(BINDING_MODES)},
    )


def normalise_execution_config(raw: Any) -> Dict[str, Any]:
    """``design.md``'s ``execution_config`` document, and nothing else in it.

    An unknown key is refused rather than dropped: a caller that sends
    ``max_notional`` instead of ``max_order_notional`` would otherwise be told the
    deployment is bound while the limit it set is not in force anywhere. A key naming
    credential material or exchange identity is refused by the same rule that governs
    node params (``schema.is_forbidden_param``), so ``execution_config`` cannot become a
    second place a secret lives (Requirement 21.7).
    """
    from backend_app.backend.strategy_dag.schema import is_forbidden_param

    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise DeployRejected(
            "EXECUTION_CONFIG_INVALID",
            "execution_config must be an object.",
            {"received_type": type(raw).__name__},
        )

    forbidden = sorted({str(k) for k in raw if is_forbidden_param(k)})
    if forbidden:
        raise DeployRejected(
            "EXECUTION_CONFIG_FORBIDDEN_FIELD",
            "execution_config carries exchange identity or credential material. An "
            "exchange account is referenced by id and its credentials are resolved "
            "inside the execution process; they are never sent in a request "
            "(Requirements 13.9, 21.7).",
            {"forbidden_fields": forbidden},
        )

    unknown = sorted({str(k) for k in raw if str(k) not in EXECUTION_CONFIG_FIELDS})
    if unknown:
        raise DeployRejected(
            "EXECUTION_CONFIG_UNKNOWN_FIELD",
            "execution_config carries field(s) no execution path reads, so the limit "
            "they express would not be in force. Remove them or use the published "
            "fields.",
            {"unknown_fields": unknown, "known_fields": list(EXECUTION_CONFIG_FIELDS)},
        )

    return {str(key): raw[key] for key in EXECUTION_CONFIG_FIELDS if key in raw}


def _requested_gap_strategy(raw: Any) -> Optional[GapHandlingStrategy]:
    """A gap strategy label as the pipeline's own enum member, or ``None``.

    An unrecognised label is passed through as a string so
    :func:`market_data_contract.resolve_gap_policy` produces the refusal - there is one
    gap rule in this codebase and this is not a second one.
    """
    if raw is None:
        return None
    if isinstance(raw, GapHandlingStrategy):
        return raw
    label = str(raw).strip().lower()
    for member in GapHandlingStrategy:
        if member.value == label or member.name.lower() == label:
            return member
    return raw  # type: ignore[return-value] - resolve_gap_policy classifies it


def resolve_binding_gap_policy(mode: str, requested: Any = None):
    """The gap policy this binding may run under (Requirement 13.7).

    A thin adapter over :func:`market_data_contract.resolve_gap_policy` and deliberately
    nothing more. Task 7.5 owns the whole rule - it already refuses every fabricating
    strategy in **every** mode, which is stricter than 13.7's live-only floor, and it
    returns the disclosure that travels to the author. Writing a second live-only rule
    here would be a second answer to one question, and the looser of the two would
    eventually be the one consulted.
    """
    try:
        return resolve_gap_policy(mode, _requested_gap_strategy(requested))
    except MarketDataContractError as exc:
        raise DeployRejected(
            exc.code, exc.message, exc.details, http_status=422
        ) from exc


@dataclass(frozen=True)
class DeploymentBinding:
    """``design.md`` -> ``STRUCTURE DeploymentBinding``.

    Frozen: it describes a deployment that is about to be written and must not drift
    between being gated and being persisted. ``exchange_account_id`` is a **reference**
    into the credential vault, never a credential (Requirement 13.9).
    """

    strategy_id: str
    version_id: str
    version: str
    user_id: str
    mode: str
    exchange_account_id: Optional[str]
    risk_config_id: Optional[str]
    execution_config: Dict[str, Any]
    dag_hash: Optional[str]
    #: The venue slug the account is for. Resolved from the account row, so the execution
    #: process can find the keys; it is NOT read from the request and NOT part of the
    #: strategy (SB-06, Requirement 12.1).
    exchange_id: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    market_type: Optional[str] = None
    #: Where the timeframe verdict came from: the venue's own vocabulary or the
    #: pipeline intersection. Stated so a caller cannot mistake one for the other.
    timeframe_source: Optional[str] = None
    gap_policy: Optional[Dict[str, Any]] = None
    warnings: Tuple[Dict[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "version": self.version,
            "user_id": self.user_id,
            "mode": self.mode,
            "exchange_account_id": self.exchange_account_id,
            "risk_config_id": self.risk_config_id,
            "execution_config": dict(self.execution_config),
            "dag_hash": self.dag_hash,
            "exchange_id": self.exchange_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "market_type": self.market_type,
            "timeframe_source": self.timeframe_source,
            "gap_policy": self.gap_policy,
            "warnings": [dict(w) for w in self.warnings],
        }


@dataclass
class BindingRequest:
    """What a deploy request may say. Every field is optional except by 13.6.

    ``environment`` is carried alongside ``mode`` rather than replacing it: the legacy
    column stays untouched (task 8.1 left it unconstrained and unbackfilled) and ``mode``
    is the constrained one.
    """

    mode: Optional[str] = None
    environment: Optional[str] = None
    exchange_account_id: Optional[str] = None
    risk_config_id: Optional[str] = None
    execution_config: Optional[Mapping[str, Any]] = None
    gap_strategy: Optional[Any] = None

    @classmethod
    def from_payload(cls, payload: Optional[Mapping[str, Any]]) -> "BindingRequest":
        data = dict(payload or {})
        return cls(
            mode=data.get("mode"),
            environment=data.get("environment"),
            exchange_account_id=_clean_id(data.get("exchange_account_id")),
            risk_config_id=_clean_id(data.get("risk_config_id")),
            execution_config=data.get("execution_config"),
            gap_strategy=data.get("gap_strategy"),
        )


def _clean_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ══════════════════════════════════════════════════════════════════════════
# GATE 1 - LIFECYCLE (Requirement 13.3)
# ══════════════════════════════════════════════════════════════════════════

#: For each lifecycle state that is not ``READY``, the prerequisite still outstanding.
#: Requirement 13.3 asks for the current state **and** the outstanding prerequisite, so a
#: refusal tells the author what to do rather than only that they may not.
#:
#: The wording tracks the transitions actually implemented: ``READY`` is reached either
#: because the graph declares no model node (Requirement 14.10, task 2.3) or because every
#: model node is bound to an active ``model_version`` (Requirement 17.9, task 6.5).
_OUTSTANDING_PREREQUISITE: Dict[str, str] = {
    "DRAFT": (
        "this version has not passed backend validation yet - validate and save it, "
        "which compiles it and records its identity hash"
    ),
    "VALIDATED": (
        "this version validated but has not been persisted as an immutable version yet - "
        "save it"
    ),
    "SAVED": (
        "this version declares model node(s) whose training has not been queued yet - "
        "start training for every model node"
    ),
    "TRAINING": (
        "training is still running for at least one model node - wait for every job to "
        "complete, or cancel and fix the blocking one"
    ),
    "TRAINED": (
        "training finished but the readiness re-check has not passed - every model node "
        "must be bound to an active model version"
    ),
    "DEPLOYED": (
        "this version is already bound to a deployment - stop that deployment, or "
        "create a new version to deploy"
    ),
    "RUNNING": (
        "this version is already running - stop that deployment before binding it again"
    ),
    "PAUSED": (
        "this version is bound to a paused deployment - resume or stop that deployment "
        "instead of creating a second binding"
    ),
    "STOPPED": (
        "this version's deployment was stopped - create a new version, or restore this "
        "one, to reach READY again"
    ),
    "ARCHIVED": "this version is archived and cannot be deployed",
}


def outstanding_prerequisite(state: Any) -> str:
    """The sentence naming what a non-``READY`` version still needs.

    An unrecognised state gets its own sentence rather than a blank: a value outside
    ``chk_lifecycle_state`` means the row disagrees with the schema, and saying so is
    more useful than "unknown".
    """
    label = str(state).upper() if state else ""
    if label in _OUTSTANDING_PREREQUISITE:
        return _OUTSTANDING_PREREQUISITE[label]
    if not label:
        return (
            "this version records no lifecycle state at all, so nothing establishes that "
            "its model nodes are bound - validate and save it again"
        )
    return (
        f"'{label}' is not a lifecycle state this platform recognises "
        f"(chk_lifecycle_state admits {', '.join(sorted(LIFECYCLE_STATES))}); the "
        f"version row disagrees with the schema and must be re-saved"
    )


def assert_version_ready(
    version_row: Mapping[str, Any], strategy_id: str, version: Any
) -> None:
    """Refuse a version whose lifecycle state is not ``READY`` (Requirement 13.3).

    ``design.md``'s ``ASSERT version.lifecycle_state = READY  // all ML nodes bound``.
    409, naming the state and the outstanding prerequisite.

    A row with no ``lifecycle_state`` key at all - a legacy row, or one written before
    migration 004 part 1 - is refused identically to one holding NULL: ``dict.get``
    returns ``None`` for both, and both mean "nothing establishes that this version's
    model nodes are bound". In practice such a row is already refused one gate earlier by
    ``strategy_service._assert_deploy_prerequisites`` (``validation_state`` is a part-1
    column too), so this is the same disposition rather than a new one.
    """
    state = version_row.get("lifecycle_state")
    label = str(state).upper() if state else None
    if label == LIFECYCLE_READY:
        return
    raise DeployRejected(
        "VERSION_NOT_READY",
        f"Cannot deploy version {version} of strategy {strategy_id}: its lifecycle "
        f"state is {label or 'not recorded'}, not {LIFECYCLE_READY}. Outstanding "
        f"prerequisite: {outstanding_prerequisite(state)}.",
        {
            "lifecycle_state": label,
            "required_lifecycle_state": LIFECYCLE_READY,
            "outstanding_prerequisite": outstanding_prerequisite(state),
            "strategy_id": strategy_id,
            "version": version,
        },
        http_status=409,
    )


# ══════════════════════════════════════════════════════════════════════════
# GATE 2 - THE MARKET THE VERSION TRADES, FROM THE VERSION (SB-06)
# ══════════════════════════════════════════════════════════════════════════


def load_binding_plan(version_row: Mapping[str, Any]) -> Any:
    """The version's stored ``compiled_plan`` as a :class:`CompiledPlan`.

    Extracted from :func:`resolve_binding_market` by task 8.6 because the model gates need
    the same plan and parsing it twice would let the market gate and the model gates
    disagree about what the version is. The refusal is unchanged: ``PLAN_UNREADABLE``
    (409), Requirement 22.5's disposition - a stored value that will not read back as a
    plan means the version must be recompiled, not that it may be deployed.
    """
    from backend_app.backend.strategy_dag.plan import CompiledPlan, PlanBuildError

    raw = version_row.get("compiled_plan")
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        return (
            CompiledPlan.from_json(raw)
            if isinstance(raw, str)
            else CompiledPlan.from_dict(raw)
        )
    except (PlanBuildError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise DeployRejected(
            "PLAN_UNREADABLE",
            "This version's stored compiled plan could not be read back, so the market "
            "it trades cannot be determined and it must be recompiled before it is "
            f"deployed. Detail: {exc}",
            {"version_id": version_row.get("id")},
            http_status=409,
        ) from exc


def resolve_binding_market(
    version_row: Mapping[str, Any], plan: Any = None
) -> Dict[str, Optional[str]]:
    """``{"symbol", "timeframe", "market_type"}`` read from the version's own plan.

    The plan's DATA nodes are the only source. Nothing here substitutes ``"BTC/USDT"``,
    ``"5m"`` or a venue - that substitution is defect SB-06, and Requirement 12.4 exists
    to delete it. ``scan_plan_markets`` is reused so a deploy, a preview and a training
    job cannot disagree about which market a graph declares.

    Raises
        :class:`DeployRejected` ``PLAN_UNREADABLE`` (409) when ``compiled_plan`` will not
        read back as a plan - the deploy prerequisite gate already required the column to
        be non-empty, so an unreadable value means the stored plan is not one and the
        version must be recompiled (Requirement 22.5's disposition).
        ``MARKET_UNRESOLVED`` / ``MARKET_AMBIGUOUS`` (422) when the plan declares no
        market or more than one.
    """
    from backend_app.backend.strategy_service import scan_plan_markets

    if plan is None:
        plan = load_binding_plan(version_row)

    symbols, timeframes, data_node = scan_plan_markets(plan)
    if not symbols or not timeframes:
        raise DeployRejected(
            "MARKET_UNRESOLVED",
            "This version's Market Data block declares no symbol and timeframe, so "
            "there is no market to check the target account against.",
            {"symbols": symbols, "timeframes": timeframes},
        )
    if len(symbols) > 1 or len(timeframes) > 1:
        raise DeployRejected(
            "MARKET_AMBIGUOUS",
            "This version reads more than one market. A deployment binds one account to "
            "one feed, so binding it would mean choosing a market on the author's "
            "behalf.",
            {"symbols": symbols, "timeframes": timeframes},
        )

    market_type = None
    if data_node is not None:
        raw_type = data_node.params.get("market_type")
        if isinstance(raw_type, str) and raw_type.strip():
            market_type = raw_type.strip().lower()

    return {
        "symbol": symbols[0],
        "timeframe": timeframes[0],
        "market_type": market_type,
    }


# ══════════════════════════════════════════════════════════════════════════
# GATE 3 - OWNERSHIP (Requirement 13.2)
# ══════════════════════════════════════════════════════════════════════════


def _rows(result: Any) -> List[Dict[str, Any]]:
    return list((getattr(result, "data", None) or []) if result else [])


async def load_owned_exchange_account(
    sb: Any, user_id: str, exchange_account_id: str
) -> Dict[str, Any]:
    """One exchange account this user owns, or a 404-mapped refusal.

    Two filters, the convention tasks 6.3/6.5/6.6 established: the request-scoped client
    applies the table's own owner policy, **and** an explicit ``.eq("user_id", ...)`` is
    added so the check does not depend on RLS alone. An account belonging to another
    tenant is indistinguishable from one that does not exist - a 404, never a 403,
    because a 403 would confirm the id.

    Both candidate tables are consulted because they hold the same accounts from two
    angles and neither is authoritative on its own: ``exchange_keys`` is what the vault
    reads, ``exchange_connections`` is what records connection state. A relation absent
    from this deployment is skipped with a debug line rather than failing the deploy -
    but if *every* candidate is absent the account cannot be confirmed and the refusal
    stands, because "no table to check" is not "the account is yours".
    """
    checked: List[str] = []
    for table in EXCHANGE_ACCOUNT_TABLES:
        try:
            result = await _execute(
                sb.table(table)
                .select("*")
                .eq("id", exchange_account_id)
                .eq("user_id", str(user_id))
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - an absent relation must not 500
            logger.debug(
                "[DeploymentBinding] %s unavailable while resolving exchange account: %s",
                table,
                exc,
            )
            continue
        checked.append(table)
        rows = _rows(result)
        if rows:
            row = dict(rows[0])
            row.setdefault("_source_table", table)
            return row

    raise DeployRejected(
        "EXCHANGE_ACCOUNT_NOT_FOUND",
        f"No exchange account {exchange_account_id} belongs to this user, so no "
        f"deployment can be bound to it.",
        {"exchange_account_id": exchange_account_id, "tables_checked": checked},
        http_status=404,
    )


def account_exchange_id(account: Mapping[str, Any]) -> Optional[str]:
    """The venue slug an account is for, lowercased, or ``None``.

    This is the handle the execution process resolves credentials with
    (``load_decrypted_keys(user_id, exchange_id)``) and the handle the market
    compatibility check names. It comes from the account row and from nowhere else -
    never from the request, and never from the strategy (SB-06).
    """
    raw = account.get("exchange_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return None


async def load_owned_risk_config(
    sb: Any, user_id: str, risk_config_id: str
) -> Dict[str, Any]:
    """One risk configuration this user owns, or a 404-mapped refusal.

    ``risk_settings`` holds at most one row per user (``user_id`` is ``UNIQUE``), so this
    both confirms the id exists and that it is this user's. Same two filters, same
    404-not-403 rule as :func:`load_owned_exchange_account`.
    """
    try:
        result = await _execute(
            sb.table(RISK_CONFIG_TABLE)
            .select("*")
            .eq("id", risk_config_id)
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - an absent relation must not 500
        logger.debug(
            "[DeploymentBinding] %s unavailable while resolving risk config: %s",
            RISK_CONFIG_TABLE,
            exc,
        )
        result = None

    rows = _rows(result)
    if rows:
        return dict(rows[0])

    raise DeployRejected(
        "RISK_CONFIG_NOT_FOUND",
        f"No risk configuration {risk_config_id} belongs to this user, so no deployment "
        f"can be bound to it.",
        {"risk_config_id": risk_config_id, "table": RISK_CONFIG_TABLE},
        http_status=404,
    )


# ══════════════════════════════════════════════════════════════════════════
# GATE 4 - MARKET COMPATIBILITY (Requirements 13.4, 13.5)
# ══════════════════════════════════════════════════════════════════════════

#: Cache for a venue's own timeframe vocabulary, keyed by venue slug. ``None`` means the
#: venue was consulted and stated nothing readable - cached so a deploy does not
#: reconstruct a CCXT descriptor per request.
_venue_timeframes: Dict[str, Optional[frozenset]] = {}


def reset_venue_timeframe_cache() -> None:
    """Forget the per-venue timeframe vocabularies. Test-only."""
    _venue_timeframes.clear()


def venue_timeframes(exchange_id: str) -> Optional[frozenset]:
    """The OHLCV intervals a venue's own CCXT descriptor lists, or ``None``.

    Static descriptor metadata, read from the installed ``ccxt`` package. **No network
    call and no credential**: ``describe()`` is a literal in the exchange class, so this
    is the venue's own statement of which intervals it serves, not a probe of it.
    ``asset_universe`` deliberately never awaits an exchange from a request path and this
    does not either.

    ``None`` - not an empty set - when ccxt is absent, the venue is unknown to it, or it
    lists no timeframes. An empty set would read as "this venue supports nothing" and
    refuse every deploy; ``None`` means "unknown", and the caller then falls back to the
    pipeline intersection and *says so*.
    """
    slug = str(exchange_id or "").strip().lower()
    if not slug:
        return None
    if slug in _venue_timeframes:
        return _venue_timeframes[slug]

    labels: Optional[frozenset] = None
    try:
        import ccxt  # type: ignore

        klass = getattr(ccxt, slug, None)
        if klass is not None:
            raw = getattr(klass, "timeframes", None)
            if not raw:
                # ccxt puts `timeframes` on the instance via describe(); constructing one
                # performs no I/O.
                raw = getattr(klass(), "timeframes", None)
            if raw:
                labels = frozenset(str(key) for key in raw)
    except Exception as exc:  # noqa: BLE001 - never fail a deploy over metadata
        logger.debug(
            "[DeploymentBinding] ccxt timeframe vocabulary for %s unreadable: %s",
            slug,
            exc,
        )
        labels = None

    _venue_timeframes[slug] = labels
    return labels


def pipeline_timeframe_labels() -> List[str]:
    """The intervals every stage of this platform's pipeline can process.

    Reuses ``routers.strategy_operations._pipeline_timeframes`` - the same intersection
    ``GET /registry/timeframes`` serves (task 7.2), which is the set the author picked
    from. A second list here would be exactly the hardcoded-vocabulary defect Phase 7
    spent four tasks removing, so this imports the one that exists rather than restating
    it. The import is deferred to call time because the router imports this module.

    Raises
        :class:`DeployRejected` (``TIMEFRAME_VOCABULARY_UNAVAILABLE``, 503) when no
        vocabulary is readable. Refusing beats admitting every label.
    """
    from fastapi import HTTPException

    from backend_app.routers.strategy_operations import _pipeline_timeframes

    try:
        timeframes, _sources = _pipeline_timeframes()
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        raise DeployRejected(
            str(detail.get("error") or "TIMEFRAME_VOCABULARY_UNAVAILABLE"),
            str(
                detail.get("message")
                or "This platform's data pipeline states no timeframe vocabulary, so no "
                "deployment's interval can be confirmed."
            ),
            {},
            http_status=503,
        ) from exc
    return [str(entry["id"]) for entry in timeframes]


async def assert_symbol_available(
    symbol: str,
    market_type: Optional[str],
    *,
    exchange_id: Optional[str],
    exchange_account_id: Optional[str],
) -> Dict[str, Any]:
    """Refuse a symbol the target account does not list (Requirement 13.4).

    ``design.md``'s ``IF NOT market_exists(account.exchange_id, symbol,
    market_type_of(version)) THEN RAISE DeployRejected("SYMBOL_NOT_AVAILABLE", symbol,
    account.exchange_id)``.

    The source is task 7.1's cached universe and nothing else: ``AssetRef.available_on``
    records which supported venues list each ``(symbol, market_type)``, and its docstring
    already names this check as its reason to exist. There is no list of symbols in this
    module.

    With **no** account bound (a paper deployment may have none), there is no venue to
    check against, so the symbol is required to be listed *somewhere* in the universe.
    That still refuses a symbol nothing trades, and the returned ``scope`` says which
    question was answered so a caller cannot read the weaker answer as the stronger one.

    Raises
        ``MARKET_UNIVERSE_UNAVAILABLE`` (503) when the universe has not loaded - the
        compatibility of this symbol with this venue is then unknown, and unknown is
        refused. ``SYMBOL_NOT_AVAILABLE`` (422) otherwise, naming the symbol and the
        account.
    """
    from backend_app.backend import asset_universe as au

    universe = await au.read_cached_universe()
    if universe is None or universe.is_empty:
        au.schedule_refresh()
        raise DeployRejected(
            "MARKET_UNIVERSE_UNAVAILABLE",
            "The tradeable market universe is not loaded, so this platform cannot "
            "confirm that the target account lists this market. The deployment is "
            "refused rather than started against an unverified market; the universe is "
            "refreshed off the request path, so retry shortly.",
            {
                "symbol": symbol,
                "market_type": market_type,
                "exchange_account_id": exchange_account_id,
                "exchange_id": exchange_id,
            },
            http_status=503,
        )

    wanted = str(symbol).strip()
    wanted_type = str(market_type).strip().lower() if market_type else None
    matches = [
        asset
        for asset in universe.assets
        if asset.symbol == wanted and (wanted_type is None or asset.market_type == wanted_type)
    ]

    if exchange_id:
        listed_on = [
            asset for asset in matches if exchange_id in {v.lower() for v in asset.available_on}
        ]
        if not listed_on:
            venues = sorted({v for asset in matches for v in asset.available_on})
            raise DeployRejected(
                "SYMBOL_NOT_AVAILABLE",
                f"Exchange account {exchange_account_id} trades on {exchange_id}, which "
                f"lists no {wanted_type or 'matching'} market for {wanted}. This version "
                f"trades {wanted}, so the deployment is refused rather than started "
                f"against a market that account cannot reach.",
                {
                    "symbol": wanted,
                    "market_type": wanted_type,
                    "exchange_id": exchange_id,
                    "exchange_account_id": exchange_account_id,
                    "listed_on": venues,
                    "universe_hash": universe.universe_hash,
                },
            )
        return {
            "scope": "account",
            "exchange_id": exchange_id,
            "available_on": sorted(listed_on[0].available_on),
            "universe_hash": universe.universe_hash,
        }

    if not matches:
        raise DeployRejected(
            "SYMBOL_NOT_AVAILABLE",
            f"No supported venue lists a {wanted_type or 'matching'} market for "
            f"{wanted}, so this version has no market to trade and the deployment is "
            f"refused.",
            {
                "symbol": wanted,
                "market_type": wanted_type,
                "exchange_account_id": exchange_account_id,
                "universe_hash": universe.universe_hash,
            },
        )
    return {
        "scope": "platform",
        "exchange_id": None,
        "available_on": sorted(matches[0].available_on),
        "universe_hash": universe.universe_hash,
    }


def assert_timeframe_supported(
    timeframe: str,
    *,
    exchange_id: Optional[str],
    exchange_account_id: Optional[str],
) -> str:
    """Refuse a timeframe the target account does not serve (Requirement 13.5).

    Two vocabularies, both real, intersected in the order that makes the refusal true:

    1. The **pipeline** intersection (task 7.2's ``/registry/timeframes``). A label
       outside it cannot be resampled, cannot be bucketed by the live loop and cannot be
       coverage-checked, so no account can serve data at it through this platform.
    2. The **venue's own** vocabulary from its CCXT descriptor, when readable. A label
       the pipeline handles but the venue does not publish is refused too - that is the
       account-specific half of 13.5.

    Returns the source of the verdict (``"venue+pipeline"`` or ``"pipeline"``), which the
    binding records, so an unreadable venue vocabulary is visible rather than silently
    treated as agreement.

    Raises
        ``TIMEFRAME_NOT_SUPPORTED`` (422), naming the timeframe and the account.
    """
    label = str(timeframe).strip()
    pipeline = pipeline_timeframe_labels()
    if label not in pipeline:
        raise DeployRejected(
            "TIMEFRAME_NOT_SUPPORTED",
            f"This platform's data pipeline serves no market data at {label}, so "
            f"exchange account {exchange_account_id} cannot supply this version's bars. "
            f"The deployment is refused.",
            {
                "timeframe": label,
                "exchange_account_id": exchange_account_id,
                "exchange_id": exchange_id,
                "supported_timeframes": pipeline,
                "source": "pipeline",
            },
        )

    venue = venue_timeframes(exchange_id) if exchange_id else None
    if venue is None:
        return "pipeline"
    if label not in venue:
        raise DeployRejected(
            "TIMEFRAME_NOT_SUPPORTED",
            f"Exchange account {exchange_account_id} trades on {exchange_id}, which "
            f"serves no market data at {label}. This version reads {label} bars, so the "
            f"deployment is refused.",
            {
                "timeframe": label,
                "exchange_account_id": exchange_account_id,
                "exchange_id": exchange_id,
                "supported_timeframes": sorted(venue & set(pipeline)),
                "source": "venue",
            },
        )
    return "venue+pipeline"


# ══════════════════════════════════════════════════════════════════════════
# THE BINDING
# ══════════════════════════════════════════════════════════════════════════


def assert_account_required_for_live(mode: str, exchange_account_id: Optional[str]) -> None:
    """Requirement 13.6, enforced in the handler as well as in the database.

    ``chk_sd_live_needs_account`` is the database half, and it only exists once 004e is
    applied. This is the half that turns the refusal into a 422 naming the missing
    account instead of a 23514 surfacing as a 500 - which is exactly what task 8.1's note
    asked 8.2 to keep.
    """
    if mode == MODE_LIVE and not exchange_account_id:
        raise DeployRejected(
            "LIVE_REQUIRES_EXCHANGE_ACCOUNT",
            "A live deployment routes real orders, so it must name the exchange account "
            "it trades through. Bind an exchange account, or deploy in paper mode.",
            {"mode": mode, "exchange_account_id": None},
        )


# ══════════════════════════════════════════════════════════════════════════
# GATE 6 - THE MODEL ARTIFACT AND ITS FEATURE SCHEMA (Requirements 17.6, 17.7)
# ══════════════════════════════════════════════════════════════════════════


async def _active_model_rows(
    sb: Any, user_id: str, version_id: str
) -> Dict[str, Dict[str, Any]]:
    """``{node_id: row}`` for this version's active model versions, ownership re-checked.

    ``model_versioning.active_model_versions`` is reused as-is; what is added is the
    explicit ownership filter the rest of this module applies. 004d's ``mv_owner_select``
    already scopes the request-scoped client, and the row's own ``user_id`` is compared
    again here, so the check does not rest on RLS alone - the same two-filter convention
    :func:`load_owned_exchange_account` uses. A row belonging to another tenant is
    dropped rather than reported, which lands on ``MODEL_NODE_NOT_BOUND`` for that node
    rather than confirming that someone else's model version exists.

    Raises
        :class:`DeployRejected` ``MODEL_VERSIONS_UNAVAILABLE`` (503) when 004d is
        unapplied. ``active_model_versions`` degrades to
        ``training_worker.ModelPersistenceUnavailable`` with a warning naming 004d, and a
        deployment that cannot read a single model version cannot check either 17.6 or
        17.7. "Could not look" is not "it verified".
    """
    from backend_app.backend.model_versioning import (
        MODEL_MIGRATION,
        active_model_versions,
    )

    try:
        rows = await active_model_versions(sb, str(version_id))
    except Exception as exc:  # noqa: BLE001 - classified, never a 500
        raise DeployRejected(
            "MODEL_VERSIONS_UNAVAILABLE",
            "This version's model bindings could not be read, so neither the model "
            "artifact checksums (Requirement 17.6) nor the feature schemas (17.7) can be "
            f"verified. Apply {MODEL_MIGRATION} if it is outstanding. The deployment is "
            f"refused rather than started with unverified models. Detail: {exc}",
            {
                "migration": MODEL_MIGRATION,
                "version_id": str(version_id),
                "failure": type(exc).__name__,
            },
            http_status=503,
        ) from exc

    owned: Dict[str, Dict[str, Any]] = {}
    for node_id, row in (rows or {}).items():
        if str((row or {}).get("user_id") or "") != str(user_id):
            logger.error(
                "[DeploymentBinding] model version %s for node %s of version %s is not "
                "owned by the deploying user; it is not consulted.",
                (row or {}).get("id"),
                node_id,
                version_id,
            )
            continue
        owned[str(node_id)] = dict(row)
    return owned


async def assert_models_verified(
    sb: Any,
    user: Mapping[str, Any],
    version_row: Mapping[str, Any],
    plan: Any,
    *,
    registry: Any = None,
    store: Any = None,
) -> List[Dict[str, Any]]:
    """Requirements 17.6 and 17.7, at deploy time. Returns any non-blocking warnings.

    17.6's second enforcer, verbatim: "THE Deployment_Service SHALL hold the deployment
    out of the running state". A deployment refused here never reaches ``DEPLOYING``, so
    it never reaches ``RUNNING`` - which is the requirement, expressed at the only point
    where it costs nothing. The runtime half (the node holding ``AWAITING_MODEL``) is
    ``model_readiness.model_ready``, which task 8.4's ``execute_plan`` calls; the verdict
    object carries the state label so the two halves cannot drift apart.

    17.7's report is the point of it: ``expected_feature_columns`` and
    ``actual_feature_columns`` are both in ``details``, ordered, alongside the missing and
    extra sets, because "the schema drifted" without the columns is not an answer an
    author can act on.

    A graph with no model node skips both gates entirely and reads nothing - there is
    nothing to verify, and a deploy of a pure-indicator strategy must not acquire a
    dependency on 004d.
    """
    from backend_app.backend import model_readiness as mr

    ml_nodes = [str(nid) for nid in (getattr(plan, "ml_nodes", None) or ())]
    if not ml_nodes:
        return []

    version_id = str(version_row.get("id") or "")
    model_rows = await _active_model_rows(sb, str(user["id"]), version_id)
    report = mr.evaluate_model_gates(
        version_id, plan, model_rows, registry=registry, store=store
    )

    if report.unbound_nodes:
        raise DeployRejected(
            "MODEL_NODE_NOT_BOUND",
            "This version declares model node(s) with no active model version: "
            f"{', '.join(report.unbound_nodes)}. Requirement 17.5 makes such a version "
            f"not READY, so the version row disagrees with its own model bindings - "
            f"retrain those nodes before deploying.",
            {
                "version_id": version_id,
                "unbound_nodes": list(report.unbound_nodes),
                "ml_nodes": ml_nodes,
            },
            http_status=409,
        )

    artifact = report.first_artifact_failure()
    if artifact is not None:
        raise DeployRejected(
            "MODEL_AWAITING_ARTIFACT",
            f"{artifact.message} The model node holds {artifact.runtime_state} and the "
            f"deployment is held out of the running state.",
            {
                **artifact.details,
                "version_id": version_id,
                "runtime_state": artifact.runtime_state,
                "verdict": artifact.code,
            },
            http_status=409,
        )

    schema = report.first_schema_failure()
    if schema is not None:
        raise DeployRejected(
            "FEATURE_SCHEMA_DRIFT",
            schema.message,
            {
                **schema.details,
                "version_id": version_id,
                "verdict": schema.code,
                "expected_feature_columns": schema.expected,
                "actual_feature_columns": schema.actual,
            },
            http_status=409,
        )

    return report.warnings


async def evaluate_binding(
    sb: Any,
    user: Mapping[str, Any],
    version_row: Mapping[str, Any],
    strategy_id: str,
    version: Any,
    request: BindingRequest,
) -> DeploymentBinding:
    """Every gate, in order, producing the binding that is about to be written.

    Reads only. Nothing here writes, so a failure at any point leaves the database
    exactly as it was - see the module docstring's transaction note. The caller performs
    the single INSERT.

    Order is deliberate: lifecycle before market (a DRAFT version's plan may not exist),
    mode and 13.6 before any ownership read (a live request with no account is refused
    without touching the account tables), ownership before compatibility (the venue to
    check against comes from the account row), and the gap policy before the write
    (13.7's refusal must cost no deployment row).

    Task 8.6 inserted the model gates (17.6, 17.7) after the request has been fully
    validated and before any account, risk or venue lookup. A version whose model artifact
    does not match its recorded checksum cannot run on **any** account, so resolving one
    would be work done to answer a question already settled - and it would put an account
    id in a log line for a deploy that was never going to happen.
    """
    assert_version_ready(version_row, strategy_id, version)

    plan = load_binding_plan(version_row)
    market = resolve_binding_market(version_row, plan)

    mode = normalise_mode(request.mode, environment=request.environment)
    assert_account_required_for_live(mode, request.exchange_account_id)
    execution_config = normalise_execution_config(request.execution_config)
    gap_policy = resolve_binding_gap_policy(mode, request.gap_strategy)

    model_warnings = await assert_models_verified(sb, user, version_row, plan)

    exchange_id: Optional[str] = None
    if request.exchange_account_id:
        account = await load_owned_exchange_account(
            sb, str(user["id"]), request.exchange_account_id
        )
        exchange_id = account_exchange_id(account)
        if exchange_id is None:
            raise DeployRejected(
                "EXCHANGE_ACCOUNT_UNUSABLE",
                f"Exchange account {request.exchange_account_id} names no venue, so "
                f"neither its market coverage nor its credentials can be resolved.",
                {"exchange_account_id": request.exchange_account_id},
            )

    if request.risk_config_id:
        await load_owned_risk_config(sb, str(user["id"]), request.risk_config_id)

    availability = await assert_symbol_available(
        market["symbol"],
        market["market_type"],
        exchange_id=exchange_id,
        exchange_account_id=request.exchange_account_id,
    )
    timeframe_source = assert_timeframe_supported(
        market["timeframe"],
        exchange_id=exchange_id,
        exchange_account_id=request.exchange_account_id,
    )

    # The 17.7 order-only warning travels with the binding rather than being logged and
    # forgotten: an author who reordered their feature blocks should see that on the
    # deploy that accepted it, not discover it the next time they retrain.
    warnings: List[Dict[str, Any]] = list(model_warnings)
    if availability["scope"] == "platform":
        warnings.append(
            {
                "code": "MARKET_CHECKED_AGAINST_PLATFORM",
                "message": (
                    "No exchange account was bound, so this market was confirmed against "
                    "the supported venues as a whole rather than against one account."
                ),
                "available_on": availability["available_on"],
            }
        )
    if timeframe_source == "pipeline":
        warnings.append(
            {
                "code": "VENUE_TIMEFRAMES_UNREADABLE",
                "message": (
                    "The target venue publishes no readable timeframe vocabulary, so the "
                    "interval was confirmed against this platform's data pipeline only."
                ),
                "exchange_id": exchange_id,
            }
        )

    return DeploymentBinding(
        strategy_id=strategy_id,
        version_id=str(version_row.get("id") or ""),
        version=str(version_row.get("version") or version or ""),
        user_id=str(user["id"]),
        mode=mode,
        exchange_account_id=request.exchange_account_id,
        risk_config_id=request.risk_config_id,
        execution_config=execution_config,
        dag_hash=version_row.get("dag_hash"),
        exchange_id=exchange_id,
        symbol=market["symbol"],
        timeframe=market["timeframe"],
        market_type=market["market_type"],
        timeframe_source=timeframe_source,
        gap_policy=gap_policy.to_dict(),
        warnings=tuple(warnings),
    )


# ══════════════════════════════════════════════════════════════════════════
# COLLECT-ALL PREFLIGHT (Requirement 13.2) - trading-lifecycle-integration task 8.1
# ══════════════════════════════════════════════════════════════════════════
#
# WHY A SECOND CALLING CONVENTION AND NOT A FLAG ON evaluate_binding
# ------------------------------------------------------------------
# :func:`evaluate_binding` refuses on the **first** gate that fails, and that is the
# right behaviour for the write path: a request that is already going to be refused must
# not go on to read an exchange account, a risk configuration and the asset universe, and
# must not put an account id in a log line for a deploy that was never going to happen.
# Requirement 13.2 asks for something the write path deliberately does not do - *every*
# failed condition in one answer - and Requirement 13.3 asks for it as a read-only summary
# the deployment configuration workflow can poll while it is open.
#
# So this is an additive second convention over the **same, unchanged gate functions**.
# Every ``assert_*`` / ``resolve_*`` / ``load_owned_*`` function above is called verbatim;
# nothing here re-implements a check, and nothing here writes. A gate added to
# ``evaluate_binding`` later must be added to :data:`BINDING_CONDITION_DEPENDENCIES` too,
# and the test suite asserts the two lists cannot silently diverge - if they did, the
# preflight would report ``deployable: true`` for a request the write path refuses, which
# is the one failure mode this surface must not have.
#
# PENDING IS NOT PASSED
# ---------------------
# When a gate fails, the gates that need **its output** cannot be evaluated at all: with
# no resolved account there is no venue to check a symbol against, and with no readable
# plan there is no market to resolve. Those are reported ``pending`` with the condition
# that blocked them named, never omitted and never assumed. The same disposition covers a
# gate that raised something other than a :class:`DeployRejected` (an unreachable table, a
# missing migration): the condition is ``pending`` carrying ``CONDITION_NOT_EVALUATED``,
# because "could not be checked" is not "checked and satisfied". ``deployable`` is true
# only when **every** condition is ``passed``, so both statuses keep the Deploy button
# disabled (Requirement 13.4) without either of them posing as the other.

#: The three statuses Requirement 13.3 names, lowercase as the response carries them.
CONDITION_PASSED = "passed"
CONDITION_FAILED = "failed"
CONDITION_PENDING = "pending"
CONDITION_STATUSES: Tuple[str, ...] = (CONDITION_PASSED, CONDITION_FAILED, CONDITION_PENDING)

#: Carried by a condition whose gate raised something that is not a classified refusal.
#: Not a :class:`DeployRejected` code - it is not a verdict about the request, it is the
#: admission that this platform could not reach a verdict.
CONDITION_NOT_EVALUATED = "CONDITION_NOT_EVALUATED"

#: Every mandatory condition, in the order :func:`evaluate_binding` gates them, mapped to
#: the conditions whose **output** it needs. A dependency here means "cannot be evaluated
#: without", not "runs after": ``market_resolved`` needs the parsed plan, and
#: ``symbol_available`` needs both the market and the resolved venue, exactly as
#: ``design.md``'s ``mark_dependents_pending`` describes. ``version_ready`` and
#: ``deployment_mode`` are independent of each other on purpose - a DRAFT version with an
#: unrecognised mode must report both, not one.
BINDING_CONDITION_DEPENDENCIES: Dict[str, Tuple[str, ...]] = {
    "version_ready": (),
    "plan_readable": (),
    "market_resolved": ("plan_readable",),
    "deployment_mode": (),
    "execution_config": (),
    "gap_policy": ("deployment_mode",),
    "binding_storable": ("deployment_mode",),
    "models_verified": ("plan_readable",),
    "exchange_account": (),
    "risk_config": (),
    "symbol_available": ("market_resolved", "exchange_account"),
    "timeframe_supported": ("market_resolved", "exchange_account"),
}

#: The condition names, in evaluation order.
BINDING_CONDITIONS: Tuple[str, ...] = tuple(BINDING_CONDITION_DEPENDENCIES)


@dataclass(frozen=True)
class BindingCondition:
    """One mandatory deployment condition and its current verdict.

    ``design.md``'s preflight element: ``{name, status, detail?, code?, message?,
    reason?}``. Frozen for the same reason :class:`DeploymentBinding` is - a condition
    describes one evaluation and must not be edited after the fact.

    ``code`` and ``message`` come from the :class:`DeployRejected` the gate raised, so the
    preflight and the write path name a failure identically; ``reason`` says which
    condition blocked a ``pending`` one. ``http_status`` is carried for a caller that
    wants it but is **not** serialised: the preflight is a 200 answer about a deployment,
    not a refusal of the preflight request.
    """

    name: str
    status: str
    detail: Optional[Dict[str, Any]] = None
    code: Optional[str] = None
    message: Optional[str] = None
    reason: Optional[str] = None
    http_status: Optional[int] = None

    @property
    def passed(self) -> bool:
        return self.status == CONDITION_PASSED

    @property
    def failed(self) -> bool:
        return self.status == CONDITION_FAILED

    @property
    def pending(self) -> bool:
        return self.status == CONDITION_PENDING

    def to_dict(self) -> Dict[str, Any]:
        """The response element. Optional keys are absent rather than ``null``."""
        out: Dict[str, Any] = {"name": self.name, "status": self.status}
        if self.detail is not None:
            out["detail"] = dict(self.detail)
        if self.code:
            out["code"] = self.code
        if self.message:
            out["message"] = self.message
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class BindingSummary:
    """``design.md``'s ``ValidationSummary``: every condition, with its own verdict.

    ``binding`` is the :class:`DeploymentBinding` the write path **would** create, present
    only when every condition passed. It is not persisted and not written by anything -
    it is what lets the preflight report the symbol, timeframe and gap policy the
    deployment would run under without a second pass over the same gates.
    """

    conditions: Tuple[BindingCondition, ...]
    binding: Optional[DeploymentBinding] = None

    @property
    def deployable(self) -> bool:
        """True only when every mandatory condition passed.

        An empty condition set is **not** deployable: "nothing was checked" must never
        enable the Deploy button.
        """
        return bool(self.conditions) and all(c.passed for c in self.conditions)

    @property
    def passed(self) -> Tuple[BindingCondition, ...]:
        return tuple(c for c in self.conditions if c.passed)

    @property
    def failed(self) -> Tuple[BindingCondition, ...]:
        return tuple(c for c in self.conditions if c.failed)

    @property
    def pending(self) -> Tuple[BindingCondition, ...]:
        return tuple(c for c in self.conditions if c.pending)

    def condition(self, name: str) -> Optional[BindingCondition]:
        for c in self.conditions:
            if c.name == name:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        """The preflight response body, exactly as ``design.md`` specifies it."""
        return {
            "deployable": self.deployable,
            "conditions": [c.to_dict() for c in self.conditions],
        }


class _Unchecked:
    """Returned by the collector when a condition did not pass, so no caller can mistake
    a gate's absent output for ``None``."""

    __slots__ = ()

    def __bool__(self) -> bool:  # pragma: no cover - defensive
        return False


UNCHECKED = _Unchecked()


class _ConditionCollector:
    """Runs each gate, records one condition per gate, and never lets one abort the rest.

    Deliberately not a generic "run all these callables" helper: each gate takes different
    arguments and produces a different kind of output, and a uniform signature would mean
    wrapping every gate - which is how a wrapper ends up re-stating a check. The gates are
    called at their own call sites below; this class owns only the bookkeeping.
    """

    def __init__(self) -> None:
        self._conditions: List[BindingCondition] = []
        self._by_name: Dict[str, BindingCondition] = {}

    @property
    def conditions(self) -> Tuple[BindingCondition, ...]:
        return tuple(self._conditions)

    def _record(self, condition: BindingCondition) -> BindingCondition:
        self._conditions.append(condition)
        self._by_name[condition.name] = condition
        return condition

    def _blockers(self, name: str) -> List[str]:
        """The dependencies of ``name`` that did not pass, in declaration order."""
        return [
            dep
            for dep in BINDING_CONDITION_DEPENDENCIES.get(name, ())
            if not (self._by_name.get(dep) is not None and self._by_name[dep].passed)
        ]

    async def run(
        self,
        name: str,
        call: Any,
        *,
        detail: Any = None,
    ) -> Any:
        """Evaluate one gate and record its condition. Returns its output or
        :data:`UNCHECKED`.

        ``call`` is a zero-argument callable so the gate is not invoked at all when a
        dependency already failed - a preflight must not read an exchange account to
        answer a question a failed gate already settled. ``detail`` maps the gate's return
        value onto the condition's ``detail`` document.
        """
        if name not in BINDING_CONDITION_DEPENDENCIES:  # pragma: no cover - programming error
            raise KeyError(f"{name} is not a declared binding condition")

        blockers = self._blockers(name)
        if blockers:
            self._record(
                BindingCondition(
                    name=name,
                    status=CONDITION_PENDING,
                    reason=f"blocked by {', '.join(blockers)}",
                    detail={"blocked_by": blockers},
                )
            )
            return UNCHECKED

        try:
            outcome = call()
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except DeployRejected as exc:
            # A classified refusal: the same code, message and named quantities the write
            # path would return, reported instead of raised.
            self._record(
                BindingCondition(
                    name=name,
                    status=CONDITION_FAILED,
                    code=exc.code,
                    message=exc.message,
                    detail=dict(exc.details) or None,
                    http_status=exc.http_status,
                )
            )
            return UNCHECKED
        except Exception as exc:  # noqa: BLE001 - a read-only summary must not 500
            logger.warning(
                "[DeploymentBinding] preflight condition %s could not be evaluated: %s",
                name,
                exc,
            )
            self._record(
                BindingCondition(
                    name=name,
                    status=CONDITION_PENDING,
                    code=CONDITION_NOT_EVALUATED,
                    reason=(
                        f"this condition could not be checked ({type(exc).__name__}), so "
                        f"it is reported as pending rather than satisfied"
                    ),
                    detail={"failure": type(exc).__name__},
                )
            )
            return UNCHECKED

        resolved_detail = detail(outcome) if callable(detail) else detail
        self._record(
            BindingCondition(
                name=name,
                status=CONDITION_PASSED,
                detail=dict(resolved_detail) if resolved_detail else None,
            )
        )
        return outcome


async def evaluate_binding_summary(
    sb: Any,
    user: Mapping[str, Any],
    version_row: Mapping[str, Any],
    strategy_id: str,
    version: Any,
    request: BindingRequest,
) -> BindingSummary:
    """Every mandatory deployment condition, each with its own verdict (Requirement 13.2).

    ``design.md`` -> "Collect-all deployment validation":
    ``evaluate_binding_collect_all``. Read-only and side-effect-free - the deployment
    configuration workflow polls it while it is open (Requirements 13.3-13.6), so it
    writes nothing, starts nothing and refuses nothing. Every gate is
    :func:`evaluate_binding`'s own, called verbatim; the only difference is that a refusal
    becomes a recorded condition instead of a raised exception.

    A gate whose input a failed gate was supposed to produce is reported ``pending``
    naming the blocker, never skipped and never assumed - see this section's header for
    why ``pending`` and ``passed`` must stay distinguishable.

    ``mode = 'live'`` while migration 004e is unapplied is reported as the
    ``BINDING_NOT_STORABLE`` failure the write path raises, with the migration named, so a
    preflight cannot say "deployable" about a deployment whose account reference has
    nowhere to live.
    """
    c = _ConditionCollector()
    user_id = str(user.get("id") or "")

    # ── Independent of everything: the version's own lifecycle state ──────────
    await c.run(
        "version_ready",
        lambda: assert_version_ready(version_row, strategy_id, version),
        detail={"lifecycle_state": LIFECYCLE_READY},
    )

    # ── The version's plan, then the market it declares (SB-06) ───────────────
    plan = await c.run(
        "plan_readable",
        lambda: load_binding_plan(version_row),
        detail=lambda p: {"dag_hash": version_row.get("dag_hash")},
    )
    market = await c.run(
        "market_resolved",
        lambda: resolve_binding_market(version_row, plan),
        detail=lambda m: dict(m),
    )

    # ── The request itself: mode + 13.6, execution config, gap policy ─────────
    mode = await c.run(
        "deployment_mode",
        lambda: _resolve_mode_and_account_requirement(request),
        detail=lambda m: {"mode": m, "exchange_account_id": request.exchange_account_id},
    )
    execution_config = await c.run(
        "execution_config",
        lambda: normalise_execution_config(request.execution_config),
        detail=lambda cfg: {"fields": sorted(cfg)},
    )
    gap_policy = await c.run(
        "gap_policy",
        lambda: resolve_binding_gap_policy(mode, request.gap_strategy),
        detail=lambda policy: policy.to_dict(),
    )

    # ── 004e: can this binding be stored at all? Probed, never assumed ────────
    await c.run(
        "binding_storable",
        lambda: _assert_summary_binding_storable(sb, mode),
        detail=lambda stored: {
            "binding_stored": bool(stored),
            "migration": None if stored else DEPLOYMENT_BINDING_MIGRATION,
        },
    )

    # ── The model artifacts and their feature schemas (17.6, 17.7) ────────────
    model_warnings = await c.run(
        "models_verified",
        lambda: assert_models_verified(sb, user, version_row, plan),
        detail=lambda warnings: {"warnings": [dict(w) for w in (warnings or [])]},
    )

    # ── Ownership: the account and the risk config, each on its own ───────────
    # Split into two conditions rather than one: if both ids are wrong, Requirement 13.2
    # wants both named, and a single combined condition could only report the first.
    exchange_id = await c.run(
        "exchange_account",
        lambda: _resolve_summary_exchange_id(sb, user_id, request.exchange_account_id),
        detail=lambda venue: {
            "exchange_account_id": request.exchange_account_id,
            "exchange_id": venue,
            "bound": request.exchange_account_id is not None,
        },
    )
    await c.run(
        "risk_config",
        lambda: _resolve_summary_risk_config(sb, user_id, request.risk_config_id),
        detail=lambda row: {
            "risk_config_id": request.risk_config_id,
            "bound": request.risk_config_id is not None,
        },
    )

    # ── Market compatibility, which needs both the market and the venue ───────
    resolved_market: Mapping[str, Any] = market if not isinstance(market, _Unchecked) else {}
    venue = None if isinstance(exchange_id, _Unchecked) else exchange_id
    availability = await c.run(
        "symbol_available",
        lambda: assert_symbol_available(
            resolved_market.get("symbol"),
            resolved_market.get("market_type"),
            exchange_id=venue,
            exchange_account_id=request.exchange_account_id,
        ),
        detail=lambda a: dict(a),
    )
    timeframe_source = await c.run(
        "timeframe_supported",
        lambda: assert_timeframe_supported(
            resolved_market.get("timeframe"),
            exchange_id=venue,
            exchange_account_id=request.exchange_account_id,
        ),
        detail=lambda source: {"source": source},
    )

    summary = BindingSummary(conditions=c.conditions)
    if not summary.deployable:
        return summary

    # Everything passed, so every output above is real: report the binding the write path
    # would create. Built here rather than by re-running the gates, so the summary and the
    # deploy cannot describe two different deployments.
    binding = DeploymentBinding(
        strategy_id=strategy_id,
        version_id=str(version_row.get("id") or ""),
        version=str(version_row.get("version") or version or ""),
        user_id=user_id,
        mode=str(mode),
        exchange_account_id=request.exchange_account_id,
        risk_config_id=request.risk_config_id,
        execution_config=dict(execution_config or {}),
        dag_hash=version_row.get("dag_hash"),
        exchange_id=venue,
        symbol=resolved_market.get("symbol"),
        timeframe=resolved_market.get("timeframe"),
        market_type=resolved_market.get("market_type"),
        timeframe_source=str(timeframe_source),
        gap_policy=gap_policy.to_dict(),
        warnings=tuple(dict(w) for w in (model_warnings or [])),
    )
    return BindingSummary(conditions=summary.conditions, binding=binding)


def _resolve_mode_and_account_requirement(request: BindingRequest) -> str:
    """``normalise_mode`` then ``assert_account_required_for_live``, both verbatim.

    One condition because 13.6 is a statement *about the mode*: a live request with no
    account has no separate "account requirement" to fix independently of the mode it
    asked for, and reporting it twice would name one problem as two.
    """
    mode = normalise_mode(request.mode, environment=request.environment)
    assert_account_required_for_live(mode, request.exchange_account_id)
    return mode


async def _assert_summary_binding_storable(sb: Any, mode: Any) -> bool:
    """Whether 004e is applied, refusing a live binding that could not be stored.

    Reuses the probe (:func:`binding_columns_supported`, which logs the warning naming the
    migration file) and the refusal (:func:`assert_binding_storable`) rather than
    re-deciding either. Returns ``True`` when the binding columns are present; ``False``
    is a *passed* condition only for paper, where the write path also proceeds and reports
    ``binding_stored: false``.
    """
    columns_available = await binding_columns_supported(sb)
    probe = DeploymentBinding(
        strategy_id="",
        version_id="",
        version="",
        user_id="",
        mode=str(mode),
        exchange_account_id=None,
        risk_config_id=None,
        execution_config={},
        dag_hash=None,
    )
    assert_binding_storable(probe, columns_available=columns_available)
    return columns_available


async def _resolve_summary_exchange_id(
    sb: Any, user_id: str, exchange_account_id: Optional[str]
) -> Optional[str]:
    """The account's venue slug, or ``None`` when no account was requested.

    ``None`` is not a failure: a paper deployment may bind no account, and
    :func:`assert_symbol_available` then answers the platform-wide question and says so in
    its ``scope``. The refusals are :func:`load_owned_exchange_account`'s own 404 and
    ``evaluate_binding``'s ``EXCHANGE_ACCOUNT_UNUSABLE``, unchanged.
    """
    if not exchange_account_id:
        return None
    account = await load_owned_exchange_account(sb, user_id, exchange_account_id)
    exchange_id = account_exchange_id(account)
    if exchange_id is None:
        raise DeployRejected(
            "EXCHANGE_ACCOUNT_UNUSABLE",
            f"Exchange account {exchange_account_id} names no venue, so neither its "
            f"market coverage nor its credentials can be resolved.",
            {"exchange_account_id": exchange_account_id},
        )
    return exchange_id


async def _resolve_summary_risk_config(
    sb: Any, user_id: str, risk_config_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The requested risk configuration, or ``None`` when none was requested."""
    if not risk_config_id:
        return None
    return await load_owned_risk_config(sb, user_id, risk_config_id)


def binding_row_columns(binding: DeploymentBinding) -> Dict[str, Any]:
    """The five 004e columns for this binding's ``strategy_deployments`` row.

    ``mode`` is always present. That is task 8.1's explicit obligation on this task:
    ``chk_sd_live_needs_account`` guards ``mode``, not the legacy ``environment`` column,
    so a writer that sets only ``environment`` leaves the database-level Requirement 13.6
    guarantee covering nothing.
    """
    return {
        "exchange_account_id": binding.exchange_account_id,
        "risk_config_id": binding.risk_config_id,
        "execution_config": dict(binding.execution_config),
        "mode": binding.mode,
        "dag_hash": binding.dag_hash,
    }


def assert_binding_storable(binding: DeploymentBinding, *, columns_available: bool) -> None:
    """Refuse a live binding that cannot be stored (task 8.1's second obligation).

    With 004e unapplied there is no ``exchange_account_id`` column, so the account
    reference would be dropped; no ``mode`` column, so nothing records that this is live;
    and no ``chk_sd_live_needs_account``, so the database cannot catch either. A live
    deployment written that way is one nothing can route and nothing can audit, while the
    response would say it is running. 503 - the operator's remedy is naming a file, not
    changing the request.

    Paper is allowed through degraded, because a paper deployment reaches no real order
    router; the caller reports ``binding_stored: false`` and the warning names the file.
    """
    if columns_available or binding.mode != MODE_LIVE:
        return
    raise DeployRejected(
        "BINDING_NOT_STORABLE",
        "A live deployment binding cannot be recorded: strategy_deployments is missing "
        f"the binding columns ({', '.join(BINDING_COLUMNS)}). Apply "
        f"{DEPLOYMENT_BINDING_MIGRATION} first. The deployment is refused rather than "
        "started with its exchange account reference and mode dropped.",
        {
            "migration": DEPLOYMENT_BINDING_MIGRATION,
            "missing_columns": list(BINDING_COLUMNS),
            "mode": binding.mode,
        },
        http_status=503,
    )


def public_binding(
    binding: DeploymentBinding,
    row: Optional[Mapping[str, Any]],
    *,
    binding_stored: bool,
) -> Dict[str, Any]:
    """The binding as a client sees it. Built from named keys, never by copying a row.

    Same construction rule task 6.5 used for ``artifact_uri``: a projection assembled
    from an explicit key list cannot regress into passing a credential through, whereas
    "copy the row and delete one field" is one schema change away from doing exactly
    that (Requirements 13.9, 21.7).

    ``binding_stored`` is reported rather than implied. Task 8.1 requires that a
    deployment is never reported as bound when the binding was not stored.
    """
    return {
        "deployment_id": (row or {}).get("id"),
        "strategy_id": binding.strategy_id,
        "version_id": binding.version_id,
        "version": binding.version,
        "mode": binding.mode,
        "status": (row or {}).get("status") or "deploying",
        "exchange_account_id": binding.exchange_account_id,
        "risk_config_id": binding.risk_config_id,
        "execution_config": dict(binding.execution_config),
        "dag_hash": binding.dag_hash,
        "symbol": binding.symbol,
        "timeframe": binding.timeframe,
        "market_type": binding.market_type,
        "timeframe_source": binding.timeframe_source,
        "gap_policy": binding.gap_policy,
        "binding_stored": bool(binding_stored),
        "binding_migration": None if binding_stored else DEPLOYMENT_BINDING_MIGRATION,
        "warnings": [dict(w) for w in binding.warnings]
        + (
            []
            if binding_stored
            else [
                {
                    "code": "BINDING_NOT_STORED",
                    "message": (
                        "strategy_deployments is missing the binding columns "
                        f"({', '.join(BINDING_COLUMNS)}), so the exchange account, risk "
                        "config, execution config, mode and plan hash were NOT persisted "
                        "with this deployment. Apply "
                        f"{DEPLOYMENT_BINDING_MIGRATION}."
                    ),
                    "migration": DEPLOYMENT_BINDING_MIGRATION,
                }
            ]
        ),
    }


__all__ = [
    "BINDING_COLUMNS",
    "BINDING_CONDITIONS",
    "BINDING_CONDITION_DEPENDENCIES",
    "BINDING_MODES",
    "BINDING_STATES",
    "BindingCondition",
    "BindingRequest",
    "BindingSummary",
    "CONDITION_FAILED",
    "CONDITION_NOT_EVALUATED",
    "CONDITION_PASSED",
    "CONDITION_PENDING",
    "CONDITION_STATUSES",
    "DEPLOYMENT_BINDING_MIGRATION",
    "DeployRejected",
    "DeploymentBinding",
    "EXCHANGE_ACCOUNT_TABLES",
    "EXECUTION_CONFIG_FIELDS",
    "RISK_CONFIG_TABLE",
    "UNCHECKED",
    "account_exchange_id",
    "assert_account_required_for_live",
    "assert_binding_storable",
    "assert_models_verified",
    "assert_symbol_available",
    "assert_timeframe_supported",
    "assert_version_ready",
    "binding_column_support_state",
    "binding_columns_supported",
    "binding_row_columns",
    "evaluate_binding",
    "evaluate_binding_summary",
    "is_missing_binding_column_error",
    "load_binding_plan",
    "load_owned_exchange_account",
    "load_owned_risk_config",
    "normalise_execution_config",
    "normalise_mode",
    "outstanding_prerequisite",
    "pipeline_timeframe_labels",
    "public_binding",
    "remember_binding_columns_absent",
    "reset_binding_column_support",
    "reset_venue_timeframe_cache",
    "resolve_binding_gap_policy",
    "resolve_binding_market",
    "venue_timeframes",
    "warn_binding_columns_absent",
]
