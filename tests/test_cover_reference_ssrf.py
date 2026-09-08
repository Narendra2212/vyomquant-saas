"""tests/test_cover_reference_ssrf.py - the cover-image allow-list, driven adversarially.

Spec: marketplace-subscriptions-paper-trading task 33.3. ``design.md`` -> "SSRF and cover
images (Requirement 22.7)". Requirement 22.7 (with 22.9 for the body).

WHAT IS UNDER TEST
------------------
``marketplace/media.py::validate_cover_reference`` - the single gate a
``library_strategies.cover_image`` write passes - and the fact that the publish handler actually
passes through it rather than writing ``payload.cover_image`` straight into its insert.

THE ONE THAT MATTERS
--------------------
``TestAStringPrefixOfAPermittedOriginIsNotThatOrigin``. Both

    https://d7d88qs4jmch.cloudfront.net.attacker.com/x
    https://d7d88qs4jmch.cloudfront.net@attacker.com/x

begin with the permitted origin *as text* and neither is on the permitted origin.
:func:`_naive_startswith_would_accept` is a deliberately naive ``str.startswith``
implementation, and the test asserts it **would** have accepted both while the real validator
refuses both - so the pair is proven to be a real discriminator between the wrong
implementation and the right one, rather than two strings that any implementation happens to
refuse.

NON-VACUITY
-----------
Three separate guards, because a security test that asserts nothing is worse than no test:

* every rejection case asserts the *catalogue code* and the *HTTP status*, not merely "it
  raised" - so a refusal for an unrelated internal reason cannot pass as the intended one;
* :func:`_naive_startswith_would_accept` proves the prefix cases discriminate (above);
* :func:`_no_network` replaces ``socket.socket`` and every name-resolution entry point with a
  raise for the duration of each validation, so "nothing server-side dereferences the
  reference" is asserted mechanically rather than asserted in prose.

NO EVENT LOOP, NO ``asyncio.run``
---------------------------------
Every test here is synchronous. ``validate_cover_reference`` is a pure function that performs
no I/O, so nothing in this file needs a loop, a client or a Persistence_Layer double.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path
from typing import Any, Tuple

import pytest

from backend_app.backend.marketplace import errors, media
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_COVER_REFERENCE_REJECTED,
    MarketplaceError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROUTER = REPO_ROOT / "backend_app" / "routers" / "library.py"

#: The configured Supabase project origin used throughout. A stand-in for a real project ref:
#: the allow-list is read from the environment, so the test states its own configuration rather
#: than depending on whatever the developer's shell happens to hold.
SUPABASE_ORIGIN = "https://projectref.supabase.co"

#: The CDN origin - this project's own distribution, which is also ``media.DEFAULT_CDN_ORIGIN``.
CDN_ORIGIN = "https://d7d88qs4jmch.cloudfront.net"

#: A reference on each permitted prefix. Both must be admitted, or every rejection below is
#: vacuous: a validator that refuses everything would otherwise pass the whole file.
PERMITTED_STORAGE_REFERENCE = (
    f"{SUPABASE_ORIGIN}/storage/v1/object/public/covers/user/cover.png"
)
PERMITTED_CDN_REFERENCE = f"{CDN_ORIGIN}/media/covers/cover.png"


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the allow-list at a known Supabase project and the default CDN origin.

    ``media`` reads both at call time (the idiom the rest of the application uses), so setting
    the environment is the whole configuration - there is nothing to reload and no module state
    to reset.
    """
    monkeypatch.setenv(media.SUPABASE_URL_ENV, SUPABASE_ORIGIN)
    monkeypatch.delenv(media.CDN_ORIGIN_ENV, raising=False)


class _NetworkReached(AssertionError):
    """Raised if validation so much as opens a socket or resolves a name."""


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outbound primitive fail loudly for the duration of a test.

    This is the executable form of the module docstring's central claim: validation is parsing,
    not dereferencing. If a future edit adds a HEAD request, an image probe or a DNS lookup to
    the validator, the test that exercises it fails here instead of quietly turning the
    validator into the SSRF primitive it exists to prevent.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise _NetworkReached(
            "cover reference validation attempted network access; it must only parse"
        )

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "gethostbyname", _refuse)


