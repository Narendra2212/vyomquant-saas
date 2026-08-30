"""
backend_app/backend/paper/errors.py - the Paper_Trading_API's structured error type.

Spec: marketplace-subscriptions-paper-trading task 5.5. ``design.md`` -> "Error Handling" ->
"Structured errors" and "The error code catalogue". Requirements 1.5, 1.7, 22.9, 26.1.

Exposes
-------
PaperError                      the Paper_Trading_API half of Requirement 22.9's structured error
PAPER_CODES                     every ``PAPER_*`` code, in the catalogue's order
PUBLIC_MESSAGE_FOR_CODE         the same dict object ``marketplace/errors.py`` owns
HTTP_STATUS_FOR_CODE            the same status table
paper_persistence_unavailable   the one refusal that must name a pending migration file
plus the code constants and the handler helpers, re-exported so a paper module never has to
import from the marketplace package by name.

WHY THE CATALOGUE IS NOT COPIED HERE
------------------------------------
``design.md`` prints one error-code table with one status per code and states that
``errors.PUBLIC_MESSAGE_FOR_CODE`` is "the single place a client-facing sentence is written".
So there is one dict, in ``marketplace/errors.py``, and this module re-exports *that object* -
not a copy, not a merge. Two dicts would be two places to edit and one place to forget, which
is exactly the drift Requirement 22.9's mechanical test exists to catch.

The import direction is paper -> marketplace and never the reverse, so there is no cycle. It is
also the only direction that can work: one FastAPI handler must serve both error types, so the
base class both types share has to live in whichever module the other one imports.

WHAT ``PaperError`` ADDS OVER THE BASE
--------------------------------------
Only the domain restriction: a ``PaperError`` may carry a ``PAPER_*`` code or one of the three
codes belonging to neither domain (``EXECUTION_ENVIRONMENT_MISMATCH``,
``EXECUTION_ENVIRONMENT_UNRESOLVED``, ``NOT_FOUND``). A marketplace code raised from a paper
path is a programming error and is refused at construction, before a response can carry it.

NO MEMORY FALLBACK
------------------
:func:`paper_persistence_unavailable` exists because ``design.md`` -> "Degradation posture" is
explicit that an unapplied paper migration is an outage answered with 503 naming the file, never
an in-memory balance: "a fabricated balance is worse than an outage" (Requirements 17.2, 28.3).
The file name travels in ``details``, not in the sentence, because the public sentence must stay
free of file names and digits (Requirement 22.9, and task 16.5's mechanical check).
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Mapping, Optional

from backend_app.backend.marketplace.errors import (
    ALLOWED_HTTP_STATUS_FOR_CODE,
    ERROR_CODES,
    EXECUTION_ENVIRONMENT_MISMATCH,
    EXECUTION_ENVIRONMENT_UNRESOLVED,
    FORBIDDEN_BODY_SUBSTRINGS,
    HTTP_STATUS_FOR_CODE,
    NOT_FOUND,
    PAPER_CODES,
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_IDEMPOTENCY_CONFLICT,
    PAPER_INSUFFICIENT_FUNDS,
    PAPER_INVARIANT_VIOLATION,
    PAPER_MARKET_DATA_UNAVAILABLE,
    PAPER_ORDER_INVALID,
    PAPER_OVER_FILL,
    PAPER_PERSISTENCE_UNAVAILABLE,
    PAPER_SESSION_LIMIT_REACHED,
    PAPER_SESSION_OPERATION_REJECTED,
    PAPER_SIMULATOR_MISCONFIGURED,
    PAPER_START_REFUSED,
    PUBLIC_MESSAGE_FOR_CODE,
    REQUEST_ID_HEADER,
    SHARED_CODES,
    StructuredError,
    current_request_id,
    http_status_for_code,
    is_known_code,
    message_for_code,
    redact_details,
    register_structured_error_handlers,
    structured_error_body,
    structured_error_handler,
)

#: The codes a :class:`PaperError` may carry: its own domain plus the three shared ones.
PAPER_DOMAIN_CODES: FrozenSet[str] = frozenset(PAPER_CODES + SHARED_CODES)


class PaperError(StructuredError):
    """A Paper_Trading_API error. Carries a ``PAPER_*`` code or a shared one.

    ``PaperError(code, http_status, message, details)`` - the same four-argument shape
    ``MarketplaceError`` has, with the same defaults: ``http_status`` and ``message`` come from
    the catalogue unless the raiser overrides them, and only ``PAPER_START_REFUSED`` may choose
    its status (403, 409 or 422 per Requirement 17.13).
    """

    DOMAIN_CODES: FrozenSet[str] = PAPER_DOMAIN_CODES


def paper_persistence_unavailable(
    migration: str,
    details: Optional[Mapping[str, Any]] = None,
) -> PaperError:
    """The 503 for an unapplied paper migration, naming ``migration`` in ``details``.

    ``design.md`` -> "Degradation posture" requires the refusal to name the file so an operator
    reading the response knows what to apply, while Requirement 22.9 keeps the sentence itself
    free of file names. Both hold by putting the name in ``details["migration"]``: a migration
    file name is a deployment artifact, not an internal path and not another tenant's
    identifier, and it is the one piece of information that makes this outage actionable.

    Pass the bare file name, not a path: ``redact_details``' deny-list treats a repository path
    as an internal disclosure and replaces it, which is the correct outcome but a less useful
    one for the operator reading the response.

    There is no ``fallback`` parameter and no in-memory branch anywhere near this function. A
    caller that cannot read a paper balance refuses; it does not invent one (Requirements 17.2,
    28.3).
    """
    merged: Dict[str, Any] = {"migration": str(migration)}
    if details:
        merged.update(dict(details))
    return PaperError(PAPER_PERSISTENCE_UNAVAILABLE, details=merged)


__all__ = [
    "ALLOWED_HTTP_STATUS_FOR_CODE",
    "ERROR_CODES",
    "EXECUTION_ENVIRONMENT_MISMATCH",
    "EXECUTION_ENVIRONMENT_UNRESOLVED",
    "FORBIDDEN_BODY_SUBSTRINGS",
    "HTTP_STATUS_FOR_CODE",
    "NOT_FOUND",
    "PAPER_CODES",
    "PAPER_CONCURRENCY_CONFLICT",
    "PAPER_DOMAIN_CODES",
    "PAPER_IDEMPOTENCY_CONFLICT",
    "PAPER_INSUFFICIENT_FUNDS",
    "PAPER_INVARIANT_VIOLATION",
    "PAPER_MARKET_DATA_UNAVAILABLE",
    "PAPER_ORDER_INVALID",
    "PAPER_OVER_FILL",
    "PAPER_PERSISTENCE_UNAVAILABLE",
    "PAPER_SESSION_LIMIT_REACHED",
    "PAPER_SESSION_OPERATION_REJECTED",
    "PAPER_SIMULATOR_MISCONFIGURED",
    "PAPER_START_REFUSED",
    "PUBLIC_MESSAGE_FOR_CODE",
    "REQUEST_ID_HEADER",
    "SHARED_CODES",
    "PaperError",
    "StructuredError",
    "current_request_id",
    "http_status_for_code",
    "is_known_code",
    "message_for_code",
    "paper_persistence_unavailable",
    "redact_details",
    "register_structured_error_handlers",
    "structured_error_body",
    "structured_error_handler",
]
