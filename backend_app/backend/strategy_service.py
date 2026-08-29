"""
backend/strategy_service.py — Strategy Service

Central operational command center for all Strategy operations.

Replaces Bot Monitor infrastructure with Strategy-centric architecture.
A deployed Strategy IS the running trading bot.

PHASE 2: Transform Strategies into operational command center
"""

import inspect
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import UUID, uuid4

from backend_app.core.dependencies import create_request_supabase_async, get_telemetry
from backend_app.backend.registry_snapshot_service import record_registry_snapshot
from backend_app.backend.strategy_builder import (
    CANONICAL_VERSION_COLUMNS,
    LIFECYCLE_READY,
    LIFECYCLE_TRAINING,
    LIFECYCLE_VALIDATED,
    CompiledVersion,
    compile_version,
)

# The lifecycle state machine (task 8.3). Imported as a module, so a test that replaces a
# seam replaces it for every caller, and so the transition rules have exactly one home.
from backend_app.backend import strategy_lifecycle as lifecycle
from backend_app.core.audit_trail import StrategyAuditAction as AuditAction

logger = logging.getLogger("StrategyService")

# ══════════════════════════════════════════════════════════════════════════
#  CANONICAL COLUMN AVAILABILITY  (strategy-builder task 2.3)
#
#  DEPLOY ORDERING HAZARD, and how it is contained.
#
#  The ten columns written below are created by
#  backend_app/migrations/004_strategy_builder_canonical.sql (part 1). That
#  file is applied BY HAND - .github/workflows/03-deploy.yml has no migration
#  step - so code can reach production before the DDL does. That exact
#  ordering already caused one production defect: migration 007 was never
#  applied, every marketplace query returned PostgreSQL 42703
#  undefined_column, and the router downgraded it to a warning and served
#  empty results.
#
#  Repeating it here would be worse than empty results: every strategy save
#  would fail. So availability is DETECTED rather than assumed:
#
#    1. Once per process, the first canonical write probes the table with a
#       read-only SELECT of the ten columns (LIMIT 1, RLS-scoped, no write).
#       The answer is cached in this module, so the probe costs one query per
#       process, never one per request.
#    2. A definitive "absent" answer - PostgreSQL 42703, PostgREST PGRST204,
#       or a message naming one of the canonical columns - degrades the write
#       to the legacy row shape and logs a warning NAMING THE MIGRATION.
#       The save succeeds.
#    3. Any other probe failure (network, auth, timeout) is NOT cached and
#       does NOT degrade anything. The canonical write is attempted, and if
#       the INSERT then fails for a reason other than a missing column, the
#       error PROPAGATES. A genuine write failure is never swallowed.
#    4. The negative answer is re-probed after
#       CANONICAL_COLUMN_RECHECK_SECONDS, so applying the migration to a
#       running fleet takes effect without a redeploy. The positive answer is
#       cached for the life of the process: migration 004 part 1 is additive
#       and drops nothing, so a column that exists cannot stop existing.
#
#  Net effect: applying the migration late DEGRADES the save (canonical
#  columns unwritten, loud warning, plan preserved in the legacy
#  execution_graph column); it does not BREAK the save. Applying it on time
#  costs one extra SELECT per process and nothing else.
# ══════════════════════════════════════════════════════════════════════════

#: How long a "columns are absent" verdict is trusted before it is re-probed.
CANONICAL_COLUMN_RECHECK_SECONDS = 300.0

#: The migration named in the degradation warning, so an operator is never left guessing.
CANONICAL_COLUMN_MIGRATION = "backend_app/migrations/004_strategy_builder_canonical.sql"

#: Error codes that mean "this column does not exist", from PostgreSQL and from PostgREST's
#: schema cache respectively. ``PGRST205`` is deliberately absent: that is a missing
#: *table*, which degrading cannot help with and must not be masked.
_MISSING_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

#: Module-level cache. ``None`` means "not yet determined".
_canonical_columns_supported: Optional[bool] = None
_canonical_columns_checked_at: float = 0.0


def reset_canonical_column_support() -> None:
    """Forget the cached canonical-column verdict.

    For tests, and for an operator who has just applied migration 004 and does not want to
    wait out :data:`CANONICAL_COLUMN_RECHECK_SECONDS`.
    """
    global _canonical_columns_supported, _canonical_columns_checked_at
    _canonical_columns_supported = None
    _canonical_columns_checked_at = 0.0


