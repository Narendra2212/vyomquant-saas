"""
backend_app/core/observability.py - the request identifier, the log-record shape and the redactor.

Spec: marketplace-subscriptions-paper-trading task 33.4. ``design.md`` -> "Observability" ->
"Request identity". Requirements 26.1, 26.3, 26.4.

Exposes
-------
get_request_id / require_request_id / set_request_id / reset_request_id / new_request_id
bind_request_id / request_id_scope
                                the ``contextvars`` request identifier, and the fallback that
                                makes Requirement 26.1 hold when
                                ``asgi_correlation_id.CorrelationIdMiddleware`` is absent
bind_authenticated_identity / reset_authenticated_identity / current_identity / LogIdentity
                                the ONLY way an identity reaches a log record (Requirement 26.4's
                                "any other user's identifiers")
log_fields / log_line           the record shape: ``event``, ``request_id``, the authenticated
                                identity, and the caller's own already-redacted fields
paper_log_key / paper_log_fields / PAPER_LOG_KEY_FIELDS
                                Requirement 26.3's ``(session_id, sequence)`` key
redact / redact_text / is_denied_key / key_tokens / normalise_log_key / looks_like_a_secret
                                Requirement 26.4's deny-list, by key name and by value pattern
DENIED_LOG_KEYS / SECRET_KEY_NAMES / PROTECTED_LOGIC_KEYS / SECRET_VALUE_PATTERNS
RedactingLogFilter / GUARDED_LOGGER_NAMES / install_log_redaction
                                the filter that puts every call site in the two packages through
                                :func:`redact` without each call site having to remember

HOW THE FALLBACK COEXISTS WITH THE MIDDLEWARE
---------------------------------------------
``main.py`` imports ``CorrelationIdMiddleware`` inside a ``try``/``except ImportError`` and adds it
only when the import succeeded. That import is not changed by this module and this module adds no
dependency. Instead the two mechanisms are layered, and the layering is *deferential*:

* :func:`get_request_id` reads this module's ``contextvars`` variable first, and if it is unset
  reads ``asgi_correlation_id.correlation_id`` when that package is importable. It returns ``""``
  when neither has an identifier - "not established", never a freshly invented one.
* :func:`require_request_id` is the minting form: the same two sources, then the inbound
  ``X-Request-ID`` header, then a ``uuid4``, and it *stores* whatever it resolved in the
  ``contextvars`` variable so every later record in the same request sees the same value.
* ``marketplace/errors.current_request_id`` already calls :func:`get_request_id` first and falls
  through to ``correlation_id``, the header and a ``uuid4`` of its own. Because
  :func:`get_request_id` is non-minting, that fall-through still reaches the middleware's value
  when the middleware is installed. Installing the middleware therefore changes *which* identifier
  is used and never *whether* one exists; removing it changes neither. ``errors.py`` needs no edit.

Starlette runs each request in its own task, and a task gets a copy of the context it was created
in, so a ``set()`` here is visible for the rest of that request and to nothing else. That is the
same property ``asgi_correlation_id`` relies on.

WHY AN IDENTITY CANNOT BE PASSED IN
-----------------------------------
Requirement 26.4 excludes "any other user's identifiers" from every log record. A deny-list cannot
enforce that: ``user_id`` is exactly the field an operator needs, so the question is never *whether*
an identifier appears but *whose*. So the shape of the API answers it instead:

* :data:`IDENTITY_FIELD_KEYS` names every field that asserts who someone is. :func:`log_fields`
  **raises** ``ValueError`` if a caller passes one. There is no keyword by which a request-supplied
  ``user_id`` can enter a record.
* The identity fields are filled from :func:`current_identity`, which reads a ``contextvars``
  variable that only :func:`bind_authenticated_identity` writes, and that function refuses any
  ``source`` outside :data:`AUTHENTICATED_IDENTITY_SOURCES` - naming
  :data:`REQUEST_SUPPLIED_IDENTITY_SOURCES` in the refusal so the mistake is legible. Binding
  ``request.json()["user_id"]`` is not something a caller can spell.
* Unbound, the identity is :data:`UNBOUND_IDENTITY` and the record carries ``user_id: None`` with
  ``identity_source: "unbound"``. An absent identity is reported as absent; Requirement 28.5's
  distinction, and never a guess.

The ``ValueError`` is deliberate rather than a silent drop. A dropped field is a log line that
looks complete and is not, and this module has no call sites yet, so nothing can break on it: the
mistake is caught the first time the offending line executes rather than the first time somebody
audits the output.

WHERE THE VALUE-PATTERN LINE IS DRAWN
-------------------------------------
Key-name matching cannot catch a secret handed to an innocuous key (``detail``, ``response``,
``body``), so :data:`SECRET_VALUE_PATTERNS` also matches on shape. The patterns are chosen to be
things that are *only* ever credentials, and the boundary is stated rather than left to be
discovered:

REDACTED - a PEM private-key header; a JWT (a ``eyJ``-prefixed or plainly three-segment
base64url string); an ``Authorization``-style ``Bearer``/``Basic``/``Token`` credential; 32 or
more unbroken hex characters; 40 or more unbroken standard-base64 characters that mix upper,
lower and digit; 60 or more unbroken base64url characters that mix upper, lower and digit; and a
13-to-19-digit string that passes the Luhn check.

NOT REDACTED - ordinary prose, however long; a UUID in its dashed form, because a
``session_id``, an ``order_id`` and a ``strategy_ref`` are the whole point of a log record and
dashes break every run rule above; hex shorter than 32 characters, so a short checksum or a
colour survives; any number, price, quantity or Minor_Units figure, because the value patterns are
applied to ``str`` and ``bytes`` only - a 16-digit integer amount can never be mistaken for a card
number; and any ISO-8601 timestamp, for the same dash-and-colon reason as the UUID.

The two acknowledged costs of drawing it there: a UUID written *without* its dashes is 32 hex
characters and is redacted, and a 40-plus-character mixed-case-with-digits identifier that happens
to contain no separator is redacted. Both are cases where a legible value is lost; neither is a
case where a credential survives, which is the direction this module errs in on purpose.

WHY THE DENY-LIST IMPORTS ``DENIED_LISTING_COLUMNS``
---------------------------------------------------
Task 33.4 requires every member of ``listing_projection.DENIED_LISTING_COLUMNS`` on the log
deny-list. It is imported rather than re-listed so the response deny-list and the log deny-list
cannot drift apart - a column added there is redacted here on the next import, with no edit.
The cost is real and worth stating: that frozenset holds ``is_active``, ``updated_at``,
``node_count``, ``evaluation_score``, ``has_ml_model`` and ``moderation_status``, none of which is
a secret, and their values will read ``[redacted]`` in a log line from these two packages. That is
a diagnosability loss accepted in exchange for one list instead of two.

WHY A ``logging.Filter`` AND NOT A CONVENTION
--------------------------------------------
Requirement 26.4 says "every log record", and a rule that each of the 130-odd call sites in
``backend/marketplace/`` and ``backend/paper/`` must remember to call :func:`redact` is a rule that
will be broken by the next call site somebody adds. :class:`RedactingLogFilter` is attached to
each of :data:`GUARDED_LOGGER_NAMES` - the loggers those two packages create, and only those - by
:func:`install_log_redaction`, which runs once at import. ``Logger.handle`` applies a logger's
filters before it calls any handler, including an inherited root handler, so every record from
those loggers is redacted no matter which handler formats it and no matter how the call site was
written. Nothing outside those logger names is touched, so ``main.py``'s ``basicConfig`` and every
other module's output are unchanged.

A logger name is created by ``logging.getLogger`` here, so a module that later asks for the same
name receives the already-filtered object regardless of import order. What this cannot do by
itself is notice a *new* logger name; ``tests/test_log_redaction.py`` walks both packages' source
for logger creations and fails if one is missing from :data:`GUARDED_LOGGER_NAMES`, which is what
makes an unguarded new call site a test failure rather than a silent hole.

THE ONE BROAD ``except``
------------------------
:meth:`RedactingLogFilter.filter` catches ``Exception`` and does not re-raise, because an
exception raised from a filter propagates out of ``logger.error(...)`` and would turn a logged
failure into a crash on the path that was already failing. It is not a swallow: the outcome is
defined and it is the *safe* one - the record's message and arguments are replaced wholesale by
the placeholder and ``record.redaction_failed`` is set to ``True``, so the line still appears,
still carries its level and logger, and cannot carry a value the redactor did not manage to
inspect. ``backend_app/core/`` is outside
``tests/test_no_broad_except_on_new_modules.py``'s audited tree; this is the reason that would be
registered there if it were not.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import traceback
import uuid
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

# ``listing_projection`` reaches only ``marketplace.money``, which is standard-library only, so
# importing it here costs nothing and cannot cycle: ``marketplace/errors.py`` imports THIS module
# from inside a function body, never at module scope.
from backend_app.backend.marketplace.errors import REDACTED_PLACEHOLDER
from backend_app.backend.marketplace.listing_projection import DENIED_LISTING_COLUMNS

# ══════════════════════════════════════════════════════════════════════════
# THE REQUEST IDENTIFIER (Requirement 26.1)
# ══════════════════════════════════════════════════════════════════════════

#: The header ``asgi_correlation_id`` reads and writes, and the one a client may supply. Spelled
#: here as well as in ``marketplace/errors.py`` would be two spellings of one contract, so it is
#: read from there when a header is consulted - see :func:`require_request_id`.
_REQUEST_ID_HEADER_FALLBACK = "X-Request-ID"

#: The request identifier for the current context. Empty string means "not established" - never a
#: freshly invented value, because inventing one here would silence
#: ``asgi_correlation_id``'s.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aerora_request_id", default=""
)

#: The longest inbound header value that will be adopted. A client controls this string, and an
#: unbounded one would end up in every log line for the request.
MAX_REQUEST_ID_LENGTH = 200


def new_request_id() -> str:
    """A fresh identifier: a ``uuid4``, canonically formatted.

    ``uuid4`` rather than a counter because two processes must not mint the same identifier, and
    because nothing in the platform reads a request identifier as a sequence.
    """
    return str(uuid.uuid4())


def _correlation_id_from_middleware() -> str:
    """``asgi_correlation_id``'s value, or ``""`` when the package or the value is absent.

    The narrow catch is the whole reason this helper exists: the package is optional
    (``ImportError``), its context variable may be unset (``LookupError``) and a future version
    may rename it (``AttributeError``). None of those can change an outcome, and all three mean
    the same thing here - no identifier from the middleware.
    """
    try:
        from asgi_correlation_id import correlation_id  # type: ignore

        resolved = correlation_id.get()
    except (ImportError, LookupError, AttributeError):
        return ""
    return str(resolved) if resolved else ""


def get_request_id() -> str:
    """The identifier for the current context, or ``""`` if none is established.

    NON-MINTING, and that is the contract ``marketplace/errors.current_request_id`` depends on:
    it calls this first and falls through to ``asgi_correlation_id``, the inbound header and a
    ``uuid4`` when this returns falsy. If this function invented an identifier, the middleware's
    would never be used and the two mechanisms would disagree about one request.
    """
    bound = request_id_var.get()
    if bound:
        return bound
    return _correlation_id_from_middleware()


def set_request_id(value: str) -> contextvars.Token:
    """Bind ``value`` as this context's request identifier. Returns the reset token.

    Truncated to :data:`MAX_REQUEST_ID_LENGTH` and stripped of anything but the characters an
    identifier is made of, because this value ends up in every log line and in a response header,
    and a client-supplied one must not be able to inject a newline into either.
    """
    return request_id_var.set(_clean_request_id(value))


def reset_request_id(token: contextvars.Token) -> None:
    """Undo the binding ``token`` came from."""
    request_id_var.reset(token)


def _clean_request_id(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[^A-Za-z0-9._:-]", "", text)
    return text[:MAX_REQUEST_ID_LENGTH]


def _request_id_from_headers(request: Any) -> str:
    """The inbound ``X-Request-ID``, or ``""``.

    The header name comes from ``marketplace/errors.REQUEST_ID_HEADER`` so the value this module
    adopts and the value that module writes back are the same header. The fallback spelling is
    used only if that import fails, which would mean the error catalogue is unavailable - a
    condition with much larger problems than a header name.
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        from backend_app.backend.marketplace.errors import REQUEST_ID_HEADER
    except ImportError:  # pragma: no cover - the catalogue is a hard dependency of the app
        REQUEST_ID_HEADER = _REQUEST_ID_HEADER_FALLBACK
    try:
        supplied = headers.get(REQUEST_ID_HEADER)
    except (AttributeError, TypeError):
        return ""
    return _clean_request_id(supplied) if supplied else ""


