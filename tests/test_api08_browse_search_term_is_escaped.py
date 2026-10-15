"""tests/test_api08_browse_search_term_is_escaped.py

API-08 (P3) -- ``GET /api/library``'s ``q`` (search) parameter used to be interpolated
raw into a PostgREST ``or()`` filter string:
``query.or_(f"name.ilike.%{q}%,description.ilike.%{q}%")``. PostgREST's filter mini-
language treats ``,``, ``.``, ``(`` and ``)`` as syntax, so a search term containing any
of them could close the current clause and open a new one against an unrelated column,
rather than being matched as literal text.

``_postgrest_quoted_ilike_pattern`` is the extracted, directly-testable fix: it wraps the
term in double quotes (PostgREST's own escaping rule for a value containing reserved
characters), backslash-escaping ``\\`` and ``"`` themselves FIRST -- in that order,
deliberately -- so the quoting itself cannot be broken out of, then wraps the result in
the ``%...%`` ilike wildcards.

This file also exercises the second half of the same finding -- "paginate at the query
level" -- end to end through the real route, using the same ``FakeTable``/``patched_db``
double ``test_marketplace_pipeline.py`` uses, extended (this session) with ``.range()``
and ``count="exact"`` support.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.routers.library import _postgrest_quoted_ilike_pattern

client = TestClient(app, raise_server_exceptions=True)


def _unescape(escaped: str) -> str:
    """The inverse of the function under test, written independently so this file does
    not just assert "whatever the function produces" -- it asserts a real round trip.
    """
    out = []
    i = 0
    while i < len(escaped):
        if escaped[i] == "\\" and i + 1 < len(escaped):
            out.append(escaped[i + 1])
            i += 2
        else:
            out.append(escaped[i])
            i += 1
    return "".join(out)


class TestThePureEscapingFunction:
    def test_a_plain_term_is_wrapped_in_quotes_and_wildcards(self):
        assert _postgrest_quoted_ilike_pattern("momentum") == '"%momentum%"'

    @pytest.mark.parametrize("reserved_char", [",", ".", "(", ")"])
    def test_a_reserved_postgrest_character_round_trips_inside_the_quoting(self, reserved_char):
        term = f"evil{reserved_char}injected"
        result = _postgrest_quoted_ilike_pattern(term)
        assert result.startswith('"%')
        assert result.endswith('%"')
        inner_escaped = result[2:-2]  # strip this function's own `"%` / `%"`
        assert _unescape(inner_escaped) == term, (
            "the reserved character must still be present and recoverable, inside the "
            "quoting, not stripped or corrupted"
        )

    def test_a_literal_double_quote_round_trips_and_cannot_break_out_of_the_quoting(self):
        term = '","or","1","eq","1'  # an attempt to close the quote and inject a new clause
        result = _postgrest_quoted_ilike_pattern(term)
        inner_escaped = result[2:-2]
        # Every quote from the user's own term is backslash-escaped -- none stands alone.
        assert '"' not in inner_escaped.replace('\\"', "")
        assert _unescape(inner_escaped) == term

    def test_backslash_is_escaped_before_quotes_so_the_ordering_cannot_be_exploited(self):
        """If quotes were escaped before backslashes were doubled, a term ending in a
        backslash immediately before this function's closing quote-and-wildcard would
        have its escaping backslash itself escaped into an extra literal backslash,
        leaving the final `"` unescaped and able to close the quoting early. Doing
        backslashes first (as the fix does) avoids this.
        """
        term = 'trailing\\'  # one literal backslash
        result = _postgrest_quoted_ilike_pattern(term)
        inner_escaped = result[2:-2]
        assert _unescape(inner_escaped) == term
        # The pattern must still end with exactly the wildcard+quote this function adds,
        # not an unescaped backslash having eaten into it.
        assert result.endswith('%"')


class TestBrowseLibraryEndToEnd:
    """Through the real route, with FakeTable's or_() a no-op (it ignores filters
    entirely, same as every other filter method on that double) -- what this proves is
    that a search term containing reserved PostgREST characters does not crash the
    request, and that server-side pagination (range()/count="exact", API-08's other
    half) returns the right page and an accurate total.
    """

    def test_a_search_term_with_reserved_characters_does_not_error(self, patched_db):
        resp = client.get("/api/library", params={"q": 'evil",description.ilike.%'})
        assert resp.status_code == 200, resp.text

    def test_pagination_returns_the_requested_page_and_an_accurate_total(self, patched_db):
        from tests.test_marketplace_pipeline import _approved_lib_row, AUTHOR_ID

        for _ in range(5):
            patched_db.stores["library_strategies"].append(
                _approved_lib_row(str(uuid.uuid4()), AUTHOR_ID, str(uuid.uuid4()))
            )

        resp = client.get("/api/library", params={"page": 1, "limit": 2})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["limit"] == 2
        assert body["pages"] == 3


from tests.test_marketplace_pipeline import db, patched_db  # noqa: F401 -- reused fixtures; `db`'s own `profiles` seed is what alias resolution needs
