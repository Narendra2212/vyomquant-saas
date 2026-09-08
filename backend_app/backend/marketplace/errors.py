"""
backend_app/backend/marketplace/errors.py - the one structured-error catalogue.

Spec: marketplace-subscriptions-paper-trading task 5.5. ``design.md`` -> "Error Handling" ->
"Structured errors" and "The error code catalogue". Requirements 1.5, 1.7, 22.9, 26.1.

Exposes
-------
StructuredError                 the base carrying ``code``, ``http_status``, ``message``, ``details``
MarketplaceError                the Marketplace_API half of Requirement 22.9's structured error
HTTP_STATUS_FOR_CODE            every code in the catalogue -> its HTTP status
ALLOWED_HTTP_STATUS_FOR_CODE    the codes the catalogue gives more than one status
PUBLIC_MESSAGE_FOR_CODE         **the single place a client-facing sentence per code is written**
MARKETPLACE_CODES / PAPER_CODES / SHARED_CODES / ERROR_CODES
http_status_for_code / message_for_code / is_known_code
redact_details                  the client-body scrubber (Requirement 22.9's deny-list)
structured_error_body           ``{"error": {...}, "request_id": ...}``
structured_error_handler        the one FastAPI handler for both domains
register_structured_error_handlers(app)   what ``main.py`` calls once

WHY ONE CATALOGUE AND NOT TWO
-----------------------------
``design.md`` prints the catalogue as one table with one HTTP status per code, and states that
``errors.PUBLIC_MESSAGE_FOR_CODE`` is "the single place a client-facing sentence is written".
One table becomes one dict here, covering the ``MARKETPLACE_*``, the ``PAPER_*`` and the three
codes that belong to neither domain (``EXECUTION_ENVIRONMENT_*``, ``NOT_FOUND``). Splitting the
sentences across two modules would either duplicate the base class and the handler or create an
import cycle between the two packages; instead ``paper/errors.py`` imports from here, defines
``PaperError`` and re-exports the same dict object, so both import paths see one complete
catalogue and no sentence is written twice.

WHY THE MESSAGES CARRY NO NUMBER AND NO INTERNAL NAME
-----------------------------------------------------
Requirements 1.7, 2.10 and 22.9 forbid a stack trace, a database error string, query text, an
internal path, an internal threshold and any foreign internal identifier in a client body.
``tests/test_marketplace_error_surface.py`` (task 16.5) asserts that mechanically: every message
here must be free of ``SELECT``, ``library_strategies``, ``strategy_backtests``, ``Traceback``,
``psycopg``, the SQLSTATE spellings, and of any digit sequence appearing in
``evidence_validator.THRESHOLDS`` or ``pricing_evaluator.WEIGHTS``. The simplest way to keep that
true under future edits is the rule this module actually follows and
:func:`assert_messages_carry_no_internals` enforces: **no message contains a digit at all**, and
no message names a table, a column, a file or a threshold. Anything numeric that a caller
legitimately needs - the permitted price range, the missing evidence fields, the failed
validation - travels in ``details``, which the raiser fills with the caller's *own* values.

WHY ``http_status`` IS AN ARGUMENT AND NOT ONLY A LOOKUP
-------------------------------------------------------
Two rows of the catalogue carry more than one status: ``MARKETPLACE_READ_FAILED`` is 500 or 503
depending on whether the read failed or the dependency was unreachable (Requirement 1.5), and
``PAPER_START_REFUSED`` is 403, 409 or 422 depending on which validation refused the start
(Requirement 17.13). Those two are the only codes with a choice, so the choice is constrained by
:data:`ALLOWED_HTTP_STATUS_FOR_CODE` rather than left open - a caller cannot quietly answer 200
or 404 for a read failure, which is precisely the substitution Requirements 1.5 and 1.7 forbid.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Logging. Requirement 26.5's "log an error for every failure on a correctness-critical path"
  belongs to the raiser, which knows the internal detail that must stay out of the body. This
  module refuses to become the place where an internal string is one attribute away from the
  response.
* Any mapping from a driver exception to a code. Translating ``23505`` on
  ``uq_submission_open_per_strategy`` into ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` needs to know
  which constraint was hit, so it lives in the service that issued the write.
* A bare ``except``. Per ``design.md`` -> "Never-swallow rule", the only exception this module
  swallows is a failure to *read a request identifier*, which is cosmetic and can never change
  an outcome.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════
# THE CODES (design.md -> "The error code catalogue")
# ══════════════════════════════════════════════════════════════════════════

# ---- Marketplace ---------------------------------------------------------
#: One or more ``MP_*``/``EV_*`` criteria failed; ``details["failures"]`` lists **all** of them
#: rather than the first (Requirement 2.10).
MARKETPLACE_ELIGIBILITY_FAILED = "MARKETPLACE_ELIGIBILITY_FAILED"
#: A read required by Requirements 2.2-2.7 did not complete, so eligibility is *unknown* and
#: therefore not admitted - distinguishable by its code from a criteria failure (Req 2.13).
MARKETPLACE_ELIGIBILITY_UNEVALUABLE = "MARKETPLACE_ELIGIBILITY_UNEVALUABLE"
#: The uniqueness constraint on "one open Submission per strategy" refused the insert (Req 2.8).
MARKETPLACE_SUBMISSION_ALREADY_OPEN = "MARKETPLACE_SUBMISSION_ALREADY_OPEN"
#: Unknown Submission, or another owner's Submission - the same body for both (Req 21.4).
MARKETPLACE_SUBMISSION_NOT_FOUND = "MARKETPLACE_SUBMISSION_NOT_FOUND"
#: ``submission_state.can_transition`` refused the edge, or the guard trigger did (Req 4.2, 4.4).
MARKETPLACE_SUBMISSION_TRANSITION_REJECTED = "MARKETPLACE_SUBMISSION_TRANSITION_REJECTED"
#: The legacy ``admin_moderate_strategy`` route was asked to set a ``moderation_status`` that
#: disagrees with the Submission_State's projected value, i.e. to move the lifecycle behind the
#: state machine's back; the six submission actions are the only way to do that (Req 4.2, 4.12).
MARKETPLACE_USE_SUBMISSION_ACTIONS = "MARKETPLACE_USE_SUBMISSION_ACTIONS"
#: Rejection reason absent, whitespace-only, or longer than the permitted maximum (Req 4.8).
MARKETPLACE_REJECTION_REASON_REQUIRED = "MARKETPLACE_REJECTION_REASON_REQUIRED"
#: An attempted mutation of persisted Backtest_Evidence (Requirement 3.14).
MARKETPLACE_EVIDENCE_IMMUTABLE = "MARKETPLACE_EVIDENCE_IMMUTABLE"
#: The evidence copy failed and the whole submission transaction rolled back (Req 3.15).
MARKETPLACE_EVIDENCE_PERSIST_FAILED = "MARKETPLACE_EVIDENCE_PERSIST_FAILED"
#: The submitted price lies outside the persisted Price_Range for the Submission (Req 8.9).
MARKETPLACE_PRICE_OUT_OF_RANGE = "MARKETPLACE_PRICE_OUT_OF_RANGE"
#: A Requirement 8.2 pricing input is absent; ``details["missing"]`` names them (Req 8.14).
MARKETPLACE_PRICE_EVIDENCE_MISSING = "MARKETPLACE_PRICE_EVIDENCE_MISSING"
#: The Listing is not ``PUBLISHED``, or the caller's Subscription is already active (Req 9.10).
MARKETPLACE_LISTING_NOT_PURCHASABLE = "MARKETPLACE_LISTING_NOT_PURCHASABLE"
#: The purchaser is the Listing's owner (Requirement 9.10).
MARKETPLACE_OWN_LISTING = "MARKETPLACE_OWN_LISTING"
#: Provider session creation failed or exceeded its deadline (Requirements 9.4, 9.13).
MARKETPLACE_CHECKOUT_UNAVAILABLE = "MARKETPLACE_CHECKOUT_UNAVAILABLE"
#: ``source_cloning_enabled`` is false on the Listing (Requirement 7.3).
MARKETPLACE_CLONING_DISABLED = "MARKETPLACE_CLONING_DISABLED"
#: No Subscription at all - deliberately distinct from the expired case (Requirement 7.10).
MARKETPLACE_NOT_SUBSCRIBED = "MARKETPLACE_NOT_SUBSCRIBED"
#: A Subscription existed and its period has ended - the other half of Requirement 7.10.
MARKETPLACE_SUBSCRIPTION_EXPIRED = "MARKETPLACE_SUBSCRIPTION_EXPIRED"
#: The executable artifact behind the Listing no longer resolves (Requirement 7.11).
MARKETPLACE_STRATEGY_UNAVAILABLE = "MARKETPLACE_STRATEGY_UNAVAILABLE"
#: A transition into ``ACTIVE`` with no matching Settlement_Record (Requirement 11.14).
MARKETPLACE_PAYMENT_REQUIRED = "MARKETPLACE_PAYMENT_REQUIRED"
#: A restricted subscriber operation on a subscribed strategy (Requirement 12.7).
MARKETPLACE_OPERATION_NOT_PERMITTED = "MARKETPLACE_OPERATION_NOT_PERMITTED"
#: A caller-supplied cover image reference whose parsed origin is not on
#: ``media.ALLOWED_COVER_PREFIXES`` (Requirement 22.7). Raised at WRITE time, by
#: ``media.validate_cover_reference``, before the reference can reach
#: ``library_strategies.cover_image``. ``details["reason"]`` carries one of
#: ``media.REJECTION_REASONS`` - a stable label, never the configured allow-list, which would
#: turn a rejection into a disclosure of the platform's own storage layout.
MARKETPLACE_COVER_REFERENCE_REJECTED = "MARKETPLACE_COVER_REFERENCE_REJECTED"
#: The Audit_Log write failed, so the state change was rolled back (Requirement 5.11).
MARKETPLACE_ACTION_NOT_RECORDED = "MARKETPLACE_ACTION_NOT_RECORDED"
#: Any rate limit in ``design.md`` -> "Security design" (Requirement 22.4).
MARKETPLACE_RATE_LIMITED = "MARKETPLACE_RATE_LIMITED"
#: Any Persistence_Layer read failure. **Never** a zero-filled 200 (Requirements 1.5, 1.7).
MARKETPLACE_READ_FAILED = "MARKETPLACE_READ_FAILED"

# ---- Paper trading -------------------------------------------------------
#: The paper migration is unapplied; ``details`` names the file. No memory fallback, because a
#: fabricated balance is worse than an outage (Requirements 17.2, 28.3).
PAPER_PERSISTENCE_UNAVAILABLE = "PAPER_PERSISTENCE_UNAVAILABLE"
#: The correctness floor blocked, the source is unmeasured, or the mock interface resolved
#: (Requirements 14.4, 14.8).
PAPER_MARKET_DATA_UNAVAILABLE = "PAPER_MARKET_DATA_UNAVAILABLE"
#: The resolved simulator is the forbidden one (Requirement 13.11).
PAPER_SIMULATOR_MISCONFIGURED = "PAPER_SIMULATOR_MISCONFIGURED"
#: A start validation refused; ``details["validation"]`` names it, nothing was created (17.13).
PAPER_START_REFUSED = "PAPER_START_REFUSED"
#: The per-user concurrent Paper_Session cap (Requirement 27.4).
PAPER_SESSION_LIMIT_REACHED = "PAPER_SESSION_LIMIT_REACHED"
#: The operation is not permitted from the session's current state; names both (Req 17.14).
PAPER_SESSION_OPERATION_REJECTED = "PAPER_SESSION_OPERATION_REJECTED"
#: Order validation failed; the order is persisted ``REJECTED`` with the reason (Req 16.5).
PAPER_ORDER_INVALID = "PAPER_ORDER_INVALID"
#: Required funds exceed the available balance; nothing was locked (Requirement 16.6).
PAPER_INSUFFICIENT_FUNDS = "PAPER_INSUFFICIENT_FUNDS"
#: A fill would exceed the order quantity (Requirement 16.7).
PAPER_OVER_FILL = "PAPER_OVER_FILL"
#: An idempotency key was reused with different parameters (Requirement 16.15).
PAPER_IDEMPOTENCY_CONFLICT = "PAPER_IDEMPOTENCY_CONFLICT"
#: The bounded retries on the account row were exhausted (Requirement 16.10).
PAPER_CONCURRENCY_CONFLICT = "PAPER_CONCURRENCY_CONFLICT"
#: An accounting invariant did not hold, so everything rolled back; ``details["invariant"]``
#: names it (Requirement 18.14).
PAPER_INVARIANT_VIOLATION = "PAPER_INVARIANT_VIOLATION"
#: Any paper Persistence_Layer read or write that DID NOT COMPLETE. The paper counterpart of
#: :data:`MARKETPLACE_READ_FAILED`, and the caller-facing status for
#: ``paper_repository.PaperPersistenceError``, which previously had none: ``PaperError`` correctly
#: refuses a ``MARKETPLACE_*`` code, and ``PAPER_PERSISTENCE_UNAVAILABLE`` means specifically
#: *the migration is not applied* - answering it for a transient driver failure would name a
#: migration that IS applied and send an operator to the wrong place. **Never** a zero balance, an
#: empty position list or an absent order presented as an answer: a broken balance read reported
#: as a zero is the fabricated figure Requirement 28.3 forbids, and a broken idempotency read
#: reported as "no such order" would place a second order (Requirements 1.7, 16.8, 17.2, 28.3).
PAPER_READ_FAILED = "PAPER_READ_FAILED"

# ---- Neither domain ------------------------------------------------------
#: The live path was reached with a ``PAPER`` or ``BACKTEST`` environment (Requirement 13.9).
EXECUTION_ENVIRONMENT_MISMATCH = "EXECUTION_ENVIRONMENT_MISMATCH"
#: The environment was absent or outside the set; it never defaults to ``LIVE`` (Req 13.10).
EXECUTION_ENVIRONMENT_UNRESOLVED = "EXECUTION_ENVIRONMENT_UNRESOLVED"
#: The single shape used for every cross-tenant reference, so a probe cannot distinguish
#: "does not exist" from "belongs to somebody else" (Requirement 21.4).
NOT_FOUND = "NOT_FOUND"


#: The ``MARKETPLACE_*`` half of the catalogue, in the table's own order.
MARKETPLACE_CODES: Tuple[str, ...] = (
    MARKETPLACE_ELIGIBILITY_FAILED,
    MARKETPLACE_ELIGIBILITY_UNEVALUABLE,
    MARKETPLACE_SUBMISSION_ALREADY_OPEN,
    MARKETPLACE_SUBMISSION_NOT_FOUND,
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
    MARKETPLACE_USE_SUBMISSION_ACTIONS,
    MARKETPLACE_REJECTION_REASON_REQUIRED,
    MARKETPLACE_EVIDENCE_IMMUTABLE,
    MARKETPLACE_EVIDENCE_PERSIST_FAILED,
    MARKETPLACE_PRICE_OUT_OF_RANGE,
    MARKETPLACE_PRICE_EVIDENCE_MISSING,
    MARKETPLACE_LISTING_NOT_PURCHASABLE,
    MARKETPLACE_OWN_LISTING,
    MARKETPLACE_CHECKOUT_UNAVAILABLE,
    MARKETPLACE_CLONING_DISABLED,
    MARKETPLACE_NOT_SUBSCRIBED,
    MARKETPLACE_SUBSCRIPTION_EXPIRED,
    MARKETPLACE_STRATEGY_UNAVAILABLE,
    MARKETPLACE_PAYMENT_REQUIRED,
    MARKETPLACE_OPERATION_NOT_PERMITTED,
    MARKETPLACE_COVER_REFERENCE_REJECTED,
    MARKETPLACE_ACTION_NOT_RECORDED,
    MARKETPLACE_RATE_LIMITED,
    MARKETPLACE_READ_FAILED,
)

#: The ``PAPER_*`` half of the catalogue, in the table's own order.
PAPER_CODES: Tuple[str, ...] = (
    PAPER_PERSISTENCE_UNAVAILABLE,
    PAPER_MARKET_DATA_UNAVAILABLE,
    PAPER_SIMULATOR_MISCONFIGURED,
    PAPER_START_REFUSED,
    PAPER_SESSION_LIMIT_REACHED,
    PAPER_SESSION_OPERATION_REJECTED,
    PAPER_ORDER_INVALID,
    PAPER_INSUFFICIENT_FUNDS,
    PAPER_OVER_FILL,
    PAPER_IDEMPOTENCY_CONFLICT,
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_INVARIANT_VIOLATION,
    PAPER_READ_FAILED,
)

#: The codes either domain may raise: the execution-environment guards, which sit on a path both
#: domains reach, and the one cross-tenant ``NOT_FOUND`` shape.
SHARED_CODES: Tuple[str, ...] = (
    EXECUTION_ENVIRONMENT_MISMATCH,
    EXECUTION_ENVIRONMENT_UNRESOLVED,
    NOT_FOUND,
)

#: Every code in the catalogue. Nothing outside this tuple may reach a client body.
ERROR_CODES: Tuple[str, ...] = MARKETPLACE_CODES + PAPER_CODES + SHARED_CODES


# ══════════════════════════════════════════════════════════════════════════
# THE HTTP STATUS PER CODE (the catalogue's second column)
# ══════════════════════════════════════════════════════════════════════════

#: Exactly the catalogue's "HTTP" column. For the two codes the table gives a choice, this is
#: the default and :data:`ALLOWED_HTTP_STATUS_FOR_CODE` holds the permitted alternatives.
HTTP_STATUS_FOR_CODE: Dict[str, int] = {
    MARKETPLACE_ELIGIBILITY_FAILED: 422,
    MARKETPLACE_ELIGIBILITY_UNEVALUABLE: 503,
    MARKETPLACE_SUBMISSION_ALREADY_OPEN: 409,
    MARKETPLACE_SUBMISSION_NOT_FOUND: 404,
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED: 409,
    MARKETPLACE_USE_SUBMISSION_ACTIONS: 409,
    MARKETPLACE_REJECTION_REASON_REQUIRED: 422,
    MARKETPLACE_EVIDENCE_IMMUTABLE: 409,
    MARKETPLACE_EVIDENCE_PERSIST_FAILED: 500,
    MARKETPLACE_PRICE_OUT_OF_RANGE: 400,
    MARKETPLACE_PRICE_EVIDENCE_MISSING: 422,
    MARKETPLACE_LISTING_NOT_PURCHASABLE: 409,
    MARKETPLACE_OWN_LISTING: 400,
    MARKETPLACE_CHECKOUT_UNAVAILABLE: 502,
    MARKETPLACE_CLONING_DISABLED: 403,
    MARKETPLACE_NOT_SUBSCRIBED: 403,
    MARKETPLACE_SUBSCRIPTION_EXPIRED: 403,
    MARKETPLACE_STRATEGY_UNAVAILABLE: 409,
    MARKETPLACE_PAYMENT_REQUIRED: 409,
    MARKETPLACE_OPERATION_NOT_PERMITTED: 403,
    # Requirement 22.7's refusal, and pinned to 422 - absent from
    # ALLOWED_HTTP_STATUS_FOR_CODE, so no call site can answer 200 and let an unvetted
    # reference through, and none can answer 404 and turn a validation refusal into a
    # statement about whether the listing exists.
    MARKETPLACE_COVER_REFERENCE_REJECTED: 422,
    MARKETPLACE_ACTION_NOT_RECORDED: 500,
    MARKETPLACE_RATE_LIMITED: 429,
    # Requirement 1.5 permits 500 or 503. 503 is the default because the overwhelmingly common
    # cause is an unreachable or slow dependency; an undefined column or a permission denial is
    # the caller-invisible-but-our-fault case and is raised explicitly with 500.
    MARKETPLACE_READ_FAILED: 503,
    PAPER_PERSISTENCE_UNAVAILABLE: 503,
    PAPER_MARKET_DATA_UNAVAILABLE: 409,
    PAPER_SIMULATOR_MISCONFIGURED: 500,
    # Requirement 17.13 permits 403, 409 or 422. 409 is the default: most start validations fail
    # on a state the caller could not have known, not on authorisation or on a malformed body.
    PAPER_START_REFUSED: 409,
    PAPER_SESSION_LIMIT_REACHED: 429,
    PAPER_SESSION_OPERATION_REJECTED: 409,
    PAPER_ORDER_INVALID: 400,
    PAPER_INSUFFICIENT_FUNDS: 400,
    PAPER_OVER_FILL: 409,
    PAPER_IDEMPOTENCY_CONFLICT: 409,
    PAPER_CONCURRENCY_CONFLICT: 409,
    PAPER_INVARIANT_VIOLATION: 500,
    # 503, and pinned to it - deliberately NOT given a choice in
    # ALLOWED_HTTP_STATUS_FOR_CODE the way MARKETPLACE_READ_FAILED is. A paper read or write that
    # did not complete is a Persistence_Layer that did not answer, which is a dependency
    # condition; the caller-invisible-but-our-fault cases the marketplace code reserves 500 for
    # already have their own codes here (PAPER_INVARIANT_VIOLATION 500,
    # PAPER_SIMULATOR_MISCONFIGURED 500). One status means no call site can turn a failed balance
    # read into a 200 (Requirements 1.7, 28.3).
    PAPER_READ_FAILED: 503,
    EXECUTION_ENVIRONMENT_MISMATCH: 409,
    EXECUTION_ENVIRONMENT_UNRESOLVED: 409,
    NOT_FOUND: 404,
}

#: The only two codes a raiser may give a status other than the default, and the only statuses
#: it may choose from. Every other code is pinned to one status, so no call site can turn a
#: read failure into a 200 or a 404 (Requirements 1.5, 1.7).
ALLOWED_HTTP_STATUS_FOR_CODE: Dict[str, FrozenSet[int]] = {
    MARKETPLACE_READ_FAILED: frozenset({500, 503}),
    PAPER_START_REFUSED: frozenset({403, 409, 422}),
}


# ══════════════════════════════════════════════════════════════════════════
# THE ONE PLACE A CLIENT-FACING SENTENCE IS WRITTEN
# ══════════════════════════════════════════════════════════════════════════

#: One sentence per code, and the only sentence per code. Read the module docstring before
#: editing: **no digit, no table name, no column name, no file name, no threshold, no internal
#: identifier**. Everything a caller needs that is numeric or specific goes in ``details``,
#: filled by the raiser from the caller's own values.
PUBLIC_MESSAGE_FOR_CODE: Dict[str, str] = {
    MARKETPLACE_ELIGIBILITY_FAILED: (
        "This strategy does not yet meet the conditions for publication. "
        "The accompanying failures list names every condition that did not pass."
    ),
    MARKETPLACE_ELIGIBILITY_UNEVALUABLE: (
        "Publication eligibility could not be evaluated right now, so nothing was submitted. "
        "Please try again shortly."
    ),
    MARKETPLACE_SUBMISSION_ALREADY_OPEN: (
        "A submission for this strategy is already open. "
        "Complete or withdraw it before starting another."
    ),
    MARKETPLACE_SUBMISSION_NOT_FOUND: "That submission was not found.",
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED: (
        "That action is not permitted from the submission's current state, "
        "so nothing was changed."
    ),
    MARKETPLACE_USE_SUBMISSION_ACTIONS: (
        "The moderation state of a listing cannot be changed here. "
        "Use the submission review actions to move it, so nothing was changed."
    ),
    MARKETPLACE_REJECTION_REASON_REQUIRED: (
        "A rejection reason is required, and it must contain visible text and stay within the "
        "permitted length."
    ),
    MARKETPLACE_EVIDENCE_IMMUTABLE: (
        "The backtest evidence recorded on a submission cannot be changed."
    ),
    MARKETPLACE_EVIDENCE_PERSIST_FAILED: (
        "The backtest evidence could not be stored, so no submission was created and nothing "
        "was changed. Please try again."
    ),
    MARKETPLACE_PRICE_OUT_OF_RANGE: (
        "The submitted price is outside the permitted range for this listing."
    ),
    MARKETPLACE_PRICE_EVIDENCE_MISSING: (
        "A price range cannot be evaluated because some required backtest figures are missing. "
        "The accompanying missing list names them."
    ),
    MARKETPLACE_LISTING_NOT_PURCHASABLE: "This listing is not available for purchase.",
    MARKETPLACE_OWN_LISTING: "You cannot subscribe to your own listing.",
    MARKETPLACE_CHECKOUT_UNAVAILABLE: (
        "Checkout could not be started because the payment provider did not respond in time. "
        "You have not been charged."
    ),
    MARKETPLACE_CLONING_DISABLED: (
        "The owner of this strategy has not allowed copies to be made from it."
    ),
    MARKETPLACE_NOT_SUBSCRIBED: (
        "An active subscription to this listing is required for this operation."
    ),
    MARKETPLACE_SUBSCRIPTION_EXPIRED: (
        "Your subscription to this listing has ended. Renew it to continue."
    ),
    MARKETPLACE_STRATEGY_UNAVAILABLE: (
        "The runnable version of this strategy is currently unavailable, so it cannot be used."
    ),
    MARKETPLACE_PAYMENT_REQUIRED: (
        "This subscription cannot become active until a confirmed payment has been recorded "
        "for it."
    ),
    MARKETPLACE_OPERATION_NOT_PERMITTED: (
        "This operation is not permitted on a strategy you subscribe to rather than own."
    ),
    MARKETPLACE_COVER_REFERENCE_REJECTED: (
        "That cover image reference is not one this platform accepts, so nothing was saved. "
        "Upload the image through the platform and use the reference it gives back."
    ),
    MARKETPLACE_ACTION_NOT_RECORDED: (
        "The action could not be recorded, so it was not applied and nothing was changed."
    ),
    MARKETPLACE_RATE_LIMITED: (
        "Too many requests were made in a short time. Please wait a moment and try again."
    ),
    MARKETPLACE_READ_FAILED: (
        "This information could not be read right now, so no figures are being shown. "
        "Please try again shortly."
    ),
    PAPER_PERSISTENCE_UNAVAILABLE: (
        "Paper trading storage is not ready on this server, so no session can run. "
        "The accompanying migration entry names what is pending."
    ),
    PAPER_MARKET_DATA_UNAVAILABLE: (
        "Validated market data is not available for this request, and paper trading never runs "
        "on invented prices."
    ),
    PAPER_SIMULATOR_MISCONFIGURED: (
        "Paper trading is not correctly configured on this server, so nothing was executed."
    ),
    PAPER_START_REFUSED: (
        "The paper session was not started. The accompanying validation entry names the check "
        "that refused it, and no session, order or balance was created."
    ),
    PAPER_SESSION_LIMIT_REACHED: (
        "You already have as many paper sessions running as are permitted. "
        "Stop one before starting another."
    ),
    PAPER_SESSION_OPERATION_REJECTED: (
        "That operation is not permitted while the session is in its current state, "
        "so nothing was changed."
    ),
    PAPER_ORDER_INVALID: (
        "The order did not pass validation and was recorded as rejected. "
        "The accompanying reason names what failed."
    ),
    PAPER_INSUFFICIENT_FUNDS: (
        "The order needs more funds than this paper account has available, "
        "so no funds were reserved."
    ),
    PAPER_OVER_FILL: (
        "The fill would exceed the quantity remaining on this order, so it was not applied."
    ),
    PAPER_IDEMPOTENCY_CONFLICT: (
        "This idempotency key was already used with different order parameters. "
        "Use a new key, or repeat the original request unchanged."
    ),
    PAPER_CONCURRENCY_CONFLICT: (
        "This paper account was being changed by another request and the attempt was given up. "
        "Nothing was changed; please try again."
    ),
    PAPER_INVARIANT_VIOLATION: (
        "The operation was undone because an accounting check did not hold. Your balances, "
        "positions and results are unchanged."
    ),
    PAPER_READ_FAILED: (
        "Your paper trading information could not be read right now, so no balances, positions "
        "or figures are being shown. Nothing was changed. Please try again shortly."
    ),
    EXECUTION_ENVIRONMENT_MISMATCH: (
        "This operation cannot run in the execution environment it was requested for, "
        "so nothing was executed."
    ),
    EXECUTION_ENVIRONMENT_UNRESOLVED: (
        "The execution environment for this operation could not be determined, "
        "so nothing was executed."
    ),
    NOT_FOUND: "The requested resource was not found.",
}


# ══════════════════════════════════════════════════════════════════════════
# THE DENY-LIST (Requirement 22.9, enforced rather than promised)
# ══════════════════════════════════════════════════════════════════════════

#: Substrings that must never appear in a client-facing message or in a ``details`` value.
#: The first group is ``design.md``'s own list for task 16.5; the rest are the shapes a leaked
#: driver error, query or traceback actually takes. Matched case-insensitively, so ``select``
#: and ``SELECT`` are both refused.
FORBIDDEN_BODY_SUBSTRINGS: Tuple[str, ...] = (
    "select",
    "insert into",
    "update ",
    "delete from",
    "library_strategies",
    "strategy_backtests",
    "marketplace_listings",
    "strategy_subscriptions",
    "traceback",
    "psycopg",
    "sqlstate",
    "42703",
    "23505",
    "23514",
    "most recent call last",
    "site-packages",
    "backend_app",
    "c:\\",
    "/usr/",
)

#: The keys the ``"error"`` member of an error body carries, and the only keys it carries.
#: ``design.md`` -> "Structured errors" prints the body as ``{"error": {code, message, details},
#: "request_id"}``; this is that inner object's key set, declared once so
#: :func:`structured_error_body` and the tests that assert the envelope shape read the same
#: definition rather than two copies of the same literal (Requirement 30.2).
ERROR_OBJECT_KEYS: FrozenSet[str] = frozenset({"code", "message", "details"})

#: The keys the error body itself carries, and the only keys it carries. Nothing else is added -
#: no ``path``, no ``timestamp`` (see :func:`structured_error_body` for why).
ERROR_ENVELOPE_KEYS: FrozenSet[str] = frozenset({"error", "request_id"})

#: Every key an error body contributes that is *structure* rather than *content*. A test that
#: walks a response body looking for something that must not be there - a Protected_Logic
#: substring, an internal identifier - needs to know which keys are the envelope's own scaffolding
#: and therefore never carry caller data. Declared here, beside the envelope it describes, so
#: every such test shares one definition.
STRUCTURAL_RESPONSE_KEYS: FrozenSet[str] = ERROR_ENVELOPE_KEYS | ERROR_OBJECT_KEYS

#: What :func:`redact_details` puts in place of a value that trips the deny-list. A fixed
#: string rather than the original, and never silently dropped: an omitted key and a redacted
#: key mean different things (Requirement 28.5's distinction).
REDACTED_PLACEHOLDER = "[redacted]"

_DIGIT_RE = re.compile(r"\d")


# ══════════════════════════════════════════════════════════════════════════
# THE ERROR TYPES
# ══════════════════════════════════════════════════════════════════════════


class StructuredError(Exception):
    """The shape every Marketplace_API and Paper_Trading_API error takes on the wire.

    ``design.md`` -> "Structured errors": one ``code``, one safe ``message``, an optional
    ``details`` object of the caller's *own* values, and the request identifier added by the
    handler. Subclassed rather than used directly so a raiser states which surface it is on;
    :func:`register_structured_error_handlers` registers one handler against *this* class and
    Starlette's handler lookup walks the exception's ``__mro__``, so both subclasses are served
    by the one handler the task asks for.

    ``DOMAIN_CODES`` is the set of codes a subclass may carry. It exists so that a marketplace
    handler cannot answer with a paper code, or the reverse: the catalogue is shared, the
    surfaces are not.
    """

    #: Overridden by each subclass. Empty here means "any code in the catalogue".
    DOMAIN_CODES: FrozenSet[str] = frozenset(ERROR_CODES)

    def __init__(
        self,
        code: str,
        http_status: Optional[int] = None,
        message: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if code not in HTTP_STATUS_FOR_CODE:
            raise ValueError(
                f"{code!r} is not in the error catalogue; add it to "
                "marketplace/errors.py with its HTTP status and its public message "
                "before raising it"
            )
        if code not in self.DOMAIN_CODES:
            raise ValueError(
                f"{code!r} is not a code {type(self).__name__} may raise"
            )

        resolved_status = (
            HTTP_STATUS_FOR_CODE[code] if http_status is None else int(http_status)
        )
        permitted = ALLOWED_HTTP_STATUS_FOR_CODE.get(
            code, frozenset({HTTP_STATUS_FOR_CODE[code]})
        )
        if resolved_status not in permitted:
            raise ValueError(
                f"{code!r} may only answer with "
                f"{sorted(permitted)}, not {resolved_status}"
            )

        self.code: str = code
        self.http_status: int = resolved_status
        #: The public sentence. Defaults to the catalogue's, which is the normal case; an
        #: override is still passed through :func:`redact_details`' deny-list by the handler.
        self.message: str = (
            PUBLIC_MESSAGE_FOR_CODE[code] if message is None else str(message)
        )
        self.details: Dict[str, Any] = dict(details or {})
        super().__init__(f"{code}: {self.message}")

    def to_error_object(self) -> Dict[str, Any]:
        """The ``"error"`` member of the response body, already scrubbed."""
        return {
            "code": self.code,
            "message": _redact_text(self.message),
            "details": redact_details(self.details),
        }

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"http_status={self.http_status!r}, details={sorted(self.details)!r})"
        )


class MarketplaceError(StructuredError):
    """A Marketplace_API error. Carries a ``MARKETPLACE_*`` code or a shared one."""

    DOMAIN_CODES: FrozenSet[str] = frozenset(MARKETPLACE_CODES + SHARED_CODES)


# ══════════════════════════════════════════════════════════════════════════
# THE LOOKUPS
# ══════════════════════════════════════════════════════════════════════════


def is_known_code(code: Any) -> bool:
    """Whether ``code`` is in the catalogue."""
    return isinstance(code, str) and code in HTTP_STATUS_FOR_CODE


def http_status_for_code(code: str) -> int:
    """The catalogue's HTTP status for ``code``. Raises ``KeyError`` for an unknown code.

    Deliberately not ``.get(code, 500)``: a code with no declared status is a gap in the
    catalogue, and answering 500 for it would hide that gap for as long as nobody looked.
    """
    return HTTP_STATUS_FOR_CODE[code]


def message_for_code(code: str) -> str:
    """The one client-facing sentence for ``code``. Raises ``KeyError`` if none is written."""
    return PUBLIC_MESSAGE_FOR_CODE[code]


# ══════════════════════════════════════════════════════════════════════════
# THE SCRUBBER
# ══════════════════════════════════════════════════════════════════════════


def _trips_deny_list(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in FORBIDDEN_BODY_SUBSTRINGS)


def _redact_text(text: str) -> str:
    """``text``, or the placeholder when it carries something Requirement 22.9 forbids."""
    return REDACTED_PLACEHOLDER if _trips_deny_list(text) else text


def redact_details(details: Any, _depth: int = 0) -> Any:
    """``details`` reduced to values that are safe to send and safe to serialise.

    Three jobs, and only three:

    * every string value is checked against :data:`FORBIDDEN_BODY_SUBSTRINGS` and replaced by
      :data:`REDACTED_PLACEHOLDER` when it trips - the safety net under Requirement 22.9 for
      the case where a raiser passes a driver message through by accident;
    * anything that is not a JSON scalar, list or mapping becomes its ``str`` and is then
      checked the same way, so a ``Decimal`` survives as text and an exception object cannot
      smuggle its ``args`` into the body;
    * nesting deeper than a small bound collapses to the placeholder, because a client body is
      not a place to walk an arbitrary object graph.

    It never invents a value and never drops a key: a caller reading ``details`` can tell
    "this was withheld" from "this was not sent".
    """
    if _depth > 4:
        return REDACTED_PLACEHOLDER
    if details is None or isinstance(details, bool) or isinstance(details, int):
        return details
    if isinstance(details, float):
        return details
    if isinstance(details, str):
        return _redact_text(details)
    if isinstance(details, Mapping):
        return {
            str(key): redact_details(value, _depth + 1)
            for key, value in details.items()
        }
    if isinstance(details, (list, tuple, set, frozenset)):
        return [redact_details(item, _depth + 1) for item in details]
    return _redact_text(str(details))


def assert_messages_carry_no_internals(
    messages: Optional[Mapping[str, str]] = None,
    forbidden_digit_sequences: Iterable[str] = (),
) -> None:
    """Raise ``AssertionError`` if any catalogue message leaks an internal.

    The rule the module docstring states, executable: no message contains a digit, and none
    contains a deny-listed substring. ``forbidden_digit_sequences`` is accepted so
    ``tests/test_marketplace_error_surface.py`` can pass the digits of every
    ``evidence_validator.THRESHOLDS`` and ``pricing_evaluator.WEIGHTS`` value; the digit-free
    rule already implies them, and checking both makes the failure message say which one bit.

    Living here rather than only in the test means a module that imports the catalogue can
    assert it at start-up if it wants to; the test remains the enforcement.
    """
    checked = PUBLIC_MESSAGE_FOR_CODE if messages is None else messages
    for code, message in checked.items():
        digit = _DIGIT_RE.search(message)
        assert digit is None, (
            f"{code}'s public message contains the digit {digit.group()!r}; "
            "numeric detail belongs in details, not in the sentence"
        )
        lowered = message.lower()
        for marker in FORBIDDEN_BODY_SUBSTRINGS:
            assert marker not in lowered, (
                f"{code}'s public message contains the forbidden substring {marker!r}"
            )
        for sequence in forbidden_digit_sequences:
            assert str(sequence) not in message, (
                f"{code}'s public message contains the internal value {sequence!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# THE REQUEST IDENTIFIER (Requirement 26.1)
# ══════════════════════════════════════════════════════════════════════════

#: The header ``asgi_correlation_id`` reads and writes, and the one a client may supply.
REQUEST_ID_HEADER = "X-Request-ID"


def current_request_id(request: Any = None) -> str:
    """The identifier Requirement 26.1 wants on every error response.

    Four sources, in descending order of authority: the platform's observability helper when it
    is present, ``asgi_correlation_id``'s context variable when the middleware is installed, the
    inbound header, and finally a fresh ``uuid4``. The last branch is what makes Requirement
    26.1 hold without adding a dependency - a response is never sent without an identifier, even
    when the optional middleware is absent, which is exactly the fallback ``design.md``
    describes.

    The narrow ``ImportError``/``LookupError``/``AttributeError`` catches are the one place this
    module tolerates a failure: an unreadable identifier is cosmetic and cannot change an
    outcome, whereas raising here would replace a correct error response with a wrong one.
    """
    try:  # the platform helper, once backend_app/core/observability.py exists
        from backend_app.core.observability import get_request_id  # type: ignore

        resolved = get_request_id()
        if resolved:
            return str(resolved)
    except (ImportError, LookupError, AttributeError):
        pass

    try:
        from asgi_correlation_id import correlation_id  # type: ignore

        resolved = correlation_id.get()
        if resolved:
            return str(resolved)
    except (ImportError, LookupError, AttributeError):
        pass

    headers = getattr(request, "headers", None)
    if headers is not None:
        try:
            from_header = headers.get(REQUEST_ID_HEADER)
        except (AttributeError, TypeError):
            from_header = None
        if from_header:
            return str(from_header)[:200]

    return str(uuid.uuid4())


# ══════════════════════════════════════════════════════════════════════════
# THE ONE HANDLER
# ══════════════════════════════════════════════════════════════════════════


def structured_error_body(exc: StructuredError, request_id: str) -> Dict[str, Any]:
    """``design.md``'s body, exactly: ``{"error": {code, message, details}, "request_id"}``.

    Nothing else is added. No ``path``, because an internal route template is of no use to a
    client and Requirement 22.9 lists internal paths among the things a body must not carry;
    no ``timestamp``, because the log record already has one keyed by the same identifier.

    The key sets are :data:`ERROR_ENVELOPE_KEYS` and :data:`ERROR_OBJECT_KEYS`.
    """
    return {"error": exc.to_error_object(), "request_id": request_id}


async def structured_error_handler(request: Any, exc: Exception) -> Any:
    """The single FastAPI handler for :class:`MarketplaceError` and ``PaperError``.

    Registered against :class:`StructuredError`, so both subclasses land here and neither
    package needs its own handler. Anything else that somehow reaches it is re-raised rather
    than dressed up as a structured error - a wrong code is worse than an unhandled exception,
    and ``main.py``'s existing catch-all still answers.
    """
    from fastapi.responses import JSONResponse

    if not isinstance(exc, StructuredError):  # pragma: no cover - defensive
        raise exc

    request_id = current_request_id(request)
    return JSONResponse(
        status_code=exc.http_status,
        content=structured_error_body(exc, request_id),
        headers={REQUEST_ID_HEADER: request_id},
    )


def register_structured_error_handlers(app: Any) -> None:
    """Install the one handler on ``app``. Idempotent, so a re-import cannot double-register.

    Called once from ``backend_app/main.py`` where the application is assembled. Registering
    the base class is what keeps "one FastAPI exception handler" literally true: Starlette
    resolves a handler by walking ``type(exc).__mro__``, so ``MarketplaceError`` and
    ``PaperError`` both resolve to this one, and it is found before ``main.py``'s
    ``Exception`` handler because ``StructuredError`` precedes ``Exception`` in that walk.
    """
    if getattr(app.state, "structured_error_handler_installed", False):
        return
    app.add_exception_handler(StructuredError, structured_error_handler)
    app.state.structured_error_handler_installed = True


__all__ = [
    "ALLOWED_HTTP_STATUS_FOR_CODE",
    "ERROR_CODES",
    "ERROR_ENVELOPE_KEYS",
    "ERROR_OBJECT_KEYS",
    "EXECUTION_ENVIRONMENT_MISMATCH",
    "EXECUTION_ENVIRONMENT_UNRESOLVED",
    "FORBIDDEN_BODY_SUBSTRINGS",
    "HTTP_STATUS_FOR_CODE",
    "MARKETPLACE_ACTION_NOT_RECORDED",
    "MARKETPLACE_CHECKOUT_UNAVAILABLE",
    "MARKETPLACE_CLONING_DISABLED",
    "MARKETPLACE_CODES",
    "MARKETPLACE_COVER_REFERENCE_REJECTED",
    "MARKETPLACE_ELIGIBILITY_FAILED",
    "MARKETPLACE_ELIGIBILITY_UNEVALUABLE",
    "MARKETPLACE_EVIDENCE_IMMUTABLE",
    "MARKETPLACE_EVIDENCE_PERSIST_FAILED",
    "MARKETPLACE_LISTING_NOT_PURCHASABLE",
    "MARKETPLACE_NOT_SUBSCRIBED",
    "MARKETPLACE_OPERATION_NOT_PERMITTED",
    "MARKETPLACE_OWN_LISTING",
    "MARKETPLACE_PAYMENT_REQUIRED",
    "MARKETPLACE_PRICE_EVIDENCE_MISSING",
    "MARKETPLACE_PRICE_OUT_OF_RANGE",
    "MARKETPLACE_RATE_LIMITED",
    "MARKETPLACE_READ_FAILED",
    "MARKETPLACE_REJECTION_REASON_REQUIRED",
    "MARKETPLACE_STRATEGY_UNAVAILABLE",
    "MARKETPLACE_SUBMISSION_ALREADY_OPEN",
    "MARKETPLACE_SUBMISSION_NOT_FOUND",
    "MARKETPLACE_SUBMISSION_TRANSITION_REJECTED",
    "MARKETPLACE_SUBSCRIPTION_EXPIRED",
    "MARKETPLACE_USE_SUBMISSION_ACTIONS",
    "MarketplaceError",
    "NOT_FOUND",
    "PAPER_CODES",
    "PAPER_CONCURRENCY_CONFLICT",
    "PAPER_IDEMPOTENCY_CONFLICT",
    "PAPER_INSUFFICIENT_FUNDS",
    "PAPER_INVARIANT_VIOLATION",
    "PAPER_MARKET_DATA_UNAVAILABLE",
    "PAPER_ORDER_INVALID",
    "PAPER_OVER_FILL",
    "PAPER_PERSISTENCE_UNAVAILABLE",
    "PAPER_READ_FAILED",
    "PAPER_SESSION_LIMIT_REACHED",
    "PAPER_SESSION_OPERATION_REJECTED",
    "PAPER_SIMULATOR_MISCONFIGURED",
    "PAPER_START_REFUSED",
    "PUBLIC_MESSAGE_FOR_CODE",
    "REDACTED_PLACEHOLDER",
    "REQUEST_ID_HEADER",
    "SHARED_CODES",
    "STRUCTURAL_RESPONSE_KEYS",
    "StructuredError",
    "assert_messages_carry_no_internals",
    "current_request_id",
    "http_status_for_code",
    "is_known_code",
    "message_for_code",
    "redact_details",
    "register_structured_error_handlers",
    "structured_error_body",
    "structured_error_handler",
]