def require_request_id(request: Any = None) -> str:
    """The identifier for this context, minting and binding one if there is none.

    The minting counterpart of :func:`get_request_id`, and the fallback Requirement 26.1 needs:
    four sources in descending order of authority - this context's binding, the middleware's
    value, the inbound header, a fresh ``uuid4`` - and whatever it resolves is bound into
    :data:`request_id_var` so every later record for the same request carries the same value.

    Called by :func:`log_fields`, which is why a record can never lack an identifier.
    """
    resolved = get_request_id()
    if not resolved:
        resolved = _request_id_from_headers(request)
    if not resolved:
        resolved = new_request_id()
    cleaned = _clean_request_id(resolved) or new_request_id()
    if request_id_var.get() != cleaned:
        request_id_var.set(cleaned)
    return cleaned


def bind_request_id(request: Any = None) -> str:
    """Resolve and bind the identifier for a request. Returns it.

    What an ASGI middleware or a WebSocket accept path calls once, so that the identifier is
    settled before the first log record. Identical to :func:`require_request_id`; the second name
    exists because "bind at the edge" and "make sure there is one" are different intentions and a
    reader should not have to infer which one a call site meant.
    """
    return require_request_id(request)


@contextlib.contextmanager
def request_id_scope(value: Optional[str] = None) -> Iterator[str]:
    """Bind ``value`` (or a fresh identifier) for the duration of the block.

    For a worker, a background task or a test: a unit of work that is not an HTTP request still
    needs one identifier across its records, and this is how it gets one without leaving it bound
    afterwards.
    """
    token = request_id_var.set(_clean_request_id(value) or new_request_id())
    try:
        yield request_id_var.get()
    finally:
        request_id_var.reset(token)