def _naive_startswith_would_accept(reference: str) -> bool:
    """The WRONG implementation, kept here as a control.

    A ``reference.startswith(permitted_origin)`` check - the obvious first attempt. Present so
    the string-prefix cases can be proven to discriminate between it and the real validator; it
    is never used to validate anything.
    """
    return any(
        reference.startswith(prefix)
        for prefix in (f"{SUPABASE_ORIGIN}/storage/v1/object/public/", CDN_ORIGIN)
    )


def _refusal(reference: Any) -> MarketplaceError:
    """Validate ``reference``, require a refusal, and hand back the error for inspection."""
    with pytest.raises(MarketplaceError) as caught:
        media.validate_cover_reference(reference)
    return caught.value


def _assert_refused(reference: Any, expected_reason: str | None = None) -> MarketplaceError:
    """The full refusal contract: the catalogue code, the pinned 422, and a stable reason."""
    error = _refusal(reference)
    assert error.code == MARKETPLACE_COVER_REFERENCE_REJECTED, (
        f"{reference!r} was refused with {error.code!r}; Requirement 22.7's refusal carries "
        f"the single stable catalogue code {MARKETPLACE_COVER_REFERENCE_REJECTED!r}"
    )
    assert error.http_status == 422, (
        f"{reference!r} was refused with HTTP {error.http_status}; the catalogue pins this code "
        f"to 422"
    )
    assert error.details.get("reason") in media.REJECTION_REASONS, (
        f"{reference!r} was refused with reason {error.details.get('reason')!r}, which is not "
        f"one of the declared REJECTION_REASONS"
    )
    if expected_reason is not None:
        assert error.details["reason"] == expected_reason, (
            f"{reference!r} was refused as {error.details['reason']!r}, expected "
            f"{expected_reason!r}"
        )
    return error


# ══════════════════════════════════════════════════════════════════════════
#  THE ALLOW-LIST ADMITS THE TWO CONFIGURED LOCATIONS (non-vacuity)
# ══════════════════════════════════════════════════════════════════════════


class TestThePermittedLocationsAreAdmitted:
    """Without this, every rejection below would pass against a reject-everything stub."""

    @pytest.mark.parametrize(
        "reference", [PERMITTED_STORAGE_REFERENCE, PERMITTED_CDN_REFERENCE]
    )
    def test_a_reference_inside_a_permitted_prefix_is_returned_unchanged(
        self, reference: str, _no_network: None
    ) -> None:
        assert media.validate_cover_reference(reference) == reference

    def test_the_allow_list_holds_exactly_the_two_configured_locations(self) -> None:
        """The Supabase storage bucket prefix and the CDN origin - and nothing else."""
        assert [prefix.as_text() for prefix in media.allowed_cover_prefixes()] == [
            f"{SUPABASE_ORIGIN}/storage/v1/object/public/",
            f"{CDN_ORIGIN}/",
        ]

    def test_an_absent_reference_is_absent_rather_than_refused(
        self, _no_network: None
    ) -> None:
        """A Listing need not carry a cover image; Requirement 22.7 governs the case ``WHERE``
        it does. Both spellings of "none" normalise to ``None``, which is what is stored."""
        assert media.validate_cover_reference(None) is None
        assert media.validate_cover_reference("") is None

    def test_the_default_cdn_origin_is_the_configured_one(self) -> None:
        """With the CDN variable unset, the default is the distribution this project deploys."""
        assert media.DEFAULT_CDN_ORIGIN == CDN_ORIGIN


# ══════════════════════════════════════════════════════════════════════════
#  THE ONE THAT MATTERS: A STRING PREFIX IS NOT AN ORIGIN
# ══════════════════════════════════════════════════════════════════════════


