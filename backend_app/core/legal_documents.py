"""LEGAL-01: the version identity of the published legal documents, server side.

WHY A SECOND COPY OF THE VERSION STRING EXISTS
==============================================
The documents themselves live in exactly one place --
``algo22-terminal/src/legal/documents.js`` -- and are not duplicated here. What
IS duplicated is the VERSION STRING, deliberately, because the two sides need it
for different reasons and neither can read the other's at runtime:

* the browser needs it to say which version the user was shown and accepted;
* the server needs it to validate that claim and to decide whether an existing
  acceptance is current.

A server that simply trusted the version the client sent would record whatever
the client said, which is precisely the weakness being fixed: the old signup
checkbox was client state that nothing verified and nothing persisted. So the
server holds its own copy and REFUSES a version it does not recognise.

``tests/test_legal01_version_agreement.py`` asserts this constant equals the
JavaScript one, so the two cannot drift. If that test fails, one side was bumped
and the other was not, and acceptances recorded in between would name a version
the other side does not have.

WHAT AN ACCEPTANCE MEANS
========================
A row in ``public.legal_acceptances`` asserts: this user, at this instant, from
this address, accepted this set of documents at this version. It is append-only.
Withdrawing acceptance is not an update -- a user who declines a new version
simply has no row for it, and ``is_acceptance_current`` returns False.
"""
from __future__ import annotations

import logging
import re
from typing import Final, FrozenSet, Optional

logger = logging.getLogger("LegalDocuments")

#: The version of the legal documents this build publishes.
#: MUST equal LEGAL_VERSION in algo22-terminal/src/legal/documents.js.
LEGAL_VERSION: Final[str] = "2026-10-23"

#: Human-facing effective date, mirrored from the same source.
LEGAL_EFFECTIVE_DATE: Final[str] = "23 October 2026"

#: The document ids a signup must accept. Mirrors SIGNUP_REQUIRED_DOCUMENTS.
REQUIRED_DOCUMENTS: Final[FrozenSet[str]] = frozenset(
    {"terms", "privacy", "risk", "refund"}
)

#: Versions this deployment will accept a record for. A user who accepted an
#: earlier version still has a valid historical record -- this is the set the
#: server will CREATE a record for, which is only ever the current one. Accepting
#: an arbitrary client-supplied version would let a client record agreement to
#: text that was never published.
_ACCEPTABLE_VERSIONS: Final[FrozenSet[str]] = frozenset({LEGAL_VERSION})

#: A version is a date, ``YYYY-MM-DD``. Validated rather than assumed so a
#: malformed value is refused at the boundary instead of reaching the database.
_VERSION_PATTERN: Final[re.Pattern] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LegalAcceptanceRefused(Exception):
    """An acceptance could not be recorded, with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def validate_version(version: Optional[str]) -> str:
    """Return ``version`` if this deployment published it, else refuse.

    Raises:
        LegalAcceptanceRefused: ``LEGAL_VERSION_MALFORMED`` or
            ``LEGAL_VERSION_UNKNOWN``.
    """
    candidate = (version or "").strip()
    if not _VERSION_PATTERN.match(candidate):
        raise LegalAcceptanceRefused(
            "LEGAL_VERSION_MALFORMED",
            f"a legal document version must look like YYYY-MM-DD; got {candidate!r}",
        )
    if candidate not in _ACCEPTABLE_VERSIONS:
        # Deliberately names the current version: a client on a stale bundle needs
        # to know it is stale, and the published version is not a secret.
        raise LegalAcceptanceRefused(
            "LEGAL_VERSION_UNKNOWN",
            f"{candidate!r} is not a version this deployment publishes. The current "
            f"version is {LEGAL_VERSION!r}. Reload the application and accept again.",
        )
    return candidate


def validate_documents(documents: Optional[object]) -> FrozenSet[str]:
    """Return the accepted document set, or refuse if it is not the required one.

    Partial acceptance is refused rather than recorded. The signup flow presents
    all four documents together, so a request naming three of them did not come
    from that flow and must not be stored as though the user had agreed to all
    of them -- nor as a partial record the rest of the system has no concept of.

    Raises:
        LegalAcceptanceRefused: ``LEGAL_DOCUMENTS_INCOMPLETE``.
    """
    if not isinstance(documents, (list, tuple, set, frozenset)):
        raise LegalAcceptanceRefused(
            "LEGAL_DOCUMENTS_INCOMPLETE",
            "the accepted documents must be a list of document ids",
        )
    supplied = frozenset(str(d).strip().lower() for d in documents if str(d).strip())
    missing = REQUIRED_DOCUMENTS - supplied
    unknown = supplied - REQUIRED_DOCUMENTS
    if missing or unknown:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(sorted(missing)))
        if unknown:
            detail.append("unrecognised: " + ", ".join(sorted(unknown)))
        raise LegalAcceptanceRefused(
            "LEGAL_DOCUMENTS_INCOMPLETE",
            "an acceptance must cover exactly "
            + ", ".join(sorted(REQUIRED_DOCUMENTS))
            + " (" + "; ".join(detail) + ")",
        )
    return supplied


def is_acceptance_current(accepted_version: Optional[str]) -> bool:
    """Whether a recorded acceptance covers the version now published.

    Used to decide whether to prompt for re-acceptance. A user with no record at
    all is not current, which is the correct answer for every account created
    before this mechanism existed -- there are 182 of them and none has a record,
    so they will all be prompted once.
    """
    return (accepted_version or "").strip() == LEGAL_VERSION


__all__ = [
    "LEGAL_VERSION",
    "LEGAL_EFFECTIVE_DATE",
    "REQUIRED_DOCUMENTS",
    "LegalAcceptanceRefused",
    "validate_version",
    "validate_documents",
    "is_acceptance_current",
]