def canonical_column_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False``, or ``None`` for "not yet determined"."""
    return _canonical_columns_supported


def _remember_canonical_support(supported: bool) -> None:
    global _canonical_columns_supported, _canonical_columns_checked_at
    _canonical_columns_supported = supported
    _canonical_columns_checked_at = time.monotonic()


def _cached_canonical_support() -> Optional[bool]:
    """The cached verdict, expiring a negative one so a late migration is picked up."""
    if _canonical_columns_supported is None:
        return None
    if _canonical_columns_supported:
        return True
    if time.monotonic() - _canonical_columns_checked_at >= CANONICAL_COLUMN_RECHECK_SECONDS:
        return None
    return False


def is_missing_canonical_column_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says a canonical column does not exist.

    Deliberately narrow. Anything this returns ``False`` for is re-raised, because the one
    behaviour worse than a save that fails loudly is a save that fails silently.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text:  # missing TABLE, not a missing column
        return False
    if any(code in text for code in _MISSING_COLUMN_CODES):
        return True
    names_canonical_column = any(
        column in text for column in CANONICAL_VERSION_COLUMNS
    )
    if not names_canonical_column:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def _warn_canonical_columns_absent(detail: str) -> None:
    logger.warning(
        "strategy_versions is missing the canonical Strategy Builder columns (%s). "
        "Apply %s (part 1), then restart or wait %.0fs for the re-probe. "
        "Until then version rows are written in the legacy shape: graph_json, "
        "compiled_plan, dag_hash, schema_version, compiler_version, registry_version, "
        "warmup_bars, validation_state and validation_report are NOT persisted, so "
        "Requirement 9.1 is not met and deploy-time hash checks cannot run. "
        "Detail: %s",
        ", ".join(CANONICAL_VERSION_COLUMNS),
        CANONICAL_COLUMN_MIGRATION,
        CANONICAL_COLUMN_RECHECK_SECONDS,
        detail,
    )


async def canonical_columns_supported(sb: Any) -> bool:
    """Whether ``strategy_versions`` carries the migration-004 columns.

    Read-only and cached: one ``SELECT <ten columns> ... LIMIT 1`` per process, executed
    through the caller's RLS-scoped client so the probe sees exactly what the write will
    see. Never writes, never raises: an indeterminate answer resolves to ``True`` so the
    canonical write is attempted and any real failure surfaces at the INSERT rather than
    being pre-emptively downgraded.
    """
    cached = _cached_canonical_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        query = (
            sb.table("strategy_versions")
            .select(",".join(CANONICAL_VERSION_COLUMNS))
            .limit(1)
            .execute()
        )
        result = await query if inspect.isawaitable(query) else query
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if is_missing_canonical_column_error(exc):
            _remember_canonical_support(False)
            _warn_canonical_columns_absent(str(exc))
            return False
        # Transient or unrelated failure: decide nothing, cache nothing.
        logger.warning(
            "Canonical column probe on strategy_versions was inconclusive (%s); "
            "attempting the canonical write and letting a real error surface.",
            exc,
        )
        return True

    # PostgREST clients that report errors on the response rather than by raising.
    error = getattr(result, "error", None)
    if error is not None and is_missing_canonical_column_error(Exception(str(error))):
        _remember_canonical_support(False)
        _warn_canonical_columns_absent(str(error))
        return False

    _remember_canonical_support(True)
    return True


def _next_version_string(existing_rows: Any) -> str:
    """The next ``v<major>.<minor>`` label after the highest one already stored.

    Matches the labelling the existing paths use (``create_strategy`` mints ``v1.0``,
    ``update_strategy`` bumps the major on a blueprint change). A row whose label does not
    parse is ignored rather than crashing the save; the worst outcome is a label collision,
    which ``unique_strategy_version`` then reports loudly.
    """
    highest = 0
    for row in existing_rows or []:
        raw = row.get("version") if isinstance(row, dict) else None
        if not isinstance(raw, str):
            continue
        head = raw.strip().lstrip("vV").split(".")[0]
        try:
            major = int(head)
        except (TypeError, ValueError):
            continue
        if major > highest:
            highest = major
    return f"v{highest + 1}.0"


class DeployPrerequisiteError(Exception):
    """Raised when a version fails the deploy prerequisite gate (Requirement 10.4).

    Deliberately not a :class:`ValueError`: both deploy endpoints already treat a bare
    ``ValueError`` as "resource not found" or "quota/subscription problem" (see
    ``routers/strategy_operations.py``'s ``deploy_version`` and ``deploy_strategy``). A
    version that exists but is not yet deployable is neither of those - it is a client-
    correctable state conflict - so this gets its own type and its own HTTP mapping (409),
    rather than being misrouted through either existing branch.

    ``missing_prerequisite`` is one of ``"validation_state"``, ``"dag_hash"`` or
    ``"compiled_plan"`` - whichever the gate found first, in that order - so a caller can
    act on it without parsing the message.
    """

    def __init__(self, message: str, missing_prerequisite: str):
        super().__init__(message)
        self.missing_prerequisite = missing_prerequisite


#: The name this gate carries in the deploy preflight summary (task 8.2, Requirement 13.3).
#: It is not one of ``deployment_binding.BINDING_CONDITIONS`` because the gate is this
#: service's, not the binding module's - but Requirement 13.1 counts "a valid, hash-verified
#: compiled plan" among the mandatory validations, so 13.3 requires its status alongside
#: them. Named here so the router and the tests read the same string.
DEPLOY_PREREQUISITE_CONDITION = "deploy_prerequisites"


class StrategyStatus(Enum):
    """Strategy lifecycle status."""
    DRAFT = "draft"
    BACKTESTING = "backtesting"
    VALIDATED = "validated"
    READY = "ready"
    DEPLOYING = "deploying"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ARCHIVED = "archived"


class StrategyEnvironment(Enum):
    """Strategy deployment environment."""
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class StrategyHealth(Enum):
    """Strategy health status."""
    HEALTHY = "healthy"
    WARNING = "warning"
    OFFLINE = "offline"
    ERROR = "error"


class StrategyService:
    """
    Central service for all Strategy operations.
    
    Manages complete Strategy lifecycle:
    - Create, Edit, Clone, Version
    - Backtest, Validate, Deploy
    - Pause, Resume, Stop, Delete
    - Publish, Subscribe, Monitor
    - Logs, Executions, Signals, Orders, Metrics, Risk
    """
    
    def __init__(self):
        self._telemetry = None
    
    def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = get_telemetry()
        return self._telemetry
    
    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res

    def _assert_deploy_prerequisites(
        self, version_data: Dict[str, Any], strategy_id: str, version: str
    ) -> None:
        """Refuse a deploy whose version lacks a prerequisite (Requirement 10.4).

        Deploys are refused, naming the missing prerequisite, when ``validation_state``
        is anything other than ``'VALID'``, or ``dag_hash`` is absent, or
        ``compiled_plan`` is absent.

        ``version_data`` may be a row written before migration 004 part 1
        (`004_strategy_builder_canonical.sql`) was applied, or a legacy pre-canonical
        version row that predates the canonical columns entirely - see
        ``_warn_canonical_columns_absent`` and ``canonical_columns_supported`` above,
        which document that exact ordering hazard for the write path. From this gate's
        perspective a column that is missing from the row is indistinguishable from a
        column that is present and ``NULL``: both mean "no recorded identity hash / plan
        for this version", and both must refuse rather than let an unrecompiled or
        legacy version deploy. ``dict.get`` returns ``None`` for an absent key, so the
        same falsy check below covers both cases without special-casing either.

        Called before any deployment row is inserted or any fleet call is made, so a
        rejected attempt costs no quota and creates no deployment record.

        Raises:
            DeployPrerequisiteError: naming the first prerequisite found unmet, in the
                order validation_state, dag_hash, compiled_plan.
        """
        validation_state = version_data.get("validation_state")
        if validation_state != "VALID":
            raise DeployPrerequisiteError(
                f"Cannot deploy version {version} of strategy {strategy_id}: "
                f"validation_state is {validation_state!r} (must be 'VALID')",
                missing_prerequisite="validation_state",
            )

        if not version_data.get("dag_hash"):
            raise DeployPrerequisiteError(
                f"Cannot deploy version {version} of strategy {strategy_id}: "
                f"dag_hash is missing",
                missing_prerequisite="dag_hash",
            )

        if not version_data.get("compiled_plan"):
            raise DeployPrerequisiteError(
                f"Cannot deploy version {version} of strategy {strategy_id}: "
                f"compiled_plan is missing",
                missing_prerequisite="compiled_plan",
            )

    async def create_strategy(
        self,
        user: dict,
        name: str,
        description: str,
        blueprint: dict,
        execution_graph: Optional[dict] = None,
        exchange: str = None,
        symbol: str = None,
        timeframe: str = None,
        tags: List[str] = None
    ) -> Dict:
        """
        Create a new Strategy.
        
        Returns:
            Strategy record with initial version
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        strategy_id = str(uuid4())
        version_id = str(uuid4())
        
        # Create strategy with version
        strategy_data = {
            "id": strategy_id,
            "user_id": user["id"],
            "name": name,
            "description": description,
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "tags": tags or [],
            "status": StrategyStatus.DRAFT.value,
            "environment": StrategyEnvironment.PAPER.value,
            "current_version": "v1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Create initial version
        version_data = {
            "id": version_id,
            "strategy_id": strategy_id,
            "version": "v1.0",
            "blueprint": blueprint,
            "execution_graph": execution_graph,  # Store compiled execution graph
            "is_draft": True,
            "is_current": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Store strategy
        q1 = sb.table("strategies").insert(strategy_data).execute()
        strategy_result = await q1 if inspect.isawaitable(q1) else q1
        
        # Store version
        q2 = sb.table("strategy_versions").insert(version_data).execute()
        version_result = await q2 if inspect.isawaitable(q2) else q2
        
        logger.info(f"Created strategy {strategy_id} v1.0 for user {user['id']}")
        
        return {
            "strategy": strategy_result.data[0] if strategy_result and strategy_result.data else strategy_data,
            "version": version_result.data[0] if version_result and version_result.data else version_data
        }
    
    async def get_strategy(self, user: dict, strategy_id: str) -> Optional[Dict]:
        """
        Get complete Strategy data with current version.
        
        Returns:
            Strategy with version, deployments, and metrics
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Get strategy
        q1 = sb.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user["id"]).execute()
        strategy_res = await q1 if inspect.isawaitable(q1) else q1
        if not strategy_res or not strategy_res.data:
            return None
        
        strategy = strategy_res.data[0]
        
        # Get current version
        q2 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        version_res = await q2 if inspect.isawaitable(q2) else q2
        version = version_res.data[0] if version_res and version_res.data else None
        
        # Get active deployments
        q3 = (sb.table("strategy_deployments")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .execute())
        deployments_res = await q3 if inspect.isawaitable(q3) else q3
        deployments = deployments_res.data if deployments_res else []
        
        # Get performance metrics
        performance = await self._get_strategy_performance(user, strategy_id)
        
        return {
            "strategy": strategy,
            "version": version,
            "deployments": deployments,
            "performance": performance
        }
    
    async def list_strategies(
        self,
        user: dict,
        status_filter: Optional[str] = None,
        environment_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        List all Strategies for user with optional filters.
        
        Returns:
            List of strategies with summary metrics
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        query = sb.table("strategies").select("*").eq("user_id", user["id"])
        
        if status_filter:
            query = query.eq("status", status_filter)
        if environment_filter:
            query = query.eq("environment", environment_filter)
        
        q1 = query.order("updated_at", desc=True).execute()
        result = await q1 if inspect.isawaitable(q1) else q1
        
        strategies = result.data or [] if result else []
        
        # Enrich with deployment and performance metrics
        enriched = []
        for strategy in strategies:
            # Get current deployment status
            q2 = (sb.table("strategy_deployments")
                               .select("*")
                               .eq("strategy_id", strategy["id"])
                               .eq("status", "running")
                               .execute())
            deployments_res = await q2 if inspect.isawaitable(q2) else q2
            
            is_running = len(deployments_res.data or []) > 0 if deployments_res else False
            
            # Get performance metrics from Telemetry
            try:
                performance = await self._get_strategy_performance(user, strategy["id"])
            except Exception as e:
                logger.warning(f"Failed to fetch performance for strategy {strategy['id']}: {e}")
                performance = None  # Explicitly indicate performance unavailable
            
            enriched.append({
                **strategy,
                "is_running": is_running,
                "deployment_count": len(deployments_res.data or []) if deployments_res else 0,
                "performance": performance
            })
        
        return enriched
    
    async def _get_strategy_performance(self, user: dict, strategy_id: str) -> Dict:
        """
        Get Strategy performance metrics from MetricsService.

        Returns:
            Performance metrics (PnL, ROI, win rate, etc.)

        Raises:
            Exception if performance fetch fails - caller should handle gracefully
        """
        from backend_app.backend.metrics_service import get_metrics_service
        metrics_service = await get_metrics_service()

        return await metrics_service.get_strategy_performance(user["id"], strategy_id)
    
    async def update_strategy(
        self,
        user: dict,
        strategy_id: str,
        updates: Dict
    ) -> Dict:
        """
        Update Strategy (creates new version if blueprint changes).
        
        Returns:
            Updated strategy with new version if applicable
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Check if blueprint is being updated
        blueprint_changed = "blueprint" in updates
        
        # Update strategy metadata
        strategy_updates = {
            **updates,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if "blueprint" in strategy_updates:
            del strategy_updates["blueprint"]  # Handle separately
        
        q1 = sb.table("strategies").update(strategy_updates).eq("id", strategy_id).eq("user_id", user["id"]).execute()
        result = await q1 if inspect.isawaitable(q1) else q1
        
        new_version = None
        edit_disposition = None
        if blueprint_changed and sb:
            # Create new version with updated blueprint
            q2 = (sb.table("strategy_versions")
                                  .select("*")
                                  .eq("strategy_id", strategy_id)
                                  .eq("is_current", True)
                                  .execute())
            current_version_res = await q2 if inspect.isawaitable(q2) else q2
            
            if current_version_res and current_version_res.data:
                current = current_version_res.data[0]

                # ── Requirement 9.4 / design.md `PROCEDURE edit_deployed_strategy`.
                # A DEPLOYED, RUNNING or PAUSED version is never mutated and, here, is
                # not even demoted: the new row is a DRAFT that is NOT current and the
                # deployed row keeps every field it had, including `is_current`. The
                # requirement's words are "SHALL leave the existing version record
                # unchanged", and a live deployment's version losing `is_current` while
                # it is still the thing executing is a change with consequences - the
                # Builder would open a draft as "the strategy" while the running plan is
                # somewhere else. An editable version keeps the pre-existing behaviour
                # exactly: it is demoted and the new draft becomes current.
                edit_disposition = lifecycle.edit_disposition(current)

                # Increment version
                version_parts = current["version"].replace("v", "").split(".")
                major, minor = int(version_parts[0]), int(version_parts[1]) if len(version_parts) > 1 else 0
                
                if blueprint_changed:
                    major += 1
                    minor = 0
                else:
                    minor += 1
                
                new_version_str = f"v{major}.{minor}"

                if not edit_disposition.read_only:
                    # Mark old version as not current
                    q3 = sb.table("strategy_versions").update({"is_current": False}).eq("id", current["id"]).execute()
                    if inspect.isawaitable(q3):
                        await q3
                
                # Create new version
                new_version_data = {
                    "id": str(uuid4()),
                    "strategy_id": strategy_id,
                    "version": new_version_str,
                    "blueprint": updates["blueprint"],
                    "is_draft": True,
                    "is_current": not edit_disposition.read_only,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                if edit_disposition.read_only:
                    # design.md: `draft <- create_draft(strategy_id, base := current.id,
                    # graph := new_graph)`. `cloned_from_version` is migration 001's own
                    # column for exactly this link, so the draft records what it forked
                    # from instead of the lineage being lost.
                    new_version_data["cloned_from_version"] = current["id"]
                    if await canonical_columns_supported(sb):
                        # A fork of a deployed version is a DRAFT: it has not been
                        # validated or compiled, and it is not deployable until it is
                        # saved through the canonical path. Written only where migration
                        # 004 part 1 has been applied; without the column the row is
                        # still a draft by `is_draft`, and the probe has already warned
                        # naming the file.
                        new_version_data["lifecycle_state"] = lifecycle.LIFECYCLE_DRAFT
                
                q4 = sb.table("strategy_versions").insert(new_version_data).execute()
                version_result = await q4 if inspect.isawaitable(q4) else q4
                new_version = version_result.data[0] if version_result and version_result.data else new_version_data
                
                if not edit_disposition.read_only:
                    # Update strategy current version
                    q5 = sb.table("strategies").update({"current_version": new_version_str}).eq("id", strategy_id).execute()
                    if inspect.isawaitable(q5):
                        await q5

                # Requirement 9.8: version creation is audited either way, and the record
                # says which of the two shapes it was.
                await lifecycle.record_audit(
                    AuditAction.VERSION_CREATED,
                    actor_id=lifecycle.actor_of(user),
                    resource_type="strategy_version",
                    resource_id=new_version_data["id"],
                    reason=(
                        edit_disposition.reason
                        if edit_disposition.read_only
                        else f"Edited strategy {strategy_id}: new version {new_version_str}"
                    ),
                    before=edit_disposition.lifecycle_state,
                    after=lifecycle.LIFECYCLE_DRAFT,
                    strategy_id=strategy_id,
                    version_id=new_version_data["id"],
                    metadata={
                        "version": new_version_str,
                        "base_version_id": current["id"],
                        "forked_from_read_only_version": edit_disposition.read_only,
                        "is_current": new_version_data["is_current"],
                    },
                )
        
        return {
            "strategy": result.data[0] if result and result.data else {},
            "new_version": new_version,
            # Requirements 9.4 and 9.9: whether the edit forked a draft, why, and what the
            # canvas should do about it. `None` when no blueprint change was requested.
            "edit": edit_disposition.to_dict() if edit_disposition is not None else None,
        }
    
    # ══════════════════════════════════════════════════════════════════════
    #  CANONICAL VERSION CREATION  (strategy-builder task 2.3)
    # ══════════════════════════════════════════════════════════════════════

    async def create_version(
        self,
        user: dict,
        strategy_id: str,
        graph: Any,
        *,
        registry: Any = None,
        version: Optional[str] = None,
        blueprint: Optional[dict] = None,
        lifecycle_state: str = LIFECYCLE_VALIDATED,
        make_current: bool = True,
        is_draft: bool = True,
        available_bars: Optional[int] = None,
        feature_columns: Optional[int] = None,
        ml_dataset_stats: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Compile ``graph`` and persist it as one immutable version row.

        Requirement 9.1: the graph, the compiled plan, the identity hash, the schema
        version, the compiler version, the registry version, the warmup bars, the
        validation state and the validation report are written as **one** record. The
        values come from :meth:`CompiledVersion.canonical_columns`; nothing is hand-built
        here, and ``dag_hash`` is read as ``plan.dag_hash`` - a field, never
        ``plan.get("dag_hash")``, which is defect SB-02.

        Requirements 3.6 and 3.7: nothing is persisted on the failure path. That is
        enforced by ORDER, not by a rollback:

        1. ``compile_version`` runs first and raises ``ValidationError`` for an invalid
           graph. At that moment no database client exists yet, so there is no partial
           row, no orphaned row with a NULL hash, and nothing to clean up.
        2. The CHECK vocabularies are validated next, still before any I/O, so a bad
           ``lifecycle_state`` cannot fail half way through the write.
        3. Ownership is asserted next, and only then is a single INSERT issued. One
           statement is atomic in PostgreSQL, so it either lands whole or not at all;
           there is no multi-statement window that could leave a half-written version.
        4. ``is_current`` and ``strategies.current_version`` are updated only AFTER the
           INSERT has succeeded, so a failed save never leaves a strategy with no current
           version.

        Tenant isolation is unchanged and enforced twice. The client comes from
        :meth:`_get_supabase`, which mints a request-scoped client carrying this user's
        access token, so PostgREST applies the ``strategy_versions`` RLS policies
        (SELECT / INSERT / UPDATE, each scoped through ``strategies.user_id = auth.uid()``).
        On top of that, ownership of ``strategy_id`` is asserted with an explicit
        ``.eq("user_id", user["id"])`` filter before the write, and the follow-up update to
        ``strategies`` carries the same filter. A strategy owned by someone else is reported
        as not found, so existence does not leak across tenants. No raw query and no
        service-role client is introduced anywhere on this path.

        Parameters
        ----------
        graph
            A canonical version 2 ``StrategyGraph`` or version 2 wire envelope.
        registry
            Descriptor source; ``None`` uses the assembled backend registry.
        version
            Version label. Defaults to the next major after the highest existing one.
        blueprint
            The legacy ``blueprint`` column, which is ``NOT NULL`` in the existing schema
            (``003_signal_trace_restoration.sql``). Defaults to the canonical graph so
            legacy readers still get a parseable document; a caller that has a real legacy
            blueprint passes it and it is written unchanged.
        lifecycle_state
            One of ``chk_lifecycle_state``. Defaults to ``VALIDATED``, matching
            ``design.md`` -> Training workflow.
        make_current
            Point ``is_current`` and ``strategies.current_version`` at the new row.
        available_bars, feature_columns
            Passed to the validator so the warmup-feasibility warning can be evaluated.
        ml_dataset_stats
            Measured statistics of the built training dataset, for validation stage 11.
            Only :meth:`create_version_and_maybe_train` has them, and passing them is
            what makes the persisted ``validation_report`` record stage 11 as ``PASSED``
            rather than ``SKIPPED``; every other caller leaves this ``None`` and the
            report honestly says the ML readiness stage could not judge the graph.

        Returns
            ``{"version": row, "dag_hash": ..., "validation_state": ..., "lifecycle_state":
            ..., "warmup_bars": ..., "registry_version": ..., "canonical_persisted": bool,
            "registry_snapshot": str, "warnings": [...], "plan": CompiledPlan,
            "report": ValidationReport}``.
            ``canonical_persisted`` is ``False`` when migration 004 part 1 has not been
            applied yet; the caller can surface that to an operator.
            ``registry_snapshot`` is a :class:`SnapshotOutcome` value naming what happened
            to the provenance record - ``written``, ``already_recorded``, ``table_absent``
            when migration 004 part 2 is unapplied, and so on. It never affects whether the
            save succeeded.

        Raises
            ``strategy_compiler.ValidationError`` for an invalid graph, with the full
            report on ``.report`` - and nothing written.
            ``ValueError`` when ``strategy_id`` is not this user's, or when no database
            client is available. A write path never reports success without writing.
        """
        # ── 1. Compile before anything else. An invalid graph stops here, with no
        #       database client open and therefore nothing to roll back (Requirement 3.6).
        compiled: CompiledVersion = compile_version(
            graph,
            registry,
            available_bars=available_bars,
            feature_columns=feature_columns,
            ml_dataset_stats=ml_dataset_stats,
        )

        # ── 2. Build and check every column value while still doing no I/O. A value
        #       outside chk_validation_state / chk_lifecycle_state raises here rather than
        #       as a 23514 from inside the INSERT.
        canonical_columns = compiled.canonical_columns(lifecycle_state=lifecycle_state)

        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if not sb:
            # Returning {} here would report a save that never happened.
            raise ValueError(
                "No database client is available; the strategy version was not saved."
            )

        # ── 3. Ownership. RLS already scopes the write; this makes the scope explicit and
        #       keeps a cross-tenant strategy indistinguishable from a missing one.
        q_owner = (
            sb.table("strategies")
            .select("id")
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        owner_res = await q_owner if inspect.isawaitable(q_owner) else q_owner
        if not owner_res or not owner_res.data:
            raise ValueError(f"Strategy {strategy_id} not found")

        q_existing = (
            sb.table("strategy_versions")
            .select("id,version,is_current")
            .eq("strategy_id", strategy_id)
            .execute()
        )
        existing_res = await q_existing if inspect.isawaitable(q_existing) else q_existing
        existing_rows = (existing_res.data or []) if existing_res else []

        version_id = str(uuid4())
        version_str = version or _next_version_string(existing_rows)
        now = datetime.now(timezone.utc).isoformat()

        base_row = {
            "id": version_id,
            "strategy_id": strategy_id,
            "version": version_str,
            # blueprint is NOT NULL in the existing schema.
            "blueprint": blueprint if blueprint is not None else canonical_columns["graph_json"],
            "is_draft": is_draft,
            "is_current": make_current,
            "created_at": now,
        }

        # ── 4. One INSERT, degrading only for a definitively missing column.
        insert_res, persisted_row, canonical_written = await self._insert_version_row(
            sb, base_row, canonical_columns
        )

        row = (
            insert_res.data[0]
            if insert_res and getattr(insert_res, "data", None)
            else persisted_row
        )

        # ── 5. Only now that the version exists does anything else move.
        if make_current:
            q_clear = (
                sb.table("strategy_versions")
                .update({"is_current": False})
                .eq("strategy_id", strategy_id)
                .neq("id", version_id)
                .execute()
            )
            if inspect.isawaitable(q_clear):
                await q_clear

            q_current = (
                sb.table("strategies")
                .update({"current_version": version_str, "updated_at": now})
                .eq("id", strategy_id)
                .eq("user_id", user["id"])
                .execute()
            )
            if inspect.isawaitable(q_current):
                await q_current

        # ── 6. Provenance (Requirement 4.16, strategy-builder task 3.2). The row above
        #       records WHICH descriptor set the author was offered; this records WHAT that
        #       descriptor set was, keyed by the same hash. Deliberately last, deliberately
        #       best-effort: record_registry_snapshot never raises, so a missing table, an
        #       unapplied migration or a database hiccup cannot fail a save that has already
        #       succeeded. It is idempotent on registry_version, so this costs one write per
        #       descriptor set for the life of the process, not one per save.
        snapshot_outcome = await record_registry_snapshot(sb, registry)

        # ── 7. Requirement 9.8: version creation is audited with actor, timestamp and
        #       reason, through core/audit_trail.py. Last, and best effort by the same
        #       argument as the snapshot above: the version exists, and an audit sink that
        #       is unreachable must not undo a save. The record is never silently dropped -
        #       `record_audit` logs a warning naming the record it could not store.
        audit_id = await lifecycle.record_audit(
            AuditAction.VERSION_CREATED,
            actor_id=lifecycle.actor_of(user),
            resource_type="strategy_version",
            resource_id=version_id,
            reason=(
                f"Saved version {version_str} of strategy {strategy_id} "
                f"({compiled.validation_state}, {canonical_columns['lifecycle_state']})"
            ),
            before=None,
            after=canonical_columns["lifecycle_state"],
            strategy_id=strategy_id,
            version_id=version_id,
            metadata={
                "version": version_str,
                "dag_hash": compiled.dag_hash,
                "validation_state": compiled.validation_state,
                "registry_version": compiled.registry_version,
                "warmup_bars": int(compiled.plan.warmup_bars),
                "canonical_persisted": canonical_written,
                "is_draft": bool(is_draft),
                "is_current": bool(make_current),
            },
        )

        logger.info(
            "Created strategy version %s (%s) for strategy %s: dag_hash=%s, "
            "validation_state=%s, lifecycle_state=%s, warmup_bars=%d, "
            "registry_version=%s, canonical_columns_persisted=%s, registry_snapshot=%s",
            version_str,
            version_id,
            strategy_id,
            compiled.dag_hash,
            compiled.validation_state,
            canonical_columns["lifecycle_state"],
            compiled.plan.warmup_bars,
            compiled.registry_version,
            canonical_written,
            snapshot_outcome.value,
        )

        return {
            "version": row,
            "dag_hash": compiled.dag_hash,
            "validation_state": compiled.validation_state,
            "lifecycle_state": canonical_columns["lifecycle_state"],
            "warmup_bars": compiled.plan.warmup_bars,
            "registry_version": compiled.registry_version,
            "canonical_persisted": canonical_written,
            "registry_snapshot": snapshot_outcome.value,
            "audit_id": audit_id,
            "warnings": compiled.warnings,
            # In-process artifacts for the caller that just saved; the serialized forms are
            # on the row. Handing back the plan object keeps the caller off a second
            # compile, and off dict-style access to it (SB-02).
            "plan": compiled.plan,
            "report": compiled.report,
        }

    async def _insert_version_row(
        self,
        sb: Any,
        base_row: Dict[str, Any],
        canonical_columns: Dict[str, Any],
    ) -> tuple:
        """INSERT one version row, degrading to the legacy shape if the columns are absent.

        Returns ``(result, row_written, canonical_written)``.

        The canonical write is attempted whenever the cached probe says the columns exist,
        or says nothing definite. Only an error that :func:`is_missing_canonical_column_error`
        recognises triggers the single retry; every other failure propagates untouched, so a
        constraint violation, a duplicate version or a connection failure still fails the
        save loudly.

        The retry is safe because a failed INSERT is a failed statement: PostgreSQL persists
        nothing from it, so there is no partial row for the retry to duplicate or collide
        with. The row id is reused, which also means a retry cannot produce two rows.
        """
        attempt_canonical = await canonical_columns_supported(sb)

        for _ in range(2):
            row = dict(base_row)
            if attempt_canonical:
                row.update(canonical_columns)
            else:
                # The plan is the artifact the runtime and the backtester need; without
                # compiled_plan it would be lost outright, so it goes to the legacy column
                # that already exists for exactly this purpose. dag_hash, validation_state
                # and the report have nowhere to live until the migration is applied - the
                # warning from _warn_canonical_columns_absent says so explicitly.
                row["execution_graph"] = canonical_columns["compiled_plan"]

            try:
                query = sb.table("strategy_versions").insert(row).execute()
                result = await query if inspect.isawaitable(query) else query
            except Exception as exc:  # noqa: BLE001 - classified, never blanket-swallowed
                if attempt_canonical and is_missing_canonical_column_error(exc):
                    _remember_canonical_support(False)
                    _warn_canonical_columns_absent(str(exc))
                    attempt_canonical = False
                    continue
                raise

            return result, row, attempt_canonical

        # Unreachable: the loop either returns or re-raises.
        raise RuntimeError("strategy version insert did not resolve")  # pragma: no cover

    async def _owns_strategy(self, sb: Any, user: dict, strategy_id: str) -> bool:
        """Whether ``strategy_id`` is this user's, asked explicitly (Requirement 21.2).

        The same two-filter pattern :meth:`create_version`, :meth:`deploy_version` and
        :meth:`_load_owned_version` use, factored out so the version-scoped reads below
        cannot each get it slightly differently: the request-scoped client already applies
        the ``strategies`` RLS policy, and this adds the explicit ``user_id`` filter that
        Requirement 21.2 asks for on top of it.

        Why the explicit filter matters even though RLS exists: ``strategy_versions`` is
        reached by ``strategy_id`` on several paths, and a read filtered only by
        ``strategy_id`` is correct *only* while RLS is in force. There is no local
        PostgreSQL and migration ``004`` is unapplied in development environments, so a
        path that leans on the policy alone is one misconfiguration away from serving
        another tenant's graph. Asking here costs one indexed lookup.
        """
        if sb is None or not strategy_id or not (user or {}).get("id"):
            return False
        query = (
            sb.table("strategies")
            .select("id")
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        result = await _execute(query)
        return bool(result and getattr(result, "data", None))

    async def get_version_history(
        self,
        user: dict,
        strategy_id: str
    ) -> List[Dict]:
        """
        Get complete version history for a Strategy.

        Ownership is asserted before the versions are read (Requirement 21.2). Another
        tenant's strategy - and one that does not exist - both yield an **empty list**,
        the same answer a strategy with no versions gets. That is deliberately not a 404:
        this is a collection endpoint, and it is the answer
        ``list_training_jobs`` gives for the same reason - telling the three cases apart
        would confirm whether a strategy identifier the caller does not own exists
        (Requirement 21.4).

        Returns:
            List of all versions with metadata
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return []

        if not await self._owns_strategy(sb, user, strategy_id):
            return []

        q1 = (sb.table("strategy_versions")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .order("created_at", desc=True)
                 .execute())
        result = await q1 if inspect.isawaitable(q1) else q1
        
        return result.data or [] if result else []
    
    async def compare_versions(
        self,
        user: dict,
        strategy_id: str,
        version_a: str,
        version_b: str
    ) -> Dict:
        """
        Compare two strategy versions.

        Ownership is asserted before either version is read (Requirement 21.2), and a
        strategy that is not this user's raises ``ValueError`` - which the router maps to
        **404**, identically to a version that does not exist, so existence does not leak
        (Requirement 21.4). Without this the two versions were fetched by ``strategy_id``
        alone and the response described another tenant's blueprints.

        Returns:
            Comparison of blueprints and metadata
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}

        if not await self._owns_strategy(sb, user, strategy_id):
            raise ValueError("One or both versions not found")

        # Get both versions
        q1 = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_a)
                       .execute())
        version_a_res = await q1 if inspect.isawaitable(q1) else q1
        
        q2 = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_b)
                       .execute())
        version_b_res = await q2 if inspect.isawaitable(q2) else q2
        
        if not version_a_res.data or not version_b_res.data:
            raise ValueError("One or both versions not found")
        
        v_a = version_a_res.data[0]
        v_b = version_b_res.data[0]
        
        # Compare blueprints
        blueprint_a = v_a.get("blueprint", {})
        blueprint_b = v_b.get("blueprint", {})
        
        comparison = {
            "version_a": {
                "version": v_a["version"],
                "created_at": v_a["created_at"],
                "is_current": v_a["is_current"],
                "is_draft": v_a["is_draft"]
            },
            "version_b": {
                "version": v_b["version"],
                "created_at": v_b["created_at"],
                "is_current": v_b["is_current"],
                "is_draft": v_b["is_draft"]
            },
            "differences": {
                "nodes_changed": self._compare_nodes(blueprint_a, blueprint_b),
                "edges_changed": self._compare_edges(blueprint_a, blueprint_b),
                "parameters_changed": self._compare_parameters(blueprint_a, blueprint_b)
            }
        }
        
        return comparison
    
    def _compare_nodes(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare nodes between two blueprints."""
        nodes_a = {n["id"]: n for n in blueprint_a.get("nodes", [])}
        nodes_b = {n["id"]: n for n in blueprint_b.get("nodes", [])}
        
        added = [id for id in nodes_b if id not in nodes_a]
        removed = [id for id in nodes_a if id not in nodes_b]
        modified = [id for id in nodes_a if id in nodes_b and nodes_a[id] != nodes_b[id]]
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified
        }
    
    def _compare_edges(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare edges between two blueprints."""
        edges_a = set((e["source"], e["target"]) for e in blueprint_a.get("edges", []))
        edges_b = set((e["source"], e["target"]) for e in blueprint_b.get("edges", []))
        
        added = list(edges_b - edges_a)
        removed = list(edges_a - edges_b)
        
        return {
            "added": added,
            "removed": removed
        }
    
    def _compare_parameters(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare parameters between two blueprints."""
        params_a = blueprint_a.get("parameters", {})
        params_b = blueprint_b.get("parameters", {})
        
        added = {k: v for k, v in params_b.items() if k not in params_a}
        removed = {k: v for k, v in params_a.items() if k not in params_b}
        modified = {k: {"old": params_a[k], "new": params_b[k]} for k in params_a if k in params_b and params_a[k] != params_b[k]}
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified
        }
    
    async def restore_version(
        self,
        user: dict,
        strategy_id: str,
        version: str
    ) -> Dict:
        """
        Restore a previous version as current.

        Creates a new version based on the restored version.

        Ownership is asserted **before** anything is read or written (Requirement 21.2).
        This is a write path with three statements - it clears ``is_current``, inserts a
        new version row and repoints ``strategies.current_version`` - and without the
        check every one of them was filtered by ``strategy_id`` or ``id`` alone, so a
        request naming another tenant's strategy restored their version, created a row
        against it and moved their current pointer. A strategy that is not this user's
        raises ``ValueError``, which the router maps to **404** exactly as a missing
        version does, so existence does not leak (Requirement 21.4). The ``strategies``
        update below now carries the ``user_id`` filter as well, so the write is scoped
        even if this assertion is ever moved.

        Returns:
            New version record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}

        if not await self._owns_strategy(sb, user, strategy_id):
            raise ValueError(f"Version {version} not found")

        # Get version to restore
        q1 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        version_res = await q1 if inspect.isawaitable(q1) else q1
        
        if not version_res or not version_res.data:
            raise ValueError(f"Version {version} not found")
        
        restored_version = version_res.data[0]
        
        # Mark current version as not current
        q2 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        current_res = await q2 if inspect.isawaitable(q2) else q2
        
        if current_res and current_res.data:
            q3 = sb.table("strategy_versions").update({"is_current": False}).eq("id", current_res.data[0]["id"]).execute()
            if inspect.isawaitable(q3):
                await q3
        
        # Get current version number to increment
        version_parts = version.replace("v", "").split(".")
        major, minor = int(version_parts[0]), int(version_parts[1]) if len(version_parts) > 1 else 0
        minor += 1
        new_version_str = f"v{major}.{minor}"
        
        # Create new version with restored blueprint
        new_version_data = {
            "id": str(uuid4()),
            "strategy_id": strategy_id,
            "version": new_version_str,
            "blueprint": restored_version["blueprint"],
            "is_draft": True,
            "is_current": True,
            "restored_from": version,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        q4 = sb.table("strategy_versions").insert(new_version_data).execute()
        version_result = await q4 if inspect.isawaitable(q4) else q4
        
        # Update strategy current version. Tenant-scoped, like every other write on this
        # path: `id` alone would be correct only while RLS is in force.
        q5 = (
            sb.table("strategies")
            .update({"current_version": new_version_str})
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if inspect.isawaitable(q5):
            await q5
        
        logger.info(f"Restored version {version} as {new_version_str} for strategy {strategy_id}")
        
        return version_result.data[0] if version_result and version_result.data else new_version_data

    async def _load_deployable_version(
        self, sb: Any, user: dict, strategy_id: str, version: str
    ) -> Dict[str, Any]:
        """The ``strategy_versions`` row a deploy or a preflight is about. Reads only.

        Extracted from :meth:`deploy_version` unchanged so the read-only preflight
        (trading-lifecycle-integration task 8.2) resolves the version, the ownership and
        the archive refusal through **the same code** the write path does. A second copy
        here would be the one failure mode the preflight must not have: a summary that says
        ``deployable`` about a version the deploy answers 404 or 409 about.

        Ownership of the version (Requirement 13.2, first of three). Two filters, the
        convention tasks 6.3/6.5/6.6 established: the request-scoped client already
        applies the ``strategy_versions`` RLS policies, and the strategy row is re-read
        with an explicit ``user_id`` filter so the check does not rest on RLS alone.
        Another tenant's version is reported as not found, never as forbidden - a 403
        would confirm that the version exists.

        ``select("*")`` rather than ``select("id")`` so the same read also answers "is
        this strategy archived" (trading-lifecycle-integration Requirement 3.3, task 5.1)
        without a second query. Naming ``archived_at`` explicitly would raise 42703
        wherever migration 005a is unapplied; with ``*`` the key is simply absent, which
        ``strategy_archive.is_archived`` reads as "active".

        Raises
            ``ValueError`` when no such version belongs to this user.
            ``strategy_archive.ArchiveRejected`` (a ``DeployRejected``, 409) when the
            strategy is archived.
        """
        q1 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        version_res = await q1 if inspect.isawaitable(q1) else q1

        if not version_res or not version_res.data:
            raise ValueError(f"Version {version} not found")

        version_data = version_res.data[0]

        owner_q = (
            sb.table("strategies")
            .select("*")
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        owner_res = await owner_q if inspect.isawaitable(owner_q) else owner_q
        if not owner_res or not getattr(owner_res, "data", None):
            raise ValueError(f"Version {version} not found")

        # Requirement 3.3: an archived strategy's identifier is not deployable. Raised as
        # an ``ArchiveRejected`` - a ``DeployRejected`` subclass - so the deploy route's
        # existing handler maps it to its own 409 without a second mapping.
        from backend_app.backend.strategy_archive import (
            OPERATION_DEPLOY,
            assert_strategy_not_archived,
        )

        assert_strategy_not_archived(
            owner_res.data[0], operation=OPERATION_DEPLOY, strategy_id=strategy_id
        )

        return version_data

    async def preflight_version_deployment(
        self,
        user: dict,
        strategy_id: str,
        version: str,
        environment: str = "paper",
        *,
        binding_request: Any = None,
    ):
        """Every mandatory deployment condition for one version, with its own verdict.

        Trading-lifecycle-integration task 8.2, Requirements 13.3-13.6. The read-only half
        of :meth:`deploy_version`: the same version load, the same ownership assertion, the
        same archive refusal, the same Requirement 10.4 prerequisite gate and the same
        Requirement 13 gates - then ``evaluate_binding_summary``'s collect-all convention
        (task 8.1) instead of ``evaluate_binding``'s fail-fast one.

        **Nothing here writes, reserves or starts anything.** The deployment configuration
        workflow polls this while it is open (13.6), so it holds no quota, creates no
        deployment row, and touches no exchange. Not even the entitlement/quota reservation
        the write path makes: reserving on a poll would consume a user's bot allowance by
        looking at a modal.

        WHY THE PREREQUISITE GATE IS A CONDITION HERE AND AN EXCEPTION THERE
        -------------------------------------------------------------------
        Requirement 13.1 names "a valid, hash-verified compiled plan" among the mandatory
        validations, which is exactly what ``_assert_deploy_prerequisites`` checks, so 13.3
        requires its status in the summary. On the write path it stays the 409 it already
        is. Reported first, because that is the order the write path gates it in - and
        because a summary that omitted it could say ``deployable`` about a version the
        deploy refuses with ``DEPLOY_PREREQUISITE_NOT_MET``.

        Returns
            :class:`~backend_app.backend.deployment_binding.BindingSummary`. Its
            ``to_dict()`` is the response body; ``deployable`` is true only when every
            condition passed.

        Raises
            ``ValueError`` when no such version belongs to this user (the caller answers
            404, identical to a non-existent id).
            ``strategy_archive.ArchiveRejected`` (409) for an archived strategy.
        """
        from backend_app.backend import deployment_binding as db_binding

        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        if not sb:
            # "Could not be checked" is not "checked and satisfied". Every condition is
            # reported pending so the poll keeps answering and the Deploy button stays
            # disabled, rather than a 500 that the modal would have to special-case.
            return db_binding.BindingSummary(
                conditions=tuple(
                    db_binding.BindingCondition(
                        name=name,
                        status=db_binding.CONDITION_PENDING,
                        code=db_binding.CONDITION_NOT_EVALUATED,
                        reason=(
                            "the database client for this request could not be created, so "
                            "no deployment condition could be checked"
                        ),
                    )
                    for name in (
                        DEPLOY_PREREQUISITE_CONDITION,
                        *db_binding.BINDING_CONDITIONS,
                    )
                )
            )

        version_data = await self._load_deployable_version(sb, user, strategy_id, version)

        try:
            self._assert_deploy_prerequisites(version_data, strategy_id, version)
            prerequisite = db_binding.BindingCondition(
                name=DEPLOY_PREREQUISITE_CONDITION,
                status=db_binding.CONDITION_PASSED,
                detail={"validation_state": version_data.get("validation_state")},
            )
        except DeployPrerequisiteError as exc:
            prerequisite = db_binding.BindingCondition(
                name=DEPLOY_PREREQUISITE_CONDITION,
                status=db_binding.CONDITION_FAILED,
                code="DEPLOY_PREREQUISITE_NOT_MET",
                message=str(exc),
                detail={"missing_prerequisite": exc.missing_prerequisite},
                http_status=409,
            )

        request = (
            binding_request
            if isinstance(binding_request, db_binding.BindingRequest)
            else db_binding.BindingRequest.from_payload(binding_request)
        )
        if request.environment is None:
            request.environment = environment

        summary = await db_binding.evaluate_binding_summary(
            sb, user, version_data, strategy_id, version, request
        )

        return db_binding.BindingSummary(
            conditions=(prerequisite, *summary.conditions),
            # The binding the write path would create is reported only when the write path
            # would in fact create one, so a failed prerequisite cannot travel with a
            # binding that describes a deployment that is not going to happen.
            binding=summary.binding if prerequisite.passed else None,
        )

    async def deploy_version(
        self,
        user: dict,
        strategy_id: str,
        version: str,
        environment: str = "paper",
        *,
        binding_request: Any = None,
    ) -> Dict:
        """Bind one immutable version to an account, a risk config and a mode, and start it.

        This is ``design.md``'s ``PROCEDURE deploy`` and Requirement 13.1's
        Deployment_Binding: the version identifier, the exchange account identifier, the
        risk configuration identifier, the execution configuration and the mode are
        recorded as **one row**. ``backend/deployment_binding.py`` owns every gate; this
        method owns the quota, the write and the runtime start.

        ``binding_request`` is a
        :class:`~backend_app.backend.deployment_binding.BindingRequest` (or ``None`` for
        the legacy call shape, which binds no account and deploys in the mode implied by
        ``environment``). It carries no credential of any kind - an exchange account is
        named by id and its keys are resolved by the execution process
        (Requirement 13.9).

        ORDER, AND WHY (see ``deployment_binding``'s module docstring for the full
        transaction note): the version is loaded and its ownership asserted, the
        prerequisite gate runs (Requirement 10.4), then every binding gate runs -
        lifecycle ``READY``, market resolution from the plan, mode, 13.6's live-needs-an-
        account, the gap policy, the account's and risk config's ownership, and market
        compatibility - and **only then** is quota reserved and a single INSERT made. Every
        assertion is a read, so a failure at any point before the INSERT leaves nothing
        behind: no row, and no quota consumed.

        Returns
            ``{"deployment": row, "binding": <client projection>, "success": bool,
            "message": str}``. ``binding.binding_stored`` is ``False`` when migration
            ``004e_deployment_bindings.sql`` has not been applied, in which case the
            binding columns were not written and the response says so rather than
            reporting a deployment as bound.

        Raises
            ``ValueError`` when no such version belongs to this user, or on a quota or
            entitlement refusal (the pre-existing contract, unchanged).
            ``DeployPrerequisiteError`` (409) from the Requirement 10.4 gate.
            ``deployment_binding.DeployRejected`` from any Requirement 13 gate, carrying
            its own ``http_status``.
        """
        from backend_app.backend import deployment_binding as db_binding

        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}

        version_data = await self._load_deployable_version(sb, user, strategy_id, version)

        # Deploy prerequisite gate (Requirement 10.4): refused before any quota is
        # reserved and before any deployment row is created, naming the missing
        # prerequisite. Runs before ownership/quota checks below have any side effect.
        self._assert_deploy_prerequisites(version_data, strategy_id, version)

        # Every Requirement 13 gate, in order. Reads only - nothing below has been
        # written yet, so a refusal here costs no deployment row and no quota.
        request = (
            binding_request
            if isinstance(binding_request, db_binding.BindingRequest)
            else db_binding.BindingRequest.from_payload(binding_request)
        )
        if request.environment is None:
            request.environment = environment
        binding = await db_binding.evaluate_binding(
            sb, user, version_data, strategy_id, version, request
        )

        # Requirement 13.1's columns exist only once 004e is applied. Detected, never
        # assumed; a live binding that cannot be stored is refused rather than written
        # without its account reference (task 8.1's obligation).
        binding_columns = await db_binding.binding_columns_supported(sb)
        db_binding.assert_binding_storable(binding, columns_available=binding_columns)
        
        # Check quota and entitlements
        from backend_app.core.subscription_engine import SubscriptionEngine, Resource, Feature
        from backend_app.core.subscription_dependencies import get_user_plan
        plan_key = await get_user_plan(user["id"], sb)
        
        # The entitlement follows the binding's `mode`, not the legacy `environment`
        # string: `mode` is the value chk_sd_mode constrains and the value that decides
        # whether fills are real, so it is the value the live-trading entitlement has to
        # be checked against.
        if binding.mode == db_binding.MODE_LIVE or environment == "live":
            has_live = await SubscriptionEngine.check_feature_entitlement(user["id"], plan_key, Feature.LIVE_TRADING.value)
            if not has_live:
                raise ValueError("Live trading requires a paid subscription plan.")
        
        allowed, current_usage, limit = await SubscriptionEngine.reserve_quota(user["id"], plan_key, Resource.BOTS.value)
        if not allowed:
            raise ValueError(f"Quota exceeded for bots: {current_usage}/{limit}. Upgrade your plan to continue.")

        # Create deployment
        deployment_id = str(uuid4())
        deployment_data = {
            "id": deployment_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": version_data["id"],
            "version": version,
            "environment": environment,
            # The venue this deployment runs on, resolved by the binding gates from the
            # named exchange account - never from `strategies.exchange`, which SB-06
            # leaves empty (the pre-existing code assigned it from there, and *after* the
            # INSERT, so it was never persisted at all).
            #
            # WHY THIS COLUMN AND NOT NONE
            #   `001_strategy_architecture.sql` declared `exchange_id` as a UUID FK into an
            #   `exchanges` table, and this line used to write a literal `None` on the
            #   grounds that migration 003 records that table as absent in production. It
            #   does - and in the same statement it REDEFINES this column as
            #   `VARCHAR(50)` with no foreign key, i.e. as the venue name. So the column
            #   being avoided is the column the venue belongs in.
            #
            #   Writing `None` here was not inert. `signal_service._deployment_facts` reads
            #   a deployment's venue from exactly this column, `signals.exchange_id` is
            #   `VARCHAR(50) NOT NULL` (003), and Requirement 15.2 requires a Signal to be
            #   traceable to the market and venue that produced it - so with no venue on
            #   the row, `mint_signal` refused every candidate with
            #   SIGNAL_ATTRIBUTION_INCOMPLETE and a deployed strategy could never trade.
            #   Found by the Requirement 26.4 end-to-end sandbox suite
            #   (`tests/sandbox_lifecycle/`), which is the first thing to run the write and
            #   the read against the same row.
            #
            #   `None` when no exchange account was named: a binding with no account has no
            #   venue to record, and inventing one would be worse than saying so.
            "exchange_id": binding.exchange_id,
            # From the version's own DATA node, via the compiled plan. Never a
            # substituted "BTC/USDT" (SB-06, Requirement 12.4).
            "exchange_symbol": binding.symbol,
            "status": "deploying",
            "worker_region": "us-east-1",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        binding_stored = False
        if binding_columns:
            deployment_data.update(db_binding.binding_row_columns(binding))
            binding_stored = True

        try:
            q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
            deployment_result = await q2 if inspect.isawaitable(q2) else q2
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
            if binding_stored and db_binding.is_missing_binding_column_error(exc):
                # 004e landed after this process cached a positive verdict, or was only
                # partially applied. Degrade to the legacy row shape with a warning naming
                # the file - never a 500 - and re-assert that a *live* binding is refused
                # rather than written without its account reference.
                db_binding.remember_binding_columns_absent()
                db_binding.warn_binding_columns_absent(str(exc))
                db_binding.assert_binding_storable(binding, columns_available=False)
                for column in db_binding.BINDING_COLUMNS:
                    deployment_data.pop(column, None)
                binding_stored = False
                q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
                deployment_result = await q2 if inspect.isawaitable(q2) else q2
            else:
                # A genuine write failure. Release the quota this attempt reserved, then
                # let it surface: a deployment that was not recorded must not be counted.
                await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
                raise
        
        # Update strategy status
        q4 = sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).eq("user_id", user["id"]).execute()
        if inspect.isawaitable(q4):
            await q4
        
        # ── Lifecycle (task 8.3, Requirements 9.6, 9.8, 13.10) ────────────────
        # The binding row exists, so the version is DEPLOYED: `READY -> DEPLOYED`,
        # "binding created" in design.md's diagram. This is the transition that also
        # makes the version immutable - 004c's trg_sv_immutable gates on exactly
        # DEPLOYED/RUNNING/PAUSED - so it happens here, immediately after the INSERT that
        # justifies it, and never before (a refused INSERT must leave the version
        # deployable). Not strict: the gate above already asserted READY, and a version
        # whose label could not be written must not un-deploy a deployment that was
        # written.
        deploy_audit_reason = (
            f"Deployed version {version} of strategy {strategy_id} in {binding.mode} mode"
            + (
                f" on exchange account {binding.exchange_account_id}"
                if binding.exchange_account_id
                else " with no exchange account (paper)"
            )
        )
        version_transition = await lifecycle.apply_version_state(
            sb,
            version_data,
            lifecycle.LIFECYCLE_DEPLOYED,
            user=user,
            reason=deploy_audit_reason,
            action=lifecycle.ACTION_DEPLOY,
            strategy_id=strategy_id,
            strict=False,
            metadata={"deployment_id": deployment_id, "mode": binding.mode},
        )
        deploy_audit_id = await lifecycle.record_audit(
            AuditAction.DEPLOYMENT_ACTION,
            actor_id=lifecycle.actor_of(user),
            resource_type="strategy_deployment",
            resource_id=deployment_id,
            reason=deploy_audit_reason,
            before=None,
            after=lifecycle.BINDING_DEPLOYING,
            strategy_id=strategy_id,
            version_id=version_data.get("id"),
            deployment_id=deployment_id,
            metadata={
                "action": lifecycle.ACTION_DEPLOY,
                "mode": binding.mode,
                "symbol": binding.symbol,
                "timeframe": binding.timeframe,
                "exchange_account_id": binding.exchange_account_id,
                "risk_config_id": binding.risk_config_id,
                "dag_hash": binding.dag_hash,
                "binding_stored": binding_stored,
            },
        )
        binding_state = lifecycle.BINDING_DEPLOYING

        # Trigger actual deployment via FleetManager
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=binding.symbol,
                blueprint=version_data["blueprint"]
            )
            
            if success:
                q5 = sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q5):
                    await q5
                
                q6 = sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q6):
                    await q6

                binding_state = lifecycle.BINDING_RUNNING
                # `DEPLOYED -> RUNNING`, "runtime started". The row this reads is the one
                # just moved to DEPLOYED, so the state is carried forward explicitly
                # rather than re-read.
                version_transition = await lifecycle.apply_version_state(
                    sb,
                    {
                        **version_data,
                        "lifecycle_state": version_transition.to_state
                        if version_transition.moved
                        else version_data.get("lifecycle_state"),
                    },
                    lifecycle.LIFECYCLE_RUNNING,
                    user=user,
                    reason=f"Runtime started for deployment {deployment_id}",
                    action=lifecycle.ACTION_DEPLOY,
                    strategy_id=strategy_id,
                    strict=False,
                    metadata={"deployment_id": deployment_id},
                )
                await lifecycle.record_audit(
                    AuditAction.DEPLOYMENT_ACTION,
                    actor_id=lifecycle.actor_of(user),
                    resource_type="strategy_deployment",
                    resource_id=deployment_id,
                    reason=f"Runtime started for deployment {deployment_id}",
                    before=lifecycle.BINDING_DEPLOYING,
                    after=lifecycle.BINDING_RUNNING,
                    strategy_id=strategy_id,
                    version_id=version_data.get("id"),
                    deployment_id=deployment_id,
                    metadata={"action": lifecycle.ACTION_DEPLOY},
                )
            else:
                await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
                q5 = sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q5):
                    await q5
                
                q6 = sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q6):
                    await q6

                # The binding is FAILED (Requirement 13.10). The VERSION stays DEPLOYED:
                # design.md's diagram has no DEPLOYED -> STOPPED edge and no FAILED state
                # for a version at all, so nothing is written there rather than an edge
                # being invented. See strategy_lifecycle's module docstring.
                binding_state = lifecycle.BINDING_FAILED
                await lifecycle.record_audit(
                    AuditAction.DEPLOYMENT_ACTION,
                    actor_id=lifecycle.actor_of(user),
                    resource_type="strategy_deployment",
                    resource_id=deployment_id,
                    reason=f"Runtime failed to start: {message}",
                    before=lifecycle.BINDING_DEPLOYING,
                    after=lifecycle.BINDING_FAILED,
                    strategy_id=strategy_id,
                    version_id=version_data.get("id"),
                    deployment_id=deployment_id,
                    metadata={"action": lifecycle.ACTION_DEPLOY},
                )
        
        logger.info(
            "Deployed version %s of strategy %s in %s mode (binding stored: %s, "
            "binding state: %s, version lifecycle: %s)",
            version,
            strategy_id,
            binding.mode,
            binding_stored,
            binding_state,
            version_transition.to_state if version_transition.moved else "unchanged",
        )

        deployment_row = (
            deployment_result.data[0]
            if deployment_result and getattr(deployment_result, "data", None)
            else deployment_data
        )
        if isinstance(deployment_row, dict):
            deployment_row = {
                **deployment_row,
                "status": lifecycle.status_column_value(binding_state),
            }
        return {
            "deployment": deployment_row,
            "binding": db_binding.public_binding(
                binding, deployment_row, binding_stored=binding_stored
            ),
            # Requirement 13.10: the binding's state, as one of the five, alongside the
            # version's own lifecycle state and the audit id that recorded the deploy.
            "binding_state": binding_state,
            "lifecycle": version_transition.to_dict(),
            "audit_id": deploy_audit_id,
            "success": success if 'success' in locals() else True,
            "message": message if 'message' in locals() else "Deployment started"
        }

    # ══════════════════════════════════════════════════════════════════════
    #  LIFECYCLE TRANSITIONS  (strategy-builder task 8.3)
    #  Requirements 9.6, 9.7, 9.8, 13.8, 13.10, 20.9
    # ══════════════════════════════════════════════════════════════════════

    async def transition_deployment(
        self,
        user: dict,
        deployment_id: str,
        action: str,
        *,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pause, resume or stop one deployment, and move its version with it.

        Requirement 13.8's three post-deploy transitions in **one** method, because they
        differ only in their target state and doing them separately is how two of three
        end up with different ownership checks. ``deploy`` is not here: it creates the
        binding and is :meth:`deploy_version`.

        Order, and why it is this order:

        1. **Read the row, scoped to this user.** ``.eq("user_id", ...)`` on top of the
           RLS-scoped client, and a row that matches neither is reported as **not found**
           rather than forbidden - the convention tasks 6.3/8.2 established, so a
           deployment id cannot be probed for existence across tenants.
        2. **Gate the transition** (:func:`strategy_lifecycle.resolve_action`). A refusal
           is a 409 naming the current state and the actions that *are* available, and it
           costs no write and no runtime call. An action whose target the deployment is
           already in is idempotent and returns without moving anything.
        3. **Ask the runtime**, best effort. ``deployment_manager`` is an in-process
           registry, and a deployment created by :meth:`deploy_version` is not in it (that
           path starts a fleet bot), so a runtime that has never heard of this deployment
           is a **warning, not a failure**: the authoritative record is the row, and
           refusing to stop a deployment because an in-memory registry lost it would be a
           control that harms. A stop, in particular, is never blocked by it.
        4. **Write the row**, with the reason preserved on it.
        5. **Move the version** (``RUNNING -> PAUSED`` and so on), non-strict, then
           **audit** with actor, timestamp and reason (Requirement 9.8).

        Returns
            :func:`strategy_lifecycle.binding_report`'s projection plus ``action``,
            ``previous_state``, ``idempotent``, ``lifecycle`` and ``audit_id``.

        Raises
            ``ValueError`` when no such deployment belongs to this user (mapped to 404).
            :class:`strategy_lifecycle.LifecycleRejected` for a refused transition,
            carrying its own HTTP status.
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if not sb:
            raise ValueError("No database client is available; nothing was changed.")

        query = (
            sb.table("strategy_deployments")
            .select("*")
            .eq("id", str(deployment_id))
            .eq("user_id", user["id"])
            .execute()
        )
        result = await _execute(query)
        rows = (getattr(result, "data", None) or []) if result else []
        if not rows:
            raise ValueError(f"Deployment {deployment_id} not found")
        row = rows[0]

        plan = lifecycle.resolve_action(action, row)
        actor = lifecycle.actor_of(user)
        stated_reason = (reason or "").strip() or (
            f"{plan.action} requested by {actor}"
        )

        if plan.idempotent:
            logger.info(
                "Deployment %s is already %s; %s was a no-op.",
                deployment_id,
                plan.current_state,
                plan.action,
            )
            return {
                **lifecycle.binding_report(row),
                "action": plan.action,
                "previous_state": plan.current_state,
                "idempotent": True,
                "lifecycle": None,
                "audit_id": None,
                "message": f"This deployment is already {plan.current_state}.",
            }

        runtime_state = await self._ask_runtime(plan.action, str(deployment_id))

        updates: Dict[str, Any] = {
            "status": lifecycle.status_column_value(plan.target_state),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if plan.target_state == lifecycle.BINDING_STOPPED:
            updates["stopped_at"] = datetime.now(timezone.utc).isoformat()
            # Requirement 20.9's "preserve the recorded reason", on the same column a
            # guard trip writes it to, so one reader answers "why did this stop?".
            updates["error_message"] = stated_reason
        elif plan.target_state == lifecycle.BINDING_RUNNING and not row.get("started_at"):
            updates["started_at"] = datetime.now(timezone.utc).isoformat()

        write = (
            sb.table("strategy_deployments")
            .update(updates)
            .eq("id", str(deployment_id))
            .eq("user_id", user["id"])
            .execute()
        )
        await _execute(write)

        # Quota follows the runtime, not the row's history: a deployment that stops is no
        # longer consuming a bot slot. Guarded by the previous state so a repeated stop
        # cannot decrement twice (the idempotent branch above returns before this).
        if (
            plan.target_state == lifecycle.BINDING_STOPPED
            and plan.current_state == lifecycle.BINDING_RUNNING
        ):
            from backend_app.core.subscription_engine import Resource, SubscriptionEngine

            await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)

        version_row = await lifecycle.load_version_row(
            sb, user_id=user["id"], version_id=str(row.get("version_id") or "")
        )
        transition = None
        target_version_state = lifecycle.VERSION_STATE_FOR_BINDING_STATE.get(
            plan.target_state
        )
        if version_row is not None and target_version_state:
            transition = await lifecycle.apply_version_state(
                sb,
                version_row,
                target_version_state,
                user=user,
                reason=stated_reason,
                action=plan.action,
                strategy_id=row.get("strategy_id"),
                # Non-strict: the deployment has already moved, and a version label with
                # no legal edge (a DEPLOYED version that never ran, being stopped) must
                # not turn a completed action into a 409.
                strict=False,
                metadata={"deployment_id": str(deployment_id)},
            )

        audit_id = await lifecycle.record_audit(
            AuditAction.DEPLOYMENT_ACTION,
            actor_id=actor,
            resource_type="strategy_deployment",
            resource_id=str(deployment_id),
            reason=stated_reason,
            before=plan.current_state,
            after=plan.target_state,
            strategy_id=row.get("strategy_id"),
            version_id=row.get("version_id"),
            deployment_id=str(deployment_id),
            metadata={"action": plan.action, "runtime": runtime_state},
        )

        updated_row = {**row, **updates}
        logger.info(
            "Deployment %s moved %s -> %s (%s): %s",
            deployment_id,
            plan.current_state,
            plan.target_state,
            plan.action,
            stated_reason,
        )
        return {
            **lifecycle.binding_report(updated_row),
            "action": plan.action,
            "previous_state": plan.current_state,
            "idempotent": False,
            "runtime": runtime_state,
            "lifecycle": transition.to_dict() if transition is not None else None,
            "audit_id": audit_id,
            "message": f"Deployment {plan.target_state.lower()}.",
        }

    async def _ask_runtime(self, action: str, deployment_id: str) -> Dict[str, Any]:
        """Tell the in-process runtime about a transition. Never fails the transition.

        ``deployment_manager`` holds deployments started through *its own* deploy path in
        a per-process dict and raises ``ValueError`` for anything else - including every
        deployment :meth:`deploy_version` creates, which starts a fleet bot instead. So
        the outcome is reported, not enforced: the row is the authoritative record of what
        a deployment is supposed to be doing, and a stop that the runtime cannot confirm
        must still be recorded as a stop.
        """
        from backend_app.backend.deployment_manager import get_deployment_manager

        try:
            manager = get_deployment_manager()
            method = {
                lifecycle.ACTION_PAUSE: "pause_deployment",
                lifecycle.ACTION_RESUME: "resume_deployment",
                lifecycle.ACTION_STOP: "stop_deployment",
            }[action]
            state = await getattr(manager, method)(deployment_id)
            return {"acknowledged": True, "status": str(getattr(state, "status", "") or "")}
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning(
                "The in-process runtime did not acknowledge %s for deployment %s (%s). "
                "The deployment row is authoritative and the transition is recorded.",
                action,
                deployment_id,
                exc,
            )
            return {"acknowledged": False, "detail": str(exc)}

    async def stop_deployments_for_guard_trip(
        self,
        user: dict,
        reason: str,
        *,
        triggered_by: str = lifecycle.TRIGGER_GUARD,
        strategy_id: Optional[str] = None,
        deployment_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Requirement 20.9: a guard trip moves this user's live deployments to ``STOPPED``.

        The Deployment_Service entry point named by the requirement. The reason is
        preserved on every row it touches and recorded again in the audit trail with the
        actor and the timestamp; nothing is raised, so a trip cannot be swallowed by one
        deployment that would not write.
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        return await lifecycle.stop_deployments_for_reason(
            sb,
            user_id=user["id"],
            reason=reason,
            triggered_by=triggered_by,
            strategy_id=strategy_id,
            deployment_ids=deployment_ids,
            user=user,
        )

    async def stop_deployments_on_kill_switch(
        self,
        user: dict,
        *,
        strategy_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Requirement 20.9's kill-switch half, read from ``core/global_safety.py``.

        Stops nothing unless the switch itself says it is active, and writes the reason
        the switch recorded rather than one composed here.
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        return await lifecycle.stop_deployments_on_kill_switch(
            sb, user_id=user["id"], strategy_id=strategy_id, user=user
        )
    
    # DEPRECATED: Marketplace operations moved to library.py router
    # Use /api/library/* endpoints instead
    
    async def delete_strategy(self, user: dict, strategy_id: str) -> bool:
        """
        Delete Strategy and all associated data.

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Stop all running deployments first
        await self.stop_all_deployments(user, strategy_id)

        # Delete strategy (cascade delete handled by database)
        if sb:
            q1 = sb.table("strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()
            if inspect.isawaitable(q1):
                await q1

        logger.info(f"Deleted strategy {strategy_id} for user {user['id']}")
        return True
    
    async def clone_strategy(
        self,
        user: dict,
        strategy_id: str,
        new_name: str,
        *,
        registry: Any = None,
    ) -> Dict:
        """Clone a Strategy: recompile the source graph through the canonical compiler.

        SB-02, fixed here. This path used to hand the raw ``blueprint`` straight to
        ``create_strategy``, which does a plain INSERT with no compilation step at
        all - no ``compile_version``, no ``dag_hash``, no ``compiled_plan``, no
        ``validation_state``. A clone made this way could never carry a real identity
        hash, and :meth:`create_version`'s canonical write path (task 2.3) was simply
        never reached. This mirrors the fix already landed in
        ``routers/strategies.py``'s clone endpoint (Requirement 3.2's "same validity
        verdict" invariant): the clone is recompiled through the single compiler and
        ``plan.dag_hash`` is read as a FIELD, never ``.get(...)``.

        Requirement 10.2 - a clone is ALWAYS persisted, never rejected outright. A
        source graph that fails to recompile is written anyway, marked
        ``validation_state = 'INVALID'`` with the full structured report attached, and
        the failure is returned in the response ``warnings[]``. Only a payload that is
        not a readable graph at all (``GraphParseError`` / ``CompilerError``) is
        refused, because there is nothing to persist as an invalid-but-real DAG in that
        case - typed exception handling throughout, no bare ``except Exception``
        swallowing the failure the way SB-02 did.

        Returns
            ``{"strategy": row, "version": row, "warnings": [...]}``. ``warnings`` is
            non-empty exactly when the recompile failed and the version was persisted
            ``INVALID``.

        Raises
            ``ValueError`` when ``strategy_id`` is not this user's.
            ``strategy_compiler.CompilerError`` / ``strategy_dag.schema.GraphParseError``
            when the source carries no readable graph at all - nothing is persisted.
        """
        from backend_app.backend.strategy_compiler import CompilerError
        from backend_app.backend.strategy_compiler import ValidationError as CompileValidationError
        from backend_app.backend.strategy_dag.schema import GraphParseError, load_graph

        # Get original strategy. Ownership is enforced by get_strategy's own
        # `.eq("user_id", user["id"])` filter - unchanged here.
        original = await self.get_strategy(user, strategy_id)
        if not original:
            raise ValueError(f"Strategy {strategy_id} not found")

        orig_strategy = original["strategy"] or {}
        orig_version = original["version"] or {}
        blueprint = orig_version.get("blueprint") or {}

        # Read the source graph the way every other version consumer does: the
        # canonical `graph_json` first, falling back to the legacy `blueprint` shape
        # (`load_graph` / `extract_raw_graph` handle both a version 1 {"nodes",
        # "edges"} payload and a version 2 envelope). A payload that carries no
        # readable graph at all is refused here, before anything is written - there is
        # no graph to persist as INVALID in that case.
        graph_source = orig_version.get("graph_json") or blueprint
        try:
            graph = load_graph(graph_source)
        except GraphParseError as exc:
            raise CompilerError(
                f"Clone source for strategy {strategy_id} carries no readable graph: {exc}"
            ) from exc

        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if not sb:
            raise ValueError(
                "No database client is available; the strategy clone was not saved."
            )

        # ── 1. Create the new strategy shell. No version row yet - create_version (or
        #       the INVALID fallback below) writes that next, so a failed recompile
        #       never leaves a strategy with a version claiming a hash it doesn't have.
        new_strategy_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        strategy_data = {
            "id": new_strategy_id,
            "user_id": user["id"],
            "name": new_name,
            "description": f"Cloned from {orig_strategy.get('name', 'strategy')}",
            "exchange": orig_strategy.get("exchange"),
            "symbol": orig_strategy.get("symbol"),
            "timeframe": orig_strategy.get("timeframe"),
            "tags": orig_strategy.get("tags") or [],
            "status": StrategyStatus.DRAFT.value,
            "environment": orig_strategy.get("environment") or StrategyEnvironment.PAPER.value,
            "current_version": None,
            "created_at": now,
            "updated_at": now,
        }
        q1 = sb.table("strategies").insert(strategy_data).execute()
        strategy_res = await q1 if inspect.isawaitable(q1) else q1
        new_strategy_row = (
            strategy_res.data[0] if strategy_res and strategy_res.data else strategy_data
        )

        warnings: List[Dict[str, Any]] = []

        try:
            # ── 2. Recompile through the single compiler and persist exactly the way
            #       create_version persists any other save (Requirement 3.2). On
            #       success this writes dag_hash, compiled_plan and
            #       validation_state='VALID' from the compiled plan/report.
            result = await self.create_version(
                user,
                new_strategy_id,
                graph,
                registry=registry,
                blueprint=blueprint,
                make_current=True,
                is_draft=True,
            )
        except CompileValidationError as exc:
            # Requirement 10.2/10.3: the recompile failed, but this is an existing,
            # already-saved strategy being cloned - it is persisted anyway, marked
            # INVALID and un-deployable, with the structured report attached and the
            # failure surfaced in the response warnings[]. This is a typed exception,
            # read for its report, not a bare `except Exception` swallowing an
            # AttributeError the way SB-02 did.
            report = exc.report
            detail: Dict[str, Any] = {
                "error": "STRATEGY_GRAPH_INVALID",
                "message": str(exc),
                "errors": list(exc.errors),
                "warnings": list(exc.warnings),
                "codes": list(exc.codes()),
            }
            if report is not None:
                detail["report"] = report.to_dict()
            logger.info(
                "[StrategyService] Clone of strategy %s recompiled INVALID: %s",
                strategy_id,
                detail["codes"],
            )
            warnings.append(detail)

            version_row = await self._persist_invalid_clone_version(
                sb, user, new_strategy_id, graph, report, blueprint
            )
            return {
                "strategy": new_strategy_row,
                "version": version_row,
                "warnings": warnings,
            }

        logger.info(f"Cloned strategy {strategy_id} as {new_strategy_id} for user {user['id']}")
        return {
            "strategy": new_strategy_row,
            "version": result["version"],
            "warnings": warnings,
        }

    async def _persist_invalid_clone_version(
        self,
        sb: Any,
        user: dict,
        strategy_id: str,
        graph: Any,
        report: Any,
        blueprint: Optional[dict],
    ) -> Dict[str, Any]:
        """Persist a clone whose recompile failed validation, marked INVALID.

        Requirement 10.2: the version row still carries ``graph_json`` and the full
        ``validation_report``, with ``dag_hash`` and ``compiled_plan`` explicitly
        ``NULL`` - never merely absent, so a reader cannot mistake "never compiled" for
        "compiled and INVALID". This never conflicts with ``chk_valid_requires_hash``
        (Phase 4, task 4.1): that constraint only binds when
        ``validation_state = 'VALID'``, and this row's state is ``INVALID``.
        """
        canonical_graph = report.canonical_graph if report is not None else graph
        graph_json = (
            canonical_graph.to_dict() if hasattr(canonical_graph, "to_dict") else canonical_graph
        )

        canonical_columns = {
            "graph_json": graph_json,
            "compiled_plan": None,
            "dag_hash": None,
            "schema_version": int(getattr(canonical_graph, "schema_version", 2) or 2),
            "compiler_version": None,
            "validation_state": "INVALID",
            "validation_report": report.to_dict() if report is not None else None,
            "lifecycle_state": "DRAFT",
            "warmup_bars": None,
            "registry_version": getattr(report, "registry_version", None),
        }

        version_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        base_row = {
            "id": version_id,
            "strategy_id": strategy_id,
            "version": "v1.0",
            # blueprint is NOT NULL in the existing schema.
            "blueprint": blueprint if blueprint is not None else graph_json,
            "is_draft": True,
            "is_current": True,
            "created_at": now,
        }

        insert_res, persisted_row, _canonical_written = await self._insert_version_row(
            sb, base_row, canonical_columns
        )
        row = (
            insert_res.data[0]
            if insert_res and getattr(insert_res, "data", None)
            else persisted_row
        )

        # There is no other version for this brand-new strategy to point at, so it
        # still becomes "current" - Requirement 10.4's deploy gate, not is_current, is
        # what keeps an INVALID clone un-deployable.
        q_current = (
            sb.table("strategies")
            .update({"current_version": base_row["version"], "updated_at": now})
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if inspect.isawaitable(q_current):
            await q_current

        return row
    
    async def deploy_strategy(
        self,
        user: dict,
        strategy_id: str,
        version: Optional[str] = None,
        environment: str = "paper",
        exchange_id: Optional[str] = None,
        exchange_account_id: Optional[str] = None,
        capital: Optional[float] = None,
        trade_size_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        **kwargs: Any
    ) -> Dict:
        """
        Deploy a Strategy (creates running bot instance).

        Returns:
            Deployment record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Get strategy
        strategy = await self.get_strategy(user, strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")
        
        # SECURITY: Validate ML/DL strategies have trained models before deployment
        strategy_data = strategy.get("strategy", {})
        nodes = []
        if isinstance(strategy_data.get("buy_logic"), dict):
            nodes = strategy_data["buy_logic"].get("_nodes", [])
        
        # Check for ML/DL nodes
        ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]
        
        if ml_nodes and not strategy_data.get("ml_model_path"):
            raise ValueError(
                "Strategy contains ML/DL nodes but no trained model reference. "
                "Train the model via POST /api/strategies/train-ml before deployment."
            )
        
        # Get specific version or current
        if version and sb:
            q1 = (sb.table("strategy_versions")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .eq("version", version)
                          .execute())
            version_res = await q1 if inspect.isawaitable(q1) else q1
            version_data = version_res.data[0] if version_res and version_res.data else None
        else:
            version_data = strategy["version"]
        
        if not version_data:
            raise ValueError(f"Version {version} not found")

        # Deploy prerequisite gate (Requirement 10.4): refused before any deployment row
        # is created or fleet call is made, naming the missing prerequisite.
        self._assert_deploy_prerequisites(
            version_data, strategy_id, version_data.get("version", version)
        )

        # ── mode, on every deployment this codebase creates ──────────────────────
        # Task 8.1's capitalised note: chk_sd_live_needs_account guards `mode`, NOT the
        # legacy `environment` column, and this writer historically set only
        # `environment`. A deployment created as environment='live' with no account was
        # therefore stored as mode='paper' and the database-level Requirement 13.6
        # guarantee covered nothing. `mode` is now written here too, and 13.6 is enforced
        # in this handler so a refusal is a 4xx naming the missing account rather than a
        # 23514 surfacing as a 500.
        #
        # This is the strategy-level legacy deploy surface, not the versioned binding
        # surface task 8.2 extends, so it gains `mode` and 13.6 and nothing else - no
        # lifecycle gate, no market compatibility gate. Those belong to
        # `deploy_version`, which is the route `design.md` names.
        from backend_app.backend import deployment_binding as db_binding

        # Create deployment
        deployment_id = str(uuid4())
        resolved_exchange_id = exchange_account_id or exchange_id
        is_uuid = False
        if resolved_exchange_id:
            try:
                UUID(str(resolved_exchange_id))
                is_uuid = True
            except (ValueError, AttributeError):
                is_uuid = False

        account_reference = str(exchange_account_id) if exchange_account_id else None
        mode = db_binding.normalise_mode(kwargs.get("mode"), environment=environment)
        db_binding.assert_account_required_for_live(mode, account_reference)

        deployment_data = {
            "id": deployment_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": version_data["id"],
            "version": version_data["version"],
            "environment": environment,
            "exchange_id": str(resolved_exchange_id) if is_uuid else None,
            "exchange_symbol": strategy.get("strategy", {}).get("symbol", strategy.get("strategy", {}).get("pair", "BTCUSDT")),
            "status": "deploying",
            "worker_region": "us-east-1",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        binding_stored = False
        if sb and await db_binding.binding_columns_supported(sb):
            deployment_data["mode"] = mode
            deployment_data["exchange_account_id"] = account_reference
            deployment_data["dag_hash"] = version_data.get("dag_hash")
            binding_stored = True
        elif mode == db_binding.MODE_LIVE:
            # No `mode` column, so no chk_sd_live_needs_account and nowhere for the
            # account reference to live. Refused rather than started as an unrecorded
            # live deployment.
            db_binding.assert_binding_storable(
                db_binding.DeploymentBinding(
                    strategy_id=strategy_id,
                    version_id=str(version_data["id"]),
                    version=str(version_data.get("version") or ""),
                    user_id=str(user["id"]),
                    mode=mode,
                    exchange_account_id=account_reference,
                    risk_config_id=None,
                    execution_config={},
                    dag_hash=version_data.get("dag_hash"),
                ),
                columns_available=False,
            )
        
        if sb:
            try:
                q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
                deployment_result = await q2 if inspect.isawaitable(q2) else q2
            except Exception as exc:  # noqa: BLE001 - classified, never swallowed
                if not (binding_stored and db_binding.is_missing_binding_column_error(exc)):
                    raise
                # 004e landed after this process cached a positive verdict. Degrade with a
                # warning naming the file rather than failing the deploy with a 500, and
                # refuse a live deployment whose binding cannot be recorded.
                db_binding.remember_binding_columns_absent()
                db_binding.warn_binding_columns_absent(str(exc))
                if mode == db_binding.MODE_LIVE:
                    raise db_binding.DeployRejected(
                        "BINDING_NOT_STORABLE",
                        "A live deployment binding cannot be recorded: "
                        "strategy_deployments is missing the binding columns "
                        f"({', '.join(db_binding.BINDING_COLUMNS)}). Apply "
                        f"{db_binding.DEPLOYMENT_BINDING_MIGRATION} first.",
                        {
                            "migration": db_binding.DEPLOYMENT_BINDING_MIGRATION,
                            "missing_columns": list(db_binding.BINDING_COLUMNS),
                            "mode": mode,
                        },
                        http_status=503,
                    ) from exc
                for column in db_binding.BINDING_COLUMNS:
                    deployment_data.pop(column, None)
                binding_stored = False
                q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
                deployment_result = await q2 if inspect.isawaitable(q2) else q2
            
            # Update strategy status
            q3 = sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).execute()
            if inspect.isawaitable(q3):
                await q3
        
        # Trigger actual deployment via FleetManager
        # This will start the bot instance
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=strategy["strategy"]["symbol"],
                blueprint=version_data["blueprint"]
            )
            
            if success and sb:
                # Update deployment status to running
                q4 = sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q4):
                    await q4
                
                # Update strategy status
                q5 = sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q5):
                    await q5
            elif sb:
                # Deployment failed
                q4 = sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q4):
                    await q4
                
                q5 = sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q5):
                    await q5
        
        logger.info(
            "Deployed strategy %s as deployment %s in %s mode (mode recorded: %s)",
            strategy_id,
            deployment_id,
            mode,
            binding_stored,
        )

        return {
            "deployment": deployment_result.data[0] if 'deployment_result' in locals() and deployment_result and deployment_result.data else deployment_data,
            "mode": mode,
            # Never reported as bound when the binding columns were not written
            # (task 8.1's obligation on this task).
            "binding_stored": binding_stored,
            "binding_migration": None if binding_stored else db_binding.DEPLOYMENT_BINDING_MIGRATION,
            "success": success if 'success' in locals() else True,
            "message": message if 'message' in locals() else "Deployment started"
        }
    
    async def stop_deployment(
        self,
        user: dict,
        deployment_id: str
    ) -> bool:
        """
        Stop a running Strategy deployment.

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return False

        # Get deployment
        q1 = sb.table("strategy_deployments").select("*").eq("id", deployment_id).eq("user_id", user["id"]).execute()
        deployment_res = await q1 if inspect.isawaitable(q1) else q1
        if not deployment_res or not deployment_res.data:
            return False

        deployment = deployment_res.data[0]

        # Stop via FleetManager
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.stop_bot(
                user_id=user["id"],
                symbol=deployment["exchange_id"]
            )
        
        # Update deployment status
        q2 = sb.table("strategy_deployments").update({
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", deployment_id).execute()
        if inspect.isawaitable(q2):
            await q2
        
        # Decrement quota if it was running
        if deployment.get("status") == "running":
            from backend_app.core.subscription_engine import SubscriptionEngine, Resource
            await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
        
        # Check if strategy has other running deployments
        q3 = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", deployment["strategy_id"])
                        .eq("status", "running")
                        .execute())
        running_res = await q3 if inspect.isawaitable(q3) else q3
        
        if running_res and not running_res.data:
            # No more running deployments, update strategy status
            q4 = sb.table("strategies").update({"status": StrategyStatus.STOPPED.value}).eq("id", deployment["strategy_id"]).execute()
            if inspect.isawaitable(q4):
                await q4
        
        logger.info(f"Stopped deployment {deployment_id}")
        return True
    
    async def stop_all_deployments(self, user: dict, strategy_id: str) -> int:
        """
        Stop all running deployments for a Strategy.

        Returns:
            Number of deployments stopped
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return 0

        # Get all running deployments
        q1 = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", strategy_id)
                        .eq("status", "running")
                        .execute())
        running_res = await q1 if inspect.isawaitable(q1) else q1

        stopped_count = 0
        for deployment in (running_res.data if running_res else []) or []:
            if await self.stop_deployment(user, deployment["id"]):
                stopped_count += 1

        return stopped_count
    
    async def pause_strategy(self, user: dict, strategy_id: str) -> bool:
        """
        Pause a running Strategy (stops all deployments).

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Stop all deployments
        await self.stop_all_deployments(user, strategy_id)
        
        # Update strategy status
        if sb:
            q1 = sb.table("strategies").update({"status": StrategyStatus.PAUSED.value}).eq("id", strategy_id).execute()
            if inspect.isawaitable(q1):
                await q1
        
        logger.info(f"Paused strategy {strategy_id}")
        return True
    
    async def resume_strategy(self, user: dict, strategy_id: str) -> Dict:
        """
        Resume a paused Strategy (redeploys with current version).

        Returns:
            Deployment record
        """
        # Deploy again with current version
        return await self.deploy_strategy(user, strategy_id)
    
    async def get_strategy_metrics(
        self,
        user: dict,
        strategy_id: str
    ) -> Dict:
        """
        Get comprehensive Strategy metrics.

        Returns:
            All performance metrics from backend
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Get strategy
        strategy = await self.get_strategy(user, strategy_id)
        if not strategy:
            return {}

        # Get performance from Telemetry
        performance = await self._get_strategy_performance(user, strategy_id)
        
        # Get deployment metrics
        deployments = []
        if sb:
            q1 = (sb.table("strategy_deployments")
                           .select("*")
                           .eq("strategy_id", strategy_id)
                           .execute())
            deployments_res = await q1 if inspect.isawaitable(q1) else q1
            deployments = deployments_res.data if deployments_res and deployments_res.data else []
        
        # Calculate backend metrics
        total_deployments = len(deployments)
        running_deployments = len([d for d in deployments if d.get("status") == "running"])
        
        return {
            "strategy_id": strategy_id,
            "status": strategy["strategy"]["status"],
            "environment": strategy["strategy"]["environment"],
            "current_version": strategy["strategy"]["current_version"],
            "performance": performance,
            "deployments": {
                "total": total_deployments,
                "running": running_deployments,
                "stopped": total_deployments - running_deployments
            },
            "health": self._calculate_strategy_health(performance, running_deployments)
        }
    
    def _calculate_strategy_health(self, performance: Dict, running_deployments: int) -> str:
        """
        Calculate Strategy health based on metrics.
        
        Returns:
            Health status
        """
        if running_deployments == 0:
            return StrategyHealth.OFFLINE.value
        
        pnl = float(performance.get("today_pnl", 0))
        if pnl < -1000:  # Significant loss
            return StrategyHealth.ERROR.value
        elif pnl < -100:
            return StrategyHealth.WARNING.value
        
        return StrategyHealth.HEALTHY.value

    # ══════════════════════════════════════════════════════════════════════
    #  TRAINING (strategy-builder task 6.3)
    #
    #  Three entry points, one admission path. The order and the guarantees are
    #  documented at the TRAINING PIPELINE banner at the bottom of this module;
    #  what follows is the persistence half.
    # ══════════════════════════════════════════════════════════════════════

    async def create_version_and_maybe_train(
        self,
        user: dict,
        strategy_id: str,
        graph: Any,
        *,
        registry: Any = None,
        training_cfg: Optional[Mapping[str, Any]] = None,
        version: Optional[str] = None,
        blueprint: Optional[dict] = None,
        make_current: bool = True,
        is_draft: bool = True,
    ) -> Dict[str, Any]:
        """The single save path: validate, compile, persist, and maybe queue training.

        ``design.md`` -> Training workflow, in its order:

        1. compile (raises ``ValidationError`` for an invalid graph, persisting nothing)
        2. fetch and grade the dataset
        3. run the feature pipeline and check its schema
        4. gate every ML node on measured data
        5. create the immutable version - **before** training, so a model binds to a
           fixed graph
        6. admit against the caps
        7. insert one ``QUEUED`` job per model node, set the version ``TRAINING``,
           enqueue, announce, and **return**

        Requirement 15.1 / 15.12 in one sentence: this coroutine never trains. It
        returns ``training: QUEUED`` with the job id, and the work happens in the
        worker (task 6.4). Nothing below awaits a model, a library import or an epoch.

        Requirement 14.10: a graph with no model node is saved ``READY`` and reports
        ``training: NOT_REQUIRED``. Not "skipped", not an empty job - the version is
        deployable and the response says training was not needed.

        Requirements 14.7 / 14.8 / 14.3 / 14.4: on any blocked path the version is
        still saved (it is a valid, compiled graph) and **no ``training_jobs`` row is
        created**. The response carries ``training: BLOCKED`` with the structured
        reason - the quality report, the failing feature issues, the gate's required
        versus available figures, or the exceeded cap's requested versus permitted
        values. A training-side refusal is deliberately NOT an HTTP error on this
        endpoint: the save succeeded, and conflating the two would throw away a valid
        version because a data range was short.

        Authorization is unchanged and enforced twice, exactly as
        :meth:`create_version` documents: a request-scoped RLS client plus an explicit
        ownership filter. The ML entitlement and quota controls
        (``require_ml_training`` / ``check_ml_quota``) are applied here for the
        training branch, so a plan without ML training gets a saved version and a
        blocked training half rather than a queued job.

        Returns
            :meth:`create_version`'s payload plus:
            ``training`` - ``{"state": ..., "required": bool, ...}``, and
            ``jobs`` - the created (or already-live) job rows, ``[]`` when none.
        """
        from backend_app.backend.ml_training_policy import JobCounts

        # ── 1. Compile once, before any I/O. An invalid graph raises here and no
        #       database client has been opened (Requirement 3.6).
        probe: CompiledVersion = compile_version(graph, registry)
        plan = probe.plan

        # ── 2. Requirement 14.10: no model node, nothing to train, version READY.
        if not plan.ml_nodes:
            saved = await self.create_version(
                user,
                strategy_id,
                graph,
                registry=registry,
                version=version,
                blueprint=blueprint,
                lifecycle_state=LIFECYCLE_READY,
                make_current=make_current,
                is_draft=is_draft,
            )
            saved["training"] = {
                "state": TRAINING_NOT_REQUIRED,
                "required": False,
                "job_id": None,
                "jobs": [],
                "message": "This strategy declares no model block, so no training is "
                           "required.",
            }
            saved["jobs"] = []
            return saved

        # ── 3. The database client, needed for the live job counts the concurrency
        #       cap reads and for both writes below.
        sb = await self._get_supabase(user)

        entitlement_block: Optional[TrainingBlocked] = None
        try:
            await self._assert_ml_training_entitled(user, sb)
        except TrainingBlocked as blocked:
            entitlement_block = blocked

        if entitlement_block is not None:
            preparation = TrainingPreparation(required=True, blocked=entitlement_block)
        else:
            job_counts = (
                await count_training_jobs(sb, user["id"])
                if sb is not None
                else JobCounts.unavailable(
                    "No database client is available, so the concurrency caps were not "
                    "applied."
                )
            )
            # ── 4. Steps 2, 3, 4 and 6 of the workflow. Writes nothing.
            preparation = await prepare_training(
                plan,
                user=user,
                registry=registry,
                training_cfg=training_cfg,
                job_counts=job_counts,
            )

        # ── 5. The immutable version. Created before training either way, because it
        #       is what a model binds to, and because a blocked training half does not
        #       make the graph invalid.
        saved = await self.create_version(
            user,
            strategy_id,
            graph,
            registry=registry,
            version=version,
            blueprint=blueprint,
            lifecycle_state=LIFECYCLE_VALIDATED,
            make_current=make_current,
            is_draft=is_draft,
            # Only reached when the gate passed, so stage 11 records PASSED on a
            # measurement rather than SKIPPED on an absence.
            ml_dataset_stats=(
                preparation.stats_by_node() if preparation.ready else None
            ),
        )
        version_row = saved.get("version") or {}
        version_id = str(version_row.get("id") or "")

        if preparation.blocked is not None:
            # No job row exists on this path, and none was attempted.
            saved["training"] = {
                "state": TRAINING_BLOCKED,
                "required": True,
                "job_id": None,
                "jobs": [],
                **preparation.to_dict(),
            }
            saved["jobs"] = []
            logger.info(
                "Version %s saved; training blocked (%s) and no training job was "
                "created.",
                version_id,
                preparation.blocked.reason,
            )
            return saved

        # ── 6/7. One QUEUED job per model node, then hand off and return.
        outcome = await self._insert_training_jobs(
            sb,
            user=user,
            strategy_id=strategy_id,
            version_id=version_id,
            preparation=preparation,
        )
        if outcome["state"] == TRAINING_QUEUED:
            await self._set_version_lifecycle(sb, version_id, LIFECYCLE_TRAINING)
        saved["training"] = outcome
        saved["jobs"] = outcome["jobs"]
        return saved

    async def _assert_ml_training_entitled(self, user: dict, sb: Any) -> None:
        """Apply the existing ML entitlement and quota controls to a training branch.

        Requirement 16.7: the caps task 6.2 added are **additive** to
        ``require_ml_training`` and ``check_ml_quota``, not a replacement. Those two
        are FastAPI dependencies, and a dependency cannot be conditional - but the
        save path only trains for a graph that declares a model node, and gating every
        save behind them would refuse a FREE user's indicator-only strategy. So the
        same two functions are called here, on the branch that actually consumes ML
        capacity, and neither is reimplemented or relaxed.

        Raises
            :class:`TrainingBlocked` (``CAP_EXCEEDED``) carrying the 403 the dependency
            would have raised, so the save still succeeds and only the training half is
            refused.
        """
        from fastapi import HTTPException

        from backend_app.core.subscription_dependencies import (
            require_feature,
            require_quota,
        )
        from backend_app.core.subscription_engine import Feature, Resource

        try:
            await require_feature(Feature.ML_TRAINING.value, user, sb)
            await require_quota(Resource.ML_TRAININGS.value, user, sb)
        except HTTPException as exc:
            raise TrainingBlocked(
                REASON_CAP_EXCEEDED,
                str(exc.detail),
                {"status_code": exc.status_code, "control": "entitlement"},
            ) from exc

    async def _insert_training_jobs(
        self,
        sb: Any,
        *,
        user: dict,
        strategy_id: str,
        version_id: str,
        preparation: "TrainingPreparation",
    ) -> Dict[str, Any]:
        """Insert one job per prepared ML node. Idempotent per ``(version, node)``.

        Returns the ``training`` sub-document for the response: ``state``, the first
        ``job_id`` (the common single-model case), every job row, and any warning.
        """
        if sb is None or not version_id:
            reason = (
                "No database client is available, so no training job could be recorded."
                if sb is None
                else "The saved version has no id, so no training job could be bound to "
                     "it."
            )
            logger.warning("Training was admitted but not queued: %s", reason)
            return {
                "state": TRAINING_UNAVAILABLE,
                "required": True,
                "job_id": None,
                "jobs": [],
                "reason": reason,
                "warnings": list(preparation.warnings),
            }

        jobs: List[Dict[str, Any]] = []
        idempotent: List[str] = []
        warnings: List[str] = list(preparation.warnings)
        queue_positions: Dict[str, Any] = {}

        for node in preparation.nodes:
            existing = await find_live_training_job(sb, version_id, node.node_id)
            if existing is not None:
                jobs.append(existing)
                idempotent.append(node.node_id)
                continue

            result = await insert_training_job(
                sb,
                node.job_row(
                    user_id=user["id"],
                    strategy_id=strategy_id,
                    version_id=version_id,
                ),
            )
            if not result["available"]:
                warnings.append(result["reason"])
                return {
                    "state": TRAINING_UNAVAILABLE,
                    "required": True,
                    "job_id": None,
                    "jobs": [],
                    "reason": result["reason"],
                    "warnings": list(dict.fromkeys(warnings)),
                }
            job = result["job"] or {}
            jobs.append(job)
            if result["idempotent"]:
                idempotent.append(node.node_id)
            else:
                job_id = str(job.get("id") or "")
                if job_id:
                    # Requirement 24.2's counts by status. Recorded only for a job this
                    # call actually created, for the same reason the audit record is: an
                    # idempotent hit created nothing, and counting one would inflate the
                    # QUEUED tally past the number of rows that exist.
                    _collector = _metrics()
                    if _collector is not None:
                        _collector.record_training_job_status("QUEUED")
                    await enqueue_training_job(job_id, user["id"])
                    await publish_training_event(
                        user["id"],
                        "training.queued",
                        {
                            "job_id": job_id,
                            "version_id": version_id,
                            "node_id": node.node_id,
                            "block_id": node.block_id,
                            "epochs_total": int(node.request.epochs),
                            "status": "QUEUED",
                        },
                    )
                    # Requirement 9.8: training job creation is audited. Only for a job
                    # this call actually created - an idempotent hit created nothing, and
                    # recording one would put a second "created" against a job that has
                    # existed since an earlier request.
                    await lifecycle.record_audit(
                        AuditAction.TRAINING_JOB_CREATED,
                        actor_id=lifecycle.actor_of(user),
                        resource_type="training_job",
                        resource_id=job_id,
                        reason=(
                            f"Queued training for node {node.node_id} ({node.block_id}) "
                            f"of version {version_id}"
                        ),
                        before=None,
                        after="QUEUED",
                        strategy_id=strategy_id,
                        version_id=version_id,
                        metadata={
                            "node_id": node.node_id,
                            "block_id": node.block_id,
                            "epochs_total": int(node.request.epochs),
                            "dataset_fingerprint": preparation.fingerprint,
                        },
                    )
            if node.admission.deferred:
                queue_positions[node.node_id] = node.admission.queue_position

        first_id = next((str(job.get("id")) for job in jobs if job.get("id")), None)
        return {
            "state": TRAINING_QUEUED,
            "required": True,
            "job_id": first_id,
            "jobs": jobs,
            "idempotent_nodes": idempotent,
            "queue_positions": queue_positions,
            "dataset_fingerprint": preparation.fingerprint,
            "market": preparation.market,
            "warnings": list(dict.fromkeys(warnings)),
        }

    async def _set_version_lifecycle(
        self, sb: Any, version_id: str, lifecycle_state: str
    ) -> bool:
        """Move one version's ``lifecycle_state``. Never fails a save that succeeded.

        The immutability trigger (``trg_sv_immutable``, migration 004 part 3) guards
        ``graph_json``, ``compiled_plan``, ``dag_hash`` and ``schema_version`` and only
        for a version already ``DEPLOYED``, ``RUNNING`` or ``PAUSED``. This update
        touches none of those columns, so it is permitted by construction rather than
        by exception.

        Degrades when migration 004 part 1 is unapplied: ``lifecycle_state`` does not
        exist, the column probe already said so and warned naming the file, and a
        second failure here would add nothing.
        """
        if sb is None or not version_id:
            return False
        if not await canonical_columns_supported(sb):
            return False
        try:
            query = (
                sb.table("strategy_versions")
                .update(
                    {
                        "lifecycle_state": lifecycle_state,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", version_id)
                .execute()
            )
            await _execute(query)
            return True
        except Exception as exc:  # noqa: BLE001 - the version and the job both exist
            logger.warning(
                "Version %s was saved and its training job queued, but its "
                "lifecycle_state could not be moved to %s: %s",
                version_id,
                lifecycle_state,
                exc,
            )
            return False

    async def create_training_job(
        self,
        user: dict,
        version_id: str,
        *,
        node_id: Optional[str] = None,
        registry: Any = None,
        training_cfg: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Queue training for an existing immutable version. Idempotent per (version, node).

        The explicit counterpart to :meth:`create_version_and_maybe_train`: the version
        already exists, so nothing is compiled into a new row and the graph is read
        back from ``graph_json``.

        Ownership is enforced before anything is read: the version is fetched through
        the request-scoped RLS client joined to ``strategies`` filtered on
        ``user_id``, so another tenant's version is reported as **not found** rather
        than refused - existence does not leak across tenants.

        ``node_id`` selects one model node; omitted, every model node in the graph is
        queued. A node the graph does not carry, or one that is not a model node, is a
        ``ValueError`` naming it.

        Raises
            ``ValueError`` when the version is not this user's, carries no readable
            graph, or names an unknown node.
            :class:`TrainingBlocked` when an admission gate refuses - and then **no
            ``training_jobs`` row exists**.
        """
        from backend_app.backend.strategy_dag.schema import load_graph

        sb = await self._get_supabase(user)
        if not sb:
            raise ValueError(
                "No database client is available; no training job was created."
            )

        row = await self._load_owned_version(sb, user, version_id)
        strategy_id = str(row.get("strategy_id") or "")
        payload = row.get("graph_json") or row.get("blueprint")
        if not payload:
            raise ValueError(
                f"Version {version_id} carries no readable graph, so nothing can be "
                f"compiled for training."
            )

        await self._assert_ml_training_entitled(user, sb)

        graph = load_graph(payload)
        compiled: CompiledVersion = compile_version(graph, registry)
        plan = compiled.plan

        if not plan.ml_nodes:
            return {
                "state": TRAINING_NOT_REQUIRED,
                "required": False,
                "version_id": version_id,
                "job_id": None,
                "jobs": [],
                "message": "This version declares no model block, so no training is "
                           "required.",
            }
        if node_id is not None and node_id not in plan.ml_nodes:
            raise ValueError(
                f"Version {version_id} has no model node {node_id!r}; its model nodes "
                f"are {list(plan.ml_nodes)}."
            )

        # ── Idempotency, checked FIRST. Two reasons, both load-bearing:
        #
        #    1. A repeat of this request - a double-click, or a retry after a dropped
        #       connection - must not spend a candle fetch, a feature pipeline run and
        #       a dataset build to arrive at "you already have one".
        #    2. The per-user concurrency cap counts jobs in QUEUED or RUNNING. Without
        #       this short circuit, the second request would be refused by the cap it
        #       had itself satisfied a moment earlier - the live job for exactly the
        #       (version, node) being asked about. Refusing a retry with
        #       MAX_CONCURRENT_USER would be a correct-looking answer to the wrong
        #       question.
        #
        # This is a convenience, NOT the guarantee. Between this read and the insert
        # another request can land, and only ``uq_tj_active_per_node`` closes that
        # window - see :func:`insert_training_job`.
        targets = tuple(plan.ml_nodes) if node_id is None else (node_id,)
        existing_jobs: List[Dict[str, Any]] = []
        for target in targets:
            live = await find_live_training_job(sb, version_id, target)
            if live is not None:
                existing_jobs.append(live)
        if existing_jobs and len(existing_jobs) == len(targets):
            first = existing_jobs[0]
            return {
                "state": TRAINING_QUEUED,
                "required": True,
                "version_id": version_id,
                "strategy_id": strategy_id,
                "job_id": str(first.get("id")) if first.get("id") else None,
                "jobs": existing_jobs,
                "idempotent_nodes": list(targets),
                "queue_positions": {},
                "warnings": [],
            }

        job_counts = await count_training_jobs(sb, user["id"])
        preparation = await prepare_training(
            plan,
            user=user,
            registry=registry,
            training_cfg=training_cfg,
            job_counts=job_counts,
        )
        if preparation.blocked is not None:
            # Raised, not returned: this endpoint's only job is to create the job, so
            # a refusal is the answer to the request rather than a footnote on a save.
            raise preparation.blocked

        selected = (
            preparation
            if node_id is None
            else TrainingPreparation(
                required=True,
                nodes=tuple(n for n in preparation.nodes if n.node_id == node_id),
                quality_report=preparation.quality_report,
                market=preparation.market,
                fingerprint=preparation.fingerprint,
                warnings=preparation.warnings,
            )
        )

        outcome = await self._insert_training_jobs(
            sb,
            user=user,
            strategy_id=strategy_id,
            version_id=version_id,
            preparation=selected,
        )
        if outcome["state"] == TRAINING_QUEUED:
            await self._set_version_lifecycle(sb, version_id, LIFECYCLE_TRAINING)
        outcome["version_id"] = version_id
        outcome["strategy_id"] = strategy_id
        return outcome

    async def _load_owned_version(
        self, sb: Any, user: dict, version_id: str
    ) -> Dict[str, Any]:
        """One ``strategy_versions`` row this user owns, or ``ValueError``.

        Two filters, deliberately: the request-scoped client applies the
        ``strategy_versions`` RLS policies (scoped through
        ``strategies.user_id = auth.uid()``), and the strategy row is then re-read with
        an explicit ``.eq("user_id", ...)``. A version belonging to another tenant is
        indistinguishable from one that does not exist.
        """
        query = (
            sb.table("strategy_versions")
            .select("*")
            .eq("id", version_id)
            .limit(1)
            .execute()
        )
        result = await _execute(query)
        rows = (getattr(result, "data", None) or []) if result else []
        if not rows:
            raise ValueError(f"Strategy version {version_id} not found")
        row = rows[0]

        owner_query = (
            sb.table("strategies")
            .select("id")
            .eq("id", row.get("strategy_id"))
            .eq("user_id", user["id"])
            .execute()
        )
        owner = await _execute(owner_query)
        if not owner or not getattr(owner, "data", None):
            raise ValueError(f"Strategy version {version_id} not found")
        return row

    async def request_job_cancellation(self, user: dict, job_id: str) -> Dict[str, Any]:
        """Set ``cancel_requested`` on one of this user's live training jobs.

        Cooperative by design (Requirement 15.7, and ``design.md``'s edge-case table):
        this sets the flag and **does not** set ``CANCELLED``. The worker honours it at
        the next epoch boundary, sets the status itself and binds no model version.
        Flipping the status here would mark a job cancelled while a process was still
        writing epochs against it, and the row would then disagree with reality.

        ``updated_at`` is written explicitly, because migration 004d deliberately
        attaches no ``BEFORE UPDATE`` trigger to ``training_jobs``.

        Ownership: the update carries ``.eq("user_id", user["id"])`` on top of the
        RLS-scoped client's ``tj_owner_update`` policy, and a job that matches neither
        is reported as not found. A user cannot cancel another user's job, and cannot
        learn that it exists.

        Raises
            ``ValueError`` when no such job belongs to this user.
            ``DeployPrerequisiteError`` when the job is not in a cancellable state,
            naming the current one - the same 409-mapped type the deploy gate uses for
            a state conflict.
        """
        sb = await self._get_supabase(user)
        if not sb:
            raise ValueError("No database client is available; nothing was cancelled.")

        try:
            query = (
                sb.table(TRAINING_JOBS_TABLE)
                .select("*")
                .eq("id", job_id)
                .eq("user_id", user["id"])
                .limit(1)
                .execute()
            )
            result = await _execute(query)
        except Exception as exc:  # noqa: BLE001
            if is_missing_training_table_error(exc):
                logger.warning(
                    "Cancellation requested for job %s but %s does not exist. Apply "
                    "%s. Detail: %s",
                    job_id,
                    TRAINING_JOBS_TABLE,
                    TRAINING_MIGRATION,
                    exc,
                )
                raise ValueError(f"Training job {job_id} not found") from exc
            raise

        rows = (getattr(result, "data", None) or []) if result else []
        if not rows:
            raise ValueError(f"Training job {job_id} not found")
        job = rows[0]

        status_now = str(job.get("status") or "")
        if job.get("cancel_requested"):
            # Already asked for. Idempotent, and it says so rather than pretending a
            # second request did something.
            return {
                "job_id": job_id,
                "status": status_now,
                "cancel_requested": True,
                "already_requested": True,
                "cancellable": status_now in TRAINING_JOB_LIVE_STATES,
            }
        if status_now not in TRAINING_JOB_LIVE_STATES:
            raise DeployPrerequisiteError(
                f"Training job {job_id} is {status_now}, so there is nothing to "
                f"cancel; only a QUEUED or RUNNING job is cancellable.",
                missing_prerequisite="status",
            )

        update = (
            sb.table(TRAINING_JOBS_TABLE)
            .update(
                {
                    "cancel_requested": True,
                    # 004d attaches no BEFORE UPDATE trigger to this table, so every
                    # writer sets this column itself.
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", job_id)
            .eq("user_id", user["id"])
            .in_("status", list(TRAINING_JOB_LIVE_STATES))
            .execute()
        )
        updated = await _execute(update)
        error_text = _result_error_text(updated)
        if error_text:
            raise RuntimeError(f"cancel_requested update failed: {error_text}")
        if not (getattr(updated, "data", None) or []):
            # The ``.in_(status, live)`` filter matched nothing, which means the job
            # finished between the read above and this write. Reporting success would
            # tell the author a running job will stop when it already has not.
            raise DeployPrerequisiteError(
                f"Training job {job_id} finished before the cancellation could be "
                f"recorded, so there is nothing to cancel.",
                missing_prerequisite="status",
            )

        await publish_training_event(
            user["id"],
            "training.cancel_requested",
            {"job_id": job_id, "status": status_now, "cancel_requested": True},
        )
        # Requirement 9.8: the cancellation request is audited with actor, timestamp and
        # reason. What is recorded is what happened - `cancel_requested` was set - not
        # "cancelled", because the worker is what ends the job (Requirement 15.7).
        audit_id = await lifecycle.record_audit(
            AuditAction.TRAINING_JOB_CANCEL_REQUESTED,
            actor_id=lifecycle.actor_of(user),
            resource_type="training_job",
            resource_id=job_id,
            reason=(
                f"Cancellation requested for training job {job_id} while {status_now}; "
                "the worker stops at the next epoch boundary."
            ),
            before=status_now,
            after=status_now,
            strategy_id=job.get("strategy_id"),
            version_id=job.get("version_id"),
            metadata={
                "cancel_requested": True,
                "node_id": job.get("node_id"),
            },
        )
        logger.info(
            "cancel_requested set on training job %s (status %s); the worker stops at "
            "the next epoch boundary.",
            job_id,
            status_now,
        )
        return {
            "job_id": job_id,
            "status": status_now,
            "cancel_requested": True,
            "already_requested": False,
            "cancellable": True,
            "audit_id": audit_id,
        }


# Singleton instance
_strategy_service = None

async def get_strategy_service() -> StrategyService:
    """Get singleton StrategyService instance."""
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service


# ══════════════════════════════════════════════════════════════════════════
#  TRAINING PIPELINE: ADMISSION, JOB CREATION, COOPERATIVE CANCEL
#  (strategy-builder task 6.3 — Requirements 12.6, 14.7, 14.8, 14.10,
#   15.1, 15.12, 15.13, 15.14; design.md § Training workflow)
#
#  WHAT THIS SECTION OWNS, AND WHAT IT DELIBERATELY DOES NOT
#  ---------------------------------------------------------
#  It owns the ORDER of the training admission path and the two writes at the
#  end of it. Every decision inside that order is made by a component that
#  already existed and is called, never re-implemented:
#
#    * the graph verdict            -> strategy_builder.compile_version
#                                      (-> strategy_dag.validator, compiler)
#    * the candle window            -> connection_engine + data_seeking_engine
#    * data quality                 -> market_data_validation.MarketDataValidator
#    * the features                 -> dag_engine.DAGEngine over the plan,
#                                      i.e. the executors that trade
#    * the feature schema check     -> feature_validator.FeatureValidator
#    * the (X, y) pair and splits   -> ml_dataset.build_supervised_dataset /
#                                      splits_for_dataset
#    * the minimum-data gate        -> ml_training_policy.check_ml_data_requirements
#    * the resource caps            -> ml_training_policy.resolve_caps / enforce_caps
#    * the immutable version row    -> StrategyService.create_version
#
#  There is no second gate, no second cap table and no second data-quality
#  rule anywhere below.
#
#  THREE GUARANTEES THAT ARE STRUCTURAL, NOT PROMISED
#  --------------------------------------------------
#  1. NO JOB ROW ON ANY BLOCKED PATH (Requirements 14.7, 14.8, 14.3, 14.4).
#     Enforced by ORDER, not by a rollback: every block returns a
#     ``TrainingPreparation`` with ``blocked`` set, and the single
#     ``training_jobs`` INSERT lives after that value has been inspected. On a
#     blocked path the insert is never reached, so there is nothing to clean
#     up and no window in which a half-admitted job exists.
#
#  2. IDEMPOTENT PER (version, node) (Requirement 15.13). Enforced by the
#     DATABASE, not by a read-then-write check. ``uq_tj_active_per_node`` is a
#     partial unique index over ``(version_id, node_id) WHERE status IN
#     ('QUEUED','RUNNING')``. A double-click therefore has its second INSERT
#     rejected by PostgreSQL, and :func:`insert_training_job` translates that
#     rejection into the existing live job. A read-then-write pre-check would
#     race: both requests would read "no live job" and both would insert.
#     The pre-check below exists only to save a round trip on the common
#     sequential case; correctness comes from the index, and the unique
#     violation is a normal outcome here rather than a 500.
#
#  3. NO EXCHANGE IDENTIFIER OR CREDENTIAL IN ``config`` (Requirement 12.6,
#     SB-06). The configuration records the RESOLVED DATA SOURCE - the DATA
#     node's own id, block id, symbol and timeframe - and never a venue and
#     never a key. :func:`assert_no_exchange_identity` walks the assembled
#     payload against ``schema.FORBIDDEN_PARAM_FIELDS`` before it is written,
#     so a future field that smuggles one in fails here rather than in
#     task 8.7's Property 23 scan of the persisted column.
#
#  WHAT DEGRADES, AND HOW LOUDLY
#  -----------------------------
#  ``training_jobs`` is created by 004d_training_and_models.sql, which is
#  applied BY HAND (.github/workflows/03-deploy.yml has no migration step) -
#  the same ordering hazard the canonical-column probe at the top of this file
#  documents for ``strategy_versions``. So a missing table is DETECTED and
#  degrades to ``training: UNAVAILABLE`` with a warning naming that file. It
#  is never a 500, and it never reports a job id that does not exist.
# ══════════════════════════════════════════════════════════════════════════

#: The table this section writes. One name, so a typo cannot make the probe and
#: the insert disagree about which relation is being talked about.
TRAINING_JOBS_TABLE = "training_jobs"

#: The migration that creates it, named in every degradation warning so an
#: operator is never left guessing which file to apply.
TRAINING_MIGRATION = "backend_app/migrations/004d_training_and_models.sql"

#: ``training_jobs.status`` — ``chk_tj_status``, verbatim.
TRAINING_JOB_STATES: frozenset = frozenset(
    {"QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
)

#: The two states ``uq_tj_active_per_node`` scopes its uniqueness to. A job in one
#: of these is LIVE: it holds the (version, node) slot and is cancellable.
TRAINING_JOB_LIVE_STATES: Tuple[str, ...] = ("QUEUED", "RUNNING")

#: ``training`` in a save response. Five states, all of them a fact rather than a
#: hope:
#:   NOT_REQUIRED — the graph declares no ML node (Requirement 14.10)
#:   QUEUED       — a row exists in QUEUED and its id is on the wire (15.1, 15.12)
#:   BLOCKED      — an admission gate refused; NO job row was created (14.7, 14.8)
#:   UNAVAILABLE  — 004d is unapplied, so no job could be recorded at all
TRAINING_NOT_REQUIRED = "NOT_REQUIRED"
TRAINING_QUEUED = "QUEUED"
TRAINING_BLOCKED = "BLOCKED"
TRAINING_UNAVAILABLE = "UNAVAILABLE"

#: Block reasons. ``design.md``'s ``blocked("DATA_QUALITY", ...)`` /
#: ``blocked("FEATURES", ...)`` / ``blocked("ML_REQUIREMENTS", ...)`` verbatim,
#: plus the three the pseudocode reaches by raising rather than returning.
REASON_DATA_SOURCE = "DATA_SOURCE_UNRESOLVED"
REASON_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
REASON_DATA_QUALITY = "DATA_QUALITY"
REASON_FEATURES = "FEATURES"
REASON_DATASET = "DATASET"
REASON_ML_REQUIREMENTS = "ML_REQUIREMENTS"
REASON_MODEL_UNPUBLISHED = "MODEL_UNPUBLISHED"
REASON_CAP_EXCEEDED = "CAP_EXCEEDED"

#: Requirement 24.2's "insufficient-data block counts": the block reasons that mean *there
#: was not enough usable data*, as opposed to the other things a gate can refuse for.
#:
#:   ML_REQUIREMENTS  the row-count and feature-count gates (Requirements 14.3, 14.4) -
#:                    the dataset was built and measured and came up short.
#:   DATASET          the window a model needs exceeds what one training fetch can read
#:                    (``TRAINING_MAX_BARS``), which is the same shortfall one step earlier.
#:
#: Deliberately **excluded**, so the omissions are decisions rather than oversights:
#: ``DATA_QUALITY`` (Requirement 14.7) is a *grading* refusal - the bars arrived and were
#: judged POOR or UNUSABLE, which is not a shortage, and it is already visible through
#: ``market_data.quality_score``. ``DATA_UNAVAILABLE`` and ``DATA_SOURCE_UNRESOLVED`` are
#: feed and wiring faults. ``MODEL_UNPUBLISHED`` is a registry fact. ``CAP_EXCEEDED`` has
#: its own metric. Folding any of them in would make this counter unusable as the answer to
#: "how often does an author not have enough history?".
INSUFFICIENT_DATA_BLOCK_REASONS: Tuple[str, ...] = (
    REASON_ML_REQUIREMENTS,
    REASON_DATASET,
)


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.2. Guarded because a metrics failure must never turn a *save* into a
    500: the version is valid and persisted whether or not the training half was counted.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _record_training_block(reason: Any) -> None:
    """Count one admission block, if it is an insufficient-data one (Requirement 24.2)."""
    label = str(reason or "")
    if label not in INSUFFICIENT_DATA_BLOCK_REASONS:
        return
    collector = _metrics()
    if collector is not None:
        collector.record_training_blocked_insufficient_data(label)


def _record_training_cap_rejection(detail: Any) -> None:
    """Count one cap rejection under the cap ``CapExceeded`` named (Requirement 24.2)."""
    collector = _metrics()
    if collector is not None:
        collector.record_training_cap_rejection(
            str(dict(detail or {}).get("cap") or "unnamed")
        )


#: Requirement 14.7: these two measured quality levels block training.
#: ``DataQualityLevel`` member NAMES, because ``DataQualityLevel.POOR.value`` is
#: the integer 50 and comparing against that would read as a score threshold this
#: module had invented.
BLOCKING_QUALITY_LEVELS: Tuple[str, ...] = ("POOR", "UNUSABLE")

#: Label defaults, used only when the training configuration names none. Every one
#: of them is recorded in ``config`` so a run is reproducible from the row alone
#: (Requirement 15.14) rather than from whatever this constant happens to be later.
DEFAULT_LABEL_HORIZON = 1
DEFAULT_LABEL_MODE = "classification"
DEFAULT_LABEL_THRESHOLD = 0.001
DEFAULT_TRAINING_SEED = 42

#: The ceiling on a single training fetch. A model that needs more history than
#: this cannot be trained from this path, and says so, rather than paginating an
#: exchange indefinitely inside an HTTP request.
TRAINING_MAX_BARS = 50_000

#: Slack over the computed minimum, so a range that is *exactly* the required row
#: count does not fail the gate because one candle was missing from the feed.
TRAINING_BAR_HEADROOM = 1.15

#: Best-effort wake-up queue for the worker (task 6.4). NOT the source of truth:
#: see :func:`enqueue_training_job`.
TRAINING_QUEUE_NAME = "training:jobs"

#: PostgreSQL / PostgREST codes that mean "this relation does not exist".
_MISSING_TABLE_CODES = ("42p01", "pgrst205", "undefined_table")

#: Codes that mean "a unique index rejected this row".
_UNIQUE_VIOLATION_CODES = ("23505", "unique_violation", "duplicate key")


class TrainingBlocked(Exception):
    """An admission gate refused, and therefore **no training job was created**.

    Carries the structured reason the requirement asks to be returned - the data
    quality report, the failing feature issues, or the gate's required-versus-
    available figures - rather than a message a UI has to parse.

    Deliberately not an ``HTTPException``: this is raised inside the service layer,
    which the worker (task 6.4) and the tests also call. The router maps it.
    """

    def __init__(self, reason: str, message: str, detail: Optional[Dict[str, Any]] = None):
        self.reason = str(reason)
        self.detail: Dict[str, Any] = dict(detail or {})
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocked": True,
            "reason": self.reason,
            "message": str(self),
            "detail": self.detail,
        }


def is_missing_training_table_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says ``training_jobs`` does not exist.

    Deliberately narrow, for the same reason
    :func:`is_missing_canonical_column_error` is: anything this returns ``False``
    for propagates, because a save that fails loudly beats a job that silently was
    never created.
    """
    text = str(exc).lower()
    if not text:
        return False
    if any(code in text for code in _MISSING_TABLE_CODES):
        return True
    if TRAINING_JOBS_TABLE not in text:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown table")
    )


def is_duplicate_active_job_error(exc: BaseException) -> bool:
    """True when ``exc`` is ``uq_tj_active_per_node`` rejecting a second live job.

    This is the mechanism behind "idempotent per (version, node)", so the rejection
    is an expected outcome on this path rather than a fault. Recognising it by the
    index name as well as by the SQLSTATE means a client library that reformats the
    message still lands here.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "uq_tj_active_per_node" in text:
        return True
    return any(code in text for code in _UNIQUE_VIOLATION_CODES)


def _result_error_text(result: Any) -> str:
    """The error a PostgREST client reported on the response rather than by raising."""
    error = getattr(result, "error", None)
    return "" if error is None else str(error)


async def _execute(query: Any) -> Any:
    """Await ``query`` when the client is async, return it when it is not.

    The same shape every method in this file uses; extracted so the training path
    does not repeat ``await q if inspect.isawaitable(q) else q`` a dozen times.
    """
    return await query if inspect.isawaitable(query) else query


# --------------------------------------------------------------------------
# 1. The resolved data source (Requirement 12.6, SB-06)
# --------------------------------------------------------------------------


def scan_plan_markets(plan: Any) -> Tuple[List[str], List[str], Any]:
    """``(symbols, timeframes, data_node)`` declared by ``plan``'s own DATA nodes.

    The single scan behind both the training data source and the node preview's
    ``market``, so those two cannot disagree about which market a graph reads.
    Order-preserving and de-duplicated, so a caller can both test for "exactly one"
    and report the set it found.

    ``data_node`` is the first DATA node declaring **both** a symbol and a
    timeframe, which is the node a training configuration attributes its data
    source to. It is ``None`` when no node declares both.
    """
    symbols: List[str] = []
    timeframes: List[str] = []
    data_node: Any = None
    for node_id in getattr(plan, "data_nodes", ()) or ():
        node = plan.node(node_id)
        if node is None:  # pragma: no cover - the plan indexes every node it orders
            continue
        symbol = node.params.get("symbol")
        timeframe = node.params.get("timeframe")
        has_symbol = isinstance(symbol, str) and symbol.strip()
        has_timeframe = isinstance(timeframe, str) and timeframe.strip()
        if has_symbol and symbol.strip() not in symbols:
            symbols.append(symbol.strip())
        if has_timeframe and timeframe.strip() not in timeframes:
            timeframes.append(timeframe.strip())
        if has_symbol and has_timeframe and data_node is None:
            data_node = node
    return symbols, timeframes, data_node


def resolve_training_data_source(plan: Any) -> Dict[str, Any]:
    """The data source a training job reads, resolved from the graph and nothing else.

    Requirement 12.6 says the Training_Service resolves its data source from the
    configuration recorded with the job; this is what gets recorded. It is the
    market - the DATA node's ``symbol`` and ``timeframe`` - and explicitly **not** a
    venue: a strategy carries no exchange identity at all (Requirement 12.1), so
    there is no venue in the graph to record, and inventing one here is defect SB-06
    in a new location. Which feed the platform reads those candles from is a server
    setting consulted at fetch time (see :func:`fetch_training_bars`) and is never
    written to the row.

    Raises
        :class:`TrainingBlocked` (``DATA_SOURCE_UNRESOLVED``) when the graph declares
        no market, or more than one. Two markets have no single bar series to train
        one model over, and choosing one on the author's behalf is the substitution
        Requirement 12.4 exists to delete.
    """
    symbols, timeframes, data_node = scan_plan_markets(plan)

    if not symbols or not timeframes:
        raise TrainingBlocked(
            REASON_DATA_SOURCE,
            "This graph's Market Data block declares no symbol and timeframe, so "
            "there is no market to fetch a training dataset for.",
            {
                "symbols": symbols,
                "timeframes": timeframes,
                "fix_hint": "Set the symbol and timeframe on the Market Data block.",
            },
        )
    if len(symbols) > 1 or len(timeframes) > 1:
        raise TrainingBlocked(
            REASON_DATA_SOURCE,
            "This graph reads more than one market. A model is trained over one bar "
            "series, so training it would mean choosing a feed on the author's behalf.",
            {"symbols": symbols, "timeframes": timeframes},
        )

    return {
        "node_id": None if data_node is None else data_node.id,
        "block_id": None if data_node is None else data_node.block_id,
        "symbol": symbols[0],
        "timeframe": timeframes[0],
    }


def assert_no_exchange_identity(payload: Any, *, path: str = "config") -> None:
    """Refuse a payload carrying exchange identity or credential material.

    Walks mappings and sequences, matching every key against
    ``schema.FORBIDDEN_PARAM_FIELDS`` case-insensitively - the same vocabulary the
    canonical parse strips from node params, so the two cannot drift.

    Raises
        ``RuntimeError``. This is a **programming** error, not a client error: the
        configuration is assembled by this module from the plan, so a forbidden key
        appearing in it means a field was added here that should not have been. It
        fails at assembly, before the write, rather than in task 8.7's Property 23
        scan of a column that already holds it.
    """
    from backend_app.backend.strategy_dag.schema import is_forbidden_param

    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if is_forbidden_param(key):
                raise RuntimeError(
                    f"{path}.{key} names exchange identity or credential material; a "
                    f"training configuration holds neither (Requirement 12.6, SB-06)"
                )
            assert_no_exchange_identity(value, path=f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for position, value in enumerate(payload):
            assert_no_exchange_identity(value, path=f"{path}[{position}]")


# --------------------------------------------------------------------------
# 2. The candle window (the one I/O boundary of the admission path)
# --------------------------------------------------------------------------


def training_bar_budget(
    *, warmup_bars: int, required_rows: int, label_horizon: int, requested: Optional[int] = None
) -> Dict[str, Any]:
    """How many bars must be fetched, and an account of how that figure was reached.

    ``needed = warmup + label_horizon + required_rows``, then headroom, then the
    ceiling. ``required_rows`` comes from
    ``ml_training_policy.required_row_count`` - the gate's own arithmetic - so the
    fetch cannot be sized by a second rule that disagrees with the gate that will
    judge it.

    Postconditions
        ``bars <= TRAINING_MAX_BARS`` for every input, including a hostile one.
        ``exceeds_ceiling`` is true exactly when the honest minimum does not fit,
        and the caller then refuses rather than fetching a window the gate will
        reject with a message about row counts.
    """
    warmup = max(0, int(warmup_bars))
    horizon = max(1, int(label_horizon))
    minimum = warmup + horizon + max(0, int(required_rows))
    with_headroom = int(minimum * TRAINING_BAR_HEADROOM) + 1

    asked: Optional[int] = None
    if requested is not None:
        try:
            asked = int(requested)
        except (TypeError, ValueError):
            asked = None
        if asked is not None and asked <= 0:
            asked = None

    bars = with_headroom if asked is None else max(asked, with_headroom)
    bars = min(TRAINING_MAX_BARS, bars)
    return {
        "requested": asked,
        "bars": bars,
        "minimum_bars": minimum,
        "target_bars": with_headroom,
        "max_bars": TRAINING_MAX_BARS,
        "warmup_bars": warmup,
        "label_horizon": horizon,
        "required_rows": max(0, int(required_rows)),
        "exceeds_ceiling": with_headroom > TRAINING_MAX_BARS,
        "clamped": bars < with_headroom or (asked is not None and asked != bars),
    }


async def fetch_training_bars(symbol: str, timeframe: str, bars: int) -> List[List[Any]]:
    """``bars`` most recent OHLCV rows for ``symbol`` at ``timeframe``.

    The platform's existing public market-data path, unchanged and REUSED AS-IS:
    ``ConnectionEngine`` opens an unauthenticated connection to the feed named by
    the server's ``DEFAULT_EXCHANGE``, and ``data_seeking_engine.DataEngine``
    paginates it. Training reads a public window; it neither touches a user's vault
    nor accepts a venue from the request (SB-06), which is why no venue reaches
    ``training_jobs.config``.

    Seam note: this is the one I/O boundary of the admission path, and it is a
    module-level function so a test can supply a deterministic window without
    replacing the compiler, the feature pipeline, the gate or the caps - the four
    things an admission test is actually about. It mirrors
    ``routers/strategy_operations._fetch_preview_bars``, which is the same
    resolution for the preview path; the two are separate because their failure
    contracts differ (a blocked training run versus a refused preview).

    Raises
        :class:`TrainingBlocked` (``DATA_UNAVAILABLE``). The venue is never named to
        the caller and never appears in a message the caller sees.
    """
    import os

    from backend_app.backend.connection_engine import ConnectionEngine
    from backend_app.backend.data_seeking_engine import DataEngine

    venue = os.getenv("DEFAULT_EXCHANGE")
    if not venue:
        logger.error("Training unavailable: DEFAULT_EXCHANGE is not configured")
        raise TrainingBlocked(
            REASON_DATA_UNAVAILABLE,
            "The platform market data feed is not configured, so no training window "
            "can be read.",
            {"configured": False},
        )

    try:
        engine = DataEngine(await ConnectionEngine(exchange_id=venue).connect())
        return await engine.fetch_historical_ohlcv(symbol, timeframe, limit=int(bars))
    except TrainingBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 - reported without naming the venue
        logger.warning(
            "Training market data fetch failed for %s %s: %s", symbol, timeframe, exc
        )
        raise TrainingBlocked(
            REASON_DATA_UNAVAILABLE,
            f"The platform market data feed could not supply a training window for "
            f"{symbol} at {timeframe}.",
            {"symbol": symbol, "timeframe": timeframe},
        ) from exc


def training_frame(rows: Any, bars: int) -> Any:
    """CCXT OHLCV rows as the frame the executors compute against.

    The five columns the executors read, indexed by bar timestamp, de-duplicated,
    sorted and tail-limited. An empty window raises rather than producing a dataset
    of zero rows that would then be reported as "insufficient data" - the feed
    returning nothing and the history being short are different facts.
    """
    import pandas as pd

    frame = pd.DataFrame(
        list(rows or []),
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    if frame.empty:
        raise TrainingBlocked(
            REASON_DATA_UNAVAILABLE,
            "The market data feed returned no bars for this symbol and timeframe, so "
            "there is no dataset to train on.",
            {"rows": 0},
        )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms")
    frame = frame.set_index("timestamp").sort_index()
    # FIRST WINS (Requirement 19.4, task 7.5). Was ``keep="last"``, which resolved a
    # duplicated bar in favour of the later arrival - a silent revision of a bar the
    # pipeline may already have trained on, and the opposite of what design.md pins.
    frame = frame[~frame.index.duplicated(keep="first")]
    return frame.tail(int(bars))


def dataset_fingerprint(frame: Any, symbol: str, timeframe: str) -> str:
    """A reproducibility fingerprint of the exact window that was trained on.

    Requirement 15.14. Covers the market, the row count, the bar timestamps and
    every OHLCV value, so a refetch that returns a revised candle - an exchange
    correcting a bad print - produces a different fingerprint and the worker's
    ``expect_fingerprint`` check (task 6.4) catches it instead of training on
    silently different data.

    Deliberately NOT a hash of the first and last timestamp: two windows can share
    both endpoints and differ in the middle, which is exactly the case a
    reproducibility check has to catch.
    """
    import hashlib

    import numpy as np

    digest = hashlib.sha256()
    digest.update(f"{symbol}|{timeframe}|{int(len(frame.index))}|".encode("utf-8"))

    index_values = np.asarray(frame.index.to_numpy())
    if index_values.dtype.kind == "M":
        index_values = index_values.astype("datetime64[ns]").astype("int64")
    digest.update(np.ascontiguousarray(index_values).tobytes())

    for column in ("open", "high", "low", "close", "volume"):
        if column in frame.columns:
            digest.update(column.encode("utf-8"))
            digest.update(
                np.ascontiguousarray(frame[column].to_numpy(dtype=float)).tobytes()
            )
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 3. Data quality (Requirement 14.7)
# --------------------------------------------------------------------------


async def quality_check_training_frame(frame: Any, symbol: str, timeframe: str) -> Tuple[Any, Any]:
    """``(cleaned_frame, report)`` from the platform's existing validator.

    ``market_data_validation.MarketDataValidator`` is REUSED AS-IS: the required
    columns, the OHLC relationships, positive prices, non-negative volume, duplicate
    timestamps and ordering are its rules, not this module's.

    Two shapes of refusal, both reported as one block
    -------------------------------------------------
    The validator GRADES some problems and REJECTS others. Outliers, data gaps, OHLC
    violations and negative volume make it raise ``DataValidationError`` outright -
    it refuses to hand back a repaired frame, which is the whole point of its "no
    synthetic data" posture. Both outcomes mean the same thing here (this window
    cannot be trained on) so both become one ``DATA_QUALITY`` block carrying what the
    validator said.

    That posture is strict, and deliberately not softened here: its thresholds are
    ``market_data_validation.ValidationConfig``'s, that component is REUSED AS-IS, and
    relaxing them from the training path would be this module quietly overriding a
    data-integrity rule the rest of the platform is held to. A window it refuses is
    reported as refused, with its own message, rather than becoming a 500 or being
    retried against looser settings.

    Raises
        :class:`TrainingBlocked` (``DATA_QUALITY``) when the measured level is
        ``POOR`` or ``UNUSABLE``, carrying the whole report (Requirement 14.7), or
        when the validator rejected the window outright. The level is compared by
        member NAME: ``DataQualityLevel.POOR.value`` is the integer 50, and comparing
        a score against that would be this module inventing a threshold the validator
        already owns.
    """
    from backend_app.backend.market_data_validation import (
        DataValidationError,
        get_validator,
    )

    try:
        cleaned, report = await get_validator().validate(frame, symbol, timeframe)
    except DataValidationError as exc:
        raise TrainingBlocked(
            REASON_DATA_QUALITY,
            f"The market data for {symbol} at {timeframe} was rejected by the "
            f"platform's data validator: {exc}",
            {"quality_level": "REJECTED", "validator_error": str(exc)},
        ) from exc
    level_name = getattr(report.quality_level, "name", str(report.quality_level))
    if level_name in BLOCKING_QUALITY_LEVELS:
        raise TrainingBlocked(
            REASON_DATA_QUALITY,
            f"The market data for {symbol} at {timeframe} is {level_name.lower()} "
            f"(quality score {report.quality_score:.1f}); training on it would learn "
            f"the feed's defects.",
            {"quality_level": level_name, "report": report.to_dict()},
        )
    return cleaned, report


# --------------------------------------------------------------------------
# 4. The feature pipeline and its schema check (Requirement 14.8)
# --------------------------------------------------------------------------


def _feature_matrix_ports(descriptor: Any) -> List[str]:
    """The ``FEATURE_MATRIX`` input ports ``descriptor`` declares, in order."""
    ports: List[str] = []
    for port in getattr(descriptor, "inputs", ()) or ():
        port_type = getattr(getattr(port, "type", None), "value", None) or str(
            getattr(port, "type", "")
        )
        if port_type == "FEATURE_MATRIX":
            ports.append(port.name)
    return ports


def _upstream_closure(plan: Any, node_id: str, *, include_self: bool) -> List[str]:
    """``node_id``'s ancestors, in ``plan.execution_order``.

    A node's value in a DAG is a function of its ancestors and nothing else, so the
    matrix produced here is identical to the matrix the same ML node receives in a
    full run. What restricting the node set removes is the collateral: the ACTION
    nodes, and any *other* ML node whose model is not bound yet.
    """
    needed = {node_id}
    stack = [node_id]
    while stack:
        current = stack.pop()
        for source in plan.predecessors(current):
            if source not in needed:
                needed.add(source)
                stack.append(source)
    if not include_self:
        needed.discard(node_id)
    return [nid for nid in plan.execution_order if nid in needed]


async def run_feature_pipeline(plan: Any, registry: Any, frame: Any, ml_node_id: str) -> Any:
    """The ``FeatureMatrix`` that reaches ``ml_node_id``'s feature ports.

    Executes the ML node's **upstream closure** through
    ``dag_engine.DAGEngine.execute_dag`` - the same call ``dag_event_loop``,
    ``dag_risk_integration``, ``backtest_runtime`` and the node preview make - so
    the features a model is trained on are produced by the executors that will
    compute them when it trades. There is no second feature path here: no formula,
    no alignment rule and no warmup rule of this module's own.

    Several matrices arriving on the same variadic port are merged with
    ``feature_matrix.concat_matrices``, which joins on the timestamp index. Joining
    by position is the silent-shift defect that contract exists to prevent, so it is
    not done here either.

    Raises
        :class:`TrainingBlocked` (``FEATURES``) when the closure cannot execute, or
        when what arrives on a feature port is not a matrix.
    """
    import asyncio

    from backend_app.backend.dag_engine import DAGEngine
    from backend_app.backend.strategy_compiler import plan_to_engine_graph
    from backend_app.backend.strategy_dag.feature_matrix import (
        FeatureMatrix,
        concat_matrices,
    )

    if registry is None:
        # The same lazy seam ``compile_plan`` and ``plan_to_engine_graph`` use. A caller
        # that named no descriptor source means "the assembled backend registry", not
        # "no registry" - and reading it as the latter reported a published model block
        # as unpublished.
        from backend_app.backend.strategy_dag.registry import get_registry

        registry = get_registry()

    node = plan.node(ml_node_id)
    descriptor = registry.get(node.block_id)
    if descriptor is None:
        raise TrainingBlocked(
            REASON_MODEL_UNPUBLISHED,
            f"The registry publishes no descriptor for {node.block_id!r}, so its "
            f"feature ports are unknown and no dataset can be assembled for it.",
            {"node_id": ml_node_id, "block_id": node.block_id},
        )

    sources: List[Tuple[str, str]] = []
    for port in _feature_matrix_ports(descriptor):
        for edge in plan.inbound_edges(ml_node_id, port):
            sources.append((edge.source, edge.source_port))
    if not sources:
        raise TrainingBlocked(
            REASON_FEATURES,
            f"ML node {ml_node_id!r} has no feature matrix connected to it, so there "
            f"is nothing to train on.",
            {"node_id": ml_node_id, "block_id": node.block_id},
        )

    engine_nodes, engine_edges = plan_to_engine_graph(plan, registry)
    closure = set(_upstream_closure(plan, ml_node_id, include_self=False))
    pipeline_nodes = [item for item in engine_nodes if item["id"] in closure]
    pipeline_edges = [
        edge
        for edge in engine_edges
        if edge["source"] in closure and edge["target"] in closure
    ]

    engine = DAGEngine(enable_tracing=False, enable_event_buffer=False)
    try:
        await asyncio.to_thread(
            engine.execute_dag, pipeline_nodes, pipeline_edges, frame
        )
    except Exception as exc:  # noqa: BLE001 - reported as a block, with the node named
        logger.info("Training feature pipeline failed on node %s: %s", ml_node_id, exc)
        raise TrainingBlocked(
            REASON_FEATURES,
            f"The feature pipeline feeding {ml_node_id!r} could not be computed: {exc}",
            {
                "node_id": ml_node_id,
                "failure": type(exc).__name__,
                "issues": engine.get_node_issues(),
                "executed_nodes": [item["id"] for item in pipeline_nodes],
            },
        ) from exc

    matrices: List[Any] = []
    for source_node, source_port in sources:
        value = engine.node_outputs.get((source_node, source_port))
        if value is None:
            value = engine.node_results.get(source_node)
        if not isinstance(value, FeatureMatrix):
            raise TrainingBlocked(
                REASON_FEATURES,
                f"Node {source_node!r} port {source_port!r} feeds {ml_node_id!r} but "
                f"produced no feature matrix, so its columns and timestamps are "
                f"unknown.",
                {
                    "node_id": ml_node_id,
                    "source_node": source_node,
                    "source_port": source_port,
                    "produced": type(value).__name__,
                },
            )
        matrices.append(value)

    return matrices[0] if len(matrices) == 1 else concat_matrices(matrices)


def check_feature_schema(matrix: Any, node_id: str, block_id: str) -> Tuple[Any, Any]:
    """``(schema, frame)`` for the usable rows of ``matrix``, or a block.

    Requirement 14.8. ``feature_validator.FeatureValidator`` is REUSED AS-IS and is
    the authority: no NaN, exact column names, exact column count, index alignment,
    no infinities.

    What the expected schema IS, said plainly
    ----------------------------------------
    At training time there is no trained model whose recorded schema could be the
    authority - that row does not exist until task 6.5 writes it. So the expected
    schema is the one the **graph** produces: the column names and order of the
    matrix the plan's feature pipeline just assembled. That makes this check the
    validator's content rules (NaN, infinity, count, alignment) rather than a
    names comparison against a prior run, and it is stated rather than implied.
    The schema returned here is exactly what task 6.5 persists as the model
    version's ``feature_schema``, which is what gives the deployment-time
    ``check_model_compatibility`` something real to compare against.

    The check runs over ``matrix.usable_slice()`` - warmup rows removed - because
    those are the rows a model is trained on. A NaN surviving that trim is a genuine
    hole in the middle of a feature, and it blocks.
    """
    import pandas as pd

    from backend_app.backend.feature_validator import (
        FeatureSchema,
        FeatureValidationError,
        FeatureValidator,
        ModelMismatchError,
    )

    usable = matrix.usable_slice()
    frame = pd.DataFrame(
        usable.values, index=pd.Index(usable.index, name="timestamp"), columns=list(usable.columns)
    )
    schema = FeatureSchema(
        expected_features=list(usable.columns),
        expected_count=len(usable.columns),
    )
    try:
        validated = FeatureValidator.validate_features(frame, schema)
    except (FeatureValidationError, ModelMismatchError) as exc:
        raise TrainingBlocked(
            REASON_FEATURES,
            f"The features produced for {node_id!r} failed the schema check: {exc}",
            {
                "node_id": node_id,
                "block_id": block_id,
                "failure": type(exc).__name__,
                "feature_names": list(usable.columns),
                "feature_columns": len(usable.columns),
                "usable_rows": int(usable.n_rows),
                "issues": [str(exc)],
            },
        ) from exc
    return schema, validated


# --------------------------------------------------------------------------
# 5. The supervised dataset (ml_dataset, REUSED AS-IS)
# --------------------------------------------------------------------------


def build_training_dataset(
    matrix: Any,
    frame: Any,
    *,
    label_horizon: int,
    label_mode: str = DEFAULT_LABEL_MODE,
    label_threshold: float = DEFAULT_LABEL_THRESHOLD,
) -> Any:
    """The aligned ``(X, y)`` pair for ``matrix``, labelled from ``frame``'s closes.

    ``ml_dataset.build_supervised_dataset`` does the work, so the ML-1 off-by-one
    stays fixed in one place: it drives X and y off ONE row range, and the warmup
    trim and the label-horizon trim are already inside the row count it reports.
    Nothing here subtracts them again.

    The label prices are ``frame['close']`` **re-indexed onto the matrix's own
    timestamps**, never taken by position: the matrix may hold fewer rows than the
    frame, and taking the first ``n`` closes would label each feature row with some
    other bar's future.

    Raises
        :class:`TrainingBlocked` (``DATASET``) when the window holds too few rows to
        produce a single labelled example, or when a matrix timestamp is absent from
        the frame.
    """
    import numpy as np
    import pandas as pd

    from backend_app.backend.ml_dataset import (
        LabelMode,
        MLDatasetError,
        build_supervised_dataset,
    )

    closes = pd.Series(frame["close"].to_numpy(dtype=float), index=frame.index)
    index = pd.Index(matrix.index)
    aligned = closes.reindex(index)
    if bool(aligned.isna().any()):
        missing = int(aligned.isna().sum())
        raise TrainingBlocked(
            REASON_DATASET,
            f"{missing} of the {len(index)} feature timestamps have no candle in the "
            f"validated window, so those rows could not be labelled.",
            {"missing_label_rows": missing, "feature_rows": int(len(index))},
        )

    try:
        mode = LabelMode(str(label_mode))
    except ValueError as exc:
        raise TrainingBlocked(
            REASON_DATASET,
            f"{label_mode!r} is not a supported label mode.",
            {"label_mode": label_mode, "supported": [m.value for m in LabelMode]},
        ) from exc

    try:
        return build_supervised_dataset(
            matrix,
            np.asarray(aligned.to_numpy(dtype=float)),
            int(label_horizon),
            label_mode=mode,
            threshold=float(label_threshold),
            price_index=np.asarray(index),
        )
    except MLDatasetError as exc:
        raise TrainingBlocked(
            REASON_DATASET,
            f"No labelled training rows could be assembled: {exc}",
            {
                "feature_rows": int(len(index)),
                "warmup_offset": int(matrix.warmup_offset),
                "label_horizon": int(label_horizon),
            },
        ) from exc


def split_training_dataset(dataset: Any, config: Any, *, feature_lookback: int = 0) -> Any:
    """Temporal splits over the dataset's OWN row space.

    ``ml_dataset.splits_for_dataset`` is used rather than
    ``make_temporal_splits(features.index, ...)``: X starts at the warmup offset and
    stops ``horizon`` rows early, so the feature row space and the dataset row space
    differ at both ends and splitting the wrong one is an off-by-one waiting to
    happen. The dataset's own horizon supplies the label-horizon half of the embargo
    floor, so the embargo and the labels cannot drift apart.

    Raises
        :class:`TrainingBlocked` (``DATASET``) when the row count cannot carry the
        requested fractions plus the embargo. That is the same shortfall the gate
        reports with required-versus-available figures; this path is reached only for
        a shape the gate does not cover, and it says which one.
    """
    from backend_app.backend.ml_dataset import MLDatasetError, splits_for_dataset

    try:
        return splits_for_dataset(
            dataset,
            config.val_fraction,
            config.test_fraction,
            config.embargo_bars,
            feature_lookback=int(feature_lookback),
        )
    except MLDatasetError as exc:
        raise TrainingBlocked(
            REASON_DATASET,
            f"The dataset cannot be split for training: {exc}",
            {
                "usable_rows": int(dataset.n_rows),
                "val_fraction": config.val_fraction,
                "test_fraction": config.test_fraction,
                "embargo_bars": config.embargo_bars,
            },
        ) from exc


# --------------------------------------------------------------------------
# 6. The recorded configuration (Requirements 12.6, 15.14)
# --------------------------------------------------------------------------


def build_training_config(
    *,
    data_source: Mapping[str, Any],
    plan: Any,
    spec: Any,
    validation_cfg: Any,
    window: Mapping[str, Any],
    frame: Any,
    epochs: int,
    batch_size: Optional[int],
    seed: int,
    label_mode: str,
    label_threshold: float,
) -> Dict[str, Any]:
    """The ``training_jobs.config`` payload: everything a rerun needs, and nothing else.

    Requirement 15.14 (record the configuration) and Requirement 12.6 (the data
    source comes from here). Every value is either resolved from the graph or is a
    figure this path actually used - no default is left implicit, because a default
    that lives in a constant rather than in the row makes yesterday's run
    irreproducible the moment the constant changes.

    Postcondition
        No key at any depth names exchange identity or credential material, asserted
        by :func:`assert_no_exchange_identity` before the value is returned.
    """
    index = frame.index
    config: Dict[str, Any] = {
        # The resolved data source. A market, never a venue (SB-06).
        "data_source": dict(data_source),
        "symbol": data_source.get("symbol"),
        "timeframe": data_source.get("timeframe"),
        # The window actually read, so a rerun asks for the same range.
        "range": {
            "bars": int(len(index)),
            "requested_bars": int(window.get("bars") or 0),
            "first_timestamp": None if len(index) == 0 else str(index[0]),
            "last_timestamp": None if len(index) == 0 else str(index[-1]),
        },
        # Splits and embargo, as the gate resolved them.
        "splits": {
            "val_fraction": validation_cfg.val_fraction,
            "test_fraction": validation_cfg.test_fraction,
            "embargo_bars": int(validation_cfg.embargo_bars),
        },
        "val_fraction": validation_cfg.val_fraction,
        "test_fraction": validation_cfg.test_fraction,
        "embargo_bars": int(validation_cfg.embargo_bars),
        "label_horizon": int(validation_cfg.label_horizon),
        "label_mode": str(label_mode),
        "label_threshold": float(label_threshold),
        # What the worker runs.
        "epochs": int(epochs),
        "batch_size": None if batch_size is None else int(batch_size),
        "seed": int(seed),
        "warmup_bars": int(plan.warmup_bars),
        # Provenance: which graph, which compiler, which model contract.
        "dag_hash": plan.dag_hash,
        "compiler_version": plan.compiler_version,
        "schema_version": int(plan.schema_version),
        "block_id": spec.block_id,
        "model_family": spec.model_family,
        "sequence_length": spec.sequence_length,
        "epoch_unit": spec.epoch_unit,
    }
    assert_no_exchange_identity(config)
    return config


# --------------------------------------------------------------------------
# 7. Live job counts, the concurrency half of the caps
# --------------------------------------------------------------------------


async def count_training_jobs(sb: Any, user_id: str) -> Any:
    """A :class:`ml_training_policy.JobCounts` for ``user_id``, or an honest absence.

    ``user_active`` is measured, and enforced twice as every other read in this file
    is: the request-scoped client applies the ``tj_owner_select`` RLS policy, and the
    query also carries an explicit ``.eq("user_id", ...)``. That is exactly the set
    the per-user concurrency cap is about.

    The GLOBAL figures are not globally measurable from here, and this says so rather
    than reporting a comfortable zero. An RLS-scoped client cannot see another
    tenant's jobs and there is no service-role client on this path, so
    ``global_running`` / ``global_queued`` carry the same user-scoped counts - an
    explicit lower bound, not a claim about the fleet. The consequence is bounded and
    safe in the one direction that matters: a lower bound can only fail to DEFER, and
    ``DEFER`` is not a rejection (Requirement 16.5 holds a job in the queue and
    estimates its position). It can never turn an over-cap request into an admission,
    because the epoch, row, column, per-user-concurrency and memory caps are all
    evaluated from figures that ARE measured here. Detecting global saturation belongs
    to the worker-side scheduler (task 6.4), which runs with the service role.

    A missing table degrades to :meth:`JobCounts.unavailable`, naming
    :data:`TRAINING_MIGRATION` - the seam task 6.2 built for exactly this, and the
    reason an unapplied migration is a warning rather than a 500.
    """
    from backend_app.backend.ml_training_policy import JobCounts

    if sb is None:
        return JobCounts.unavailable(
            "No database client is available, so live training job counts could not be "
            "read; the per-user and global concurrency caps were not applied. The "
            "worker re-checks caps before the first epoch (Requirement 16.4)."
        )

    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("id,status")
            .eq("user_id", user_id)
            .in_("status", list(TRAINING_JOB_LIVE_STATES))
            .execute()
        )
        result = await _execute(query)
    except Exception as exc:  # noqa: BLE001 - classified, never blanket-swallowed
        if is_missing_training_table_error(exc):
            logger.warning(
                "%s is absent, so live training job counts could not be read and the "
                "concurrency caps were not applied. Apply %s. Every per-request cap "
                "(epochs, rows, feature columns, memory) was still enforced, and the "
                "worker re-checks caps before the first epoch. Detail: %s",
                TRAINING_JOBS_TABLE,
                TRAINING_MIGRATION,
                exc,
            )
            return JobCounts.unavailable(
                f"{TRAINING_JOBS_TABLE} is absent; apply {TRAINING_MIGRATION}. The "
                f"concurrency caps were not applied on this request."
            )
        logger.warning(
            "Live training job counts could not be read (%s); the concurrency caps "
            "were not applied on this request.",
            exc,
        )
        return JobCounts.unavailable(
            f"Live training job counts could not be read ({exc}); the concurrency caps "
            f"were not applied on this request."
        )

    error_text = _result_error_text(result)
    if error_text and is_missing_training_table_error(Exception(error_text)):
        logger.warning(
            "%s is absent (%s), so the concurrency caps were not applied. Apply %s.",
            TRAINING_JOBS_TABLE,
            error_text,
            TRAINING_MIGRATION,
        )
        return JobCounts.unavailable(
            f"{TRAINING_JOBS_TABLE} is absent; apply {TRAINING_MIGRATION}."
        )

    rows = (getattr(result, "data", None) or []) if result else []
    running = sum(1 for row in rows if (row or {}).get("status") == "RUNNING")
    queued = sum(1 for row in rows if (row or {}).get("status") == "QUEUED")
    return JobCounts(
        user_active=len(rows),
        global_running=running,
        global_queued=queued,
        available=True,
    )


# --------------------------------------------------------------------------
# 8. Admission: the whole order, in one place
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeTrainingPlan:
    """Everything one ML node needs to become one ``training_jobs`` row."""

    node_id: str
    block_id: str
    spec: Any
    validation_cfg: Any
    stats: Any
    dataset: Any
    splits: Any
    feature_schema: Any
    config: Dict[str, Any]
    fingerprint: str
    request: Any
    admission: Any
    verdict: Any

    def job_row(self, *, user_id: str, strategy_id: str, version_id: str) -> Dict[str, Any]:
        """The ``training_jobs`` INSERT payload for this node.

        ``updated_at`` is set explicitly. Migration 004d deliberately attaches no
        ``BEFORE UPDATE`` trigger to this table - the header of
        ``004d_training_and_models.sql`` says so and task 6.1's report repeats it -
        so every status transition this codebase writes has to set the column
        itself. This is the first of those writes.
        """
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": str(uuid4()),
            "user_id": user_id,
            "strategy_id": strategy_id,
            "version_id": version_id,
            "node_id": self.node_id,
            "block_id": self.block_id,
            "status": "QUEUED",
            "cancel_requested": False,
            "config": self.config,
            "dataset_fingerprint": self.fingerprint,
            "dataset_rows": int(self.stats.total_rows or self.stats.usable_rows),
            "usable_rows": int(self.stats.usable_rows),
            "feature_columns": int(self.stats.usable_feature_columns),
            "feature_names": list(self.stats.feature_names),
            "split_sizes": self.splits.sizes if self.splits is not None else None,
            "epochs_total": int(self.request.epochs),
            "epoch_current": 0,
            "progress": 0,
            "created_at": now,
            "updated_at": now,
        }


@dataclass(frozen=True)
class TrainingPreparation:
    """The admission verdict for a whole graph: ready, blocked, or not required.

    ``blocked`` and ``nodes`` are mutually exclusive by construction. A caller
    inspects ``blocked`` first and, on a block, never reaches the insert - which is
    how "no training job on any blocked path" is a structural property of this type
    rather than a promise in a docstring.
    """

    required: bool
    nodes: Tuple[NodeTrainingPlan, ...] = ()
    blocked: Optional[TrainingBlocked] = None
    quality_report: Optional[Any] = None
    market: Optional[Dict[str, Any]] = None
    fingerprint: str = ""
    warnings: Tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.required and self.blocked is None and bool(self.nodes)

    def stats_by_node(self) -> Dict[str, Any]:
        """``{node_id: DatasetStats}`` for validator stage 11.

        The training path is the caller that HAS the fetched dataset, so it is the
        caller that supplies the measured statistics. Handing these to
        ``validate(..., ml_dataset_stats=...)`` is what makes the persisted
        ``validation_report`` record stage 11 as ``PASSED`` rather than ``SKIPPED``;
        a version saved from the plain validate path honestly reports the latter.
        """
        return {node.node_id: node.stats for node in self.nodes}

    def to_dict(self) -> Dict[str, Any]:
        if self.blocked is not None:
            payload = self.blocked.to_dict()
            payload["required"] = self.required
            if self.quality_report is not None:
                payload.setdefault("quality", self.quality_report.to_dict())
            return payload
        return {
            "blocked": False,
            "required": self.required,
            "market": self.market,
            "dataset_fingerprint": self.fingerprint,
            "warnings": list(self.warnings),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "block_id": node.block_id,
                    "usable_rows": int(node.stats.usable_rows),
                    "feature_columns": int(node.stats.usable_feature_columns),
                    "feature_names": list(node.stats.feature_names),
                    "epochs": int(node.request.epochs),
                    "split_sizes": (
                        node.splits.sizes if node.splits is not None else None
                    ),
                    "admission": node.admission.to_dict(),
                }
                for node in self.nodes
            ],
        }


async def prepare_training(
    plan: Any,
    *,
    user: dict,
    registry: Any = None,
    training_cfg: Optional[Mapping[str, Any]] = None,
    job_counts: Any = None,
) -> TrainingPreparation:
    """Run the whole admission path for ``plan`` without writing anything.

    ``design.md`` -> Training workflow, steps 2 to 4 and 6, in that order:

    ======  =========================================================
    step    what runs, and whose rule it is
    ======  =========================================================
    2       fetch the window; ``MarketDataValidator`` grades it (14.7)
    3       run the feature pipeline; ``FeatureValidator`` checks it (14.8)
    4       build (X, y); ``check_ml_data_requirements`` gates it (14.3-14.6)
    6       ``resolve_caps`` + ``enforce_caps`` admit or refuse it (16.x)
    ======  =========================================================

    **Loop invariant (step 4):** each ML node is gated independently and the FIRST
    failure blocks the whole request, because a partially trained strategy is not a
    strategy (design.md's own words).

    Preconditions
        ``plan`` compiled. No database client is held; the only I/O is the candle
        fetch, which is the module-level :func:`fetch_training_bars` seam.

    Postconditions
        Either ``ready`` - and then, for every ML node, a measured
        :class:`DatasetStats`, an admitted or deferred :class:`Admission`, a config
        carrying no exchange identity, and a dataset fingerprint - or ``blocked``
        carrying the structured reason. **Nothing is persisted either way**, so a
        block cannot have left a job row behind.
    """
    from backend_app.backend.ml_training_policy import (
        CapExceeded,
        DatasetStats,
        MLTrainingPolicyError,
        ModelSpecView,
        TrainingRequest,
        ValidationConfig,
        check_ml_data_requirements,
        enforce_caps,
        required_row_count,
        resolve_caps,
    )

    cfg: Dict[str, Any] = dict(training_cfg or {})
    warnings: List[str] = []

    ml_nodes = tuple(getattr(plan, "ml_nodes", ()) or ())
    if not ml_nodes:
        # Requirement 14.10: nothing to train. Not "skipped", not an empty job.
        return TrainingPreparation(required=False)

    try:
        # ── The resolved data source, from the graph and nothing else ─────
        market = resolve_training_data_source(plan)
        symbol = market["symbol"]
        timeframe = market["timeframe"]

        # ── Model specs and split config, before the fetch, so the window is
        #    sized by the gate's own arithmetic rather than by a guess ──────
        label_horizon = int(cfg.get("label_horizon") or DEFAULT_LABEL_HORIZON)
        label_mode = str(cfg.get("label_mode") or DEFAULT_LABEL_MODE)
        label_threshold = float(
            cfg.get("label_threshold")
            if cfg.get("label_threshold") is not None
            else DEFAULT_LABEL_THRESHOLD
        )
        seed = int(cfg.get("seed") if cfg.get("seed") is not None else DEFAULT_TRAINING_SEED)

        specs: Dict[str, Any] = {}
        configs: Dict[str, Any] = {}
        for node_id in ml_nodes:
            node = plan.node(node_id)
            spec = ModelSpecView.for_block_id(node.block_id)
            if spec is None:
                raise TrainingBlocked(
                    REASON_MODEL_UNPUBLISHED,
                    f"The registry publishes no model descriptor for "
                    f"{node.block_id!r}, so its data requirements cannot be checked.",
                    {"node_id": node_id, "block_id": node.block_id},
                )
            specs[node_id] = spec
            configs[node_id] = ValidationConfig.for_model(
                spec,
                label_horizon=label_horizon,
                feature_lookback=int(plan.warmup_bars),
                val_fraction=cfg.get("val_fraction"),
                test_fraction=cfg.get("test_fraction"),
                embargo_bars=cfg.get("embargo_bars"),
            )

        window = training_bar_budget(
            warmup_bars=int(plan.warmup_bars),
            required_rows=max(
                required_row_count(specs[node_id], configs[node_id])
                for node_id in ml_nodes
            ),
            label_horizon=label_horizon,
            requested=cfg.get("bars"),
        )
        if window["exceeds_ceiling"]:
            raise TrainingBlocked(
                REASON_DATASET,
                f"This graph needs a window of {window['target_bars']} bars before its "
                f"models have enough usable rows, and a single training fetch reads at "
                f"most {TRAINING_MAX_BARS}.",
                {"window": dict(window)},
            )

        # ── Step 2: the real fetch and the real numbers ───────────────────
        rows = await fetch_training_bars(symbol, timeframe, window["bars"])
        frame = training_frame(rows, window["bars"])
        frame, quality = await quality_check_training_frame(frame, symbol, timeframe)
        fingerprint = dataset_fingerprint(frame, symbol, timeframe)

        prepared: List[NodeTrainingPlan] = []
        for node_id in ml_nodes:
            spec = specs[node_id]
            validation_cfg = configs[node_id]
            node = plan.node(node_id)

            # ── Step 3: the features, and the schema check on them ────────
            matrix = await run_feature_pipeline(plan, registry, frame, node_id)
            feature_schema, _validated = check_feature_schema(
                matrix, node_id, node.block_id
            )

            # ── Step 4: measured statistics, then the gate ────────────────
            dataset = build_training_dataset(
                matrix,
                frame,
                label_horizon=validation_cfg.label_horizon,
                label_mode=label_mode,
                label_threshold=label_threshold,
            )
            stats = DatasetStats.from_dataset(dataset)
            verdict = check_ml_data_requirements(
                plan,
                stats,
                spec,
                validation_cfg,
                node_id=node_id,
                feature_lookback=int(plan.warmup_bars),
            )
            if not verdict.ok:
                # Requirements 14.3, 14.4: block, create NO job, and return the
                # required and available figures for BOTH dimensions.
                raise TrainingBlocked(
                    REASON_ML_REQUIREMENTS, verdict.message(), verdict.to_dict()
                )

            splits = split_training_dataset(
                dataset, validation_cfg, feature_lookback=int(plan.warmup_bars)
            )

            # ── Step 6: the caps ─────────────────────────────────────────
            request = TrainingRequest.build(
                spec,
                stats,
                epochs=cfg.get("epochs"),
                batch_size=cfg.get("batch_size"),
            )
            caps = resolve_caps(user, spec)
            admission = enforce_caps(request, caps, user, job_counts=job_counts)
            warnings.extend(admission.warnings)

            prepared.append(
                NodeTrainingPlan(
                    node_id=node_id,
                    block_id=node.block_id,
                    spec=spec,
                    validation_cfg=validation_cfg,
                    stats=stats,
                    dataset=dataset,
                    splits=splits,
                    feature_schema=feature_schema,
                    config=build_training_config(
                        data_source=market,
                        plan=plan,
                        spec=spec,
                        validation_cfg=validation_cfg,
                        window=window,
                        frame=frame,
                        epochs=request.epochs,
                        batch_size=request.batch_size,
                        seed=seed,
                        label_mode=label_mode,
                        label_threshold=label_threshold,
                    ),
                    fingerprint=fingerprint,
                    request=request,
                    admission=admission,
                    verdict=verdict,
                )
            )

        return TrainingPreparation(
            required=True,
            nodes=tuple(prepared),
            quality_report=quality,
            market=market,
            fingerprint=fingerprint,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    except TrainingBlocked as blocked:
        _record_training_block(blocked.reason)
        return TrainingPreparation(required=True, blocked=blocked)
    except CapExceeded as exceeded:
        # Requirement 16.3: name the one cap, its requested value and its permitted
        # value. Reported as a block rather than raised, so the caller can persist a
        # perfectly valid version and refuse only the training half - and so no job
        # row is created either way.
        _record_training_cap_rejection(exceeded.to_dict())
        return TrainingPreparation(
            required=True,
            blocked=TrainingBlocked(
                REASON_CAP_EXCEEDED, str(exceeded), exceeded.to_dict()
            ),
        )
    except MLTrainingPolicyError as policy_error:
        # A cap rejection whose cap could not be resolved. Counted, and counted as
        # unnamed rather than filed under a cap this branch does not know.
        _record_training_cap_rejection({})
        return TrainingPreparation(
            required=True,
            blocked=TrainingBlocked(
                REASON_CAP_EXCEEDED,
                str(policy_error),
                {"failure": type(policy_error).__name__},
            ),
        )


# --------------------------------------------------------------------------
# 9. The two writes
# --------------------------------------------------------------------------


async def find_live_training_job(
    sb: Any, version_id: str, node_id: str
) -> Optional[Dict[str, Any]]:
    """The QUEUED or RUNNING job for ``(version_id, node_id)``, if there is one.

    A convenience, NOT the idempotency mechanism: between this read and the insert
    another request can land, and only ``uq_tj_active_per_node`` closes that window.
    Returns ``None`` when the table is absent, because "no live job" and "no table"
    are handled by the caller's own degraded path.
    """
    if sb is None:
        return None
    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("*")
            .eq("version_id", version_id)
            .eq("node_id", node_id)
            .in_("status", list(TRAINING_JOB_LIVE_STATES))
            .limit(1)
            .execute()
        )
        result = await _execute(query)
    except Exception as exc:  # noqa: BLE001
        if is_missing_training_table_error(exc):
            return None
        raise
    rows = (getattr(result, "data", None) or []) if result else []
    return rows[0] if rows else None


async def insert_training_job(sb: Any, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Insert one ``training_jobs`` row, idempotently per ``(version_id, node_id)``.

    Returns ``{"job": row_or_None, "idempotent": bool, "available": bool,
    "reason": str}``.

    Three outcomes, all of them honest:

    * **inserted** - ``job`` is the new row, ``idempotent`` false.
    * **already live** - ``uq_tj_active_per_node`` rejected the INSERT, so a QUEUED
      or RUNNING job for this (version, node) already exists. It is read back and
      returned with ``idempotent`` true. This is the whole of Requirement 15.13 and
      of "idempotent per (version, node)": the invariant is held by the partial
      unique index, which is the only thing that can hold it against two concurrent
      requests, and the rejection is translated rather than surfaced as a 500.
    * **unavailable** - ``training_jobs`` does not exist because
      ``004d_training_and_models.sql`` has not been applied. A warning naming that
      file is logged, ``available`` is false, and no job id is invented.

    Every other failure PROPAGATES. The one behaviour worse than a training run that
    fails to start is one that reports a job id nothing will ever pick up.
    """
    try:
        query = sb.table(TRAINING_JOBS_TABLE).insert(dict(row)).execute()
        result = await _execute(query)
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if is_missing_training_table_error(exc):
            logger.warning(
                "Training was admitted but no job could be recorded: %s does not "
                "exist. Apply %s. The strategy version was saved and stays "
                "un-deployable until a model is bound; nothing was queued, and no job "
                "id was returned. Detail: %s",
                TRAINING_JOBS_TABLE,
                TRAINING_MIGRATION,
                exc,
            )
            return {
                "job": None,
                "idempotent": False,
                "available": False,
                "reason": (
                    f"{TRAINING_JOBS_TABLE} does not exist; apply "
                    f"{TRAINING_MIGRATION}. Training was admitted but could not be "
                    f"queued."
                ),
            }
        if is_duplicate_active_job_error(exc):
            existing = await find_live_training_job(
                sb, str(row["version_id"]), str(row["node_id"])
            )
            logger.info(
                "A live training job already exists for version %s node %s; "
                "returning it rather than creating a second one (uq_tj_active_per_node).",
                row["version_id"],
                row["node_id"],
            )
            return {
                "job": existing,
                "idempotent": True,
                "available": True,
                "reason": "",
            }
        raise

    error_text = _result_error_text(result)
    if error_text:
        if is_missing_training_table_error(Exception(error_text)):
            logger.warning(
                "Training was admitted but no job could be recorded: %s (apply %s).",
                error_text,
                TRAINING_MIGRATION,
            )
            return {
                "job": None,
                "idempotent": False,
                "available": False,
                "reason": (
                    f"{TRAINING_JOBS_TABLE} does not exist; apply "
                    f"{TRAINING_MIGRATION}."
                ),
            }
        if is_duplicate_active_job_error(Exception(error_text)):
            existing = await find_live_training_job(
                sb, str(row["version_id"]), str(row["node_id"])
            )
            return {
                "job": existing,
                "idempotent": True,
                "available": True,
                "reason": "",
            }
        raise RuntimeError(f"training_jobs insert failed: {error_text}")

    written = (getattr(result, "data", None) or []) if result else []
    return {
        "job": written[0] if written else dict(row),
        "idempotent": False,
        "available": True,
        "reason": "",
    }


async def enqueue_training_job(job_id: str, user_id: str) -> bool:
    """Hand ``job_id`` to the worker. Returns immediately; never raises.

    A WAKE-UP HINT, not the source of truth. The authoritative queue is the
    ``training_jobs`` row itself: it is ``QUEUED``, ``idx_tj_user_status`` indexes
    exactly that, and the worker's claim step (task 6.4) reads it. So a Redis that
    is down delays a job, it does not lose one - which is why this is best-effort
    and why a failure is a warning rather than a rolled-back insert.

    Requirement 15.1: this is where "without waiting for training to finish" is
    concrete. Nothing here trains, imports a model library, or awaits a worker.
    """
    try:
        from backend_app.backend.redis_manager import get_redis_manager

        manager = await get_redis_manager()
        if manager is None:
            logger.warning(
                "Training job %s is QUEUED in the database but could not be announced "
                "(no Redis); the worker will pick it up on its next scan of QUEUED "
                "jobs.",
                job_id,
            )
            return False
        return bool(
            await manager.queue_push(
                TRAINING_QUEUE_NAME,
                {
                    "job_id": str(job_id),
                    "user_id": str(user_id),
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 - a hint that failed is not a failed save
        logger.warning(
            "Training job %s is QUEUED in the database but could not be announced "
            "(%s); the worker will pick it up on its next scan of QUEUED jobs.",
            job_id,
            exc,
        )
        return False


async def publish_training_event(user_id: str, event: str, payload: Mapping[str, Any]) -> bool:
    """Announce a training state change over the existing multiplexed connection.

    Requirement 15.11, best-effort: a browser that is not connected must not fail a
    save. Nothing here opens a socket; both fan-outs write to connections that are
    already open (Requirement 23.1).

    TWO SEAMS, ONE PUBLISHER (task 6.7)
    -----------------------------------
    * the user's private channel, which every other producer in this codebase uses
      (``app_state.ws_manager.broadcast_user``); and
    * ``training.{job_id}``, delivered only to connections whose ownership of that
      job was resolved at subscribe time by
      ``core/websocket_auth.authorize_channel_subscription``.

    Both are attempted, independently, and a failure of either is a debug line
    rather than an exception: this is an announcement, and the ``training_jobs`` row
    remains the source of truth. The return value is True when at least one seam
    accepted the frame, so a caller can distinguish "announced" from "nobody heard".
    """
    published = False

    try:
        from backend_app.core.state import app_state

        manager = getattr(app_state, "ws_manager", None)
        broadcast = getattr(manager, "broadcast_user", None) if manager else None
        if broadcast is not None:
            await broadcast(str(user_id), {"type": event, **dict(payload)})
            published = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Training event %s could not be published: %s", event, exc)

    # The per-job channel. ``job_id`` comes from the payload because that is where
    # every caller already puts it; without one there is no channel to publish to and
    # the private-channel frame above is the whole announcement.
    job_id = str((payload or {}).get("job_id") or "")
    if job_id:
        try:
            from backend_app.backend.websocket_manager import get_websocket_manager

            channel_manager = get_websocket_manager()
            delivered = await channel_manager.broadcast_training_event(
                job_id, event, dict(payload), owner_id=str(user_id)
            )
            published = published or bool(delivered)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Training event %s could not be published on training.%s: %s",
                event,
                job_id,
                exc,
            )

    return published