# ══════════════════════════════════════════════════════════════════════════
# THE AUTHENTICATED IDENTITY (Requirement 26.4, structurally)
# ══════════════════════════════════════════════════════════════════════════

#: The sources an identity may be bound from: each is a credential the platform itself verified.
AUTHENTICATED_IDENTITY_SOURCES: FrozenSet[str] = frozenset(
    {
        "jwt_claim",
        "session_token",
        "service_principal",
        "websocket_auth",
        "admin_dependency",
    }
)

#: The sources an identity may NOT be bound from, named in the refusal so the mistake is legible
#: rather than merely rejected. Every one of these is a string the caller chose.
REQUEST_SUPPLIED_IDENTITY_SOURCES: FrozenSet[str] = frozenset(
    {
        "request_body",
        "query_param",
        "path_param",
        "header",
        "form_field",
        "websocket_message",
        "cookie",
    }
)

#: Every field name that asserts who somebody is. :func:`log_fields` refuses all of them as
#: keyword arguments; they are filled from :func:`current_identity` instead.
IDENTITY_FIELD_KEYS: FrozenSet[str] = frozenset(
    {
        "user_id",
        "owner_id",
        "author_id",
        "actor_id",
        "admin_id",
        "reviewer_id",
        "subscriber_id",
        "caller_id",
        "tenant_id",
        "account_id",
        "sub",
        "email",
        "username",
    }
)

#: The keys :func:`log_fields` writes itself. A caller may not supply one, for the same reason it
#: may not supply an identity: a record whose ``request_id`` came from the caller is not evidence.
RESERVED_FIELD_KEYS: FrozenSet[str] = frozenset(
    {"event", "request_id", "identity_source"}
) | IDENTITY_FIELD_KEYS


