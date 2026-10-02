"""Behavioural cover for the tenant clause production-launch-hardening task 13.18 fixed.

WHAT THIS FILE ADDS THAT THE DRIFT SUITE CANNOT
-----------------------------------------------
``tests/test_schema_table_reference_drift.py`` proves a PARSE-LEVEL fact: no code path under
``backend_app/`` names ``strategies.tenant_id`` any more, and the gate still reads
``strategies.user_id``. That is the right shape of guard for drift, and it says nothing at all
about what :func:`eligibility_gate.evaluate` now *answers*. The production symptom was a
VERDICT, not a parse: read 1 selected a column ``public.strategies`` does not have, so it raised
``42703``, so :func:`evaluate`'s blanket ``except`` returned ``unevaluable=True`` with an EMPTY
outcome tuple (Requirement 2.13), and ``POST /api/library/submissions`` turned that into a 503.
Nothing could be published to the Marketplace at all.

So this file observes the verdict itself, before and after, for the three callers that matter.

THE FAKE IS SCHEMA-FAITHFUL, WHICH IS THE WHOLE POINT
-----------------------------------------------------
A permissive test double - one whose ``.select()`` ignores its projection, like the pipeline and
concurrency suites' ``FakeTable`` - CANNOT reproduce this defect. It would have returned the
strategy row happily with the pre-fix projection, and the gate would have passed. That is
precisely why the existing marketplace suites stayed green for the whole life of the bug.

:class:`_SchemaFaithfulTable` therefore parses the projection and raises a ``42703``-shaped
error for any column the table does not have, exactly as Postgres does. Its notion of "the
columns the table has" is NOT hand-written here: it is ``marketplace._ELIGIBILITY_HANDLER``'s
select-literal manifest, the same declaration ``test_select_literals_are_within_manifest``
holds the gate's projections against, and from which task 13.18 removed ``tenant_id``. One
source of truth, so this fake cannot drift away from the guard that polices the real reads.

It also narrows every returned row to the projected columns, again like Postgres. Without that
narrowing the pre-fix replica below would still have found ``user_id`` on a row it never
selected, and the before/after would have been vacuous.

**Validates: Requirements 2.2, 2.13, 21.1**
"""

import asyncio
from typing import Any, Dict, FrozenSet, List, Mapping, Optional
from unittest.mock import patch

from backend_app.backend.marketplace import _ELIGIBILITY_HANDLER
from backend_app.backend.marketplace import eligibility_gate

#: The columns each table the gate reads actually has, taken from the gate's own select-literal
#: manifest rather than restated. ``strategies`` carries no ``tenant_id`` here because
#: ``public.strategies`` carries none in production - the fact task 13.17 measured against the
#: live database (``42703``, with ``user_id`` on the same table answering OK).
_PRODUCTION_COLUMNS: Mapping[str, FrozenSet[str]] = _ELIGIBILITY_HANDLER["tables"]

OWNER_ID = "11111111-1111-4111-8111-111111111111"
STRANGER_ID = "22222222-2222-4222-8222-222222222222"
STRATEGY_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"

#: A tenant claim that is neither absent nor the caller's own id. Reachable: ``dependencies.py``
#: reads ``tenant_id`` from the token's claim or ``app_metadata`` and only falls back to
#: ``user_id``. See the final test, which pins what the gate does with it rather than asserting
#: it cannot happen.
FOREIGN_TENANT_ID = "99999999-9999-4999-8999-999999999999"


class _UndefinedColumn(Exception):
    """A fake Postgres ``42703`` for a column the relation does not have.

    Shaped like the driver error the real read raised: a ``.code`` of ``"42703"`` and a message
    naming the relation and the column. :func:`evaluate` does not inspect either - any
    exception from any of the four reads is unevaluable - but keeping the shape honest means
    this double fails the way production failed rather than in some way of its own.
    """

    def __init__(self, table: str, column: str) -> None:
        self.code = "42703"
        self.message = f'column {table}.{column} does not exist'
        super().__init__(self.message)


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


def _projected_columns(projection: str) -> List[str]:
    """The column names a PostgREST ``.select()`` literal asks for.

    The gate's projections are flat comma-separated column lists, some assembled across source
    lines, so splitting on commas and stripping is faithful. ``*`` asks for whatever exists and
    names nothing undefined.
    """
    return [
        part.strip()
        for part in str(projection).split(",")
        if part.strip() and part.strip() != "*"
    ]


