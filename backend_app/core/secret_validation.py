# -*- coding: utf-8 -*-
"""Is this string an actual credential, or is it the template's placeholder?

ENV-03. ``bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")`` is ``True``, and that one fact was
enough to defeat every credential check in this codebase, because all of them were spelled
``if not value:``. A non-empty string satisfied them, so the placeholders committed in
``backend_app/.env`` and ``.env.example`` were indistinguishable from configured secrets:

    SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY   # 30 characters, truthy
    MASTER_ENCRYPTION_KEYS=YOUR_FERNET_ENCRYPTION_KEY           # 26 characters, truthy
    STRIPE_SECRET_KEY=sk_live_your-stripe-secret-key            # passes the sk_live_ check

The last one is the clearest statement of why presence is not configuration:
``billing._validate_keys`` accepts a Stripe key only when it starts with ``sk_live_``,
specifically so a test-mode key cannot grant a paid entitlement — and the template's own
placeholder starts with ``sk_live_``.

FOUR VERDICTS, NOT TWO. The thing a credential check has to answer is not "truthy?" but:

    MISSING      the variable is not set at all
    EMPTY        set, but to nothing (or only whitespace)
    PLACEHOLDER  set to a template value nobody replaced
    USABLE       set to something that is at least not obviously fake

``USABLE`` is deliberately the weakest of the four claims. This module cannot tell a real
Supabase service-role key from a random string of the same shape, and it does not pretend
to: it recognises *fakes*, and "usable" means only "not recognisably a fake". Strength
checks that CAN be made (HS256 key length, entropy) live next to the variable they apply
to — see ``Settings.jwt_secret_problems``.

ONE VOCABULARY. ``Settings.WEAK_JWT_SECRET_PATTERNS`` used to carry its own private list of
weak-secret substrings. Two lists for one concept is how ``detect_ml_nodes`` ended up with
two spellings of "this is a model node" and a gate that missed half of them, so
``config.py`` now points at :data:`PLACEHOLDER_SUBSTRINGS` instead of keeping a second copy.

NO VALUE IS EVER RETURNED, LOGGED OR FORMATTED. Every function here takes a secret and
returns a verdict or a sentence about a verdict. None of them put any part of the input in
their output. A control that handles secrets must not be the thing that discloses them.

Regression: tests/test_env03_placeholder_secrets_are_invalid.py
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

__all__ = [
    "SecretVerdict",
    "PLACEHOLDER_SUBSTRINGS",
    "PLACEHOLDER_EXACT",
    "classify_secret",
    "secret_is_usable",
    "describe_verdict",
]


class SecretVerdict(str, Enum):
    """What a configured value actually is.

    ``str`` subclass so a verdict is directly loggable and comparable to a literal without
    callers reaching for ``.value``.
    """

    MISSING = "MISSING"
    EMPTY = "EMPTY"
    PLACEHOLDER = "PLACEHOLDER"
    USABLE = "USABLE"

    @property
    def is_usable(self) -> bool:
        return self is SecretVerdict.USABLE


#: Substrings that mark a value as a template placeholder. Matched case-insensitively
#: against the whole value.
#:
#: CHOSEN TO BE UNAMBIGUOUS. Every entry is a word that appears in this repository's own
#: templates (``.env.example``, ``backend_app/.env``, the Terraform and task-definition
#: samples) or in the conventional placeholder vocabulary. Bare words that occur naturally
#: inside real credentials — ``key``, ``secret``, ``token``, ``test`` — are deliberately NOT
#: here; they appear in :data:`PLACEHOLDER_EXACT` instead, where they only match a value
#: that consists of nothing else.
#:
#: FALSE-POSITIVE COST. A real credential is random, so the chance that a 200-character
#: base64 key contains a specific 7-letter English word is on the order of 1e-9. A false
#: positive refuses a boot with a message naming the variable, which an operator can act on
#: in seconds. A false NEGATIVE ships a placeholder to production. The asymmetry is the
#: whole reason this list errs toward matching.
PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    # "fill this in" conventions, both spellings this repository actually uses
    # (backend_app/.env uses YOUR_UPPER_SNAKE, .env.example uses your-lower-kebab).
    "your_",
    "your-",
    "your.",
    "yourmailserver",
    "yourdomain",
    "yourproject",
    "youraccount",
    "insert_",
    "insert-",
    "fill_me",
    "fill-me",
    # explicit "replace before deploying"
    "changeme",
    "change-me",
    "change_me",
    "change me",
    "changethis",
    "change-this",
    "change_this",
    "replaceme",
    "replace-me",
    "replace_me",
    "to-be-replaced",
    "to_be_replaced",
    # self-describing fakes
    "placeholder",
    "dummy",
    "notarealkey",
    "notasecret",
    "fakekey",
    "fake-key",
    "fake_key",
    "sample-",
    "sample_",
    "example.com",
    "example.org",
    "example.net",
    # markers left by whoever wrote the template
    "todo",
    "fixme",
    "xxxxxxxx",
    # weak-by-construction values. These three are inherited from the JWT check that used
    # to own this vocabulary and are load-bearing for it:
    # tests/test_jwt_secret_is_production_grade.py pins "my-default-...",
    # "correct-horse-battery-password-staple" and "insecure-but-long-enough".
    "default",
    "password",
    "insecure",
    "dev-secret",
    "dev_secret",
    "test-secret",
    "test_secret",
)


#: Values that are placeholders only when they are the ENTIRE value. Compared
#: case-insensitively after stripping surrounding whitespace.
#:
#: These words all occur inside legitimate credentials, so matching them as substrings
#: would refuse real configuration. ``"string"`` is here because it is the value an operator
#: gets by clicking Execute in the generated OpenAPI documentation and copying the example.
PLACEHOLDER_EXACT: frozenset[str] = frozenset(
    {
        "none",
        "null",
        "nil",
        "nan",
        "n/a",
        "na",
        "tbd",
        "tba",
        "unset",
        "undefined",
        "unknown",
        "key",
        "secret",
        "token",
        "value",
        "string",
        "test",
        "testing",
        "foo",
        "bar",
        "baz",
        "qux",
        "abc",
        "abc123",
        "xxx",
        "x",
        "-",
        "--",
        "...",
        "???",
        "0",
        "1",
        "true",
        "false",
    }
)


def classify_secret(value: Optional[str]) -> SecretVerdict:
    """Classify a configured value as MISSING, EMPTY, PLACEHOLDER or USABLE.

    Pure: reads no environment, logs nothing, and returns no part of ``value``.

    ``None`` is MISSING (the variable is absent). ``""`` and whitespace-only are EMPTY —
    told apart from MISSING on purpose, because they are different operator mistakes: a
    missing variable was forgotten, an empty one was set to nothing, which is usually a
    secret-manager reference that resolved to nothing. Collapsing them hides the second.
    """
    if value is None:
        return SecretVerdict.MISSING

    stripped = value.strip()
    if not stripped:
        return SecretVerdict.EMPTY

    lowered = stripped.lower()

    if lowered in PLACEHOLDER_EXACT:
        return SecretVerdict.PLACEHOLDER

    # ``<your-key-here>`` style. Matched on the whole shape rather than on a bare "<"
    # substring, because an angle bracket can legitimately occur inside a URL-encoded
    # password and should not by itself condemn a value.
    if stripped.startswith("<") and stripped.endswith(">"):
        return SecretVerdict.PLACEHOLDER

    if any(pattern in lowered for pattern in PLACEHOLDER_SUBSTRINGS):
        return SecretVerdict.PLACEHOLDER

    # A value made of one repeated character carries no key material regardless of length.
    # Length and entropy in general are the business of whoever owns the variable; this one
    # case is here because it is unambiguously a placeholder ("xxxxx", "aaaa", "0000").
    if len(set(stripped)) == 1:
        return SecretVerdict.PLACEHOLDER

    return SecretVerdict.USABLE


def secret_is_usable(value: Optional[str]) -> bool:
    """``True`` only when :func:`classify_secret` returns USABLE.

    The replacement for ``if not value:`` at a credential check. "Usable" is the weak claim
    described in this module's docstring: not recognisably fake, NOT verified against the
    provider.
    """
    return classify_secret(value) is SecretVerdict.USABLE


def describe_verdict(name: str, verdict: SecretVerdict) -> str:
    """One operator-facing sentence about ``name``, naming the verdict and never the value.

    ``name`` is an environment variable name, which is not a secret. The value is not
    passed to this function at all, so it cannot leak through it.
    """
    if verdict is SecretVerdict.MISSING:
        return f"{name} is not set."
    if verdict is SecretVerdict.EMPTY:
        return f"{name} is set to an empty value."
    if verdict is SecretVerdict.PLACEHOLDER:
        return (
            f"{name} is set to a template placeholder, not a credential. It matches the "
            f"placeholder vocabulary in core/secret_validation.py "
            f"(PLACEHOLDER_SUBSTRINGS / PLACEHOLDER_EXACT). The matching pattern is "
            f"deliberately not named here, because naming it would write part of the "
            f"configured value to the log."
        )
    return f"{name} is configured."
