"""
tests/property/test_fixed_round_trips.py - P-57, the fixed round trips.

Spec: marketplace-subscriptions-paper-trading task 33.12. ``design.md`` -> "Performance" ->
"Fixed round trips" and "The N+1 being removed"; ``design.md`` -> "Property-to-test mapping"
(``P-57 | tests/property/test_fixed_round_trips.py | row counts 0...50 and 0...200 | a counting
FakeDB wrapper; assert the count is constant across all generated sizes``).
Requirements 27.1, 27.2.

THE PROPERTY LIVING HERE
-----------------------
``test_p57_round_trips_are_independent_of_row_count``   (Requirements 27.1, 27.2)

Exactly one ``test_p{n}_`` function at module scope, and nothing nested carries that prefix:
``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and would count a nested
helper as a second property. Everything else in this file is either a rider that pins the
mechanism the property leans on, or the counting store itself.

WHAT IS COUNTED, AND WHY IT IS STATEMENTS AND NOT ROWS
-----------------------------------------------------
**Statements issued, never rows returned.** A page of fifty Listings and a page of none must cost
the same number of Persistence_Layer statements; how many rows come back is precisely what
Requirement 27.1 says the count may not depend on. Every statement in this repository passes
through exactly one place - ``FakeSupabase._Query.execute`` calls ``client._execute(self)`` - so
:class:`CountingStore` overrides ``_execute`` and nothing else. That is the same boundary
``backend_app/backend/paper/paper_repository.py`` counts a round trip at.

THE CONSTANTS, AND WHERE THEY COME FROM
---------------------------------------
A property that only asserted "the count is constant" would let a regression from three
statements to thirty pass, as long as it was uniformly thirty. So the constants are PINNED, and
they are not literals this file chose: :func:`_design_round_trips` reads them out of
``design.md``'s own "Fixed round trips" table, and
:func:`test_the_pinned_constants_are_the_designs_own` fails if the two disagree.

===============================================  =====  ==========================================
Response                                             K  The statements, in the order they issue
===============================================  =====  ==========================================
``GET /api/library``, page returning >=1 Listing     2  ``library_strategies``, ``profiles``
``GET /api/library``, page returning no Listing     1  ``library_strategies``
``GET /api/library`` + authenticated caller         4  the two above, then ``library_strategies``
                                                       (the caller's clones) and
                                                       ``library_ratings`` (the caller's ratings)
``GET /api/library/my-strategies``                  3  ``strategies``, ``library_subscriptions``,
                                                       ``paper_sessions``
===============================================  =====  ==========================================

The count is asserted as the ordered TABLE SEQUENCE, not only as a total: two statements against
the wrong two tables is not the shape Requirement 27.1 describes, and a total alone would accept
it.

WHY THE EMPTY CATALOGUE PAGE COSTS ONE AND NOT TWO
--------------------------------------------------
``marketplace/aliases.py::resolve_aliases`` returns ``{}`` **without reading** when the page
carries no author ids - "Zero ids is zero round trips, not an unfiltered read of every profile on
the platform", stated in its own docstring and deliberate. So an empty catalogue page costs one
statement and a non-empty one costs two, and that single step is a dependency on whether any row
came back at all.

This file does NOT force that to two. It pins the invariant that actually holds, which is the one
Requirement 27.1 is about and the one an N+1 breaks:

* the count is **2** for every page returning between 1 and 50 Listings - it does not grow with
  the Listing count, and it does not grow with the number of DISTINCT AUTHORS on the page either;
* the count is **1** for a page returning none, and it is never more than 2 anywhere in the
  generated space, so the empty case cannot hide an extra read;
* the alias resolution is **one** statement on ``profiles`` whose only predicate is a single
  ``in_("id", ...)`` carrying every distinct author of the page at once. That is the specific
  shape task 16.2 put in place of ``_get_author_alias``'s per-row
  ``profiles...eq("id", user_id).single()``, which made a 50-Listing page cost 51 round trips
  (``design.md`` -> "The N+1 being removed"). A per-row alias read would fail the count, the
  sequence AND this predicate assertion, in that order.

WHAT THE PROPERTY ALSO ASSERTS, SO IT CANNOT PASS ON AN ERROR PATH
-----------------------------------------------------------------
A 503 from ``browse_library`` also costs two statements. So every response is asserted to be a
**200 carrying the right number of rendered entries** before its statement count is asserted:
``len(items) == min(listing_count, 50)`` for the catalogue and
``total == owned + subscribed`` for the Strategies_Page. Without that, a broken projection would
read as fixed round trips.

WHAT IS DRIVEN, AND WHAT IS DOUBLED
-----------------------------------
Real: the actual ``backend_app.main.app``, the real ``browse_library`` and ``my_strategies``
handlers, ``marketplace/aliases.py::resolve_aliases``, ``listing_projection.project_listing`` and
``marketplace/library_entries.py``'s whole derivation.

Doubled: only the Persistence_Layer, through :class:`CountingStore`, which subclasses
``tests/property/test_tenant_isolation_matrix.MatrixStore`` - itself a subclass of
``tests/test_paper_repository.FakeSupabase``. ``tests/paper_seed.py`` records the rule this
repository holds to: there is exactly ONE Persistence_Layer double, and a second would be a
second set of assumptions about the database. Nothing in ``MatrixStore`` is modified; the harness
is ``MatrixStore``'s own ``_matrix_app``, imported rather than rebuilt, so the seams are the four
that file already documents (the identity dependency, the three persistence accessors, the
never-caching ``redis_manager`` and the subscription-plan gates) plus the rate limiter's
suspension. One additional seam, used for exactly one of the three requests and named where it is
applied: ``library.py::_user_id_from_credentials``, the pure bearer-token decode, so the
AUTHENTICATED catalogue page can be measured without minting a token. It performs no I/O, so
replacing it moves no statement off the count.

The row shapes are ``tests/test_my_strategies_ownership_and_actions.py``'s
(``_embedded_listing_row``, ``_subscription_row``, ``_owned_row``), imported for the same reason
the store is: that file is task 17.4's, its rows are the proven shapes for these two endpoints -
every column ``listing_projection.LISTING_SELECT`` requests, every denied column, and every
Protected_Logic document - and a second set of row literals here would be a second opinion about
what a Listing row looks like.

THE COUNTING-STORE API TASK 33.11 CONSUMES
------------------------------------------
Task 33.11 (P-46, list scoping) needs to show that "no list endpoint retrieved a row it then
filtered out". :class:`CountingStore` is the instrument, and this is its whole surface:

``store.reset()``                     empty every table and discard the log; one store per run
``store.round_trips``                 the ``list`` of :class:`Statement`, in issue order
``store.log()``                       the whole log as a :class:`StatementLog`
``store.window(start)``               the log from index ``start`` - what one request cost
``StatementLog.count``                how many statements
``StatementLog.tables()``             their table names, in order
``StatementLog.on(table)``            the statements against one table
``StatementLog.rows_retrieved(t)``    every row every select on ``t`` handed back
``StatementLog.filters_on(t)``        their predicates, as ``(operator, column, value)``
``StatementLog.filter_values(t, c)``  the values ``t`` was filtered by on column ``c``
``StatementLog.describe()``           the log as text, for a failure message

A row is "retrieved and then filtered out" exactly when it is in ``rows_retrieved(table)`` and
not in the response. Two cautions, both pinned by riders below rather than left for the next
author to discover:

1. ``browse_library`` applies **no server-side range**. It reads every row matching the public
   filter and slices the page in Python, so with more matching Listings than fit on the page it
   retrieves rows it then drops - a PAGINATION drop, not an ownership drop.
   :func:`test_the_catalogue_slices_its_page_in_python_not_in_the_query` pins that exactly (three
   Listings, ``limit=1`` -> three rows retrieved, one rendered). It costs no extra statement, so
   P-57 holds either way, but a list-scoping property that compared retrieved rows to rendered
   rows without allowing for it would report a leak that is not one.
2. ``my_strategies`` drops nothing: all three of its statements carry ``user_id = caller`` as a
   predicate, which the property asserts on every example through ``filter_values``. That is the
   "the scoping is in the query" fact, produced by this store, in the shape 33.11 can read.

WHAT THIS FILE DOES NOT ESTABLISH
---------------------------------
1. **No latency is measured.** Requirement 27.6's measurements are the observability task's;
   counting a dictionary lookup's duration here would be timing the double.
2. **PostgREST's embedding is not executed.** Round trip 2 of the Strategies_Page arrives as
   ``library_subscriptions`` rows each carrying an embedded ``library_strategies`` object, which
   is what makes it ONE statement; the double returns whatever the row holds, so the embed is
   seeded onto the row rather than joined. That the query asks for the embed at all is
   ``tests/test_my_strategies_ownership_and_actions.py``'s assertion on
   ``library_entries.SUBSCRIPTION_SELECT``, and the rider below re-checks it here, because "one
   statement" would otherwise be one statement returning half an answer.
3. **The Redis browse cache is not exercised.** It is stubbed to never cache, so every catalogue
   measurement is the cache-MISS path - the expensive one, and the only one that reads the
   database at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from backend_app.backend.marketplace import aliases as _aliases
from backend_app.backend.marketplace import library_entries as _library_entries
from backend_app.routers import library as library_router

# ── The census, written once for the property modules of this spec. ───────────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── THE Persistence_Layer double of this repository, and task 33.1's harness. ─────────────
# ``MatrixStore`` is subclassed, not copied, and ``_matrix_app`` is imported, not rebuilt. See
# this module's docstring on why there is exactly one double and one harness.
from tests.property.test_tenant_isolation_matrix import (
    EXAMPLES,
    PROPERTY_SETTINGS,
    U1,
    MatrixStore,
    _matrix_app,
)

# ── Task 17.4's proven row shapes for exactly these two endpoints. ────────────────────────
from tests.test_my_strategies_ownership_and_actions import (
    _embedded_listing_row,
    _owned_row,
    _subscription_row,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_DOC = (
    REPO_ROOT
    / ".kiro"
    / "specs"
    / "marketplace-subscriptions-paper-trading"
    / "design.md"
)

#: The authenticated caller. The matrix's first tenant, so this file introduces no third identity;
#: ``library.py::_safe_uuid`` answers 422 for anything that is not a well-formed UUID.
CALLER = U1

#: The catalogue's own bound (``design.md`` -> "Bounds": existing 1-50, default 20). Every
#: catalogue measurement asks for the largest page the API permits, so the generated Listing count
#: and the number of rows the page RETURNS are the same number for 0...50.
CATALOGUE_PAGE_LIMIT = 50

#: The quantification of P-57: Listing counts 0...50, combined owned-and-subscribed entries 0...200.
MAX_LISTINGS = 50
MAX_ENTRIES = 200


# ══════════════════════════════════════════════════════════════════════════
# THE PINNED CONSTANTS - READ OUT OF THE DESIGN, NOT CHOSEN HERE
# ══════════════════════════════════════════════════════════════════════════


def _design_round_trips(response_label: str) -> int:
    """The round-trip count ``design.md``'s "Fixed round trips" table gives ``response_label``.

    The table's rows read ``| Catalogue page of <=50 Listings | **2** | ... |``. Reading the number
    out of the design rather than writing it here is what makes the pinned constant the design's
    figure: if the design is revised, this file follows it and
    :func:`test_the_pinned_constants_are_the_designs_own` is what fails, which is the point of
    pinning at all.
    """
    text = DESIGN_DOC.read_text(encoding="utf-8")
    pattern = (
        r"^\|\s*" + re.escape(response_label) + r"[^|]*\|\s*\*\*(?P<trips>\d+)\*\*\s*\|"
    )
    match = re.search(pattern, text, re.MULTILINE)
    assert match is not None, (
        f"design.md's 'Fixed round trips' table has no row starting {response_label!r} with a "
        f"bolded round-trip count; P-57's constants are read from that table and cannot be "
        f"derived without it"
    )
    return int(match.group("trips"))


#: ``GET /api/library`` for a page that returns at least one Listing: the paged
#: ``library_strategies`` select, then the one batched ``profiles`` read.
CATALOGUE_ROUND_TRIPS = 2

#: ``GET /api/library`` for a page that returns no Listing at all. One, not two: see the module
#: docstring - ``resolve_aliases`` performs no read for zero author ids, by design.
CATALOGUE_ROUND_TRIPS_FOR_AN_EMPTY_PAGE = 1

#: ``GET /api/library`` with an authenticated caller: the two above plus
#: ``_enrich_cards_with_user_context``'s two batched ``in_`` reads (the caller's clones and the
#: caller's own ratings). Both are per-PAGE, not per-card, which is the property being pinned.
CATALOGUE_ROUND_TRIPS_AUTHENTICATED = 4

#: ``GET /api/library/my-strategies``: ``strategies``, ``library_subscriptions`` (with the Listing
#: embedded in the same request) and ``paper_sessions``. Unconditional - all three issue whatever
#: the entry count, including zero.
STRATEGIES_ROUND_TRIPS = 3

#: The exact statement sequences, as table names in issue order. Asserted instead of a bare total:
#: two statements against the wrong two tables is not what Requirement 27.1 describes.
CATALOGUE_TABLES: Tuple[str, ...] = ("library_strategies", "profiles")
CATALOGUE_TABLES_FOR_AN_EMPTY_PAGE: Tuple[str, ...] = ("library_strategies",)
CATALOGUE_TABLES_AUTHENTICATED: Tuple[str, ...] = (
    "library_strategies",
    "profiles",
    "library_strategies",
    "library_ratings",
)
STRATEGIES_TABLES: Tuple[str, ...] = (
    "strategies",
    "library_subscriptions",
    "paper_sessions",
)


# ══════════════════════════════════════════════════════════════════════════
# THE COUNTING PERSISTENCE_LAYER - ONE OVERRIDE, ON THE ONE BOUNDARY
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Statement:
    """One Persistence_Layer statement, as the thing a round trip is counted by.

    ``rows`` is what the statement HANDED BACK - the deep copies ``FakeSupabase`` already makes,
    so holding them costs nothing and mutating the response cannot reach the stored rows. It is
    the member task 33.11 needs: a row that was retrieved and then filtered out is a row in here
    that is not in the response body.
    """

    index: int
    op: str
    table: str
    cols: Optional[str]
    filters: Tuple[Tuple[str, str, Any], ...]
    returned: int
    rows: Tuple[Any, ...] = field(default=(), repr=False)
    ignored: Tuple[str, ...] = ()

    def describe(self) -> str:
        predicates = ", ".join(
            f"{operator}({column}={_short(value)})"
            for operator, column, value in self.filters
        )
        return (
            f"#{self.index} {self.op} {self.table}"
            f"[{predicates or 'no predicate'}] -> {self.returned} row(s)"
        )


def _short(value: Any, width: int = 60) -> str:
    """One predicate value, short enough to read in a failure message."""
    if isinstance(value, (tuple, list, set, frozenset)):
        items = list(value)
        head = ", ".join(str(item) for item in items[:3])
        suffix = f", ...+{len(items) - 3}" if len(items) > 3 else ""
        return f"[{len(items)}: {head}{suffix}]"
    text = str(value)
    return text if len(text) <= width else text[: width - 3] + "..."


@dataclass(frozen=True)
class StatementLog:
    """The statements one response cost. The window a round-trip count is asserted over."""

    statements: Tuple[Statement, ...]

    @property
    def count(self) -> int:
        return len(self.statements)

    def tables(self) -> Tuple[str, ...]:
        return tuple(statement.table for statement in self.statements)

    def on(self, table: str) -> Tuple[Statement, ...]:
        return tuple(s for s in self.statements if s.table == table)

    def rows_retrieved(self, table: str) -> Tuple[Any, ...]:
        """Every row every select on ``table`` handed back, in order. Task 33.11's input."""
        out: List[Any] = []
        for statement in self.statements:
            if statement.table == table and statement.op == "select":
                out.extend(statement.rows)
        return tuple(out)

    def filters_on(self, table: str) -> Tuple[Tuple[str, str, Any], ...]:
        out: List[Tuple[str, str, Any]] = []
        for statement in self.on(table):
            out.extend(statement.filters)
        return tuple(out)

    def filter_values(self, table: str, column: str) -> Tuple[Any, ...]:
        return tuple(
            value
            for _operator, name, value in self.filters_on(table)
            if name == column
        )

    def describe(self) -> str:
        if not self.statements:
            return "  (no statement was issued)"
        return "\n".join("  " + s.describe() for s in self.statements)