@dataclass(frozen=True)
class LogIdentity:
    """Who the current context is authenticated as, and how that was established.

    Frozen, and built only by :func:`bind_authenticated_identity`. ``source`` is carried into the
    record rather than discarded so a reader can tell a verified identity from an absent one
    without inferring it from ``user_id`` being ``None``.
    """

    user_id: Optional[str]
    source: str

    def as_fields(self) -> Dict[str, Any]:
        """The two keys this identity contributes to a record."""
        return {"user_id": self.user_id, "identity_source": self.source}


#: What :func:`current_identity` answers when nothing has been bound: absent, and saying so.
UNBOUND_IDENTITY = LogIdentity(user_id=None, source="unbound")

_identity_var: contextvars.ContextVar[LogIdentity] = contextvars.ContextVar(
    "aerora_log_identity", default=UNBOUND_IDENTITY
)


def bind_authenticated_identity(user_id: Any, *, source: str) -> contextvars.Token:
    """Bind the authenticated subject for this context. Returns the reset token.

    ``source`` must be one of :data:`AUTHENTICATED_IDENTITY_SOURCES`. A ``source`` from
    :data:`REQUEST_SUPPLIED_IDENTITY_SOURCES`, or any unknown one, raises ``ValueError`` - which
    is how Requirement 26.4's "any other user's identifiers" is enforced in the API's shape
    instead of asserted in a comment. There is no path by which a value read out of a request
    body, a query string or a path parameter becomes a log record's ``user_id``.

    Keyword-only ``source`` so that the two arguments cannot be transposed at a call site.
    """
    if source in REQUEST_SUPPLIED_IDENTITY_SOURCES:
        raise ValueError(
            f"{source!r} is a request-supplied identity source, so it cannot be bound as an "
            "authenticated identity: a log record's identity fields carry who the caller was "
            "verified to be, never who the request said it was (Requirement 26.4). Bind from "
            f"one of {sorted(AUTHENTICATED_IDENTITY_SOURCES)} after the credential was checked."
        )
    if source not in AUTHENTICATED_IDENTITY_SOURCES:
        raise ValueError(
            f"{source!r} is not a known authenticated identity source; the permitted sources "
            f"are {sorted(AUTHENTICATED_IDENTITY_SOURCES)}. Add a new one here, beside the "
            "request-supplied set it must not overlap, rather than at the call site."
        )
    if user_id is None or not str(user_id).strip():
        raise ValueError(
            "an authenticated identity must carry a subject; pass the verified identifier, and "
            "leave the identity unbound rather than binding an empty one"
        )
    return _identity_var.set(LogIdentity(user_id=str(user_id), source=source))


def reset_authenticated_identity(token: contextvars.Token) -> None:
    """Undo the binding ``token`` came from."""
    _identity_var.reset(token)


def current_identity() -> LogIdentity:
    """The authenticated identity for this context, or :data:`UNBOUND_IDENTITY`."""
    return _identity_var.get()


@contextlib.contextmanager
def authenticated_identity_scope(user_id: Any, *, source: str) -> Iterator[LogIdentity]:
    """Bind an authenticated identity for the duration of the block."""
    token = bind_authenticated_identity(user_id, source=source)
    try:
        yield current_identity()
    finally:
        _identity_var.reset(token)


# ══════════════════════════════════════════════════════════════════════════
# THE DENY-LIST BY KEY NAME (Requirement 26.4)
# ══════════════════════════════════════════════════════════════════════════

#: The nineteen names task 33.4 lists. ``api_key`` and ``apiKey`` are both here on purpose: they
#: normalise to the same token sequence, which is the statement that key matching is
#: case-insensitive and separator-insensitive rather than literal.
SECRET_KEY_NAMES: Tuple[str, ...] = (
    "api_key",
    "apiKey",
    "secret",
    "api_secret",
    "password",
    "passphrase",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "stripe_signature",
    "x_razorpay_signature",
    "card",
    "pan",
    "cvv",
    "client_secret",
    "encrypted_api_key",
    "encrypted_secret_key",
    "encrypted_password",
)

#: Every key of a Protected_Logic document. Requirement 26.4 names Protected_Logic among the
#: things a log record may not carry, and these are the keys it is stored under.
PROTECTED_LOGIC_KEYS: Tuple[str, ...] = (
    "buy_logic",
    "sell_logic",
    "indicators",
    "risk",
    "ml_model_path",
    "blueprint",
    "graph_json",
    "execution_graph",
    "compiled_plan",
)

#: How a key name is broken into words: separators split, then each chunk is split on camelCase
#: boundaries with an acronym kept whole, so ``APIKey``, ``apiKey`` and ``api_key`` all become
#: ``('api', 'key')``.
_KEY_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def key_tokens(name: Any) -> Tuple[str, ...]:
    """``name`` as a tuple of lower-case words.

    ``apiKey``, ``api_key``, ``API_KEY`` and ``APIKey`` all give ``('api', 'key')``;
    ``X-Razorpay-Signature`` and ``x_razorpay_signature`` both give
    ``('x', 'razorpay', 'signature')``. This is the normalisation the whole key rule rests on.
    """
    text = "" if name is None else str(name)
    return tuple(match.group(0).lower() for match in _KEY_WORD_RE.finditer(text))