class TestAStringPrefixOfAPermittedOriginIsNotThatOrigin:
    """``startswith`` accepts both of these. Parsed host equality accepts neither."""

    SUFFIXED_HOST = f"{CDN_ORIGIN}.attacker.com/x"
    USERINFO_HOST = f"{CDN_ORIGIN}@attacker.com/x"

    def test_a_longer_host_that_starts_with_the_permitted_one_is_refused(
        self, _no_network: None
    ) -> None:
        _assert_refused(self.SUFFIXED_HOST, media.REASON_HOST_NOT_PERMITTED)

    def test_the_permitted_origin_used_as_userinfo_is_refused(
        self, _no_network: None
    ) -> None:
        _assert_refused(self.USERINFO_HOST, media.REASON_USERINFO_PRESENT)

    def test_the_same_two_against_the_supabase_prefix(self, _no_network: None) -> None:
        _assert_refused(
            f"{SUPABASE_ORIGIN}.attacker.com/storage/v1/object/public/x",
            media.REASON_HOST_NOT_PERMITTED,
        )
        _assert_refused(
            f"{SUPABASE_ORIGIN}@attacker.com/storage/v1/object/public/x",
            media.REASON_USERINFO_PRESENT,
        )

    def test_the_naive_implementation_would_have_accepted_both(self) -> None:
        """The control. If this ever fails, the two cases above have stopped discriminating
        between a ``startswith`` check and a parsed-host check, and a new pair is needed."""
        for reference in (self.SUFFIXED_HOST, self.USERINFO_HOST):
            assert _naive_startswith_would_accept(reference), (
                f"{reference!r} no longer fools a naive startswith check, so it no longer "
                f"proves anything about the real validator"
            )

    def test_the_real_validator_and_the_naive_one_disagree_on_exactly_these(self) -> None:
        """Stated as a disagreement rather than as two separate facts, because the value of
        the pair is that the two implementations differ on it."""
        for reference in (self.SUFFIXED_HOST, self.USERINFO_HOST):
            assert _naive_startswith_would_accept(reference) is True
            _assert_refused(reference)


# ══════════════════════════════════════════════════════════════════════════
#  THE CLASSIC SSRF TARGETS
# ══════════════════════════════════════════════════════════════════════════


#: The cloud metadata endpoints, over both schemes. Refused for the reason that matters - the
#: host is not one of ours - rather than because ``http`` was blocked: ``http`` is inside
#: ``ALLOWED_COVER_SCHEMES``' outer bound (a locally configured storage origin may be plain
#: ``http``), so these cases exercise the HOST comparison and not a scheme filter that would
#: have hidden it.
CLOUD_METADATA_CASES: Tuple[Tuple[str, str], ...] = (
    ("http://169.254.169.254/latest/meta-data/", media.REASON_HOST_NOT_PERMITTED),
    ("https://169.254.169.254/latest/meta-data/", media.REASON_HOST_NOT_PERMITTED),
    (
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        media.REASON_HOST_NOT_PERMITTED,
    ),
    ("http://metadata.google.internal/computeMetadata/v1/", media.REASON_HOST_NOT_PERMITTED),
    ("http://[fd00:ec2::254]/latest/meta-data/", media.REASON_HOST_NOT_PERMITTED),
)