class CountingStore(MatrixStore):
    """``MatrixStore`` that records every statement. The counting ``FakeDB`` P-57 needs.

    ONE override, on ``_execute``, because that is the one place every statement passes through -
    ``_Query.execute`` is ``return self.client._execute(self)`` and nothing in the routers reaches
    the rows any other way. Counting anywhere else would be counting something other than a round
    trip.

    Nothing about ``MatrixStore``'s behaviour changes: the response this returns is the response
    ``MatrixStore`` produced, unmodified, so an endpoint measured here sees exactly the database
    the tenant-isolation matrix drives. ``MatrixStore``'s own ``upsert`` path builds its inner
    statements against ``FakeSupabase._execute`` directly, which is why an upsert of ``n`` payloads
    counts as the ONE statement the caller issued rather than as ``n``.

    ONE store per property run, ``reset`` between examples: the patches in ``_matrix_app`` capture
    the store by closure, so a fresh object per example would not be the object the router reads.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        #: Every statement, in issue order. The log, not a counter: a count nobody can read back
        #: cannot say WHICH statement was the extra one.
        self.round_trips: List[Statement] = []

    # -- the log ------------------------------------------------------------
    def log(self) -> StatementLog:
        return StatementLog(tuple(self.round_trips))

    def window(self, start: int) -> StatementLog:
        """The statements issued since :meth:`mark` returned ``start`` - i.e. one response's."""
        return StatementLog(tuple(self.round_trips[start:]))

    def mark(self) -> int:
        return len(self.round_trips)

    def reset(self) -> None:
        """Empty every table and discard the log, so the next example starts from nothing."""
        for attribute in set(self.TABLES.values()):
            setattr(self, attribute, [])
        self.statements.clear()
        self.ops.clear()
        self.undeclared_tables.clear()
        self.round_trips = []
        self._sequence = 0

    # -- the one boundary ---------------------------------------------------
    def _execute(self, q: Any) -> Any:
        index = len(self.round_trips)
        response = super()._execute(q)
        data = response.get("data") if isinstance(response, dict) else getattr(
            response, "data", None
        )
        if isinstance(data, list):
            rows: Tuple[Any, ...] = tuple(data)
        elif data is None:
            rows = ()
        else:  # a ``.single()`` / ``.maybe_single()`` response - one row, not a list
            rows = (data,)
        self.round_trips.append(
            Statement(
                index=index,
                op=q.op,
                table=q.table_name,
                cols=getattr(q, "cols", None),
                filters=tuple(getattr(q, "filters", ()) or ()),
                returned=len(rows),
                rows=rows,
                ignored=tuple(getattr(q, "ignored", ()) or ()),
            )
        )
        return response


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATED DATASETS
# ══════════════════════════════════════════════════════════════════════════