def normalise_log_key(name: Any) -> str:
    """``name`` in one canonical spelling: lower-case words joined by ``_``."""
    return "_".join(key_tokens(name))


def _deny_token_sequences() -> Tuple[Tuple[str, ...], ...]:
    entries: Set[Tuple[str, ...]] = set()
    for raw in (
        tuple(SECRET_KEY_NAMES)
        + tuple(PROTECTED_LOGIC_KEYS)
        + tuple(sorted(DENIED_LISTING_COLUMNS))
    ):
        tokens = key_tokens(raw)
        if tokens:
            entries.add(tokens)
    return tuple(sorted(entries))


#: Every deny-listed key as a token sequence. Matched as a CONTIGUOUS SUBSEQUENCE of a key's own
#: token sequence, which is what makes ``user_api_key`` and ``stripe_client_secret`` match while
#: ``tokenizer`` and ``panel`` do not: the unit of comparison is a word, not a substring.
DENY_TOKEN_SEQUENCES: Tuple[Tuple[str, ...], ...] = _deny_token_sequences()

#: The same deny-list in its canonical spelling. Declared for introspection and for the tests
#: that assert the three sources are all present; the matching itself uses
#: :data:`DENY_TOKEN_SEQUENCES`.
DENIED_LOG_KEYS: FrozenSet[str] = frozenset(
    "_".join(tokens) for tokens in DENY_TOKEN_SEQUENCES
)


def is_denied_key(name: Any) -> bool:
    """Whether a value under ``name`` must be replaced.

    True when any deny-listed entry's words appear contiguously in ``name``'s words. Case and
    separators are irrelevant; word boundaries are not.
    """
    tokens = key_tokens(name)
    if not tokens:
        return False
    for entry in DENY_TOKEN_SEQUENCES:
        span = len(entry)
        if span > len(tokens):
            continue
        for start in range(len(tokens) - span + 1):
            if tokens[start : start + span] == entry:
                return True
    return False


# ══════════════════════════════════════════════════════════════════════════
# THE DENY-LIST BY VALUE PATTERN (Requirement 26.4)
# ══════════════════════════════════════════════════════════════════════════


def _is_mixed_alphanumeric(text: str) -> bool:
    """Whether ``text`` mixes upper case, lower case and a digit.

    The entropy gate on the two base64 rules. A credential is generated and mixes all three; a
    long word, a long lower-case path segment and a SCREAMING_CONSTANT do not. Without this gate
    the base64 rules match ordinary identifiers, which is the over-matching the task warns about.
    """
    return (
        any(character.islower() for character in text)
        and any(character.isupper() for character in text)
        and any(character.isdigit() for character in text)
    )


def _passes_luhn(digits: str) -> bool:
    """The Luhn check, so a 16-digit sequence number is not mistaken for a card number."""
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = ord(character) - 48
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_PEM_RE = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}")
_JWT_GENERIC_RE = re.compile(
    r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"
)
_AUTH_SCHEME_RE = re.compile(
    r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE
)
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_BASE64_STD_RE = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}")
_BASE64_URL_RE = re.compile(r"\b[A-Za-z0-9_-]{60,}\b")
_PAN_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _replace_scheme(match: "re.Match[str]") -> str:
    return f"{match.group(1)} {REDACTED_PLACEHOLDER}"


def _replace_if_mixed(match: "re.Match[str]") -> str:
    found = match.group(0)
    return REDACTED_PLACEHOLDER if _is_mixed_alphanumeric(found) else found


def _replace_if_luhn(match: "re.Match[str]") -> str:
    found = match.group(0)
    digits = re.sub(r"\D", "", found)
    if 13 <= len(digits) <= 19 and _passes_luhn(digits):
        return REDACTED_PLACEHOLDER
    return found


#: Every value-shape rule, as ``(label, pattern, replacement)``. Ordered: the most specific rules
#: run first so that a JWT is reported as a JWT rather than swallowed by a base64 rule. The label
#: is what a test names when it asserts which rule bit.
SECRET_VALUE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]", Any], ...] = (
    ("pem_private_key", _PEM_RE, REDACTED_PLACEHOLDER),
    ("jwt", _JWT_RE, REDACTED_PLACEHOLDER),
    ("three_segment_base64url", _JWT_GENERIC_RE, REDACTED_PLACEHOLDER),
    ("authorization_scheme", _AUTH_SCHEME_RE, _replace_scheme),
    ("long_hex", _LONG_HEX_RE, REDACTED_PLACEHOLDER),
    ("mixed_base64", _BASE64_STD_RE, _replace_if_mixed),
    ("mixed_base64url", _BASE64_URL_RE, _replace_if_mixed),
    ("luhn_card_number", _PAN_RE, _replace_if_luhn),
)


def looks_like_a_secret(value: Any) -> Optional[str]:
    """The label of the first value pattern ``value`` trips, or ``None``.

    Applied to text only. A number is never inspected, which is what keeps a price, a quantity
    and a Minor_Units figure out of the redactor's way.
    """
    if not isinstance(value, str):
        return None
    for label, pattern, replacement in SECRET_VALUE_PATTERNS:
        for match in pattern.finditer(value):
            if callable(replacement):
                if replacement(match) != match.group(0):
                    return label
            else:
                return label
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE REDACTOR (Requirement 26.4)
# ══════════════════════════════════════════════════════════════════════════

