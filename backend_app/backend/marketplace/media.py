"""
backend_app/backend/marketplace/media.py - the cover-image allow-list.

Spec: marketplace-subscriptions-paper-trading task 33.3. ``design.md`` -> "SSRF and cover
images (Requirement 22.7)". Requirement 22.7.

Exposes
-------
ALLOWED_COVER_PREFIXES      the import-time snapshot of :func:`allowed_cover_prefixes`
allowed_cover_prefixes      the configured permitted locations, read at call time
validate_cover_reference    **the one gate** a ``library_strategies.cover_image`` write passes
REJECTION_REASONS           every stable ``details["reason"]`` label the refusal can carry

NOTHING SERVER-SIDE DEREFERENCES A COVER REFERENCE
--------------------------------------------------
State it rather than imply it, because it is the premise the whole design rests on: **no
Marketplace_API handler, worker, sweep or projection fetches a caller-supplied cover image
reference.** There is no ``requests.get``, no ``httpx`` call, no ``urlopen``, no image probe,
no thumbnailer, no metadata read and no HEAD request anywhere on the path. The reference is
stored as text by the publish handler, returned as text by ``listing_projection`` (its
``cover_image`` member), and rendered by the frontend in an ``<img src>``, where the *browser*
- not this server - performs the only fetch that ever happens. That is precisely what makes an
allow-list applied at **write** time sufficient for Requirement 22.7: the server never becomes
the confused deputy, because the server never makes the request. A read-time re-check would
protect nothing that this write-time check has not already refused, and a server-side fetch
added later would be a new decision requiring its own review - not something this module
silently already covers.

ALLOW-LIST, NEVER A DENY-LIST
-----------------------------
The function admits a reference only when its **parsed** origin equals a configured permitted
origin, and refuses everything else - including every reference form nobody has thought of - by
omission. There is no list of bad hosts, no "block the metadata address" special case and no
scheme blacklist: ``file:``, ``data:``, ``gopher:``, ``ftp:``, ``javascript:``,
``http://169.254.169.254/latest/meta-data/``, ``http://localhost/``, ``http://[::1]/``, a
``.internal`` name and an RFC 1918 address are all refused for the same single reason - they are
not on the allow-list - and so is the next scheme somebody invents.

WHY THE HOST IS COMPARED PARSED AND EXACT, NOT WITH ``startswith``
-----------------------------------------------------------------
A ``reference.startswith(prefix)`` check is the obvious implementation and it is wrong. Both

    https://d7d88qs4jmch.cloudfront.net.attacker.com/x      (a longer host, same prefix text)
    https://d7d88qs4jmch.cloudfront.net@attacker.com/x      (the permitted origin as *userinfo*)

begin with the permitted origin as a string and neither is on the permitted origin. The first
resolves to the host ``d7d88qs4jmch.cloudfront.net.attacker.com``; the second resolves to the
host ``attacker.com`` with everything before the ``@`` discarded as credentials. So this module
never compares reference *text* against prefix *text*. It splits the reference with
``urllib.parse.urlsplit``, takes ``.hostname`` (which is the host with any userinfo and any port
removed, lower-cased), and requires it to be **equal** to a configured host - not to start with
it, not to end with it, not to contain it. The scheme and the port must match the configured
origin exactly too, and a ``@`` anywhere in the authority is refused outright before the
comparison, so a credential-bearing reference cannot even reach it.
``tests/test_cover_reference_ssrf.py`` pins both attacks, and pins that a naive ``startswith``
implementation would have accepted them.

WHERE THE PERMITTED ORIGINS COME FROM
-------------------------------------
Two, and only two, both read from the environment the way the rest of this application reads
configuration (``os.environ.get``, at call time - the idiom
``routers/library._get_service_client`` and ``checkout_service._frontend_base_url`` already
use), so nothing here is a hard-coded credential and a deployment changes the allow-list by
changing its configuration:

1. **The Supabase storage bucket prefix** - ``SUPABASE_URL`` (the same variable
   ``core/config.Settings.SUPABASE_URL``, ``routers/library`` and every worker read) joined with
   :data:`SUPABASE_STORAGE_PATH_PREFIX`, the fixed public-object route of Supabase Storage. Only
   the *public object* route is permitted: a signed-URL route or the storage admin API is not on
   the list and is therefore refused.
2. **The CDN origin** - :data:`CDN_ORIGIN_ENV`, defaulting to :data:`DEFAULT_CDN_ORIGIN`, the
   distribution this project already deploys the frontend to (``.github/workflows``
   ``06-frontend-deploy.yml`` and ``scripts/inspect_production_frontend.py`` name the same
   host). A public CDN hostname is not a secret and carries no credential; it is defaulted so
   that an environment which has not set the variable still gets a working allow-list rather
   than an empty one that silently refuses every cover image.

A reference must name something *inside* a permitted prefix - the prefix path itself, with
nothing after it, is an origin and not an image, and is refused.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Any network call. Validation is **parsing**, not dereferencing. This module imports no HTTP
  client and resolves no DNS name: resolving one would both defeat the point (a DNS answer can
  change between the check and any later use) and make the validator itself the SSRF primitive
  it exists to prevent.
* Any repair of a rejected reference. Nothing is rewritten, no scheme is upgraded and no host
  is "corrected"; a reference is either admitted unchanged or refused. A normaliser is where an
  allow-list quietly becomes a deny-list.
* The read path. ``listing_projection`` returns the stored value; because every stored value
  passed this gate, and because nothing server-side fetches it, there is nothing left for the
  read path to decide.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

from backend_app.backend.marketplace.errors import (
    MARKETPLACE_COVER_REFERENCE_REJECTED,
    MarketplaceError,
)

# ══════════════════════════════════════════════════════════════════════════
# THE CONFIGURED PERMITTED LOCATIONS
# ══════════════════════════════════════════════════════════════════════════

#: The environment variable holding the Supabase project origin. The same one
#: ``core/config.Settings.SUPABASE_URL``, ``routers/library._get_service_client`` and the
#: workers read; not re-spelled anywhere else in this module (Requirement 30.2).
SUPABASE_URL_ENV = "SUPABASE_URL"

#: Supabase Storage's fixed public-object route. Everything a browser may render publicly sits
#: under it; the signed-URL and admin routes do not, and are therefore not permitted.
SUPABASE_STORAGE_PATH_PREFIX = "/storage/v1/object/public/"

#: The environment variable holding the CDN origin, so a deployment can point the allow-list at
#: its own distribution or custom domain without a code change.
CDN_ORIGIN_ENV = "MEDIA_CDN_ORIGIN"

#: The distribution this project deploys to today (``.github/workflows/06-frontend-deploy.yml``
#: verifies the same hostname in the built bundle). A public CDN hostname, not a secret and not
#: a credential; defaulted so an unset variable yields a working allow-list rather than one that
#: refuses every cover image.
DEFAULT_CDN_ORIGIN = "https://d7d88qs4jmch.cloudfront.net"

#: The only schemes a reference may carry. A permitted prefix contributes its OWN scheme and the
#: reference must match that prefix's scheme exactly (see :func:`validate_cover_reference`), so
#: this tuple is the outer bound rather than the decision: it is what refuses ``file:``,
#: ``data:``, ``javascript:``, ``ftp:``, ``gopher:``, a scheme-relative ``//host/x`` (whose
#: parsed scheme is empty) and every other scheme, by omission.
ALLOWED_COVER_SCHEMES: Tuple[str, ...] = ("https", "http")

#: The default port of each permitted scheme, so ``https://host/x`` and ``https://host:443/x``
#: are the same origin while ``https://host:8443/x`` is not.
_DEFAULT_PORT_FOR_SCHEME = {"https": 443, "http": 80}

#: The longest reference accepted. Equal to the ``max_length`` already declared on
#: ``routers/library.PublishStrategyRequest.cover_image``, so the ceiling is one number rather
#: than two that can drift, and a reference that would be refused by the column's own bound is
#: refused here with a catalogue code instead of by the database.
MAX_COVER_REFERENCE_LENGTH = 500

#: Substrings a permitted path may never contain: dot-dot traversal in either literal or
#: percent-encoded form, an encoded separator, and a backslash. None of them has a legitimate
#: place in a stored object key, and refusing them keeps the accepted value as boring as the
#: ``<img src>`` that renders it.
_FORBIDDEN_PATH_SUBSTRINGS: Tuple[str, ...] = ("..", "%2e%2e", "%2f", "%5c", "\\")


class CoverPrefix(NamedTuple):
    """One permitted location, already parsed. Never compared as text.

    ``host`` is the exact hostname a reference must equal, ``scheme`` and ``port`` the exact
    scheme and effective port it must carry, and ``path_prefix`` the path it must sit strictly
    inside.
    """

    scheme: str
    host: str
    port: int
    path_prefix: str

    def as_text(self) -> str:
        """The prefix as it reads in configuration - for a docstring or a log, never for a
        comparison."""
        default = _DEFAULT_PORT_FOR_SCHEME.get(self.scheme)
        authority = self.host if self.port == default else f"{self.host}:{self.port}"
        return f"{self.scheme}://{authority}{self.path_prefix}"


# ══════════════════════════════════════════════════════════════════════════
# THE STABLE REFUSAL LABELS
# ══════════════════════════════════════════════════════════════════════════

#: The reference was not text at all.
REASON_NOT_A_STRING = "not_a_string"
#: Longer than :data:`MAX_COVER_REFERENCE_LENGTH`.
REASON_TOO_LONG = "too_long"
#: Carried a non-ASCII character (an internationalised or homograph host reaches a browser
#: punycode-encoded, so a raw non-ASCII reference is never the right value).
REASON_NOT_ASCII = "not_ascii"
#: Carried whitespace or a control character, including a newline or a tab.
REASON_CONTROL_CHARACTER = "control_character"
#: Could not be split into a scheme, an authority and a path.
REASON_MALFORMED = "malformed"
#: The scheme is absent (a scheme-relative ``//host/x``) or outside
#: :data:`ALLOWED_COVER_SCHEMES`.
REASON_SCHEME_NOT_PERMITTED = "scheme_not_permitted"
#: The authority carried a ``@``, i.e. userinfo or an embedded credential.
REASON_USERINFO_PRESENT = "userinfo_present"
#: Carried a fragment, which an ``<img src>`` has no use for.
REASON_FRAGMENT_PRESENT = "fragment_present"
#: The path carried one of :data:`_FORBIDDEN_PATH_SUBSTRINGS`.
REASON_PATH_NOT_PERMITTED = "path_not_permitted"
#: The parsed host is not, exactly, a permitted host.
REASON_HOST_NOT_PERMITTED = "host_not_permitted"
#: The host is permitted but the scheme, the port or the path is not the permitted prefix's -
#: an allowed host reached over an unexpected scheme or port, or outside its storage route.
REASON_PREFIX_NOT_PERMITTED = "prefix_not_permitted"

#: Every label :func:`validate_cover_reference` can put in ``details["reason"]``, declared once
#: so a caller and a test read the same set. Labels only: no configured host, no permitted
#: prefix and no part of the platform's own storage layout travels to a client.
REJECTION_REASONS: Tuple[str, ...] = (
    REASON_NOT_A_STRING,
    REASON_TOO_LONG,
    REASON_NOT_ASCII,
    REASON_CONTROL_CHARACTER,
    REASON_MALFORMED,
    REASON_SCHEME_NOT_PERMITTED,
    REASON_USERINFO_PRESENT,
    REASON_FRAGMENT_PRESENT,
    REASON_PATH_NOT_PERMITTED,
    REASON_HOST_NOT_PERMITTED,
    REASON_PREFIX_NOT_PERMITTED,
)

#: The field named in ``details``, so a client can tell which input was refused.
COVER_REFERENCE_FIELD = "cover_image"


# ══════════════════════════════════════════════════════════════════════════
# READING THE CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════


def _parse_prefix(candidate: str, path_prefix: str = "/") -> Optional[CoverPrefix]:
    """``candidate`` as a :class:`CoverPrefix`, or ``None`` when it is not usable.

    ``None`` rather than a raise: a permitted origin that is absent or misconfigured must
    remove itself from the allow-list, which fails **closed** (that reference form is refused).
    Raising here would turn a configuration typo into a 500 on the publish path, and defaulting
    to something permissive would turn it into an open allow-list.
    """
    text = (candidate or "").strip()
    if not text:
        return None

    split = urlsplit(text)
    scheme = (split.scheme or "").lower()
    if scheme not in ALLOWED_COVER_SCHEMES:
        return None
    if "@" in split.netloc:
        return None

    host = (split.hostname or "").lower()
    if not host:
        return None

    try:
        port = split.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORT_FOR_SCHEME[scheme]

    configured_path = split.path or ""
    joined = configured_path.rstrip("/") + path_prefix
    if not joined.startswith("/"):
        joined = "/" + joined
    return CoverPrefix(scheme=scheme, host=host, port=int(port), path_prefix=joined)


def allowed_cover_prefixes() -> Tuple[CoverPrefix, ...]:
    """The permitted locations, read from the environment at call time.

    Call time rather than import time, because that is how the rest of this application reads
    its configuration and because a process that is configured after import (a worker, a test,
    a container whose environment is assembled by its entrypoint) must see the configured value
    rather than whatever was set when the module happened to be first imported.

    An unconfigured or unparseable entry drops out, so the worst configuration mistake yields a
    SHORTER allow-list and never a longer one.
    """
    prefixes = []

    supabase = _parse_prefix(
        os.environ.get(SUPABASE_URL_ENV) or "", SUPABASE_STORAGE_PATH_PREFIX
    )
    # No default for the storage origin: unlike a public CDN hostname, a project origin is
    # deployment-specific, and inventing one would put a host nobody configured on the
    # allow-list.
    if supabase is not None:
        prefixes.append(supabase)

    # ``get(name)`` then an explicit ``is None`` test, NOT ``get(name) or DEFAULT``: an operator
    # who sets the variable to the empty string is saying "no CDN origin", and that has to
    # remove the entry from the allow-list rather than silently restore the default. Only an
    # ABSENT variable takes the default.
    configured_cdn = os.environ.get(CDN_ORIGIN_ENV)
    if configured_cdn is None:
        configured_cdn = DEFAULT_CDN_ORIGIN
    cdn = _parse_prefix(configured_cdn, "/")
    if cdn is not None:
        prefixes.append(cdn)

    return tuple(prefixes)


#: The import-time snapshot of :func:`allowed_cover_prefixes`, for a reader, a log line or a
#: test that wants to name the allow-list without calling it. :func:`validate_cover_reference`
#: does NOT consult this: it calls the function, so a process configured after import is
#: validated against its real configuration rather than against this snapshot.
ALLOWED_COVER_PREFIXES: Tuple[CoverPrefix, ...] = allowed_cover_prefixes()


# ══════════════════════════════════════════════════════════════════════════
# THE ONE GATE
# ══════════════════════════════════════════════════════════════════════════


def _refuse(reason: str) -> MarketplaceError:
    """The single refusal shape: the catalogue's 422 code, a stable reason label, nothing else.

    No configured host, no permitted prefix, no parsed component and no echo of the caller's
    own reference - so the body carries no query, no table name, no traceback and nothing about
    the platform's storage layout that the caller did not already know (Requirement 22.9).
    """
    return MarketplaceError(
        MARKETPLACE_COVER_REFERENCE_REJECTED,
        details={"field": COVER_REFERENCE_FIELD, "reason": reason},
    )


def validate_cover_reference(reference: Any) -> Optional[str]:
    """Return ``reference`` unchanged if it is a permitted cover image reference; else raise.

    Requirement 22.7, applied at write time on ``library_strategies.cover_image``.

    Args:
        reference: the caller-supplied value. ``None`` and ``""`` mean *no cover image* - a
            Listing is not required to carry one - and both return ``None``, which is what the
            write path stores. Every other value must be a permitted reference.

    Returns:
        The reference exactly as supplied, or ``None`` for the absent case. Nothing is
        rewritten, so what is stored is what was sent and what the frontend renders.

    Raises:
        MarketplaceError: ``MARKETPLACE_COVER_REFERENCE_REJECTED``, HTTP 422, with
            ``details["reason"]`` one of :data:`REJECTION_REASONS`. Raised for **everything**
            that is not, on a parsed comparison, inside a configured permitted prefix.

    This function performs no I/O of any kind. It does not fetch the reference, does not resolve
    its host and does not open a socket; see the module docstring on why parsing is the whole
    job.
    """
    if reference is None:
        return None
    if not isinstance(reference, str):
        # ``bool`` is not ``str``, so ``True`` lands here too rather than being coerced.
        raise _refuse(REASON_NOT_A_STRING)
    if reference == "":
        return None

    if len(reference) > MAX_COVER_REFERENCE_LENGTH:
        raise _refuse(REASON_TOO_LONG)
    if not reference.isascii():
        raise _refuse(REASON_NOT_ASCII)
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in reference):
        # Checked before parsing: ``urlsplit`` silently strips leading and trailing control
        # characters, so a reference carrying one would otherwise be compared as a value
        # different from the one that was sent.
        raise _refuse(REASON_CONTROL_CHARACTER)

    try:
        split = urlsplit(reference)
    except ValueError:
        raise _refuse(REASON_MALFORMED)

    scheme = (split.scheme or "").lower()
    if scheme not in ALLOWED_COVER_SCHEMES:
        # Also the scheme-relative ``//attacker.com/x``, whose parsed scheme is empty.
        raise _refuse(REASON_SCHEME_NOT_PERMITTED)

    if "@" in split.netloc:
        # Userinfo, or an embedded ``user:password``. Refused before any host comparison, so
        # ``https://permitted.example@attacker.com/x`` cannot be read as the permitted host.
        raise _refuse(REASON_USERINFO_PRESENT)

    host = (split.hostname or "").lower()
    if not host:
        raise _refuse(REASON_HOST_NOT_PERMITTED)

    try:
        port = split.port
    except ValueError:
        raise _refuse(REASON_MALFORMED)
    if port is None:
        port = _DEFAULT_PORT_FOR_SCHEME[scheme]

    if split.fragment:
        raise _refuse(REASON_FRAGMENT_PRESENT)

    path = split.path or ""
    lowered_path = path.lower()
    if any(marker in lowered_path for marker in _FORBIDDEN_PATH_SUBSTRINGS):
        raise _refuse(REASON_PATH_NOT_PERMITTED)

    prefixes = allowed_cover_prefixes()
    for prefix in prefixes:
        if (
            scheme == prefix.scheme
            and host == prefix.host  # EXACT host equality; never a text prefix test
            and int(port) == prefix.port
            and path.startswith(prefix.path_prefix)
            and len(path) > len(prefix.path_prefix)  # must name something inside the prefix
        ):
            return reference

    # Nothing matched. The two labels distinguish "that host is not ours at all" from "that is
    # one of our hosts, reached in a way that is not permitted" - a distinction the caller can
    # act on, and one that discloses nothing, since the caller supplied the host.
    if any(host == prefix.host for prefix in prefixes):
        raise _refuse(REASON_PREFIX_NOT_PERMITTED)
    raise _refuse(REASON_HOST_NOT_PERMITTED)


__all__ = [
    "ALLOWED_COVER_PREFIXES",
    "ALLOWED_COVER_SCHEMES",
    "CDN_ORIGIN_ENV",
    "COVER_REFERENCE_FIELD",
    "CoverPrefix",
    "DEFAULT_CDN_ORIGIN",
    "MAX_COVER_REFERENCE_LENGTH",
    "REASON_CONTROL_CHARACTER",
    "REASON_FRAGMENT_PRESENT",
    "REASON_HOST_NOT_PERMITTED",
    "REASON_MALFORMED",
    "REASON_NOT_ASCII",
    "REASON_NOT_A_STRING",
    "REASON_PATH_NOT_PERMITTED",
    "REASON_PREFIX_NOT_PERMITTED",
    "REASON_SCHEME_NOT_PERMITTED",
    "REASON_TOO_LONG",
    "REASON_USERINFO_PRESENT",
    "REJECTION_REASONS",
    "SUPABASE_STORAGE_PATH_PREFIX",
    "SUPABASE_URL_ENV",
    "allowed_cover_prefixes",
    "validate_cover_reference",
]