#: One author id per catalogue row, at most. Distinct from :data:`CALLER` by construction, so a
#: generated Listing is never the caller's own and the catalogue page is always a non-owner view.
def _author_id(n: int) -> str:
    return f"{n:08d}-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _listing_id(n: int) -> str:
    return f"{n:08d}-1111-4111-8111-111111111111"


def _strategy_id(n: int) -> str:
    return f"{n:08d}-2222-4222-8222-222222222222"


def _subscription_id(n: int) -> str:
    return f"{n:08d}-3333-4333-8333-333333333333"


ONE_AUTHOR = "one-author-for-every-row"
AUTHOR_PER_ROW = "a-distinct-author-for-every-row"
SOME_AUTHORS = "a-few-authors-shared-across-the-rows"

OWNED_ONLY = "owned-only"
SUBSCRIBED_ONLY = "subscribed-only"
HALF_AND_HALF = "half-owned-half-subscribed"
DRAWN_SPLIT = "a-drawn-split"

#: Boundaries, sampled explicitly rather than left to ``integers``' own bias, so the census floors
#: below are met by construction instead of by luck: ``integers(0, 50)`` lands on 0 or 50 only a
#: few times in a hundred, which is not a margin worth resting a floor on.
#:
#: Several values are listed TWICE OR THREE TIMES, which is how ``sampled_from`` is weighted: the
#: ends of each range and the one-row page are the cases P-57 is actually about - a fixed count has
#: to hold at both ends of a range or it is not fixed - and the multiplicities are what put each of
#: them nine or more times into a hundred examples across repeated runs rather than two or three.
#: They were measured, not guessed. The duplication is the weighting, not an oversight.
_LISTING_BOUNDARIES = (
    0,
    0,
    1,
    1,
    1,
    2,
    MAX_LISTINGS - 1,
    MAX_LISTINGS,
    MAX_LISTINGS,
    MAX_LISTINGS,
)
_ENTRY_BOUNDARIES = (
    0,
    0,
    1,
    2,
    MAX_ENTRIES - 1,
    MAX_ENTRIES,
    MAX_ENTRIES,
    MAX_ENTRIES,
)