#: A deny-listed key inside a serialised blob: ``api_key=x``, ``"api_key": "x"``,
#: ``apiKey => 'x'``. This is what makes the rule hold "inside a serialised blob, not just on a
#: top-level key" - a payload that was already turned into text before it reached the log call.
_KEY_VALUE_IN_TEXT_RE = re.compile(
    r"""(?P<quote>["']?)
        (?P<key>[A-Za-z][A-Za-z0-9_\-]{1,60})
        (?P=quote)
        (?P<gap>\s*(?:=>|[:=])\s*)
        (?P<value>"[^"]{0,4000}"|'[^']{0,4000}'|[^\s,;)\]}]{1,4000})
    """,
    re.VERBOSE,
)

#: How deep :func:`redact` will walk. Deeper than the error catalogue's bound because a log record
#: is not sent to a client and a paper event payload is legitimately nested; bounded all the same,
#: because an unbounded walk in a logging path is a way to hang a process.
MAX_REDACTION_DEPTH = 12


def _redact_key_values_in_text(text: str) -> str:
    """Every deny-listed ``key=value`` pair in ``text``, with the value replaced.

    Scanned by hand rather than with ``re.sub``, and that is the whole point of the loop. ``sub``
    consumes each match whole, so an INNOCUOUS pair swallows whatever follows it inside its own
    value span and a deny-listed pair written there is never examined: ``ValueError: token=abc``
    matches as key ``ValueError``, value ``token=abc``, and ``token`` is never asked about. That is
    the shape the last line of a formatted traceback has, so it is the shape that matters most.

    So a non-matching key advances the scan only past the KEY, to the ``gap`` it was followed by,
    and the value is re-examined from there. The position strictly increases every iteration - a
    key is at least two characters, so ``start('gap')`` is always beyond where the search began -
    which is what bounds the loop.
    """
    pieces: list = []
    position = 0
    length = len(text)
    while position < length:
        match = _KEY_VALUE_IN_TEXT_RE.search(text, position)
        if match is None:
            break
        if not is_denied_key(match.group("key")):
            # Keep the key as written and resume AT its separator, so a deny-listed pair inside
            # what would have been this pair's value is still reached.
            resume = match.start("gap")
            pieces.append(text[position:resume])
            position = resume
            continue
        quote = match.group("quote")
        value = match.group("value")
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            replacement = f"{value[0]}{REDACTED_PLACEHOLDER}{value[0]}"
        else:
            replacement = REDACTED_PLACEHOLDER
        pieces.append(text[position : match.start()])
        pieces.append(
            f"{quote}{match.group('key')}{quote}{match.group('gap')}{replacement}"
        )
        position = match.end()
    pieces.append(text[position:])
    return "".join(pieces)


def redact_text(text: Any) -> str:
    """One piece of text with every deny-listed value replaced.

    Two passes, and both are needed: the key-inside-text pass catches
    ``{"api_key": "..."}`` that was serialised before it reached the log call, and the
    value-pattern pass catches a credential handed to a key nobody thought to deny. Text that
    trips neither is returned unchanged, character for character - a redactor that reformats
    ordinary messages would be turned off within a week.
    """
    rendered = "" if text is None else str(text)
    rendered = _redact_key_values_in_text(rendered)
    for _label, pattern, replacement in SECRET_VALUE_PATTERNS:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def _redact_value(value: Any, depth: int, seen: Set[int]) -> Any:
    if depth > MAX_REDACTION_DEPTH:
        return REDACTED_PLACEHOLDER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        decoded = bytes(value).decode("utf-8", "replace")
        return value if redact_text(decoded) == decoded else REDACTED_PLACEHOLDER
    marker = id(value)
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        if marker in seen:
            return REDACTED_PLACEHOLDER
        seen = seen | {marker}
    if isinstance(value, Mapping):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            redacted[name] = (
                REDACTED_PLACEHOLDER
                if is_denied_key(name)
                else _redact_value(item, depth + 1, seen)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1, seen) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_redact_value(item, depth + 1, seen) for item in sorted(value, key=repr)]
    rendered = str(value)
    return value if redact_text(rendered) == rendered else REDACTED_PLACEHOLDER


def redact(record: Any) -> Any:
    """``record`` with every deny-listed value replaced by ``[redacted]``.

    Polymorphic on purpose, because "record" means two things on this path and both need the same
    rule applied:

    * a ``logging.LogRecord`` - its ``msg``, its ``args``, its formatted traceback and its stack
      text are redacted IN PLACE and the same object is returned, so a
      :class:`RedactingLogFilter` can hand it straight on to the handlers;
    * anything else - a mapping, a sequence, a scalar, a serialised blob - is redacted into a new
      value and returned; the input is not mutated.

    A key on the deny-list loses its WHOLE value, subtree included: a ``risk`` document or an
    ``encrypted_api_key`` is not made safe by descending into it. Every other value is walked to
    :data:`MAX_REDACTION_DEPTH`, so a secret nested nine levels down is found, and a container
    that contains itself collapses to the placeholder rather than recursing forever.

    A key is never dropped and a value is never invented: ``[redacted]`` says "this was withheld",
    which is a different statement from a key that is simply absent.
    """
    if isinstance(record, logging.LogRecord):
        return _redact_log_record(record)
    return _redact_value(record, 0, set())


