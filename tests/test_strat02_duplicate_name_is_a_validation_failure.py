"""STRAT-02: a duplicate strategy name is a 409, not a 500.

WHAT WAS BROKEN
---------------
Migration 068 added the constraint that makes duplicate names impossible:

    CREATE UNIQUE INDEX uq_strategies_owner_name_live
        ON strategies (user_id, lower(name)) WHERE archived_at IS NULL

Nothing translated its violation. `routers/strategies.py` had no `23505` handling at
all -- grep found the SQLSTATE nowhere in that file or in `strategy_service.py` -- so the
one case the constraint exists to catch reached the author as:

    POST /api/strategies        500  {"error": "STRATEGY_CREATE_ERROR", "message": <raw>}
    PUT  .../{id}/rename        503  "Unable to rename strategy. Please try again later."
    POST .../{id}/clone         500  "Database error cloning strategy. Please try again later."

All three are wrong in the same way, and the rename and clone messages are wrong in a
second way as well: they advise retrying, and a retry proposes the same name and hits the
same constraint every time. T05 asks for "clear validation when an existing name is
entered".

WHY NOT A LOOKUP BEFORE THE WRITE
---------------------------------
A read-then-write cannot be correct here. Two concurrent saves both read "no such name"
and both proceed, which is also the T05 requirement that concurrent saves must not
silently overwrite one another. The constraint is the only thing that holds under
concurrency, so the write is attempted and the refusal is classified -- the 409 is then
produced by the same mechanism that guarantees it, rather than by a check that can be
raced.

THE CLONE CASE IS DIFFERENT, AND IS NOT A 409 ON THE FIRST COLLISION
--------------------------------------------------------------------
`clone_strategy` names the copy "<original> (Copy)" unconditionally, so the SECOND clone
of one strategy always collided. T05 requires that duplicating create a new identity, and
it does -- the obstacle was only the label. So the label advances: "(Copy)", "(Copy 2)",
"(Copy 3)", bounded, and only an exhausted window answers 409.
"""
from __future__ import annotations

import pathlib

import pytest

from backend_app.routers import strategies as strategies_router

REPO = pathlib.Path(__file__).resolve().parents[1]