@dataclass(frozen=True)
class Dataset:
    """One generated dataset: a catalogue of ``listings`` and a Strategies_Page of ``entries``."""

    listing_count: int
    author_mode: str
    owned: int
    subscribed: int

    @property
    def entries(self) -> int:
        return self.owned + self.subscribed

    @property
    def page_size(self) -> int:
        """How many Listings the page RETURNS - the count Requirement 27.1 quantifies over."""
        return min(self.listing_count, CATALOGUE_PAGE_LIMIT)

    @property
    def author_ids(self) -> Tuple[str, ...]:
        """One author id per Listing, in row order, according to :attr:`author_mode`."""
        if self.author_mode == ONE_AUTHOR:
            return tuple(_author_id(0) for _ in range(self.listing_count))
        if self.author_mode == AUTHOR_PER_ROW:
            return tuple(_author_id(n) for n in range(self.listing_count))
        span = max(1, self.listing_count // 3)
        return tuple(_author_id(n % span) for n in range(self.listing_count))

    @property
    def distinct_authors(self) -> Tuple[str, ...]:
        """The distinct authors of the page, in first-appearance order - what one ``in_`` carries."""
        seen: Dict[str, None] = {}
        for author in self.author_ids[: self.page_size]:
            seen.setdefault(author, None)
        return tuple(seen)


@st.composite
def datasets(draw: Any) -> Dataset:
    """Listing counts 0...50 x author distributions x combined entry counts 0...200.

    The author distribution is drawn as a MODE rather than as a number, so "every row has its own
    author" - the case a per-row alias read costs one statement each for, and therefore the case
    P-57 exists for - is drawn about a third of the time rather than whenever ``integers`` happens
    to land on the top of its range.
    """
    listing_count = draw(
        st.one_of(
            st.sampled_from(_LISTING_BOUNDARIES),
            st.integers(min_value=0, max_value=MAX_LISTINGS),
        )
    )
    author_mode = draw(st.sampled_from((ONE_AUTHOR, AUTHOR_PER_ROW, SOME_AUTHORS)))
    total = draw(
        st.one_of(
            st.sampled_from(_ENTRY_BOUNDARIES),
            st.integers(min_value=0, max_value=MAX_ENTRIES),
        )
    )
    split = draw(st.sampled_from((OWNED_ONLY, SUBSCRIBED_ONLY, HALF_AND_HALF, DRAWN_SPLIT)))
    if split == OWNED_ONLY:
        owned = total
    elif split == SUBSCRIBED_ONLY:
        owned = 0
    elif split == HALF_AND_HALF:
        owned = total // 2
    else:
        owned = draw(st.integers(min_value=0, max_value=total))
    return Dataset(
        listing_count=listing_count,
        author_mode=author_mode,
        owned=owned,
        subscribed=total - owned,
    )


def seed_dataset(store: CountingStore, dataset: Dataset) -> None:
    """Put ``dataset`` in the store: the catalogue, its creators, and the caller's own entries.

    Three deliberate choices:

    * every Listing on the catalogue page is ``is_active`` and ``moderation_status='approved'``, so
      the public filter admits all of them and the page returns exactly
      :attr:`Dataset.page_size` rows. A row filtered out by the query would make the row count
      this property quantifies over something other than the count it seeded;
    * every author has a ``profiles`` row carrying a ``display_name``.
      ``listing_projection.project_listing`` REFUSES a row whose alias is missing or blank
      (Requirement 6.5), which ``browse_library`` turns into ``MARKETPLACE_READ_FAILED`` - so an
      unseeded creator would make the measurement a measurement of the error path;
    * the SUBSCRIBED entries' Listings are seeded onto the ``library_subscriptions`` rows as the
      embedded resource PostgREST returns them in the same request, and NOT as separate
      ``library_strategies`` rows. That is what makes round trip 2 one statement, and it keeps the
      catalogue's row count exactly the generated Listing count.
    """
    authors = dataset.author_ids
    store.seed(
        "profiles",
        *(
            {"id": author, "display_name": f"creator-{author[:8]}"}
            for author in dict.fromkeys(authors)
        ),
    )
    store.seed(
        "library_strategies",
        *(
            _embedded_listing_row(
                author_id=authors[n],
                listing_id=_listing_id(n),
            )
            for n in range(dataset.listing_count)
        ),
    )
    store.seed(
        "strategies",
        *(
            dict(_owned_row(_strategy_id(n)), user_id=CALLER)
            for n in range(dataset.owned)
        ),
    )
    store.seed(
        "library_subscriptions",
        *(
            dict(
                _subscription_row(
                    subscription_id=_subscription_id(n),
                    listing=_embedded_listing_row(
                        listing_id=_listing_id(MAX_LISTINGS + n)
                    ),
                ),
                user_id=CALLER,
            )
            for n in range(dataset.subscribed)
        ),
    )
    if dataset.owned:
        # One RUNNING session, so round trip 3 returns a row rather than always answering empty:
        # "three statements" would otherwise be three statements one of which found nothing.
        store.seed(
            "paper_sessions",
            {
                "id": _subscription_id(MAX_ENTRIES + 1),
                "user_id": CALLER,
                "source_strategy_id": _strategy_id(0),
                "listing_id": None,
                "session_state": _library_entries.RUNNING_SESSION_STATE,
                "environment": "PAPER",
            },
        )


# ══════════════════════════════════════════════════════════════════════════
# ISSUING ONE REQUEST, AND MEASURING EXACTLY WHAT IT COST
# ══════════════════════════════════════════════════════════════════════════


def _issue(store: CountingStore, client: Any, url: str) -> Tuple[Any, StatementLog]:
    """One request, and the statements it - and only it - issued."""
    start = store.mark()
    response = client.get(url)
    return response, store.window(start)


CATALOGUE_URL = f"/api/library?page=1&limit={CATALOGUE_PAGE_LIMIT}"
STRATEGIES_URL = "/api/library/my-strategies"


def _assert_round_trips(
    *,
    label: str,
    window: StatementLog,
    expected_tables: Sequence[str],
    dataset: Dataset,
    rows_returned: int,
) -> None:
    """The one assertion this property makes, in the one place it is made.

    The ordered table sequence is asserted, which subsumes the count; the count is then asserted
    separately so the failure message names the number a reader is looking for. The message
    carries the whole statement log and the growth figures, because "3 != 4" does not say which
    statement was the extra one or whether it grew with the rows.
    """
    expected = tuple(expected_tables)
    actual = window.tables()
    context = (
        f"{label}: {dataset.listing_count} Listing(s) seeded, page returned {rows_returned} "
        f"row(s), {len(dataset.distinct_authors)} distinct author(s) on the page, "
        f"{dataset.owned} owned + {dataset.subscribed} subscribed entr(y/ies). "
        f"Statements issued ({window.count}):\n{window.describe()}"
    )
    assert actual == expected, (
        f"the statement SEQUENCE is not the fixed one Requirement 27.1/27.2 requires: expected "
        f"{list(expected)}, got {list(actual)}. If the extra statement repeats a table already in "
        f"the sequence, that is the N+1 - one read per row or per author instead of one batched "
        f"read for the page. {context}"
    )
    assert window.count == len(expected), (
        f"{label} cost {window.count} round trip(s), not the pinned {len(expected)}. {context}"
    )


def _assert_the_alias_read_is_one_batched_read(
    *, window: StatementLog, dataset: Dataset
) -> None:
    """The N+1 that task 16.2 removed, asserted against specifically.

    ``_get_author_alias`` issued ``profiles.select(...).eq("id", user_id).single()`` per row; the
    replacement is ``aliases.resolve_aliases``' single ``in_("id", author_ids)``. So: exactly one
    statement on ``profiles``, its only predicate an ``in`` on ``id``, and the set of ids it
    carries is exactly the page's distinct authors. A per-row read fails all three, and the third
    is the one that fails if a future edit chunked the id list - chunking would make the count grow
    with the author count, which is what Requirement 27.1 forbids (``aliases.py`` -> "What is
    deliberately not here").
    """
    reads = window.on(_aliases.PROFILES_TABLE)
    assert len(reads) == 1, (
        f"the catalogue page performed {len(reads)} reads of {_aliases.PROFILES_TABLE!r} for "
        f"{dataset.page_size} row(s) and {len(dataset.distinct_authors)} distinct author(s); the "
        f"batched alias read is ONE statement per response (design.md -> 'The N+1 being "
        f"removed'). Statements:\n{window.describe()}"
    )
    statement = reads[0]
    assert statement.cols == _aliases.ALIAS_SELECT, (
        f"the alias read asked for {statement.cols!r}, not aliases.ALIAS_SELECT "
        f"({_aliases.ALIAS_SELECT!r})"
    )
    operators = tuple(operator for operator, _column, _value in statement.filters)
    assert operators == ("in",), (
        f"the alias read's predicates are {operators}, not a single 'in': a per-row "
        f"`.eq('id', ...).single()` is the N+1 this property exists to catch. "
        f"{statement.describe()}"
    )
    _operator, column, value = statement.filters[0]
    assert column == "id", f"the alias read filtered on {column!r}, not 'id'"
    assert set(value) == set(dataset.distinct_authors), (
        f"the one alias read carried {len(set(value))} author id(s) but the page has "
        f"{len(dataset.distinct_authors)} distinct author(s); every author on the page must be "
        f"resolved by that ONE read, or the count grows with the author count"
    )


def _assert_the_strategies_reads_are_scoped_in_the_query(
    *, window: StatementLog, dataset: Dataset
) -> None:
    """All three Strategies_Page statements carry ``user_id = caller`` as a PREDICATE.

    Produced by this store for task 33.11 (P-46): the scoping being in the query is what makes
    "every returned row is owned by the caller" a fact about the read rather than about a Python
    filter applied afterwards. Asserted here because it is also what keeps the count at three - a
    read that fetched every user's rows and filtered in Python would still be three statements
    today and would become a per-entry read the moment anybody tried to page it.
    """
    for table in STRATEGIES_TABLES:
        values = window.filter_values(table, "user_id")
        assert values == (CALLER,), (
            f"the {table!r} read of GET {STRATEGIES_URL} filters user_id by {values!r}, not "
            f"exactly ({CALLER!r},); the caller scope must be in the query. "
            f"Statements:\n{window.describe()}"
        )
        for statement in window.on(table):
            assert statement.returned == len(statement.rows), (
                "the counting store disagrees with itself about how many rows a statement "
                f"returned: {statement.describe()}"
            )
    _ = dataset


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - SO THE PROPERTY CANNOT PASS VACUOUSLY
# ══════════════════════════════════════════════════════════════════════════

#: Every bucket, its floor and its Hypothesis ``event`` label. A floor is met by FORCING the case
#: in :func:`datasets` - never by lowering the floor. The two that matter most are
#: ``an_author_for_every_row`` (the N+1's own case: 50 rows, 50 distinct authors, still one alias
#: read) and ``a_full_page_of_fifty`` / ``two_hundred_entries`` (the tops of P-57's two ranges).
#: Every floor is 5 or more against a measured worst case of 9 or more over repeated runs (the
#: thinnest are ``a_single_listing`` at 9 and ``a_full_page_of_fifty`` at 11), so a shortfall means
#: the generator stopped producing the case rather than that a run was unlucky.
CENSUS_FLOORS: Mapping[str, int] = {
    "an_empty_catalogue_page": 5,
    "a_single_listing": 5,
    "a_full_page_of_fifty": 5,
    "one_author_for_many_rows": 5,
    "an_author_for_every_row": 5,
    "an_authenticated_page_that_was_enriched": 10,
    "an_empty_combined_list": 5,
    "owned_entries_only": 5,
    "subscribed_entries_only": 5,
    "both_kinds_of_entry": 5,
    "two_hundred_entries": 5,
}

CENSUS_LABELS: Mapping[str, str] = {
    "an_empty_catalogue_page": "catalogue: no Listing returned",
    "a_single_listing": "catalogue: one Listing",
    "a_full_page_of_fifty": "catalogue: a full page of 50",
    "one_author_for_many_rows": "catalogue: many rows, one author",
    "an_author_for_every_row": "catalogue: one author per row",
    "an_authenticated_page_that_was_enriched": "catalogue: authenticated, enriched",
    "an_empty_combined_list": "strategies: no entry",
    "owned_entries_only": "strategies: owned only",
    "subscribed_entries_only": "strategies: subscribed only",
    "both_kinds_of_entry": "strategies: owned and subscribed",
    "two_hundred_entries": "strategies: 200 entries",
}


def _record(census: Recorder, dataset: Dataset) -> None:
    """One example's census marks."""
    if dataset.page_size == 0:
        census.mark("an_empty_catalogue_page")
    if dataset.page_size == 1:
        census.mark("a_single_listing")
    if dataset.page_size == CATALOGUE_PAGE_LIMIT:
        census.mark("a_full_page_of_fifty")
    if dataset.page_size >= 2:
        if len(dataset.distinct_authors) == 1:
            census.mark("one_author_for_many_rows")
        if len(dataset.distinct_authors) == dataset.page_size:
            census.mark("an_author_for_every_row")
        census.mark("an_authenticated_page_that_was_enriched")
    if dataset.entries == 0:
        census.mark("an_empty_combined_list")
    if dataset.owned and not dataset.subscribed:
        census.mark("owned_entries_only")
    if dataset.subscribed and not dataset.owned:
        census.mark("subscribed_entries_only")
    if dataset.owned and dataset.subscribed:
        census.mark("both_kinds_of_entry")
    if dataset.entries == MAX_ENTRIES:
        census.mark("two_hundred_entries")


# ══════════════════════════════════════════════════════════════════════════
# THE PROPERTY - P-57
#
# Feature: marketplace-subscriptions-paper-trading, Property 57 (invariant, fixed round trips):
# for all Listing counts 0...50 and all combined owned-and-subscribed entry counts 0...200, the
# number of Persistence_Layer round trips recorded while serving one catalogue page is a constant
# independent of the Listing count, and the number recorded while serving the Strategies_Page
# combined list is a constant independent of the entry count.
#
# **Validates: Requirements 27.1, 27.2**
# ══════════════════════════════════════════════════════════════════════════


def test_p57_round_trips_are_independent_of_row_count(request: Any) -> None:
    """The catalogue page and the Strategies_Page cost a fixed number of statements.

    For all Listing counts 0...50, all author distributions over those rows, and all combined
    owned-and-subscribed entry counts 0...200:

    * ``GET /api/library`` costs exactly **2** statements - the paged ``library_strategies``
      select and ONE batched ``profiles`` read - for every page that returns at least one Listing,
      whatever the Listing count and whatever the number of distinct authors on it; **1** for a
      page that returns none, because ``resolve_aliases`` deliberately performs no read for zero
      ids; and never more than 2 anywhere in the generated space;
    * ``GET /api/library`` for an authenticated caller costs exactly **4** - those two plus the
      two batched enrichment reads - and the enrichment is per-page, not per-card;
    * ``GET /api/library/my-strategies`` costs exactly **3** - ``strategies``,
      ``library_subscriptions`` with the Listing embedded in the same request, and
      ``paper_sessions`` - unconditionally, including for an empty list.

    The oracle is the counting store: statements ISSUED, at the one boundary every statement
    passes through. Every response is first asserted to be a 200 carrying the right number of
    entries, so no measurement is of an error path.

    **Validates: Requirements 27.1, 27.2**
    """
    store = CountingStore()
    census = Recorder("P-57", CENSUS_FLOORS, CENSUS_LABELS)

    with _matrix_app(store, CALLER) as client:

        @PROPERTY_SETTINGS
        @given(dataset=datasets())
        def check(dataset: Dataset) -> None:
            census.start()
            store.reset()
            seed_dataset(store, dataset)
            _record(census, dataset)

            # ── GET /api/library, unauthenticated ────────────────────────
            response, window = _issue(store, client, CATALOGUE_URL)
            assert response.status_code == 200, (
                f"the catalogue page for {dataset.listing_count} Listing(s) answered "
                f"{response.status_code}, so the statements below are an error path's and not a "
                f"page's: {response.text[:400]}"
            )
            body = response.json()
            assert len(body["items"]) == dataset.page_size, (
                f"the page rendered {len(body['items'])} item(s) for {dataset.listing_count} "
                f"seeded Listing(s) at limit {CATALOGUE_PAGE_LIMIT}; the row count P-57 "
                f"quantifies over is the count the page RETURNS"
            )
            assert body["total"] == dataset.listing_count
            if dataset.page_size:
                _assert_round_trips(
                    label=f"GET {CATALOGUE_URL}",
                    window=window,
                    expected_tables=CATALOGUE_TABLES,
                    dataset=dataset,
                    rows_returned=len(body["items"]),
                )
                _assert_the_alias_read_is_one_batched_read(
                    window=window, dataset=dataset
                )
            else:
                _assert_round_trips(
                    label=f"GET {CATALOGUE_URL} (empty page)",
                    window=window,
                    expected_tables=CATALOGUE_TABLES_FOR_AN_EMPTY_PAGE,
                    dataset=dataset,
                    rows_returned=0,
                )
            # The ceiling, asserted separately from the two cases: whichever branch was taken,
            # no catalogue page anywhere in the generated space costs more than the pinned 2.
            assert window.count <= CATALOGUE_ROUND_TRIPS, (
                f"a catalogue page cost {window.count} statements, above the pinned ceiling of "
                f"{CATALOGUE_ROUND_TRIPS}:\n{window.describe()}"
            )

            # ── GET /api/library, authenticated ──────────────────────────
            # The one extra seam, applied to exactly this request: the pure bearer-token decode,
            # so the caller's own context is folded on without minting a token. It performs no
            # I/O, so it moves no statement off the count.
            with patch.object(
                library_router,
                "_user_id_from_credentials",
                lambda _credentials: CALLER,
            ):
                response, window = _issue(store, client, CATALOGUE_URL)
            assert response.status_code == 200, response.text[:400]
            items = response.json()["items"]
            assert len(items) == dataset.page_size
            if dataset.page_size:
                assert all("user_has_cloned" in item for item in items), (
                    "the authenticated page carries no caller context, so the two enrichment "
                    "reads below were not the enrichment's"
                )
                _assert_round_trips(
                    label=f"GET {CATALOGUE_URL} (authenticated)",
                    window=window,
                    expected_tables=CATALOGUE_TABLES_AUTHENTICATED,
                    dataset=dataset,
                    rows_returned=len(items),
                )
            else:
                _assert_round_trips(
                    label=f"GET {CATALOGUE_URL} (authenticated, empty page)",
                    window=window,
                    expected_tables=CATALOGUE_TABLES_FOR_AN_EMPTY_PAGE,
                    dataset=dataset,
                    rows_returned=0,
                )

            # ── GET /api/library/my-strategies ───────────────────────────
            response, window = _issue(store, client, STRATEGIES_URL)
            assert response.status_code == 200, (
                f"the combined list for {dataset.entries} entr(y/ies) answered "
                f"{response.status_code}: {response.text[:400]}"
            )
            body = response.json()
            assert body["total"] == dataset.entries, (
                f"the combined list carried {body['total']} entr(y/ies) for {dataset.owned} "
                f"owned + {dataset.subscribed} subscribed; the entry count P-57 quantifies over "
                f"is the count the response RETURNS"
            )
            assert body["owned_total"] == dataset.owned
            assert body["subscribed_total"] == dataset.subscribed
            _assert_round_trips(
                label=f"GET {STRATEGIES_URL}",
                window=window,
                expected_tables=STRATEGIES_TABLES,
                dataset=dataset,
                rows_returned=body["total"],
            )
            _assert_the_strategies_reads_are_scoped_in_the_query(
                window=window, dataset=dataset
            )

            # No predicate any of these statements asked for was ignored by the double, so none of
            # the counts above depends on a filter it silently forgave.
            assert store.unapplied_operators() == (), (
                f"the double ignored {store.unapplied_operators()}; a count measured through an "
                f"ignored predicate is not the production count"
            )
            census.finish()

        with publish_hypothesis_statistics(request.node):
            check()

    census.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# RIDER 1 - THE INSTRUMENT ITSELF
# ══════════════════════════════════════════════════════════════════════════


def test_the_counting_store_records_one_statement_per_execute() -> None:
    """The counting store counts, and counts once.

    A property whose oracle is an instrument needs the instrument checked: a ``_execute`` override
    that recorded nothing would make "two statements" unfalsifiable at zero, and one that recorded
    twice would make every constant here double. Three known statements are issued directly - two
    selects and one insert - and the log is asserted to be exactly those three, in order, with the
    row counts they really returned.
    """
    store = CountingStore()
    store.seed(
        "profiles",
        {"id": _author_id(1), "display_name": "one"},
        {"id": _author_id(2), "display_name": "two"},
    )

    store.table("profiles").select("id,display_name").in_(
        "id", [_author_id(1), _author_id(2)]
    ).execute()
    store.table("profiles").select("id").eq("id", _author_id(1)).single().execute()
    store.table("library_ratings").insert({"library_id": _listing_id(0), "rating": 5}).execute()

    log = store.log()
    assert log.tables() == ("profiles", "profiles", "library_ratings"), log.describe()
    assert log.count == 3, log.describe()
    assert [s.op for s in log.statements] == ["select", "select", "insert"]
    assert [s.returned for s in log.statements] == [2, 1, 1], log.describe()
    assert log.filter_values("profiles", "id") == (
        (_author_id(1), _author_id(2)),
        _author_id(1),
    )
    assert len(log.rows_retrieved("profiles")) == 3
    assert "in(id=" in log.describe()

    # ``reset`` really empties both the tables and the log, which is what makes one store safe to
    # reuse across a hundred examples.
    store.reset()
    assert store.log().count == 0
    assert store.rows_of("profiles") == []


# ══════════════════════════════════════════════════════════════════════════
# RIDER 2 - THE CONSTANTS ARE THE DESIGN'S, NOT THIS FILE'S
# ══════════════════════════════════════════════════════════════════════════


def test_the_pinned_constants_are_the_designs_own() -> None:
    """``design.md``'s "Fixed round trips" table gives 2 for the catalogue page and 3 for the
    Strategies_Page combined list, and this file pins those two numbers rather than numbers of its
    own choosing.

    The authenticated page's 4 is the catalogue's 2 plus the two batched enrichment reads, so it is
    derived from the table's figure rather than being a third independent claim.
    """
    catalogue = _design_round_trips("Catalogue page of")
    strategies = _design_round_trips("Strategies_Page combined list")
    assert CATALOGUE_ROUND_TRIPS == catalogue, (
        f"this file pins {CATALOGUE_ROUND_TRIPS} round trip(s) for a catalogue page; design.md's "
        f"table says {catalogue}"
    )
    assert STRATEGIES_ROUND_TRIPS == strategies, (
        f"this file pins {STRATEGIES_ROUND_TRIPS} round trip(s) for the Strategies_Page combined "
        f"list; design.md's table says {strategies}"
    )
    assert len(CATALOGUE_TABLES) == CATALOGUE_ROUND_TRIPS
    assert len(STRATEGIES_TABLES) == STRATEGIES_ROUND_TRIPS
    assert len(CATALOGUE_TABLES_AUTHENTICATED) == CATALOGUE_ROUND_TRIPS_AUTHENTICATED
    assert CATALOGUE_TABLES_AUTHENTICATED[:2] == CATALOGUE_TABLES, (
        "the authenticated page must be the anonymous page plus the enrichment reads; if its "
        "first two statements differ, the two are not the same read"
    )
    assert (
        CATALOGUE_ROUND_TRIPS_AUTHENTICATED - CATALOGUE_ROUND_TRIPS == 2
    ), "the caller's own context is two batched reads: the clones and the ratings"
    assert CATALOGUE_ROUND_TRIPS_FOR_AN_EMPTY_PAGE == CATALOGUE_ROUND_TRIPS - 1, (
        "an empty page is the paged select and no alias read; see the module docstring on why "
        "that one step is not forced up to the constant"
    )
    assert EXAMPLES >= 100, (
        f"design.md's property configuration requires at least 100 examples; the shared "
        f"PROPERTY_SETTINGS carries {EXAMPLES}"
    )


# ══════════════════════════════════════════════════════════════════════════
# RIDER 3 - THE EMBED, WITHOUT WHICH "ONE STATEMENT" IS HALF AN ANSWER
# ══════════════════════════════════════════════════════════════════════════


def test_round_trip_two_asks_for_the_listing_in_the_same_request() -> None:
    """The Strategies_Page's second statement carries the Listing as an embedded resource.

    Three statements for a list of two hundred entries is only meaningful if the second one
    returns the Listing fields each entry needs. If the embed were dropped, the handler would need
    one Listing read per subscribed entry and the constant would become ``2 + n`` - so the embed is
    the mechanism the pinned 3 rests on, and it is asserted where the 3 is asserted.
    """
    projection = _library_entries.SUBSCRIPTION_SELECT
    assert "library_strategies!inner(" in projection, (
        "round trip 2 does not embed library_strategies; without the embed the Listing costs one "
        "read per entry (Requirement 27.2)"
    )
    assert "marketplace_submissions(submission_state)" in projection, (
        "round trip 2 does not embed the Submission state, which entitlement is decided from; a "
        "separate read for it would be a fourth statement"
    )
    assert "*" not in projection


# ══════════════════════════════════════════════════════════════════════════
# RIDER 4 - THE PINNED PAGINATION FACT TASK 33.11 NEEDS TO KNOW
# ══════════════════════════════════════════════════════════════════════════


def test_the_catalogue_slices_its_page_in_python_not_in_the_query() -> None:
    """``browse_library`` retrieves every matching Listing and slices the page in Python.

    PINNED, not asserted as desirable. ``design.md``'s table describes the catalogue's first
    statement as ``.range(offset, offset+limit-1)`` with ``count="exact"``; the handler as it
    stands applies neither, reading all matching rows and taking ``all_rows[offset:offset+limit]``.
    That does NOT cost an extra statement - the count stays 2, which is why P-57 holds - but it
    means a row can be retrieved and then dropped, and task 33.11's "no list endpoint retrieved a
    row it then filtered out" has to know the difference between:

    * a PAGINATION drop, which this is, and which is not an ownership question at all; and
    * an OWNERSHIP drop, which would be Requirement 21.5's subject.

    Three Listings at ``limit=1``: three rows retrieved, one rendered, two dropped in Python, and
    the alias read carries only the one author whose row survived the slice. Pinned exactly, so
    closing it turns this test red and points at the handler rather than at P-57.
    """
    store = CountingStore()
    dataset = Dataset(
        listing_count=3, author_mode=AUTHOR_PER_ROW, owned=0, subscribed=0
    )
    seed_dataset(store, dataset)

    with _matrix_app(store, CALLER) as client:
        response, window = _issue(store, client, "/api/library?page=1&limit=1")

    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert len(body["items"]) == 1, body
    assert body["total"] == 3

    # The count is unchanged by the slice - which is the P-57-relevant half.
    assert window.tables() == CATALOGUE_TABLES, window.describe()

    retrieved = window.rows_retrieved("library_strategies")
    assert len(retrieved) == 3, (
        f"the catalogue read returned {len(retrieved)} row(s) for a page of 1; if it has grown a "
        f"server-side range, this pinned observation is out of date and task 33.11's "
        f"retrieved-versus-rendered comparison can be made unconditional. "
        f"Statements:\n{window.describe()}"
    )
    rendered = {item["listing_id"] for item in body["items"]}
    dropped = {row["id"] for row in retrieved} - rendered
    assert len(dropped) == 2, (
        f"expected exactly two rows retrieved and dropped by the Python page slice; got "
        f"{sorted(dropped)}"
    )

    # The alias read follows the SLICE, not the result set: one author, not three.
    alias = window.on("profiles")[0]
    _operator, _column, value = alias.filters[0]
    assert len(set(value)) == 1, (
        f"the alias read carried {len(set(value))} author id(s) for a one-row page; it resolves "
        f"the page, not the result set"
    )


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