class _SchemaFaithfulTable:
    """A chain-able fake table that answers ``42703`` for a column it does not have.

    Supports the chain the gate's four reads use - ``select``/``eq``/``in_``/``execute``, plus
    ``order``/``limit``/``single`` as no-ops so an unrelated read cannot fail on a missing
    method - and narrows the rows it returns to the projection, as a real one does.
    """

    def __init__(self, name: str, rows: List[Dict[str, Any]], columns: FrozenSet[str]) -> None:
        self._name = name
        self._rows = rows
        self._columns = columns
        self._projection: List[str] = []
        self._filters: List[Any] = []

    def select(self, projection: str = "*", *args: Any, **kwargs: Any) -> "_SchemaFaithfulTable":
        self._projection = _projected_columns(projection)
        for column in self._projection:
            if column not in self._columns:
                raise _UndefinedColumn(self._name, column)
        return self

    def eq(self, column: str, value: Any) -> "_SchemaFaithfulTable":
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "_SchemaFaithfulTable":
        self._filters.append(("in", column, values))
        return self

    def order(self, *args: Any, **kwargs: Any) -> "_SchemaFaithfulTable":
        return self

    def limit(self, *args: Any, **kwargs: Any) -> "_SchemaFaithfulTable":
        return self

    def single(self) -> "_SchemaFaithfulTable":
        return self

    def _matched(self) -> List[Dict[str, Any]]:
        result = list(self._rows)
        for kind, column, value in self._filters:
            if kind == "eq":
                result = [r for r in result if str(r.get(column, "")) == str(value)]
            elif kind == "in":
                wanted = {str(v) for v in value}
                result = [r for r in result if str(r.get(column, "")) in wanted]
        return result

    def execute(self) -> _Resp:
        if not self._projection:
            return _Resp(self._matched())
        return _Resp(
            [
                {column: row.get(column) for column in self._projection}
                for row in self._matched()
            ]
        )


class _SchemaFaithfulSupabase:
    """A Persistence_Layer double whose tables know their own columns."""

    def __init__(self) -> None:
        self.stores: Dict[str, List[Dict[str, Any]]] = {
            name: [] for name in _PRODUCTION_COLUMNS
        }

    def table(self, name: str) -> _SchemaFaithfulTable:
        return _SchemaFaithfulTable(
            name,
            self.stores.setdefault(name, []),
            _PRODUCTION_COLUMNS.get(name, frozenset()),
        )


class _NoopAuditLogger:
    """No-op audit logger - the gate audits every evaluation and there is no Redis here."""

    async def record_or_raise(self, *args: Any, **kwargs: Any) -> None:
        return None


def _seeded_db() -> _SchemaFaithfulSupabase:
    """``OWNER_ID``'s strategy and one saved version. Deliberately no backtest evidence.

    The evidence criteria are not what task 13.18 touched, and a verdict does not need to be an
    ADMIT to prove the tenant clause: what the defect destroyed was the gate's ability to answer
    at all. So these cases assert on ``unevaluable``, on the outcome tuple being populated, and
    on ``MP_TENANT``/``MP_OWNERSHIP`` specifically - not on admission, which the evidence
    criteria legitimately still refuse here.

    Note the row carries a ``tenant_id`` key. Nothing can read it: ``strategies`` does not
    declare that column, so a projection naming it raises ``42703`` and a projection that does
    not name it never sees the key. It is here only so these tests cannot accidentally pass by
    the column secretly existing.
    """
    db = _SchemaFaithfulSupabase()
    db.stores["strategies"].append(
        {
            "id": STRATEGY_ID,
            "user_id": OWNER_ID,
            "tenant_id": None,
            "archived_at": None,
        }
    )
    db.stores["strategy_versions"].append(
        {
            "id": VERSION_ID,
            "strategy_id": STRATEGY_ID,
            "version": 1,
            "is_draft": False,
            "validation_state": "valid",
            "blueprint": {"nodes": [], "edges": []},
            "graph_json": {"nodes": [], "edges": []},
        }
    )
    return db