INTERNAL_HOST_CASES: Tuple[Tuple[str, str], ...] = (
    ("https://localhost/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://127.0.0.1/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://[::1]/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://10.0.0.7/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://192.168.1.10/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://172.16.4.4/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://redis.internal/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("https://supabase.svc.cluster.local/cover.png", media.REASON_HOST_NOT_PERMITTED),
    # The loopback and the private ranges over plain http as well, which is how they are
    # normally reached - and refused on the host, not on the scheme.
    ("http://localhost:8000/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("http://127.0.0.1:6379/cover.png", media.REASON_HOST_NOT_PERMITTED),
    ("http://[::1]:8000/cover.png", media.REASON_HOST_NOT_PERMITTED),
)

NON_HTTP_SCHEME_CASES: Tuple[Tuple[str, str], ...] = (
    ("file:///etc/passwd", media.REASON_SCHEME_NOT_PERMITTED),
    ("file://localhost/etc/passwd", media.REASON_SCHEME_NOT_PERMITTED),
    ("gopher://127.0.0.1:6379/_INFO", media.REASON_SCHEME_NOT_PERMITTED),
    ("dict://127.0.0.1:11211/stats", media.REASON_SCHEME_NOT_PERMITTED),
    ("data:image/png;base64,iVBORw0KGgo=", media.REASON_SCHEME_NOT_PERMITTED),
    ("javascript:alert(1)", media.REASON_SCHEME_NOT_PERMITTED),
    # An otherwise permitted host reached over an unexpected scheme.
    (f"ftp://{CDN_ORIGIN.split('://')[1]}/media/cover.png", media.REASON_SCHEME_NOT_PERMITTED),
)

SHAPE_CASES: Tuple[Tuple[str, str], ...] = (
    # Scheme-relative: the parsed scheme is empty, so it is refused by omission.
    ("//attacker.com/x", media.REASON_SCHEME_NOT_PERMITTED),
    (f"//{CDN_ORIGIN.split('://')[1]}/media/cover.png", media.REASON_SCHEME_NOT_PERMITTED),
    # Userinfo, and an embedded credential.
    ("https://user@attacker.com/x", media.REASON_USERINFO_PRESENT),
    ("https://user:hunter2@attacker.com/x", media.REASON_USERINFO_PRESENT),
    (
        f"https://user:hunter2@{CDN_ORIGIN.split('://')[1]}/media/cover.png",
        media.REASON_USERINFO_PRESENT,
    ),
    # A permitted host on an unexpected port, and over the wrong scheme for its prefix.
    (f"{CDN_ORIGIN}:8443/media/cover.png", media.REASON_PREFIX_NOT_PERMITTED),
    ("http://d7d88qs4jmch.cloudfront.net/media/cover.png", media.REASON_PREFIX_NOT_PERMITTED),
    # A permitted host, outside its permitted storage route.
    (f"{SUPABASE_ORIGIN}/storage/v1/object/sign/covers/x", media.REASON_PREFIX_NOT_PERMITTED),
    (f"{SUPABASE_ORIGIN}/rest/v1/library_strategies", media.REASON_PREFIX_NOT_PERMITTED),
    # The prefix itself names an origin, not an image.
    (f"{SUPABASE_ORIGIN}/storage/v1/object/public/", media.REASON_PREFIX_NOT_PERMITTED),
    (f"{CDN_ORIGIN}/", media.REASON_PREFIX_NOT_PERMITTED),
    # Traversal out of the permitted route, literal and encoded.
    (
        f"{SUPABASE_ORIGIN}/storage/v1/object/public/../../secret",
        media.REASON_PATH_NOT_PERMITTED,
    ),
    (
        f"{SUPABASE_ORIGIN}/storage/v1/object/public/%2e%2e%2f%2e%2e/secret",
        media.REASON_PATH_NOT_PERMITTED,
    ),
    # A fragment, and whitespace or a control character smuggled in.
    (f"{CDN_ORIGIN}/media/cover.png#frag", media.REASON_FRAGMENT_PRESENT),
    (f" {CDN_ORIGIN}/media/cover.png", media.REASON_CONTROL_CHARACTER),
    (f"{CDN_ORIGIN}/media/cover.png\n", media.REASON_CONTROL_CHARACTER),
    (f"https://\r{CDN_ORIGIN.split('://')[1]}/x", media.REASON_CONTROL_CHARACTER),
    # A homograph host: refused as non-ASCII before any comparison is attempted.
    ("https://d7d88qs4jmch.cloudfrоnt.net/x", media.REASON_NOT_ASCII),
    # Nothing resembling a URL at all.
    ("cover.png", media.REASON_SCHEME_NOT_PERMITTED),
    ("/storage/v1/object/public/covers/cover.png", media.REASON_SCHEME_NOT_PERMITTED),
    ("not a url", media.REASON_CONTROL_CHARACTER),
)


class TestEverythingOutsideTheAllowListIsRefused:
    @pytest.mark.parametrize(
        "reference,reason", CLOUD_METADATA_CASES, ids=lambda v: str(v)[:60]
    )
    def test_cloud_metadata_is_refused(
        self, reference: str, reason: str, _no_network: None
    ) -> None:
        _assert_refused(reference, reason)

    @pytest.mark.parametrize(
        "reference,reason", INTERNAL_HOST_CASES, ids=lambda v: str(v)[:60]
    )
    def test_an_internal_host_is_refused(
        self, reference: str, reason: str, _no_network: None
    ) -> None:
        _assert_refused(reference, reason)

    @pytest.mark.parametrize(
        "reference,reason", NON_HTTP_SCHEME_CASES, ids=lambda v: str(v)[:60]
    )
    def test_a_non_web_scheme_is_refused(
        self, reference: str, reason: str, _no_network: None
    ) -> None:
        _assert_refused(reference, reason)

    @pytest.mark.parametrize("reference,reason", SHAPE_CASES, ids=lambda v: str(v)[:60])
    def test_an_unexpected_reference_shape_is_refused(
        self, reference: str, reason: str, _no_network: None
    ) -> None:
        _assert_refused(reference, reason)

    def test_an_over_long_reference_is_refused(self, _no_network: None) -> None:
        over_long = (
            f"{CDN_ORIGIN}/media/"
            + "a" * (media.MAX_COVER_REFERENCE_LENGTH + 1)
            + ".png"
        )
        assert len(over_long) > media.MAX_COVER_REFERENCE_LENGTH
        _assert_refused(over_long, media.REASON_TOO_LONG)

    @pytest.mark.parametrize(
        "reference",
        [
            0,
            1,
            True,
            False,
            3.5,
            b"https://d7d88qs4jmch.cloudfront.net/x",
            bytearray(b"x"),
            ["https://d7d88qs4jmch.cloudfront.net/x"],
            {"url": "https://d7d88qs4jmch.cloudfront.net/x"},
            object(),
        ],
        ids=lambda v: type(v).__name__,
    )
    def test_a_non_string_reference_is_refused(
        self, reference: Any, _no_network: None
    ) -> None:
        """Refused rather than coerced. ``True`` is not ``str``, so it lands here too."""
        _assert_refused(reference, media.REASON_NOT_A_STRING)

    def test_an_empty_allow_list_refuses_everything(
        self, monkeypatch: pytest.MonkeyPatch, _no_network: None
    ) -> None:
        """Misconfiguration fails CLOSED: with neither location configured, the permitted set
        is empty and even a previously permitted reference is refused."""
        monkeypatch.setenv(media.SUPABASE_URL_ENV, "")
        monkeypatch.setenv(media.CDN_ORIGIN_ENV, "")
        assert media.allowed_cover_prefixes() == ()
        _assert_refused(PERMITTED_CDN_REFERENCE, media.REASON_HOST_NOT_PERMITTED)


# ══════════════════════════════════════════════════════════════════════════
#  THE REDIRECT CHAIN
# ══════════════════════════════════════════════════════════════════════════


class TestARedirectChainOnAPermittedHost:
    """A reference on a permitted host that redirects elsewhere.

    **The disposition is: admitted, and that is correct.** A redirect is only ever followed by
    whoever performs the fetch, and nothing server-side performs one - the frontend renders the
    reference in an ``<img src>`` and the *browser* follows any redirect, in the browser's own
    origin, subject to the browser's own image-loading rules. This server issues no request, so
    a redirect cannot steer it anywhere: there is no confused deputy to confuse. Refusing the
    reference would therefore protect nothing while breaking a legitimate CDN behaviour
    (a distribution answering ``302`` to a canonical or resized object).

    What the allow-list *does* guarantee is that the FIRST hop is on a location this platform
    controls, which is the only hop this platform can decide anything about. The rest of the
    chain is the browser's business, and if a handler ever starts dereferencing a cover
    reference, that is a new decision requiring its own review - :func:`_no_network` fails the
    moment it is taken here.
    """

    REDIRECTING_REFERENCE = f"{CDN_ORIGIN}/media/redirect/cover.png"

    def test_the_first_hop_is_what_is_validated_and_it_is_admitted(
        self, _no_network: None
    ) -> None:
        assert (
            media.validate_cover_reference(self.REDIRECTING_REFERENCE)
            == self.REDIRECTING_REFERENCE
        )

    def test_the_redirect_target_would_itself_be_refused_if_it_were_sent(
        self, _no_network: None
    ) -> None:
        """The complement: the allow-list is what it is regardless of how a reference was
        reached, so the target of such a redirect is refused when a caller sends it directly."""
        _assert_refused(
            "http://169.254.169.254/latest/meta-data/", media.REASON_HOST_NOT_PERMITTED
        )

    def test_validation_never_follows_anything(self, _no_network: None) -> None:
        """Both dispositions above, with every outbound primitive armed to raise. Validation is
        parsing; it resolves no name and opens no socket."""
        media.validate_cover_reference(self.REDIRECTING_REFERENCE)
        _assert_refused("https://attacker.com/x")


# ══════════════════════════════════════════════════════════════════════════
#  THE 422 BODY LEAKS NOTHING (Requirements 22.7, 22.9)
# ══════════════════════════════════════════════════════════════════════════


ALL_REJECTED_REFERENCES: Tuple[Any, ...] = tuple(
    reference
    for reference, _ in (
        CLOUD_METADATA_CASES + INTERNAL_HOST_CASES + NON_HTTP_SCHEME_CASES + SHAPE_CASES
    )
) + (
    f"{CDN_ORIGIN}.attacker.com/x",
    f"{CDN_ORIGIN}@attacker.com/x",
    12345,
)


class TestTheRefusalBodyCarriesACodeAndNothingElse:
    def test_the_code_is_in_the_catalogue_and_pinned_to_422(self) -> None:
        assert MARKETPLACE_COVER_REFERENCE_REJECTED in errors.ERROR_CODES
        assert errors.HTTP_STATUS_FOR_CODE[MARKETPLACE_COVER_REFERENCE_REJECTED] == 422
        # Absent from ALLOWED_HTTP_STATUS_FOR_CODE, so no call site can answer 200 and let an
        # unvetted reference through.
        assert MARKETPLACE_COVER_REFERENCE_REJECTED not in errors.ALLOWED_HTTP_STATUS_FOR_CODE
        with pytest.raises(ValueError):
            MarketplaceError(MARKETPLACE_COVER_REFERENCE_REJECTED, http_status=200)

    def test_the_public_sentence_is_the_catalogues_single_one(self) -> None:
        error = _refusal("https://attacker.com/x")
        assert error.message == errors.message_for_code(
            MARKETPLACE_COVER_REFERENCE_REJECTED
        )

    @pytest.mark.parametrize(
        "reference", ALL_REJECTED_REFERENCES, ids=lambda v: str(v)[:60]
    )
    def test_the_serialised_body_leaks_no_internal_detail(self, reference: Any) -> None:
        """Requirement 22.9 on this refusal specifically: no query, no table name, no
        traceback, no internal path - and no configured hostname the caller did not send."""
        error = _refusal(reference)
        body = errors.structured_error_body(error, request_id="fixed-request-id")

        assert set(body) == set(errors.ERROR_ENVELOPE_KEYS)
        assert set(body["error"]) == set(errors.ERROR_OBJECT_KEYS)
        assert body["error"]["code"] == MARKETPLACE_COVER_REFERENCE_REJECTED

        rendered = repr(body)
        lowered = rendered.lower()
        for marker in errors.FORBIDDEN_BODY_SUBSTRINGS:
            assert marker not in lowered, (
                f"the refusal body for {reference!r} carries the forbidden substring {marker!r}"
            )
        for banned in ("traceback", "select ", "psycopg", "supabase", "cloudfront"):
            assert banned not in lowered, (
                f"the refusal body for {reference!r} carries {banned!r}; a rejection must not "
                f"describe the platform's own storage layout"
            )

    def test_the_details_are_a_field_and_a_stable_reason_and_nothing_more(self) -> None:
        error = _refusal("https://attacker.com/x")
        assert set(error.details) == {"field", "reason"}
        assert error.details["field"] == media.COVER_REFERENCE_FIELD
        assert error.details["reason"] in media.REJECTION_REASONS

    def test_no_configured_prefix_appears_in_any_refusal(self) -> None:
        """The allow-list is the platform's, not the caller's. Stated separately from the
        deny-list check above because it is a different guarantee: a caller must not be able to
        discover a permitted origin by probing with a rejected one."""
        for reference in ALL_REJECTED_REFERENCES:
            rendered = repr(
                errors.structured_error_body(_refusal(reference), request_id="r")
            )
            for prefix in media.allowed_cover_prefixes():
                assert prefix.host not in rendered, (
                    f"the refusal body for {reference!r} discloses the permitted host "
                    f"{prefix.host!r}"
                )


# ══════════════════════════════════════════════════════════════════════════
#  THE WRITE PATH IS ACTUALLY INSTRUMENTED
# ══════════════════════════════════════════════════════════════════════════


def _publish_handler_source() -> ast.AST:
    """The ``publish_strategy`` function definition, parsed.

    Read from the file rather than from the imported module so this assertion needs no
    application import, no FastAPI app and no Persistence_Layer - and so it names the file a
    reviewer has to change to break it.
    """
    tree = ast.parse(LIBRARY_ROUTER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "publish_strategy"
        ):
            return node
    raise AssertionError("publish_strategy was not found in backend_app/routers/library.py")


class TestTheOnlyCoverImageWriteGoesThroughTheGate:
    """``publish_strategy``'s insert is the ONE place ``library_strategies.cover_image`` is
    written - the settings patch writes ``source_cloning_enabled``, the moderation patch writes
    the moderation columns, the clone writes ``clone_count`` and the rating aggregate writes the
    rating columns, and none of the four names this column. So one call site has to hold the
    gate, and this asserts it holds it."""

    def test_the_handler_calls_the_validator(self) -> None:
        handler = _publish_handler_source()
        calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "validate_cover_reference"
        ]
        assert len(calls) == 1, (
            "publish_strategy must call validate_cover_reference exactly once; found "
            f"{len(calls)} call(s)"
        )

    def test_the_insert_does_not_write_the_unvalidated_payload_field(self) -> None:
        """The bypass this guards against is a later edit putting ``payload.cover_image`` back
        into the insert payload, which would leave the validator called and its result
        ignored."""
        handler = _publish_handler_source()
        offenders = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Attribute)
            and node.attr == "cover_image"
            and isinstance(node.value, ast.Name)
            and node.value.id == "payload"
        ]
        assert len(offenders) == 1, (
            "`payload.cover_image` may be read exactly once - as the argument to "
            f"validate_cover_reference - but it is read {len(offenders)} time(s); a second read "
            "is how the validated value gets bypassed"
        )
        call = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "validate_cover_reference"
        ][0]
        assert offenders[0] in list(ast.walk(call)), (
            "the single `payload.cover_image` read is not the argument to "
            "validate_cover_reference, so the raw value reaches something else"
        )

    def test_no_other_module_writes_the_column(self) -> None:
        """A second write path would need its own gate. Asserted by absence: no source file
        under ``backend_app/`` other than the router mentions the column at all."""
        mentions = sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "backend_app").rglob("*.py")
            if "cover_image" in path.read_text(encoding="utf-8", errors="ignore")
        )
        assert mentions == [
            # The catalogue entry for the refusal, which names the column it protects.
            "backend_app/backend/marketplace/errors.py",
            # The gate itself, which names the column it guards.
            "backend_app/backend/marketplace/media.py",
            # The one handler that writes it.
            "backend_app/routers/library.py",
        ], (
            "a further module mentions cover_image; every write path needs the Requirement 22.7 "
            f"gate. Found: {mentions}"
        )
