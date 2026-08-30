"""
backend_app/backend/marketplace/aliases.py - the one batched creator-alias read.

Spec: marketplace-subscriptions-paper-trading task 8.2. ``design.md`` -> "Performance" ->
"The N+1 being removed", and ``design.md`` -> "Never-swallow rule" (the ``_get_author_alias``
bullet). Requirements 6.5, 27.1, 28.5, 1.5.

Exposes
-------
PROFILES_TABLE          ``"profiles"`` - the one table an alias is read from
ALIAS_SELECT            ``"id,display_name"`` - the explicit projection, and deliberately
                        **not** ``email``
resolve_aliases(author_ids, client) -> Dict[str, str]
                        one batched read for a whole page of Listings; an id with no readable
                        alias is **absent from the map**, never carried as a substitute

WHAT THIS REPLACES
------------------
``backend_app/routers/library.py::_get_author_alias(user_id)`` (line 234) issues one
``profiles.select("display_name, email").eq("id", user_id).single()`` per call, and
``browse_library``, ``get_featured_strategies``, ``get_trending_strategies``,
``get_creator_profile``, ``compare_strategies``, ``get_recommendations`` and
``get_user_favorites`` call it once per row: a 50-Listing page costs 51 round trips today.
Requirement 27.1 fixes the round trips for a catalogue page independently of the row count, so
the per-row read becomes one ``.in_("id", author_ids)`` for the whole page and
``listing_projection.project_listing`` receives the resolved map rather than a callback it
would fan out over. Task 16.2 repoints the call sites; this module is only the read.

WHY A MISSING ALIAS IS AN ABSENT KEY AND NEVER ``"Anonymous"``
-------------------------------------------------------------
The old helper answered ``"Anonymous"`` for three different situations: the profile row does
not exist, the row exists but carries no ``display_name``, and *the read failed*. The third is
the substitution Requirement 28.5 forbids - a value required by the response was unavailable,
so it must be omitted or reported unavailable, not replaced by a plausible-looking constant -
and Requirement 1.5 adds that a failed read owes the caller a structured error with a 500 or
503 status rather than a populated success body. So the two cases are split here:

* **the batched read did not complete** -> ``MarketplaceError(MARKETPLACE_READ_FAILED)``, and
  no map is returned at all, so no endpoint can render a page of fabricated creators;
* **the read completed and an id has no usable alias** -> that id is simply not a key in the
  returned map. The projection layer then omits the creator field for that Listing, exactly as
  Requirement 6.9 has it omit ``avg_rating`` when no rating exists. An absent key and a present
  key mean different things, which is the whole point of Requirement 28.5.

Callers therefore use ``alias_by_author_id.get(author_id)`` and must handle ``None`` by
omitting the field - never by inserting a placeholder of their own.

WHY ``email`` IS NOT IN THE PROJECTION
--------------------------------------
Requirement 6.5 says the creator is represented by a server-resolved display alias and that
the response carries neither the creator's user identifier nor their email address nor their
authentication identity. The old helper's ``resp.data.get("email", "Anonymous")`` fallback put
a creator's email address into a public catalogue body whenever ``display_name`` was null, so
the column is not selected here at all: a value that is never read cannot be leaked by a later
edit to the projection.

WHY THE CLIENT IS AN ARGUMENT
-----------------------------
The rest of this package imports only the standard library, and this module keeps as much of
that as a read can: it imports no FastAPI, no supabase, no settings module and holds no
connection. The caller passes the service-role handle it already has
(``library.py::_get_service_client()``), which is what lets task 33.12's counting ``FakeDB``
wrapper measure the round trips this function actually performs - P-57 tests the *absence* of
an N+1, and that is only decidable by counting.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* **Chunking.** Splitting the id list into several ``in_`` calls would make the round-trip
  count grow with the row count, which is the property Requirement 27.1 forbids. A catalogue
  page is capped at 50 Listings (``design.md`` -> "Bounds"), so one filter covers a page.
* **Caching.** A cached alias is a value not read from persisted data on the request that
  serves it (Requirement 28.2), and a stale one is a wrong name on a paid listing.
* **A fallback of any kind.** See above.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from backend_app.backend.marketplace.errors import (
    MARKETPLACE_READ_FAILED,
    MarketplaceError,
)

__all__ = [
    "ALIAS_SELECT",
    "PROFILES_TABLE",
    "resolve_aliases",
]


#: The single table a creator alias is read from.
PROFILES_TABLE = "profiles"

#: The explicit projection. ``id`` so the rows can be keyed back to the Listings that asked for
#: them, ``display_name`` because that is the alias, and nothing else - see the module docstring
#: on ``email``.
ALIAS_SELECT = "id,display_name"


def _normalised_ids(author_ids: Iterable[str]) -> List[str]:
    """The ids worth asking about: non-blank strings, de-duplicated, order preserved.

    Order is preserved rather than sorted so that a failing generated example in a property
    test replays as the caller produced it. Blank and non-string entries are dropped instead of
    raising: a Listing row with a null ``author_id`` is a data defect for the projection layer
    to omit a creator for, not a reason to fail the whole catalogue page - and sending ``""``
    into an ``in_`` filter would only widen the read.
    """
    seen: Dict[str, None] = {}
    for raw in author_ids:
        if not isinstance(raw, str):
            continue
        author_id = raw.strip()
        if not author_id:
            continue
        seen.setdefault(author_id, None)
    return list(seen)


def _read_failed(reason: str, http_status: int, requested: int) -> MarketplaceError:
    """The one error this module raises.

    ``details`` carries the caller's own shape - how many ids were asked about and which
    internal step did not complete - and never the driver's message, whose text is precisely
    what Requirement 22.9's deny-list exists to keep out of a client body. The raiser logs the
    original exception; the body says only that the read did not happen.
    """
    return MarketplaceError(
        MARKETPLACE_READ_FAILED,
        http_status=http_status,
        details={"resource": "creator_aliases", "reason": reason, "requested": requested},
    )


def resolve_aliases(author_ids: Iterable[str], client: Any) -> Dict[str, str]:
    """Map each author id that has a readable display alias to that alias, in one read.

    Parameters
    ----------
    author_ids:
        The author ids of the rows being projected. May contain duplicates, blanks and
        ``None``; those cost nothing and are dropped. An empty result performs **no** read.
    client:
        Anything exposing the platform's usual
        ``client.table(name).select(cols).in_(col, values).execute()`` chain whose result
        carries ``.data``. Passed in rather than constructed here, so this module holds no
        connection and a counting fake can measure the round trips (P-57).

    Returns
    -------
    Dict[str, str]
        One entry per author id whose ``profiles`` row was read **and** carries a
        display name with visible text. An id with no profile row, or a row whose
        ``display_name`` is null or blank, is **absent** from the mapping - callers omit the
        creator field for it rather than substituting anything (Requirement 28.5).

    Raises
    ------
    MarketplaceError
        ``MARKETPLACE_READ_FAILED``, at 503 when the read itself did not complete and at 500
        when it answered in a shape this function cannot interpret. Either way no mapping is
        returned, because a partial mapping is indistinguishable to a caller from a complete
        one in which some creators genuinely have no alias (Requirements 1.5, 1.7).
    """
    wanted = _normalised_ids(author_ids)
    if not wanted:
        # Zero ids is zero round trips, not an unfiltered read of every profile on the
        # platform. An empty page legitimately needs no alias.
        return {}

    try:
        response = (
            client.table(PROFILES_TABLE)
            .select(ALIAS_SELECT)
            .in_("id", wanted)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as the catalogue's structured error
        raise _read_failed("read_failed", 503, len(wanted)) from exc

    if response is None:
        raise _read_failed("no_response", 500, len(wanted))

    rows = getattr(response, "data", None)
    if rows is None and isinstance(response, Mapping):
        rows = response.get("data")
    if not isinstance(rows, list):
        # ``None`` here is not "no profiles matched" - PostgREST answers a list for that. It
        # means the read did not produce rows, and treating it as an empty page would hand
        # every caller an aliasless catalogue that looks exactly like a successful one.
        raise _read_failed("unreadable_response", 500, len(wanted))

    aliases: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise _read_failed("unreadable_row", 500, len(wanted))
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id.strip():
            raise _read_failed("unreadable_row", 500, len(wanted))
        row_id = row_id.strip()
        if row_id in aliases:
            continue
        display_name = row.get("display_name")
        if not isinstance(display_name, str):
            # Null, or something that is not text. No alias is readable, so no key is written.
            continue
        display_name = display_name.strip()
        if not display_name:
            continue
        aliases[row_id] = display_name
    return aliases
