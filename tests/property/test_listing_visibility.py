"""Property test for Property 50 — a Listing is publicly visible exactly when its
Submission_State is ``PUBLISHED``, and in every other state a non-owner cannot tell it from
a listing that exists in no tenant.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property 50: Listing visibility is exactly the PUBLISHED
state`` and ``design.md § Property-to-test mapping`` ->
``P-50 | tests/property/test_listing_visibility.py | all 8 states × catalogue and detail
paths | submission_state.PUBLIC_STATES``. Property 50 in full:

    For all Submissions and all eight Submission_State values: the Listing appears in every
    public catalogue response and every public detail response if and only if its
    Submission_State is ``PUBLISHED``; and for a non-owner requesting a Listing in any other
    state, the response is identical in status and body to the response for a Listing
    identifier that exists in no tenant.

    **Validates: Requirements 4.6, 4.7, 6.10**

THE ORACLE
----------
``submission_state.PUBLIC_STATES`` — the states the Listing_Projection may expose. It is
``frozenset({SubmissionState.PUBLISHED})``: exactly the one state, per Requirements 4.6/4.7.
The property reads that set rather than re-spelling ``== 'PUBLISHED'``, so if the oracle ever
widened this test would follow it — and the mapping-level assertion below would then be the
thing that fails, which is the point.

WHY THIS IS ASSERTED AS A MECHANISM, NOT RUN AGAINST A LIVE DATABASE
-------------------------------------------------------------------
There is no PostgreSQL and no HTTP server in this test environment. Every sibling
migration/route test in the repository verifies the mechanism by STATIC PARSE of the
migration and the router rather than by executing them — ``tests/property/
test_db_transition_guards.py`` (task 14.9's sibling P-49) asserts the DB refusal *mechanism*
rather than issuing a live UPDATE, and ``tests/test_submission_state_agreement.py`` reads the
migration text off disk. This file follows that convention exactly.

Visibility is produced by two layers, and P-50 is true iff BOTH agree with the oracle:

  (a) THE PROJECTION. ``trg_submission_projects_moderation_status`` (an AFTER INSERT OR
      UPDATE trigger in ``007_marketplace_submissions.sql``) applies
      ``MODERATION_STATUS_FOR_STATE`` and ``is_active := (submission_state = 'PUBLISHED')`` to
      the ``library_strategies`` row inside the same transaction as the transition
      (Requirement 4.12's ONE definition). So a Submission_State ``s`` projects to
      ``(is_active = IS_ACTIVE_FOR_STATE[s], moderation_status = MODERATION_STATUS_FOR_STATE[s])``.

  (b) THE PUBLIC FILTER. Every public catalogue route and the public detail route in
      ``routers/library.py`` filter ``.eq("is_active", True).in_("moderation_status",
      ["approved", "featured"])``. A ``library_strategies`` row is served to a non-owner iff
      it passes that filter. The detail route additionally ``.single()``s and raises a 404
      "not found or not publicly available" when the filtered row is absent — the SAME 404 a
      genuinely non-existent id produces, which is the indistinguishability half of P-50.

Compose the two: a Listing is publicly visible for Submission_State ``s`` iff

    IS_ACTIVE_FOR_STATE[s]  AND  MODERATION_STATUS_FOR_STATE[s] in CATALOGUE_VISIBLE_STATUSES

and P-50 asserts that this composed predicate equals ``s in PUBLIC_STATES`` for all eight ``s``.
This is the strongest, DB-free form of the property: it is provable over the two mappings and
the route filter, all read from source, without a database.

WHAT THE HYPOTHESIS PROPERTY DOES
---------------------------------
``@given`` draws ``s`` from ``st.sampled_from`` of ALL eight ``SubmissionState`` values and
asserts, for each, that ``_projects_to_publicly_visible(s) == (s in PUBLIC_STATES)`` — the
catalogue-and-detail visibility computed from the projection mappings equals membership of
the oracle. Sampling the full eight-value space (with ``max_examples`` well above eight) makes
the counterexample name the exact offending state if the projection ever drifted from the
oracle.

Three structural riders pin the mechanism the mapping-level property leans on:

  * ``test_public_routes_apply_the_active_approved_filter`` — every public catalogue route AND
    the public detail route in ``library.py`` apply ``.eq("is_active", True)`` and
    ``.in_("moderation_status", [...approved...])``; the ``CATALOGUE_VISIBLE_STATUSES`` used by
    the oracle above is read out of that route source, not hard-coded here.
  * ``test_projection_makes_only_published_active_and_approved`` — the ``007`` projection
    function maps ``is_active`` to ``(submission_state = 'PUBLISHED')`` and its
    moderation_status CASE agrees with ``MODERATION_STATUS_FOR_STATE`` for all eight states.
  * ``test_detail_route_is_indistinguishable_from_absent`` — the public detail route
    ``.single()``s the SAME ``is_active``+``moderation_status`` filter and raises a 404 whose
    body says "not found or not publicly available", so a non-visible listing and an absent id
    yield the identical status and body (Requirement 6.10 / 4.7 indistinguishability).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import FrozenSet, Set

from hypothesis import given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace.errors import (
    HTTP_STATUS_FOR_CODE,
    NOT_FOUND,
    PUBLIC_MESSAGE_FOR_CODE,
)
from backend_app.backend.marketplace.submission_state import (
    IS_ACTIVE_FOR_STATE,
    MODERATION_STATUS_FOR_STATE,
    PUBLIC_STATES,
    SubmissionState,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROUTER = REPO_ROOT / "backend_app" / "routers" / "library.py"
SUBMISSIONS_MIGRATION = (
    REPO_ROOT / "backend_app" / "migrations" / "007_marketplace_submissions.sql"
)

#: The shared property configuration ``design.md § Property-based testing configuration``
#: prescribes: at least 100 examples, no per-example deadline. Eight states is a tiny space,
#: so 200 examples covers it many times over and matches the sibling files' decorator.
PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)


# ---------------------------------------------------------------------------
# The public catalogue filter, read out of the router source rather than hard-coded.
# ---------------------------------------------------------------------------


def _catalogue_visible_statuses() -> FrozenSet[str]:
    """The ``moderation_status`` values the public routes admit — read from ``library.py``.

    Every public route filters ``.in_("moderation_status", ["approved", "featured"])``. Parse
    that list literal out of the router source so the oracle uses exactly the values the code
    uses; if the route ever narrowed or widened the list, the parsed set follows it and the
    mapping-level property re-computes visibility against the real filter.
    """
    src = LIBRARY_ROUTER.read_text(encoding="utf-8")
    matches = re.findall(
        r"\.in_\(\s*['\"]moderation_status['\"]\s*,\s*\[(?P<items>[^\]]*)\]\s*\)",
        src,
    )
    assert matches, (
        "no `.in_(\"moderation_status\", [...])` filter found in library.py; the public "
        "catalogue routes must gate visibility on moderation_status, so P-50 has nothing to "
        "read the visible statuses from"
    )
    statuses: Set[str] = set()
    for items in matches:
        statuses.update(re.findall(r"['\"]([a-z_]+)['\"]", items))
    assert "approved" in statuses, (
        "the public moderation_status filter does not admit 'approved'; a PUBLISHED "
        "Submission projects to moderation_status='approved', so it would never be visible"
    )
    return frozenset(statuses)


CATALOGUE_VISIBLE_STATUSES = _catalogue_visible_statuses()


# ---------------------------------------------------------------------------
# The composed visibility oracle: projection mappings + public route filter.
# ---------------------------------------------------------------------------


def _projects_to_publicly_visible(state: SubmissionState) -> bool:
    """Whether Submission_State ``state`` projects to a catalogue-visible ``library_strategies``
    row — the composition of the ``007`` projection with the public route filter.

    A Listing is served to a non-owner iff its projected row has ``is_active = True`` AND its
    projected ``moderation_status`` is in the public filter's set. The projection sets
    ``is_active := (state = PUBLISHED)`` (``IS_ACTIVE_FOR_STATE``) and ``moderation_status``
    from ``MODERATION_STATUS_FOR_STATE``. This is the exact predicate a public request would
    evaluate against the projected row.
    """
    return (
        IS_ACTIVE_FOR_STATE[state]
        and MODERATION_STATUS_FOR_STATE[state] in CATALOGUE_VISIBLE_STATUSES
    )


# ---------------------------------------------------------------------------
# THE PROPERTY — P-50, over all eight Submission_State values.
#
# Feature: marketplace-subscriptions-paper-trading, Property 50 (invariant): a Listing appears
# in every public catalogue and detail response iff its Submission_State is PUBLISHED; in any
# other state a non-owner's response is identical in status and body to the response for an
# identifier existing in no tenant.
#
# **Validates: Requirements 4.6, 4.7, 6.10**
# ---------------------------------------------------------------------------


@PROPERTY_SETTINGS
@given(state=st.sampled_from(list(SubmissionState)))
def _prop_visible_iff_published(state: SubmissionState) -> None:
    """For every Submission_State, catalogue+detail visibility equals oracle membership.

    ``_projects_to_publicly_visible(state)`` is what the projection-plus-filter mechanism
    actually serves; ``state in PUBLIC_STATES`` is the oracle. P-50 is exactly their equality
    for all eight states.
    """
    visible = _projects_to_publicly_visible(state)
    in_oracle = state in PUBLIC_STATES
    assert visible == in_oracle, (
        f"Submission_State {state.value}: catalogue/detail visibility is {visible} "
        f"(is_active={IS_ACTIVE_FOR_STATE[state]}, "
        f"moderation_status={MODERATION_STATUS_FOR_STATE[state]!r}, "
        f"catalogue-visible statuses={sorted(CATALOGUE_VISIBLE_STATUSES)}), but the oracle "
        f"PUBLIC_STATES says {in_oracle}. A Listing must be publicly visible iff PUBLISHED."
    )


def test_p50_listing_visible_iff_published() -> None:
    """Property 50: a Listing is publicly visible iff its Submission_State is PUBLISHED.

    Driven by ``_prop_visible_iff_published`` over all eight ``SubmissionState`` values, so the
    property-coverage scoreboard counts this single ``test_p50_`` function. As a guard against
    a vacuous oracle, this also pins the oracle itself: exactly PUBLISHED is public.

    **Validates: Requirements 4.6, 4.7, 6.10**
    """
    # The oracle is exactly {PUBLISHED} — pin it so the property cannot pass by an oracle that
    # silently widened to admit another state.
    assert PUBLIC_STATES == frozenset({SubmissionState.PUBLISHED}), (
        f"PUBLIC_STATES is {sorted(s.value for s in PUBLIC_STATES)}, expected exactly "
        f"{{PUBLISHED}}; Requirements 4.6/4.7 make PUBLISHED the one publicly visible state"
    )
    # Non-vacuity: at least one state is visible and at least one is not, so the equality is
    # exercised on both sides.
    visible_states = {s for s in SubmissionState if _projects_to_publicly_visible(s)}
    assert visible_states == {SubmissionState.PUBLISHED}, (
        f"the projection makes {sorted(s.value for s in visible_states)} publicly visible; "
        f"exactly {{PUBLISHED}} must be visible"
    )
    _prop_visible_iff_published()


# ---------------------------------------------------------------------------
# Structural rider 1: the public routes actually apply the is_active + moderation_status
# filter the oracle composes with. Without this, the mapping-level property would be
# asserting a filter the code does not run.
# ---------------------------------------------------------------------------


def _public_select_calls(src: str):
    """Yield the text of each ``svc.table("library_strategies").select(...)...execute()`` chain
    in ``library.py`` that gates on ``moderation_status`` (i.e. a public read).

    Split on ``.execute()`` and keep the chains that both select from ``library_strategies``
    and reference ``moderation_status`` as an ``.in_`` filter — those are the public catalogue
    and detail reads. Writer chains (``.update``/``.insert``) and the owner's own-listings read
    (which selects ``moderation_status`` as a column but does not ``.in_``-filter it) are not
    included.
    """
    chains = src.split(".execute()")
    for chain in chains:
        if 'table("library_strategies")' not in chain:
            continue
        if ".in_(" not in chain or "moderation_status" not in chain:
            continue
        yield chain


def test_public_routes_apply_the_active_approved_filter() -> None:
    """Every public ``library_strategies`` read gates on ``is_active = True`` AND
    ``moderation_status in [...approved...]`` — the two-column filter P-50's oracle composes
    with the projection. At least one such read exists (browse), and each one that filters on
    moderation_status also requires ``is_active = True``."""
    src = LIBRARY_ROUTER.read_text(encoding="utf-8")
    public_reads = list(_public_select_calls(src))
    assert public_reads, (
        "no public library_strategies read applies an .in_(\"moderation_status\", ...) "
        "filter in library.py; the catalogue would not gate visibility at all"
    )
    for chain in public_reads:
        assert re.search(
            r"\.eq\(\s*['\"]is_active['\"]\s*,\s*True\s*\)", chain
        ), (
            "a public library_strategies read filters moderation_status but not "
            "is_active = True; a suspended/unpublished Listing (is_active False) could leak"
        )
        assert re.search(
            r"\.in_\(\s*['\"]moderation_status['\"]\s*,\s*\[[^\]]*['\"]approved['\"]",
            chain,
        ) or "approved" in "".join(
            re.findall(
                r"\.in_\(\s*['\"]moderation_status['\"]\s*,\s*\[([^\]]*)\]",
                chain,
            )
        ), (
            "a public library_strategies read does not admit moderation_status 'approved'; a "
            "PUBLISHED Submission projects to 'approved' and would be invisible"
        )
    # And the composed oracle only admits 'approved'/'featured' — never 'pending'/'rejected',
    # the statuses every non-PUBLISHED state projects to.
    assert "pending" not in CATALOGUE_VISIBLE_STATUSES, (
        "the public filter admits 'pending'; DRAFT/SUBMITTED/UNDER_REVIEW/APPROVED all "
        "project to 'pending' and would leak into the catalogue"
    )
    assert "rejected" not in CATALOGUE_VISIBLE_STATUSES, (
        "the public filter admits 'rejected'; REJECTED/SUSPENDED/UNPUBLISHED project to "
        "'rejected' and would leak into the catalogue"
    )


# ---------------------------------------------------------------------------
# Structural rider 2: the 007 projection makes only PUBLISHED active, and its moderation_status
# CASE agrees with MODERATION_STATUS_FOR_STATE for all eight states. This is layer (a).
# ---------------------------------------------------------------------------


def _projection_function_body() -> str:
    """The body of ``public.marketplace_project_listing_state()`` in the 007 migration."""
    sql = SUBMISSIONS_MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.marketplace_project_listing_state"
        r"\s*\(\s*\)\s*RETURNS\s+TRIGGER\s+AS\s+\$\$(?P<body>.*?)\$\$\s*LANGUAGE",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "no `CREATE OR REPLACE FUNCTION public.marketplace_project_listing_state() RETURNS "
        "TRIGGER` found in 007; P-50 cannot verify the projection layer"
    )
    body = match.group("body")
    assert body.strip(), "the projection function body is empty"
    return body


def test_projection_makes_only_published_active_and_approved() -> None:
    """Layer (a): the projection sets ``is_active := (submission_state = 'PUBLISHED')`` and its
    moderation_status CASE equals ``MODERATION_STATUS_FOR_STATE`` for all eight states — so the
    only state that projects to a catalogue-visible row is PUBLISHED."""
    body = _projection_function_body()

    # is_active is exactly "state = 'PUBLISHED'" — the one clause that makes only PUBLISHED
    # active. Matches `target_active := (NEW.submission_state = 'PUBLISHED')`.
    assert re.search(
        r"NEW\.submission_state\s*=\s*'PUBLISHED'",
        body,
        re.IGNORECASE,
    ), (
        "the projection does not set is_active from (submission_state = 'PUBLISHED'); "
        "IS_ACTIVE_FOR_STATE makes exactly PUBLISHED active and the trigger must match"
    )

    # The moderation_status CASE has one WHEN per state, agreeing with MODERATION_STATUS_FOR_STATE.
    case_pairs = dict(
        re.findall(
            r"WHEN\s+'([A-Z_]+)'\s+THEN\s+target_status\s*:=\s*'([a-z]+)'",
            body,
        )
    )
    for state, expected in MODERATION_STATUS_FOR_STATE.items():
        assert case_pairs.get(state.value) == expected, (
            f"the 007 projection maps {state.value} -> "
            f"{case_pairs.get(state.value)!r}, but MODERATION_STATUS_FOR_STATE says "
            f"{expected!r}; the trigger and the Python mapping have drifted (Requirement 4.12)"
        )
    # All eight states are covered — no state is silently omitted from the CASE.
    assert set(case_pairs) == {s.value for s in SubmissionState}, (
        f"the projection CASE covers {sorted(case_pairs)}, not all eight Submission_State "
        f"values; a missing state would fall through to the RAISE or be misprojected"
    )


# ---------------------------------------------------------------------------
# Structural rider 3: the indistinguishability half. The public detail route .single()s the
# same filter and raises a 404 "not found or not publicly available" — the SAME status and body
# a genuinely-absent id yields, so a non-owner cannot tell a non-visible listing from one that
# exists in no tenant (Requirement 6.10 / 4.7).
# ---------------------------------------------------------------------------


def _detail_route_source() -> str:
    """The source of the ``get_library_detail`` function in ``library.py``."""
    src = LIBRARY_ROUTER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "get_library_detail"
        ):
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(
        "get_library_detail not found in library.py; the public detail route is what makes a "
        "non-visible listing indistinguishable from an absent one"
    )


def test_detail_route_is_indistinguishable_from_absent() -> None:
    """The public detail route applies the same ``is_active``+``moderation_status`` filter,
    ``.single()``s it, and on an empty result raises ONE not-found error whose body does not
    distinguish a non-visible listing from a truly absent id — the indistinguishability half of
    P-50.

    There is a single not-found path: whether the id names no row at all, or names a row whose
    Submission_State projects it out of the catalogue, the filtered ``.single()`` matches nothing
    and the SAME shared ``NOT_FOUND`` error is raised — one code, one HTTP status, one generic
    message from ``errors.PUBLIC_MESSAGE_FOR_CODE``. A non-owner therefore receives an identical
    status and body in both cases.

    Task 16.1 replaced the hand-written ``HTTPException(404, detail="…")`` with that shared
    shape (``design.md`` → the error catalogue: ``NOT_FOUND`` 404, "the single shape used for
    every cross-tenant reference"). The assertion below tracks the shape, not the spelling: what
    it pins is unchanged — exactly ONE not-found raise in the handler, carrying a generic body."""
    detail_src = _detail_route_source()

    # Same public filter as the catalogue: is_active True + moderation_status approved/featured.
    assert re.search(r"\.eq\(\s*['\"]is_active['\"]\s*,\s*True\s*\)", detail_src), (
        "the detail route does not filter is_active = True; a non-active (e.g. SUSPENDED) "
        "listing could be served, making it distinguishable from an absent id"
    )
    assert re.search(
        r"\.in_\(\s*['\"]moderation_status['\"]\s*,\s*\[[^\]]*['\"]approved['\"]",
        detail_src,
    ), (
        "the detail route does not gate moderation_status on 'approved'; visibility would not "
        "track the projected state"
    )
    # It reduces to a single row and raises 404 on absence.
    assert ".single()" in detail_src, (
        "the detail route does not .single() the filtered read; without it the not-found "
        "shape for a filtered-out listing could differ from an absent id"
    )
    # Exactly ONE not-found raise: the shared NOT_FOUND shape, and no second, differently
    # worded 404 alongside it. Both spellings are counted, so a regression that reintroduced a
    # bespoke `HTTPException(404, detail="…")` next to the shared one is caught as two paths.
    shared_not_found = re.findall(
        r"raise\s+MarketplaceError\(\s*NOT_FOUND\b", detail_src
    )
    bespoke_404s = re.findall(r"HTTP_404_NOT_FOUND", detail_src)
    assert len(shared_not_found) + len(bespoke_404s) == 1, (
        f"the detail route has {len(shared_not_found) + len(bespoke_404s)} not-found paths "
        f"({len(shared_not_found)} shared NOT_FOUND, {len(bespoke_404s)} bespoke 404); there "
        f"must be exactly ONE so a filtered-out listing and an absent id are indistinguishable"
    )
    assert len(shared_not_found) == 1, (
        "the detail route's not-found path does not raise the shared "
        "MarketplaceError(NOT_FOUND); the one shape used for every cross-tenant reference is "
        "what makes a hidden listing and an absent id answer identically (Requirements 6.10, "
        "21.4)"
    )
    # The one body is generic: the shared code's single client-facing sentence, which speaks to
    # both causes and names no listing, owner or Submission_State.
    assert HTTP_STATUS_FOR_CODE[NOT_FOUND] == 404, (
        f"the shared NOT_FOUND code maps to HTTP {HTTP_STATUS_FOR_CODE[NOT_FOUND]}, not 404; "
        f"the detail route's hidden-listing answer must be a plain not-found"
    )
    body_text = PUBLIC_MESSAGE_FOR_CODE[NOT_FOUND].lower()
    assert "not found" in body_text or "not publicly available" in body_text, (
        f"the shared NOT_FOUND message {PUBLIC_MESSAGE_FOR_CODE[NOT_FOUND]!r} does not read as "
        f"a generic not-found/not-available message; it must not reveal that a specific "
        f"listing exists but is hidden"
    )
    for leak in ("listing", "owner", "publish", "suspend", "moderation"):
        assert leak not in body_text, (
            f"the shared NOT_FOUND message {PUBLIC_MESSAGE_FOR_CODE[NOT_FOUND]!r} mentions "
            f"{leak!r}; the body must disclose neither the Listing's existence, its owner nor "
            f"its Submission_State (Requirement 6.10)"
        )


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