def _evaluate(db: _SchemaFaithfulSupabase, caller: Mapping[str, Any]) -> Any:
    """Drive the real :func:`evaluate` against ``db``, with only the audit sink stubbed."""

    async def _drive() -> Any:
        return await eligibility_gate.evaluate(caller, STRATEGY_ID, [], db)

    with patch(
        "backend_app.core.audit_trail.get_strategy_audit_logger",
        return_value=_NoopAuditLogger(),
    ):
        return asyncio.run(_drive())


def _outcome(verdict: Any, code: str) -> Optional[Any]:
    for outcome in verdict.outcomes:
        if outcome.code == code:
            return outcome
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE BEFORE STATE
# ══════════════════════════════════════════════════════════════════════════


def _pre_fix_read_strategy(
    supabase: Any, strategy_id: Any, caller_id: Any
) -> Optional[Mapping[str, Any]]:
    """Read 1 exactly as it stood before task 13.18, projection and all.

    A replica of the pre-fix function body rather than the pre-fix module - the one line that
    changed is the ``.select()`` literal, reproduced here verbatim. Patched over
    ``_read_strategy`` this makes :func:`evaluate` issue the read production issued, against a
    double with production's columns.
    """
    response = (
        supabase.table("strategies")
        .select("id, user_id, tenant_id, archived_at")
        .eq("id", strategy_id)
        .eq("user_id", caller_id)
        .execute()
    )
    rows = eligibility_gate._rows(response)
    return rows[0] if rows else None


def test_the_pre_fix_projection_made_every_evaluation_unevaluable() -> None:
    """The defect's actual behaviour: no verdict at all, for a caller who owns the strategy.

    This is the state production was in. The strategy exists, it is the caller's, it has a
    saved valid version - and the gate returns no outcomes whatsoever, because read 1 named a
    column the table does not have and Requirement 2.13 says a read that did not complete is
    unevaluable rather than a verdict. The route maps this to a 503, which is why nothing could
    be submitted.

    **Validates: Requirements 2.13**
    """
    db = _seeded_db()
    with patch.object(eligibility_gate, "_read_strategy", _pre_fix_read_strategy):
        verdict = _evaluate(db, {"id": OWNER_ID, "tenant_id": OWNER_ID})

    assert verdict.unevaluable is True
    assert verdict.admitted is False
    assert verdict.outcomes == (), (
        "the pre-fix read should yield NO per-criterion outcomes - an unevaluable verdict is "
        "the absence of a decision, not a rejection"
    )