class _PgError(Exception):
    """A unique-violation shaped like the ones PostgREST and psycopg surface."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _owner_name_violation() -> _PgError:
    return _PgError(
        'duplicate key value violates unique constraint '
        '"uq_strategies_owner_name_live"',
        code="23505",
    )


class TestTheClassifier:
    def test_the_owner_name_violation_is_recognised(self):
        assert strategies_router._is_duplicate_strategy_name(_owner_name_violation())

    def test_the_sqlstate_alone_is_not_enough(self):
        """`strategies` has a primary key and may gain other unique indexes.

        Answering "that name is taken" for an unrelated uniqueness violation would be a
        confident wrong answer.
        """
        other = _PgError(
            'duplicate key value violates unique constraint "strategies_pkey"',
            code="23505",
        )
        assert not strategies_router._is_duplicate_strategy_name(other)

    def test_the_constraint_name_alone_is_not_enough(self):
        """A stray mention of the index name must not trigger it without the SQLSTATE."""
        mention = _PgError(
            "could not create uq_strategies_owner_name_live: disk full", code="53100"
        )
        assert not strategies_router._is_duplicate_strategy_name(mention)

    def test_an_unrelated_error_is_not_recognised(self):
        assert not strategies_router._is_duplicate_strategy_name(
            Exception("connection refused")
        )

    def test_the_classifier_reads_pgcode_as_well_as_code(self):
        exc = Exception(
            'duplicate key value violates unique constraint '
            '"uq_strategies_owner_name_live"'
        )
        exc.pgcode = "23505"  # type: ignore[attr-defined]
        assert strategies_router._is_duplicate_strategy_name(exc)


class TestTheRefusal:
    def test_a_duplicate_name_raises_409_naming_the_field(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            strategies_router._raise_if_duplicate_strategy_name(
                _owner_name_violation(), "Momentum v2"
            )
        exc = caught.value
        assert exc.status_code == 409, (
            "a name the owner already uses is a validation failure, not a server fault"
        )
        assert exc.detail["error"] == "STRATEGY_NAME_ALREADY_EXISTS"
        assert exc.detail["field"] == "name"
        assert "Momentum v2" in exc.detail["message"]
        # The remedy has to be something the author can act on.
        assert "different name" in exc.detail["message"]

    def test_an_unrelated_error_passes_through_untouched(self):
        """The classifier must not swallow a real fault by answering 409 for it."""
        strategies_router._raise_if_duplicate_strategy_name(
            Exception("connection refused"), "anything"
        )  # returns, raises nothing

    def test_the_message_survives_a_missing_name(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            strategies_router._raise_if_duplicate_strategy_name(_owner_name_violation())
        assert "already have a strategy named" in caught.value.detail["message"]


class TestEveryWritePathIsCovered:
    """The three handlers that can hit this constraint all consult the classifier.

    Asserted structurally because the failure mode is a path that was MISSED, not a path
    that was implemented wrongly -- which is exactly how this shipped: the constraint
    landed in 068 and no handler learned about it.
    """

    @pytest.mark.parametrize(
        "handler",
        ["create_strategy", "rename_strategy", "clone_strategy"],
    )
    def test_the_handler_classifies_the_violation(self, handler: str):
        import ast
        import inspect

        source = inspect.getsource(getattr(strategies_router, handler))
        tree = ast.parse(inspect.cleandoc(source).replace("@router", "# @router"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called & {
            "_raise_if_duplicate_strategy_name", "_is_duplicate_strategy_name"
        }, (
            f"{handler} writes a strategy name and never classifies a uniqueness "
            "violation, so a duplicate name surfaces as a server fault. Call "
            "_raise_if_duplicate_strategy_name in its except branch."
        )


class TestCloneAdvancesTheLabel:
    def test_the_attempt_window_is_bounded(self):
        assert isinstance(strategies_router._CLONE_NAME_ATTEMPTS, int)
        assert 1 < strategies_router._CLONE_NAME_ATTEMPTS <= 25, (
            "unbounded retries would turn a taken name into a slow request; a very "
            "large bound turns it into a slower one"
        )

    def test_the_clone_handler_advances_the_name_rather_than_failing_once(self):
        """The second clone of one strategy must succeed, not 500.

        Pinned on the source because the alternative -- standing up a fake PostgREST that
        enforces a partial unique index on `lower(name)` -- would be testing the fake.
        """
        import inspect

        source = inspect.getsource(strategies_router.clone_strategy)
        assert "_CLONE_NAME_ATTEMPTS" in source, (
            "clone no longer advances the copy's label, so a second clone of the same "
            "strategy collides on uq_strategies_owner_name_live"
        )
        assert "continue" in source, (
            "clone does not retry on a taken name -- it must advance the label, not "
            "refuse on the first collision"
        )


class TestTheConstraintItIsTranslating:
    def test_migration_068_declares_the_index_this_code_names(self):
        """If the index is ever renamed, the classifier stops matching and every
        duplicate name silently becomes a 500 again."""
        sql = (REPO / "backend_app" / "migrations"
               / "068_strat01_strategy_name_uniqueness.sql").read_text(encoding="utf-8")
        assert strategies_router._OWNER_NAME_INDEX in sql, (
            f"{strategies_router._OWNER_NAME_INDEX} is not declared by migration 068 any "
            "more. The classifier matches on that exact name, so renaming the index "
            "without updating _OWNER_NAME_INDEX returns 500 for every duplicate name."
        )
        assert "user_id, lower(name)" in sql, (
            "the index is no longer per-owner and case-insensitive, so the 409 message "
            "('you already have a strategy named X') may no longer be accurate"
        )
