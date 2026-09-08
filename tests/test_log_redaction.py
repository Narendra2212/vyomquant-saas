"""The guard on ``backend_app/core/observability.py``: no deny-listed value reaches a log line.

Feature: marketplace-subscriptions-paper-trading, task 33.4.
Requirements 26.1 (a request identifier on every record), 26.3 (a Paper_Session record keyed by
``(session_id, sequence)``) and 26.4 (what a record may never carry).

WHAT IS DRIVEN
--------------
Every log call site in ``backend_app/backend/marketplace/`` and ``backend_app/backend/paper/``,
found by walking both packages' source with ``ast`` at collection time. The walk, the file set and
the "the package is missing, so this is not a pass" skip are **imported** from
``tests/test_no_broad_except_on_new_modules.py`` rather than written a second time: two independent
enumerations of the same two packages would drift, and the point of enumerating reflectively is
that a call site added later is driven without an edit here.

A call site is driven by building the ``logging.LogRecord`` that site would produce - its module,
its line, its level, its logger - and handing it to ``Logger.handle``, which is the method that
applies a logger's filters before any handler sees the record. So what is exercised is the same
:class:`~backend_app.core.observability.RedactingLogFilter` attachment the running application has,
at every site, and a new site under a logger name absent from ``GUARDED_LOGGER_NAMES`` fails
:func:`test_every_logger_name_in_both_packages_is_guarded`.

WHAT MAKES THIS NON-VACUOUS
---------------------------
A test that asserts "the secret is absent from the output" passes trivially against a redactor that
destroys all text, and passes trivially against a sentinel the redactor mangles for an unrelated
reason. Both holes are closed explicitly:

* :func:`test_no_sentinel_is_itself_secret_shaped` asserts every sentinel this file uses survives
  :func:`redact_text` untouched when it is not under a deny-listed key. If a sentinel were
  secret-shaped, every key assertion below would pass without the key rule doing anything.
* :func:`test_an_ordinary_message_is_not_mangled` and the ordinary sentence carried by *every*
  driven record assert the complement: prose, a UUID, an ISO timestamp, a price and a
  ``reason=CODE`` pair come out character for character.
* :func:`test_the_filter_is_what_bites` drives the identical record through an unguarded logger
  name and asserts the value **survives** there, so the redaction observed above is attributable
  to the filter and not to the capture handler.

THE DENY-LIST IS IMPORTED, NEVER RE-TYPED
-----------------------------------------
:data:`DRIVEN_DENY_KEYS` is built from ``SECRET_KEY_NAMES``, ``PROTECTED_LOGIC_KEYS`` and
``listing_projection.DENIED_LISTING_COLUMNS``, so a column added to the response deny-list is
driven here on the next run. :data:`TASK_NAMED_KEYS` is the one list written out by hand - the
nineteen names task 33.4 states - and it exists precisely so that a name silently dropped from
``SECRET_KEY_NAMES`` fails :func:`test_the_deny_list_covers_every_key_the_task_names`.
"""

from __future__ import annotations

import ast
import contextvars
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.marketplace.errors import REDACTED_PLACEHOLDER
from backend_app.backend.marketplace.listing_projection import DENIED_LISTING_COLUMNS
from backend_app.backend.paper.paper_events import ENVELOPE_FIELDS
from backend_app.core import observability as ob

# The walk, the audited file set and the skip-rather-than-pass rule, reused rather than rewritten.
from tests.test_no_broad_except_on_new_modules import (
    _LOG_OBJECTS,
    _qualnames,
    _relative,
    audited_modules,
)

# ══════════════════════════════════════════════════════════════════════════
#  THE DENY-LIST, ENUMERATED FROM THE SOURCE OF TRUTH
# ══════════════════════════════════════════════════════════════════════════

#: The nineteen key names task 33.4 writes out. The only hand-typed list in this file, and the
#: reason it is hand-typed: it is the check that the module's own tuple still holds all of them.
TASK_NAMED_KEYS: Tuple[str, ...] = (
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

#: Every Protected_Logic document key task 33.4 names.
TASK_NAMED_PROTECTED_LOGIC_KEYS: Tuple[str, ...] = (
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

#: Every key driven by this file, from the three sources task 33.4 names. ``DENIED_LISTING_COLUMNS``
#: is imported, so this cannot drift from the response deny-list.
DRIVEN_DENY_KEYS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        tuple(ob.SECRET_KEY_NAMES)
        + tuple(ob.PROTECTED_LOGIC_KEYS)
        + tuple(sorted(DENIED_LISTING_COLUMNS))
    )
)