def test_the_schema_faithful_double_rejects_the_absent_column_at_the_read() -> None:
    """The double's own premise, asserted so the test above cannot pass for the wrong reason.

    If :class:`_SchemaFaithfulTable` silently permitted ``tenant_id``, the previous test would
    still fail somewhere and look like a proof. Pin the mechanism: selecting the absent column
    raises ``42703``, selecting the column that exists does not.
    """
    db = _seeded_db()

    raised: Optional[_UndefinedColumn] = None
    try:
        db.table("strategies").select("id, user_id, tenant_id, archived_at")
    except _UndefinedColumn as exc:
        raised = exc
    assert raised is not None, "the double accepted a column production does not have"
    assert raised.code == "42703"
    assert "strategies.tenant_id" in raised.message

    rows = db.table("strategies").select("id, user_id, archived_at").execute().data
    assert rows and rows[0]["user_id"] == OWNER_ID
    assert "tenant_id" not in rows[0], (
        "a projection that does not name tenant_id must not return it - otherwise the gate "
        "could read a column it never selected and these tests would be vacuous"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE AFTER STATE - THE THREE CALLERS
# ══════════════════════════════════════════════════════════════════════════


def test_a_caller_with_tenant_context_gets_a_verdict_and_passes_the_tenant_clause() -> None:
    """Case 1: an HTTP caller, whose ``tenant_id`` ``dependencies.py`` filled with their own id.

    The ordinary production path. The tenant clause passes because the row's tenant IS its
    ``user_id`` under tenant == user, and a real verdict is reached - the outcome tuple is
    populated, which it never was while the defect stood.

    **Validates: Requirements 2.2, 21.1**
    """
    verdict = _evaluate(_seeded_db(), {"id": OWNER_ID, "tenant_id": OWNER_ID})

    assert verdict.unevaluable is False
    assert verdict.outcomes, "a completed evaluation must carry per-criterion outcomes"

    tenant = _outcome(verdict, eligibility_gate.MP_TENANT)
    owner = _outcome(verdict, eligibility_gate.MP_OWNERSHIP)
    assert tenant is not None and tenant.passed is True
    assert owner is not None and owner.passed is True


def test_a_caller_without_tenant_context_is_not_refused_on_tenant() -> None:
    """Case 2: the trap. A caller carrying no tenant - internal, or a token with no claim.

    Under tenant == user the strategy ALWAYS has a tenant (its owner), so the pre-13.18
    ``_tenant_matches`` rule - exactly one side absent means no match - would have refused this
    caller unconditionally. Swapping the column without touching that rule would have turned
    "always unevaluable" into "always refused", which is quieter and worse. Pinned here as a
    verdict, not as a unit of ``_tenant_matches``.

    **Validates: Requirements 2.2**
    """
    verdict = _evaluate(_seeded_db(), {"id": OWNER_ID, "tenant_id": None})

    assert verdict.unevaluable is False
    tenant = _outcome(verdict, eligibility_gate.MP_TENANT)
    owner = _outcome(verdict, eligibility_gate.MP_OWNERSHIP)
    assert tenant is not None and tenant.passed is True, (
        "a caller with no tenant context must not be refused on the tenant clause: read 1 "
        "already filtered .eq('user_id', caller_id), so the row in hand is the caller's own"
    )
    assert owner is not None and owner.passed is True


def test_someone_elses_strategy_is_refused_rather_than_unevaluable() -> None:
    """Case 3: a caller who does not own the strategy. A REFUSAL, and a legible one.

    Read 1's ``.eq("user_id", caller_id)`` returns no row, so the strategy is indistinguishable
    from one that does not exist and both ownership and tenant fail. The distinction that
    matters: this is ``unevaluable=False`` with named failures - the gate decided - where the
    defect produced the same ultimate refusal with no reason attached.

    **Validates: Requirements 2.2**
    """
    verdict = _evaluate(_seeded_db(), {"id": STRANGER_ID, "tenant_id": STRANGER_ID})

    assert verdict.unevaluable is False
    assert verdict.admitted is False
    tenant = _outcome(verdict, eligibility_gate.MP_TENANT)
    owner = _outcome(verdict, eligibility_gate.MP_OWNERSHIP)
    assert tenant is not None and tenant.passed is False
    assert owner is not None and owner.passed is False, (
        "the tenant clause must not pass vacuously for a strategy the caller cannot see"
    )


def test_the_tenant_seam_reads_the_owner_column() -> None:
    """``_strategy_tenant`` is the one place "the owner column IS the tenant column" lives.

    Pinned directly so a future real ``tenant_id`` column has exactly one function to change,
    and so the seam cannot be quietly inlined back into :func:`evaluate`.

    **Validates: Requirements 2.2**
    """
    assert eligibility_gate._strategy_tenant({"user_id": OWNER_ID}) == OWNER_ID
    assert eligibility_gate._strategy_tenant(None) is None
    assert eligibility_gate._strategy_tenant({}) is None


def test_a_foreign_tenant_claim_still_refuses() -> None:
    """A token carrying a tenant that is NOT the caller's id refuses, and that is deliberate.

    ``dependencies.py`` only falls back to ``user_id``; a token with a real ``tenant_id`` claim
    keeps it. Under tenant == user such a caller matches no strategy of their own, so this
    refuses. Recorded as the gate's actual behaviour rather than asserted to be unreachable: if
    the platform ever issues a distinct tenant claim, THIS test is the one that will have to
    change, and it names what the change means.

    **Validates: Requirements 2.2, 21.1**
    """
    verdict = _evaluate(_seeded_db(), {"id": OWNER_ID, "tenant_id": FOREIGN_TENANT_ID})

    assert verdict.unevaluable is False
    tenant = _outcome(verdict, eligibility_gate.MP_TENANT)
    owner = _outcome(verdict, eligibility_gate.MP_OWNERSHIP)
    assert owner is not None and owner.passed is True
    assert tenant is not None and tenant.passed is False