def _redact_log_record(record: logging.LogRecord) -> logging.LogRecord:
    record.msg = (
        redact_text(record.msg)
        if isinstance(record.msg, str)
        else _redact_value(record.msg, 0, set())
    )
    if record.args:
        if isinstance(record.args, Mapping):
            record.args = _redact_value(dict(record.args), 0, set())
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_value(item, 0, set()) for item in record.args)
        else:  # pragma: no cover - logging only ever produces a tuple or a mapping
            record.args = _redact_value(record.args, 0, set())
    if record.exc_text:
        record.exc_text = redact_text(record.exc_text)
    elif record.exc_info:
        # Format it here so the FORMATTER uses our redacted text: ``logging.Formatter`` reuses
        # ``exc_text`` when it is already set. A traceback carries the arguments of every frame
        # in its exception messages, which is where a credential most often reaches a log.
        record.exc_text = redact_text(
            "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        )
    if record.stack_info:
        record.stack_info = redact_text(record.stack_info)
    return record


# ══════════════════════════════════════════════════════════════════════════
# THE RECORD SHAPE (Requirements 26.1, 26.3, 26.4)
# ══════════════════════════════════════════════════════════════════════════


def log_fields(event: str, **fields: Any) -> Dict[str, Any]:
    """The mapping a log call site passes as its one argument.

    Always carries ``event``, ``request_id`` (Requirement 26.1, minted by
    :func:`require_request_id` when nothing bound one), ``user_id`` and ``identity_source`` from
    :func:`current_identity`, and then the caller's own fields put through :func:`redact`.

    Raises ``ValueError`` for a field in :data:`RESERVED_FIELD_KEYS`. That is the enforcement, not
    a convention: an identity field cannot be supplied by a caller, so a request-supplied
    identifier has no keyword to arrive under, and ``request_id`` cannot be overridden by the
    thing being logged about.
    """
    if not isinstance(event, str) or not event.strip():
        raise ValueError("a log record needs a non-empty event name")
    supplied_reserved = sorted(
        name for name in fields if normalise_log_key(name) in RESERVED_FIELD_KEYS
    )
    if supplied_reserved:
        raise ValueError(
            f"{supplied_reserved} cannot be passed to log_fields: an identity field is filled "
            "from the authenticated context by current_identity(), never from a value the "
            "caller had in hand, so that a log record cannot carry another user's identifiers "
            "(Requirement 26.4). Bind the verified subject with bind_authenticated_identity() "
            "instead."
        )
    built: Dict[str, Any] = {
        "event": redact_text(event),
        "request_id": require_request_id(),
    }
    built.update(current_identity().as_fields())
    for name, value in fields.items():
        built[name] = (
            REDACTED_PLACEHOLDER
            if is_denied_key(name)
            else _redact_value(value, 0, set())
        )
    return built


def log_line(event: str, **fields: Any) -> str:
    """:func:`log_fields` rendered as one line of ``key=value`` pairs.

    For a call site that logs to a plain-text handler, which is what ``main.py``'s
    ``basicConfig`` installs. Values are ``repr``-ed so a value containing a space cannot be read
    as two fields.
    """
    built = log_fields(event, **fields)
    return " ".join(f"{name}={value!r}" for name, value in built.items())


# ── Requirement 26.3's Paper_Session key ──

#: The per-event key ``design.md`` names for a Paper_Session record. The same two fields the
#: WebSocket envelope carries, which is the point: one log record lines up with the exact frame
#: the client received. ``tests/test_log_redaction.py`` asserts both are members of
#: ``paper_events.ENVELOPE_FIELDS``, so the key and the frame cannot drift.
PAPER_LOG_KEY_FIELDS: Tuple[str, str] = ("session_id", "sequence")


def paper_log_key(session_id: Any, sequence: Any) -> Tuple[str, int]:
    """``(session_id, sequence)``, validated.

    ``sequence`` must be an integer of at least 1, which is ``chk_paper_event_sequence``'s rule
    on ``paper_events.sequence`` and ``PaperEventEnvelope``'s ``ge=1``. A ``bool`` is refused
    even though it is an ``int``: ``True`` as a sequence number would silently key a record to
    event 1.

    Validated rather than accepted, because a record keyed by the wrong sequence is worse than no
    record: it points an operator at a frame the client never received.
    """
    if session_id is None or not str(session_id).strip():
        raise ValueError("a paper log record needs a session identifier")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise ValueError(
            f"a paper log record's sequence must be an int, got {type(sequence).__name__}"
        )
    if sequence < 1:
        raise ValueError(
            f"a paper log record's sequence starts at 1, got {sequence}; the sequence is the "
            "event's position in the session, and 0 is not a position any event holds"
        )
    return (str(session_id), sequence)


def paper_log_fields(
    event: str, session_id: Any, sequence: Any, **fields: Any
) -> Dict[str, Any]:
    """A Paper_Session log record: :func:`log_fields` keyed by :func:`paper_log_key`.

    Requirement 26.3's structured record for a lifecycle operation, an order state transition, a
    fill, a balance change or a session error. The key is positional and required, so a paper
    record cannot be emitted without the two fields that make it correlatable.
    """
    identifier, position = paper_log_key(session_id, sequence)
    for name in PAPER_LOG_KEY_FIELDS:
        if name in fields:
            raise ValueError(
                f"{name!r} is the paper log key and is passed positionally; supplying it twice "
                "is how a record ends up keyed to a frame that does not exist"
            )
    return log_fields(event, session_id=identifier, sequence=position, **fields)


# ══════════════════════════════════════════════════════════════════════════
# THE FILTER, AND WHERE IT IS INSTALLED
# ══════════════════════════════════════════════════════════════════════════


class RedactingLogFilter(logging.Filter):
    """Puts every record logged through a guarded logger through :func:`redact`.

    Attached to a LOGGER rather than to a handler, because ``Logger.handle`` applies a logger's
    filters before it calls any handler - including a handler inherited from the root - so one
    attachment covers every output the record reaches. Attaching to the root's handlers instead
    would redact the whole process's output, which the task forbids.

    Also sets ``record.request_id`` from :func:`get_request_id`, so a structured formatter can
    read it without the call site passing it. Setting an attribute changes no existing output:
    ``main.py``'s format string does not mention it.
    """

    #: Set on a record whose redaction raised, so the failure is visible in a structured handler
    #: rather than only in the fact that the message became a placeholder.
    FAILURE_ATTRIBUTE = "redaction_failed"

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            setattr(record, self.FAILURE_ATTRIBUTE, False)
            record.request_id = get_request_id()
            redact(record)
        except Exception:  # noqa: BLE001 - see the module docstring's "one broad except"
            # FAIL CLOSED. The record is emitted, at its own level and from its own logger, with
            # nothing in it that was not inspected. Re-raising would propagate out of the
            # caller's ``logger.error(...)`` and crash the path that was already failing.
            record.msg = REDACTED_PLACEHOLDER
            record.args = None
            record.exc_info = None
            record.exc_text = REDACTED_PLACEHOLDER
            record.stack_info = None
            setattr(record, self.FAILURE_ATTRIBUTE, True)
        return True


#: Every logger the two packages this specification created write through. Kept explicit rather
#: than derived, because ``logging.getLogger(name)`` here CREATES the logger object, so a module
#: that later asks for the same name receives the already-filtered one whatever the import order
#: is - a scan of existing loggers would depend on that order and silently miss the ones not yet
#: created. ``tests/test_log_redaction.py`` walks both packages' source for logger creations and
#: fails if one is missing from this tuple, which is what makes a new call site under a new
#: logger name a test failure instead of an unredacted log line.
GUARDED_LOGGER_NAMES: Tuple[str, ...] = (
    # backend_app/backend/marketplace/
    "MarketplaceCheckout",
    "MarketplaceExpirySweep",
    "MarketplaceSettlement",
    "MarketplaceSubmissionService",
    "MarketplaceSubscriptionReinstatement",
    "backend_app.backend.marketplace.subscriber_operation_guard",
    # backend_app/backend/paper/
    "PaperChannel",
    "PaperMarketFeed",
    "PaperReplay",
    "PaperRepository",
    "PaperSessionService",
    "PaperSimulator",
)


def install_log_redaction(
    logger_names: Iterable[str] = GUARDED_LOGGER_NAMES,
) -> Tuple[str, ...]:
    """Attach one :class:`RedactingLogFilter` to each named logger. Returns the names guarded.

    Idempotent: a logger that already carries a :class:`RedactingLogFilter` is left alone, so a
    re-import, a second call or a test calling it directly cannot stack filters.
    """
    guarded = []
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(
            isinstance(existing, RedactingLogFilter) for existing in logger.filters
        ):
            logger.addFilter(RedactingLogFilter())
        guarded.append(name)
    return tuple(guarded)


def guarded_logger(name: str) -> logging.Logger:
    """The logger ``name``, guaranteed to carry the redacting filter.

    What a new module in either package should call instead of ``logging.getLogger``. It does not
    remove the obligation to add the name to :data:`GUARDED_LOGGER_NAMES` - the test checks that
    tuple, not this function - but it means a module that uses it is protected from its first
    line, before :func:`install_log_redaction` has been reached.
    """
    install_log_redaction((name,))
    return logging.getLogger(name)


# Installed at import. The filter touches only the twelve logger names above, so no output from
# any other module changes, and ``main.py``'s ``logging.basicConfig`` is neither called nor
# altered from here.
install_log_redaction()


__all__ = [
    "AUTHENTICATED_IDENTITY_SOURCES",
    "DENIED_LOG_KEYS",
    "DENY_TOKEN_SEQUENCES",
    "GUARDED_LOGGER_NAMES",
    "IDENTITY_FIELD_KEYS",
    "LogIdentity",
    "MAX_REDACTION_DEPTH",
    "MAX_REQUEST_ID_LENGTH",
    "PAPER_LOG_KEY_FIELDS",
    "PROTECTED_LOGIC_KEYS",
    "REQUEST_SUPPLIED_IDENTITY_SOURCES",
    "RESERVED_FIELD_KEYS",
    "RedactingLogFilter",
    "SECRET_KEY_NAMES",
    "SECRET_VALUE_PATTERNS",
    "UNBOUND_IDENTITY",
    "authenticated_identity_scope",
    "bind_authenticated_identity",
    "bind_request_id",
    "current_identity",
    "get_request_id",
    "guarded_logger",
    "install_log_redaction",
    "is_denied_key",
    "key_tokens",
    "log_fields",
    "log_line",
    "looks_like_a_secret",
    "new_request_id",
    "normalise_log_key",
    "paper_log_fields",
    "paper_log_key",
    "redact",
    "redact_text",
    "request_id_scope",
    "request_id_var",
    "require_request_id",
    "reset_authenticated_identity",
    "reset_request_id",
    "set_request_id",
]