#: One distinguishable value per deny-listed key. Deliberately shaped so that NOTHING about the
#: value itself invites redaction - upper case and digits only, no separator that could read as
#: base64 or hex - so a value that is gone is gone because of its key.
SENTINELS: Dict[str, str] = {
    key: f"SENTINELVALUE{index:03d}" for index, key in enumerate(DRIVEN_DENY_KEYS)
}

#: The complement, carried by every driven record: an ordinary operator sentence with the things a
#: log line exists to carry - an identifier, a timestamp, a price, a reason code. It must come out
#: byte for byte, and it is what makes "the sentinels are absent" a statement about the deny-list
#: rather than about a redactor that eats everything.
ORDINARY_SENTENCE = (
    "paper session 6f1c2b8a-1111-4222-8333-9444aaaa5555 sequence=42 order rejected "
    "reason=INSUFFICIENT_BALANCE required=19875.25 available=100.00 at 2024-05-01T10:00:00Z"
)


def _nested_denied_fields(depth: int = 9) -> Dict[str, Any]:
    """Every deny-listed key ``depth`` mappings down, so nesting is driven, not just a top key."""
    innermost: Dict[str, Any] = {key: SENTINELS[key] for key in DRIVEN_DENY_KEYS}
    node: Dict[str, Any] = {"leaf": innermost, "items": [innermost]}
    for level in range(depth - 1, 0, -1):
        node = {f"level_{level}": node}
    return node


def _serialised_blob() -> str:
    """Every deny-listed pair already turned into text before it reached the log call.

    Two spellings in one string, because both occur: a JSON fragment and a ``key=value`` trail.
    """
    json_like = ", ".join(f'"{key}": "{SENTINELS[key]}"' for key in DRIVEN_DENY_KEYS)
    trail = " ".join(f"{key}={SENTINELS[key]}" for key in DRIVEN_DENY_KEYS)
    return "{" + json_like + "} " + trail


def denied_fields() -> Dict[str, Any]:
    """The record body every call site is driven with: top level, nested, and serialised."""
    fields: Dict[str, Any] = {key: SENTINELS[key] for key in DRIVEN_DENY_KEYS}
    fields["context"] = _nested_denied_fields()
    fields["detail"] = _serialised_blob()
    return fields


# ══════════════════════════════════════════════════════════════════════════
#  THE LOG CALL SITES, ENUMERATED REFLECTIVELY
# ══════════════════════════════════════════════════════════════════════════

#: ``logger.<method>`` names that emit a record, mapped to the level the record carries.
LOG_METHOD_LEVELS: Dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "exception": logging.ERROR,
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
    "log": logging.INFO,
}


@dataclass(frozen=True)
class CallSite:
    """One ``logger.<method>(...)`` call in an audited package."""

    module: str
    line: int
    method: str
    qualname: str
    logger_name: str

    @property
    def level(self) -> int:
        return LOG_METHOD_LEVELS[self.method]

    @property
    def ident(self) -> str:
        return f"{Path(self.module).name}:{self.line}:{self.method}"

    def describe(self) -> str:
        return (
            f"{self.module}:{self.line} in {self.qualname} "
            f"-> logger {self.logger_name!r}.{self.method}()"
        )


