"""Unit tests for ``marketplace/aliases.py`` (task 8.2, Requirements 6.5, 27.1, 28.5, 1.5).

These pin the four behaviours the task asks for: the read is batched into one round trip
whatever the row count, the projection never asks for ``email``, an unreadable alias is an
absent key rather than a fabricated ``"Anonymous"``, and a failed read raises
``MarketplaceError(MARKETPLACE_READ_FAILED)`` instead of answering with a populated map.

The round-trip constant across generated row counts is asserted as property P-57 in
``tests/property/test_fixed_round_trips.py`` (task 33.12); here it is one worked example.
"""

from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.marketplace.aliases import (
    ALIAS_SELECT,
    PROFILES_TABLE,
    resolve_aliases,
)
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_READ_FAILED,
    MarketplaceError,
)


# ─── A counting fake of the ``table().select().in_().execute()`` chain ────────────

#: "answer from ``rows``" as distinct from "answer with this literal ``data``, which may be
#: ``None``" - the two cases have different expected outcomes and cannot share a default.
_UNSET = object()


class _Response:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeTable:
    def __init__(self, client: "_FakeClient") -> None:
        self._client = client

    def select(self, columns: str) -> "_FakeTable":
        self._client.selects.append(columns)
        return self

    def in_(self, column: str, values: List[str]) -> "_FakeTable":
        self._client.filters.append((column, list(values)))
        return self

    def execute(self) -> _Response:
        self._client.round_trips += 1
        if self._client.raises is not None:
            raise self._client.raises
        requested = self._client.filters[-1][1] if self._client.filters else []
        if self._client.data is not _UNSET:
            return _Response(self._client.data)
        return _Response(
            [
                {"id": row_id, "display_name": self._client.rows[row_id]}
                for row_id in requested
                if row_id in self._client.rows
            ]
        )


class _FakeClient:
    """Records every table, projection, filter and round trip it is asked for."""

    def __init__(
        self,
        rows: Optional[Dict[str, Any]] = None,
        raises: Optional[Exception] = None,
        data: Any = _UNSET,
    ) -> None:
        self.rows: Dict[str, Any] = dict(rows or {})
        self.raises = raises
        self.data = data
        self.tables: List[str] = []
        self.selects: List[str] = []
        self.filters: List[Any] = []
        self.round_trips = 0

    def table(self, name: str) -> _FakeTable:
        self.tables.append(name)
        return _FakeTable(self)


# ─── Requirement 27.1: one round trip for a whole page ───────────────────────────


class TestBatching:
    def test_fifty_authors_cost_one_round_trip(self):
        rows = {f"author-{n}": f"Creator {n}" for n in range(50)}
        client = _FakeClient(rows=rows)

        aliases = resolve_aliases(list(rows), client)

        assert client.round_trips == 1
        assert client.tables == [PROFILES_TABLE]
        assert aliases == rows

    def test_duplicate_and_blank_ids_are_collapsed_into_one_filter(self):
        client = _FakeClient(rows={"a": "Ada", "b": "Bo"})

        aliases = resolve_aliases(["a", "a", " b ", "", "   ", None, 7], client)  # type: ignore[list-item]

        assert client.round_trips == 1
        assert client.filters == [("id", ["a", "b"])]
        assert aliases == {"a": "Ada", "b": "Bo"}

    def test_no_author_ids_performs_no_read(self):
        client = _FakeClient()

        assert resolve_aliases([], client) == {}
        assert resolve_aliases(["", "  ", None], client) == {}  # type: ignore[list-item]
        assert client.round_trips == 0


# ─── Requirement 6.5: an alias, never an email or an identity ────────────────────


class TestProjection:
    def test_the_projection_is_id_and_display_name_only(self):
        client = _FakeClient(rows={"a": "Ada"})

        resolve_aliases(["a"], client)

        assert client.selects == [ALIAS_SELECT]
        assert "email" not in ALIAS_SELECT
        assert ALIAS_SELECT == "id,display_name"


# ─── Requirement 28.5: omission, never substitution ──────────────────────────────


class TestUnreadableAliasIsOmitted:
    def test_an_author_with_no_profile_row_is_absent_from_the_map(self):
        client = _FakeClient(rows={"a": "Ada"})

        aliases = resolve_aliases(["a", "missing"], client)

        assert aliases == {"a": "Ada"}
        assert "missing" not in aliases

    @pytest.mark.parametrize("display_name", [None, "", "   ", 42, {"x": 1}])
    def test_a_row_without_visible_text_is_absent_rather_than_anonymous(
        self, display_name
    ):
        client = _FakeClient(rows={"a": display_name})

        aliases = resolve_aliases(["a"], client)

        assert aliases == {}
        assert "Anonymous" not in aliases.values()

    def test_a_readable_alias_is_returned_stripped(self):
        client = _FakeClient(rows={"a": "  Ada Lovelace  "})

        assert resolve_aliases(["a"], client) == {"a": "Ada Lovelace"}


# ─── Requirements 1.5 and 1.7: a failed read is an error, not a populated map ─────


class TestFailedReadRaises:
    def test_a_raising_read_becomes_marketplace_read_failed_at_503(self):
        client = _FakeClient(rows={"a": "Ada"}, raises=RuntimeError("connection reset"))

        with pytest.raises(MarketplaceError) as caught:
            resolve_aliases(["a"], client)

        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.http_status == 503
        assert isinstance(caught.value.__cause__, RuntimeError)

    def test_the_error_body_carries_no_driver_text(self):
        client = _FakeClient(
            rows={"a": "Ada"},
            raises=RuntimeError('SELECT failed on profiles: psycopg2 42703'),
        )

        with pytest.raises(MarketplaceError) as caught:
            resolve_aliases(["a"], client)

        body = repr(caught.value.to_error_object())
        assert "psycopg2" not in body
        assert "42703" not in body

    @pytest.mark.parametrize("data", [None, {"id": "a"}, "rows", 0])
    def test_an_uninterpretable_answer_is_a_500_not_an_empty_map(self, data):
        client = _FakeClient(data=data)

        with pytest.raises(MarketplaceError) as caught:
            resolve_aliases(["a"], client)

        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.http_status == 500

    def test_a_row_of_the_wrong_shape_is_a_500(self):
        client = _FakeClient(data=[{"display_name": "Ada"}])

        with pytest.raises(MarketplaceError) as caught:
            resolve_aliases(["a"], client)

        assert caught.value.http_status == 500