def _module_logger_name(path: Path, tree: ast.AST) -> Optional[str]:
    """The name the module passes to ``logging.getLogger``, or ``None`` if it creates no logger.

    ``getLogger(__name__)`` is resolved to the dotted import path of the file, which is what the
    running module produces - ``subscriber_operation_guard`` is registered that way.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "getLogger" or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            return argument.value
        if isinstance(argument, ast.Name) and argument.id == "__name__":
            return _relative(path).removesuffix(".py").replace("/", ".")
    return None


def call_sites_in(path: Path) -> List[CallSite]:
    """Every logger call in one module, in line order."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=_relative(path))
    qualnames = _qualnames(tree)
    logger_name = _module_logger_name(path, tree)
    found: List[CallSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in LOG_METHOD_LEVELS:
            continue
        target = node.func.value
        if isinstance(target, ast.Name):
            target_name: Optional[str] = target.id
        elif isinstance(target, ast.Attribute):
            target_name = target.attr
        else:
            target_name = None
        if target_name not in _LOG_OBJECTS:
            continue
        if logger_name is None:
            pytest.fail(
                f"{_relative(path)}:{node.lineno} logs through {target_name!r} but the module "
                "creates no logger with logging.getLogger, so this file cannot tell which logger "
                "the record goes to and cannot assert that it is guarded"
            )
        found.append(
            CallSite(
                module=_relative(path),
                line=node.lineno,
                method=node.func.attr,
                qualname=qualnames.get(id(node), "<module>"),
                logger_name=logger_name,
            )
        )
    return sorted(found, key=lambda site: (site.module, site.line))


def all_call_sites() -> List[CallSite]:
    """Every logger call in both audited packages."""
    sites: List[CallSite] = []
    for path in audited_modules():
        sites.extend(call_sites_in(path))
    return sites


CALL_SITES: List[CallSite] = all_call_sites()


# ══════════════════════════════════════════════════════════════════════════
#  DRIVING A CALL SITE
# ══════════════════════════════════════════════════════════════════════════


class _Capture(logging.Handler):
    """Collects the formatted text of every record it is handed."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s|%(levelname)s|%(message)s"))
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _exception_info() -> Any:
    """A live ``exc_info`` whose message carries a deny-listed pair.

    The last line of a formatted traceback is where a credential most often reaches a log, and it
    is a ``key=value`` pair sitting behind an innocuous ``ExceptionType:`` prefix.
    """
    try:
        raise RuntimeError(f"driver refused: token={SENTINELS['token']}")
    except RuntimeError:
        return sys.exc_info()


def _drive(site: CallSite, fields: Dict[str, Any]) -> Tuple[str, logging.LogRecord]:
    """Emit the record ``site`` would emit and return its formatted text and the record.

    ``Logger.handle`` rather than ``logger.info(...)`` so the record carries the call site's own
    module, line and level. ``handle`` is the method that applies the logger's filters, which is
    where :class:`RedactingLogFilter` sits, so nothing about the redaction path is bypassed.
    """
    logger = logging.getLogger(site.logger_name)
    capture = _Capture()
    logger.addHandler(capture)
    try:
        record = logging.LogRecord(
            name=site.logger_name,
            level=site.level,
            pathname=site.module,
            lineno=site.line,
            msg=ORDINARY_SENTENCE + " fields=%s",
            args=(fields,),
            exc_info=_exception_info() if site.method == "exception" else None,
            func=site.qualname,
        )
        logger.handle(record)
    finally:
        logger.removeHandler(capture)
    assert len(capture.lines) == 1, (
        f"{site.describe()} produced {len(capture.lines)} lines, expected exactly one; a record "
        "that never reached a handler cannot be asserted about"
    )
    return capture.lines[0], record


def _leaked(text: str) -> List[str]:
    """Every sentinel still present in ``text``, named so a failure says which key leaked."""
    return [
        f"{key}={SENTINELS[key]}" for key in DRIVEN_DENY_KEYS if SENTINELS[key] in text
    ]


@pytest.fixture(autouse=True)
def _isolated_request_id() -> Any:
    """Every test starts with no request identifier bound and leaves none behind."""
    token = ob.request_id_var.set("")
    try:
        yield
    finally:
        ob.request_id_var.reset(token)


# ══════════════════════════════════════════════════════════════════════════
#  THE DENY-LIST ITSELF
# ══════════════════════════════════════════════════════════════════════════


def test_the_deny_list_covers_every_key_the_task_names() -> None:
    """All three sources task 33.4 names are on the deny-list, by ``is_denied_key``."""
    expected = (
        set(TASK_NAMED_KEYS)
        | set(TASK_NAMED_PROTECTED_LOGIC_KEYS)
        | set(DENIED_LISTING_COLUMNS)
    )
    missing = sorted(name for name in expected if not ob.is_denied_key(name))
    assert not missing, (
        "task 33.4 requires these keys on the log deny-list and is_denied_key says no: "
        f"{missing}"
    )
    assert set(TASK_NAMED_PROTECTED_LOGIC_KEYS) == set(ob.PROTECTED_LOGIC_KEYS), (
        "PROTECTED_LOGIC_KEYS and the Protected_Logic document keys task 33.4 names have "
        f"diverged: {sorted(set(TASK_NAMED_PROTECTED_LOGIC_KEYS) ^ set(ob.PROTECTED_LOGIC_KEYS))}"
    )
    assert set(TASK_NAMED_KEYS) == set(ob.SECRET_KEY_NAMES), (
        "SECRET_KEY_NAMES and the nineteen names task 33.4 writes out have diverged: "
        f"{sorted(set(TASK_NAMED_KEYS) ^ set(ob.SECRET_KEY_NAMES))}"
    )


def test_the_deny_list_is_driven_from_the_listing_projection() -> None:
    """Every ``DENIED_LISTING_COLUMNS`` member is driven, so the two lists cannot drift.

    Imported rather than copied: a column added there is a key redacted here on the next run, with
    no edit to either file.
    """
    assert DENIED_LISTING_COLUMNS, "the response deny-list is empty, so nothing is being driven"
    assert set(DENIED_LISTING_COLUMNS) <= set(DRIVEN_DENY_KEYS)
    for column in sorted(DENIED_LISTING_COLUMNS):
        assert ob.normalise_log_key(column) in ob.DENIED_LOG_KEYS, (
            f"{column!r} is denied in a marketplace response but not in a log record"
        )


def test_no_sentinel_is_itself_secret_shaped() -> None:
    """The values this file hides must survive when they are NOT under a deny-listed key.

    Without this, every "the sentinel is absent" assertion in this file could be satisfied by the
    value-pattern rule alone and the key rule could be broken without a single failure.
    """
    for key in DRIVEN_DENY_KEYS:
        sentinel = SENTINELS[key]
        assert ob.looks_like_a_secret(sentinel) is None, (
            f"the sentinel for {key!r} trips the value-pattern rule, which would make the key "
            "assertions in this file pass without the key rule doing anything"
        )
        assert ob.redact_text(sentinel) == sentinel
        assert ob.redact({"note": sentinel}) == {"note": sentinel}


@pytest.mark.parametrize("key", DRIVEN_DENY_KEYS)
def test_a_denied_key_loses_its_value_at_the_top_level(key: str) -> None:
    redacted = ob.redact({key: SENTINELS[key], "note": "kept"})
    assert redacted[key] == REDACTED_PLACEHOLDER
    assert redacted["note"] == "kept", "an innocuous sibling must survive intact"
    assert key in redacted, "a denied key is redacted, never dropped: absent and withheld differ"


@pytest.mark.parametrize("key", DRIVEN_DENY_KEYS)
def test_a_denied_key_loses_its_value_nine_levels_down(key: str) -> None:
    """Redaction holds at depth, not only on a top-level key."""
    redacted = ob.redact(_nested_denied_fields())
    assert SENTINELS[key] not in repr(redacted), (
        f"{key!r} survived nine levels of nesting; the value was {SENTINELS[key]!r}"
    )


@pytest.mark.parametrize("key", DRIVEN_DENY_KEYS)
def test_a_denied_key_loses_its_value_inside_a_serialised_blob(key: str) -> None:
    """A body already turned into text before it reached the log call is still redacted."""
    sentinel = SENTINELS[key]
    for blob in (
        f'{{"{key}": "{sentinel}"}}',
        f"{key}={sentinel}",
        f"{key} => '{sentinel}'",
        f"stage=submit {key}: {sentinel} outcome=refused",
    ):
        assert sentinel not in ob.redact_text(blob), f"{blob!r} still carries its value"


@pytest.mark.parametrize(
    "spelling",
    [
        "api_key",
        "apiKey",
        "ApiKey",
        "API_KEY",
        "APIKey",
        "api-key",
        "user_api_key",
        "exchangeApiKey",
        "X-Razorpay-Signature",
        "x_razorpay_signature",
        "stripeSignature",
        "client_secret",
        "clientSecret",
        "STRIPE_CLIENT_SECRET",
        "MLModelPath",
        "graphJson",
    ],
)
def test_key_matching_ignores_case_and_separators(spelling: str) -> None:
    """One key rule, however the call site spelled the name."""
    assert ob.is_denied_key(spelling), f"{spelling!r} must be denied"
    assert ob.redact({spelling: "SENTINELSPELLING"})[spelling] == REDACTED_PLACEHOLDER


@pytest.mark.parametrize(
    "spelling",
    ["tokenizer", "panel", "cardinality", "discarded", "passwordless", "secretary"],
)
def test_a_word_that_merely_contains_a_denied_name_is_kept(spelling: str) -> None:
    """The unit of comparison is a word, not a substring - the complement of the rule above."""
    assert not ob.is_denied_key(spelling)
    assert ob.redact({spelling: "KEPTVALUE"}) == {spelling: "KEPTVALUE"}


# ══════════════════════════════════════════════════════════════════════════
#  THE VALUE-PATTERN RULE, AND ITS COMPLEMENT
# ══════════════════════════════════════════════════════════════════════════

#: A secret handed to a key nobody thought to deny, with the rule label it must trip and the
#: substring that must not survive.
SECRET_SHAPED_VALUES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "detail",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        "jwt",
        "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    ),
    (
        "response",
        "Bearer aGVsbG8td29ybGQtY3JlZGVudGlhbA",
        "authorization_scheme",
        "aGVsbG8td29ybGQtY3JlZGVudGlhbA",
    ),
    (
        "body",
        "signature 0123456789abcdef0123456789abcdef",
        "long_hex",
        "0123456789abcdef0123456789abcdef",
    ),
    (
        "note",
        "-----BEGIN RSA PRIVATE KEY-----",
        "pem_private_key",
        "BEGIN RSA PRIVATE KEY",
    ),
    ("memo", "4111111111111111", "luhn_card_number", "4111111111111111"),
)


@pytest.mark.parametrize(
    ("innocuous_key", "value", "label", "must_vanish"),
    SECRET_SHAPED_VALUES,
    ids=[case[2] for case in SECRET_SHAPED_VALUES],
)
def test_a_secret_shaped_value_under_an_innocuous_key_is_redacted(
    innocuous_key: str, value: str, label: str, must_vanish: str
) -> None:
    """Key-name matching cannot catch a credential handed to ``detail``; value shape can."""
    assert not ob.is_denied_key(innocuous_key), (
        f"{innocuous_key!r} must be innocuous for this test to be about the value rule"
    )
    assert ob.looks_like_a_secret(value) == label
    assert must_vanish not in ob.redact_text(value)
    assert must_vanish not in repr(ob.redact({"context": {innocuous_key: value}}))


@pytest.mark.parametrize(
    "sentence",
    [
        ORDINARY_SENTENCE,
        "paper session started",
        "submission 6f1c2b8a-1111-4222-8333-9444aaaa5555 moved to APPROVED",
        "settlement recorded at 2024-05-01T10:00:00.123456Z for 1999 minor units",
        "feed state FALLBACK_REST after 3 reconnects, latency 42.5 ms",
        "checkout refused: reason=PRICE_OUT_OF_RANGE limit=4999",
        "colour #a1b2c3 checksum deadbeef",
    ],
)
def test_an_ordinary_message_is_not_mangled(sentence: str) -> None:
    """The complement that makes every absence assertion in this file mean something.

    A redactor that destroyed all text would satisfy "the values are gone" trivially. These are the
    things a log line exists to carry - an identifier, a timestamp, a price, a reason code, a short
    checksum - and they must come out character for character.
    """
    assert ob.redact_text(sentence) == sentence
    assert ob.redact({"message": sentence}) == {"message": sentence}
    assert REDACTED_PLACEHOLDER not in ob.redact_text(sentence)


def test_an_innocuous_prefix_cannot_hide_a_denied_pair() -> None:
    """``ValueError: token=abc`` - the shape the last line of a traceback has.

    Regression for a defect this file exposed: the key-inside-text scan consumed a non-denied
    pair's value whole, so a deny-listed pair written inside that span was never examined.
    """
    for text, must_vanish in (
        ("error: token=SENTINELPREFIX1 boom", "SENTINELPREFIX1"),
        ("ValueError: api_key=SENTINELPREFIX2", "SENTINELPREFIX2"),
        ('{"detail": "password=SENTINELPREFIX3"}', "SENTINELPREFIX3"),
        ("outcome=refused reason: client_secret=SENTINELPREFIX4", "SENTINELPREFIX4"),
    ):
        redacted = ob.redact_text(text)
        assert must_vanish not in redacted, f"{text!r} -> {redacted!r}"


# ══════════════════════════════════════════════════════════════════════════
#  EVERY CALL SITE IN BOTH PACKAGES
# ══════════════════════════════════════════════════════════════════════════


def test_the_walk_found_call_sites_in_both_packages() -> None:
    """Non-vacuity: an enumeration that found nothing would make every drive below a no-op."""
    assert CALL_SITES, "no logger call site was found in either audited package"
    for package in ("marketplace", "paper"):
        count = sum(1 for site in CALL_SITES if f"/{package}/" in site.module)
        assert count > 0, f"no call site was found under {package}/, so it is not being driven"
    assert len({site.module for site in CALL_SITES}) >= 10, (
        "the walk found call sites in fewer modules than either package has, which usually means "
        "the logger-object names in _LOG_OBJECTS no longer match how the modules name their logger"
    )


def test_every_logger_name_in_both_packages_is_guarded() -> None:
    """A new logger name in either package must be added to ``GUARDED_LOGGER_NAMES``.

    This is what turns "somebody added a module with its own logger" into a failure here rather
    than an unredacted log line in production.
    """
    from_source = {site.logger_name for site in CALL_SITES}
    unguarded = sorted(from_source - set(ob.GUARDED_LOGGER_NAMES))
    assert not unguarded, (
        f"these loggers are used in an audited package and are not guarded: {unguarded}. Add each "
        "to observability.GUARDED_LOGGER_NAMES, or create it with observability.guarded_logger()."
    )
    stale = sorted(set(ob.GUARDED_LOGGER_NAMES) - from_source)
    assert not stale, (
        f"GUARDED_LOGGER_NAMES names loggers no audited module logs through: {stale}. A stale name "
        "is a filter attached to nothing and reads as coverage that does not exist."
    )
    for name in sorted(from_source):
        logger = logging.getLogger(name)
        assert any(
            isinstance(existing, ob.RedactingLogFilter) for existing in logger.filters
        ), f"logger {name!r} carries no RedactingLogFilter"


@pytest.mark.parametrize("site", CALL_SITES, ids=[site.ident for site in CALL_SITES])
def test_every_call_site_redacts_every_denied_value(site: CallSite) -> None:
    """Task 33.4's own sentence: drive the site with a record containing each deny-listed key and
    assert the emitted text contains none of the values - and that the ordinary part survives."""
    text, record = _drive(site, denied_fields())
    leaked = _leaked(text)
    assert not leaked, f"{site.describe()} emitted {leaked}\nline: {text}"
    assert ORDINARY_SENTENCE in text, (
        f"{site.describe()} mangled the ordinary part of its own message, so the absence of the "
        f"deny-listed values above proves nothing\nline: {text}"
    )
    assert getattr(record, ob.RedactingLogFilter.FAILURE_ATTRIBUTE) is False, (
        f"{site.describe()} fell back to the fail-closed placeholder, so the redactor raised "
        "rather than redacted"
    )
    assert REDACTED_PLACEHOLDER in text, (
        f"{site.describe()} emitted no placeholder at all, which means nothing was withheld"
    )


def test_a_traceback_and_a_stack_trace_are_redacted() -> None:
    """``exc_text`` and ``stack_info`` are text the call site never composed, and are redacted."""
    logger_name = "PaperSessionService"
    logger = logging.getLogger(logger_name)
    capture = _Capture()
    logger.addHandler(capture)
    try:
        record = logging.LogRecord(
            name=logger_name,
            level=logging.ERROR,
            pathname="backend_app/backend/paper/paper_session_service.py",
            lineno=1,
            msg=ORDINARY_SENTENCE,
            args=None,
            exc_info=_exception_info(),
        )
        record.stack_info = (
            f"Stack (most recent call last):\n  api_secret={SENTINELS['secret']}"
        )
        logger.handle(record)
    finally:
        logger.removeHandler(capture)
    text = capture.lines[0]
    assert SENTINELS["token"] not in text, f"the exception message leaked its token\n{text}"
    assert SENTINELS["secret"] not in text, f"the stack text leaked its secret\n{text}"
    assert "RuntimeError" in text, "the traceback itself must survive; only the values go"
    assert ORDINARY_SENTENCE in text


def test_the_filter_is_what_bites() -> None:
    """The same record through an UNGUARDED logger keeps its value.

    Without this the whole file could be passing because the capture handler drops text, or because
    the sentinels never reached a record. It also states the scope decision: only the audited
    packages' loggers are filtered, and the rest of the process's output is untouched.
    """
    unguarded_name = "test_log_redaction.unguarded_control_logger"
    logger = logging.getLogger(unguarded_name)
    assert not any(
        isinstance(existing, ob.RedactingLogFilter) for existing in logger.filters
    )
    capture = _Capture()
    logger.addHandler(capture)
    try:
        logger.handle(
            logging.LogRecord(
                name=unguarded_name,
                level=logging.ERROR,
                pathname="control.py",
                lineno=1,
                msg="fields=%s",
                args=({"api_key": SENTINELS["api_key"]},),
                exc_info=None,
            )
        )
    finally:
        logger.removeHandler(capture)
    assert SENTINELS["api_key"] in capture.lines[0], (
        "an unguarded logger emitted the value redacted, which means the assertions elsewhere in "
        "this file are not attributable to RedactingLogFilter"
    )


def test_installing_the_filter_twice_does_not_stack_it() -> None:
    """``install_log_redaction`` runs at import and may run again; filters must not accumulate."""
    name = ob.GUARDED_LOGGER_NAMES[0]

    def attached() -> int:
        return sum(
            1
            for existing in logging.getLogger(name).filters
            if isinstance(existing, ob.RedactingLogFilter)
        )

    before = attached()
    ob.install_log_redaction((name,))
    assert before == attached() == 1


# ══════════════════════════════════════════════════════════════════════════
#  REQUIREMENT 26.1 - THE REQUEST IDENTIFIER
# ══════════════════════════════════════════════════════════════════════════


def _without_the_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import asgi_correlation_id`` fail, as it does when the package is not installed.

    ``None`` in ``sys.modules`` is the interpreter's own "this import is blocked" marker and raises
    ``ImportError``, which is exactly what ``main.py``'s conditional import sees when the optional
    dependency is absent.
    """
    monkeypatch.setitem(sys.modules, "asgi_correlation_id", None)


def _with_the_middleware(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Stand in for ``CorrelationIdMiddleware`` having set its context variable for this request."""
    module = types.ModuleType("asgi_correlation_id")
    variable: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
        "correlation_id", default=None
    )
    variable.set(value)
    module.correlation_id = variable  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asgi_correlation_id", module)


def test_the_context_fallback_yields_an_id_when_the_middleware_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 26.1 holds without the optional dependency."""
    _without_the_middleware(monkeypatch)
    assert ob.get_request_id() == "", (
        "get_request_id must be non-minting: minting here would shadow the middleware's value "
        "whenever it is installed"
    )
    minted = ob.require_request_id()
    assert minted, "no identifier was minted, so a log record could carry none"
    assert ob.request_id_var.get() == minted, "the minted identifier was not bound"
    assert ob.get_request_id() == minted, "a later record in the same request saw a different id"
    assert ob.require_request_id() == minted, "a second call minted a second identifier"
    assert ob.log_fields("paper.session.started")["request_id"] == minted


def test_the_request_id_defers_to_the_middleware_when_it_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the middleware installed the two mechanisms must not fight over one request."""
    _with_the_middleware(monkeypatch, "MW-0123456789")
    assert ob.get_request_id() == "MW-0123456789"
    assert ob.require_request_id() == "MW-0123456789", (
        "require_request_id minted its own identifier while the middleware had one, so one "
        "request would appear under two identifiers"
    )
    assert ob.log_fields("marketplace.checkout.created")["request_id"] == "MW-0123456789"


def test_an_explicit_binding_outranks_the_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker or a WebSocket path that bound its own identifier keeps it, and restores it."""
    _with_the_middleware(monkeypatch, "MW-0123456789")
    with ob.request_id_scope("scoped-0001") as scoped:
        assert scoped == "scoped-0001"
        assert ob.get_request_id() == "scoped-0001"
    assert ob.get_request_id() == "MW-0123456789", "the scope did not restore the outer state"


def test_a_client_supplied_request_id_cannot_inject_into_a_log_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identifier reaches every record and a response header, so it is cleaned and bounded."""
    _without_the_middleware(monkeypatch)

    class _Request:
        headers = {"X-Request-ID": "abc\r\nSet-Cookie: x=1 " + "z" * 400}

    resolved = ob.require_request_id(_Request())
    assert "\r" not in resolved and "\n" not in resolved and " " not in resolved
    assert len(resolved) <= ob.MAX_REQUEST_ID_LENGTH
    assert resolved.startswith("abc")


def test_the_filter_puts_the_request_id_on_every_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structured handler can read ``record.request_id`` without the call site passing it."""
    _without_the_middleware(monkeypatch)
    with ob.request_id_scope("scoped-0002"):
        _text, record = _drive(CALL_SITES[0], {"note": "kept"})
        assert record.request_id == "scoped-0002"


# ══════════════════════════════════════════════════════════════════════════
#  REQUIREMENT 26.4 - AN IDENTITY COMES FROM THE AUTHENTICATED CONTEXT ONLY
# ══════════════════════════════════════════════════════════════════════════


def test_the_two_identity_source_sets_are_disjoint() -> None:
    assert ob.AUTHENTICATED_IDENTITY_SOURCES
    assert ob.REQUEST_SUPPLIED_IDENTITY_SOURCES
    assert not (ob.AUTHENTICATED_IDENTITY_SOURCES & ob.REQUEST_SUPPLIED_IDENTITY_SOURCES)


@pytest.mark.parametrize("field", sorted(ob.IDENTITY_FIELD_KEYS))
def test_an_identity_field_cannot_be_passed_to_a_log_record(field: str) -> None:
    """Structural, not documentary: there is no keyword an identifier can arrive under."""
    with pytest.raises(ValueError) as raised:
        ob.log_fields("marketplace.submission.created", **{field: "OTHERUSER"})
    assert field in str(raised.value)


@pytest.mark.parametrize("spelling", ["userId", "USER_ID", "Tenant_Id", "ownerId"])
def test_a_respelled_identity_field_is_refused_too(spelling: str) -> None:
    """The refusal normalises the key, so a camelCase spelling is not a way around it."""
    with pytest.raises(ValueError):
        ob.log_fields("paper.session.started", **{spelling: "OTHERUSER"})


@pytest.mark.parametrize("source", sorted(ob.REQUEST_SUPPLIED_IDENTITY_SOURCES))
def test_an_identity_cannot_be_bound_from_a_request_supplied_source(source: str) -> None:
    with pytest.raises(ValueError) as raised:
        ob.bind_authenticated_identity("OTHERUSER", source=source)
    assert source in str(raised.value)


@pytest.mark.parametrize("source", sorted(ob.AUTHENTICATED_IDENTITY_SOURCES))
def test_a_record_takes_its_identity_from_the_authenticated_context(source: str) -> None:
    with ob.authenticated_identity_scope("verified-user-1", source=source) as identity:
        assert identity.user_id == "verified-user-1"
        fields = ob.log_fields(
            "marketplace.subscription.activated", requested_owner="OTHERUSER"
        )
    assert fields["user_id"] == "verified-user-1"
    assert fields["identity_source"] == source
    assert fields["requested_owner"] == "OTHERUSER", (
        "a non-identity field is not the enforcement point; it is kept, and it is not what "
        "user_id is read from"
    )
    assert ob.current_identity() == ob.UNBOUND_IDENTITY, "the scope did not unbind the identity"


def test_an_unbound_identity_is_reported_as_absent() -> None:
    """Absent is reported as absent, never guessed - Requirement 28.5's distinction."""
    assert ob.current_identity() == ob.UNBOUND_IDENTITY
    fields = ob.log_fields("paper.order.rejected")
    assert fields["user_id"] is None
    assert fields["identity_source"] == "unbound"


def test_a_reserved_field_cannot_be_overridden_by_the_thing_being_logged_about() -> None:
    """``request_id`` and ``event`` are the record's own, not the caller's to supply."""
    for field in ("request_id", "identity_source"):
        with pytest.raises(ValueError):
            ob.log_fields("paper.session.stopped", **{field: "supplied"})


# ══════════════════════════════════════════════════════════════════════════
#  REQUIREMENT 26.3 - THE PAPER_SESSION KEY
# ══════════════════════════════════════════════════════════════════════════


def test_the_paper_log_key_matches_the_websocket_envelope() -> None:
    """The log key and the frame a client received must be the same two fields."""
    assert ob.PAPER_LOG_KEY_FIELDS == ("session_id", "sequence")
    for field in ob.PAPER_LOG_KEY_FIELDS:
        assert field in ENVELOPE_FIELDS, (
            f"{field!r} keys a log record but is not in the WebSocket envelope, so an operator "
            "cannot line the record up with the frame the client received"
        )


def test_a_paper_record_carries_its_key_and_still_redacts() -> None:
    assert ob.paper_log_key("session-1", 7) == ("session-1", 7)
    fields = ob.paper_log_fields(
        "paper.order.filled",
        "session-1",
        7,
        api_key=SENTINELS["api_key"],
        venue="NSE",
    )
    assert fields["session_id"] == "session-1"
    assert fields["sequence"] == 7
    assert fields["venue"] == "NSE"
    assert fields["api_key"] == REDACTED_PLACEHOLDER
    assert SENTINELS["api_key"] not in repr(fields)


@pytest.mark.parametrize("sequence", [0, -1, True, "7", 1.0, None])
def test_a_paper_record_refuses_a_sequence_that_is_not_a_position(sequence: Any) -> None:
    """A record keyed to a frame the client never received points an operator at nothing."""
    with pytest.raises(ValueError):
        ob.paper_log_key("session-1", sequence)


@pytest.mark.parametrize("field", ob.PAPER_LOG_KEY_FIELDS)
def test_a_paper_record_refuses_a_duplicated_key_field(field: str) -> None:
    """A record cannot be keyed to one frame positionally and another by keyword.

    Refused by the signature itself: ``session_id`` and ``sequence`` are formal parameters of
    ``paper_log_fields``, so a second spelling raises ``TypeError`` before the body runs. Asserted
    as ``TypeError`` rather than the ``ValueError`` the body raises for the same case, because
    that ``ValueError`` branch is unreachable while the two names are formal parameters - see this
    file's report. The guarantee holds either way; the exception type is the one Python produces.
    """
    with pytest.raises(TypeError):
        ob.paper_log_fields("paper.order.filled", "session-1", 7, **{field: 9})


def test_a_paper_log_line_renders_without_a_value() -> None:
    """The plain-text rendering ``main.py``'s handler formats is redacted too."""
    line = ob.log_line("paper.session.error", api_key=SENTINELS["api_key"], stage="submit")
    assert SENTINELS["api_key"] not in line
    assert REDACTED_PLACEHOLDER in line
    assert "stage='submit'" in line
