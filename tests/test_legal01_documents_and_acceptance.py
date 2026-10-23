"""LEGAL-01 regression tests.

WHAT THIS FINDING WAS
=====================
In-app legal documents were stub text that contradicted the product's actual
behaviour, there were TWO divergent sets of them both being served, and nothing
recorded which version any user had accepted.

WHAT THESE TESTS PROTECT
========================
Four things, each of which was actually broken rather than hypothetically so:

 1. ONE SOURCE OF TRUTH. The prose lives in
    algo22-terminal/src/legal/documents.js and nowhere else. Two copies existed
    and they disagreed about refunds -- one promised pro-rated service credits,
    the other said all purchases were final -- so which page a customer happened
    to open decided whether they were owed money.

 2. THE VERSION STRINGS AGREE. The browser and the server each hold a copy of
    LEGAL_VERSION for different reasons and neither can read the other's at
    runtime. If one is bumped and the other is not, acceptances recorded in
    between name a version the other side does not have.

 3. THE THREE FALSE STATEMENTS STAY GONE. Each was checked against code, not
    intent, and each would silently come back if someone restored the old prose.

 4. ACCEPTANCE IS VALIDATED SERVER-SIDE. A client cannot record agreement to a
    version this deployment never published, and partial acceptance is refused
    rather than stored.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCUMENTS_JS = REPO / "algo22-terminal" / "src" / "legal" / "documents.js"
CONSENT_JS = REPO / "algo22-terminal" / "src" / "legal" / "consent.js"
BOOTSTRAP_JS = REPO / "algo22-terminal" / "src" / "bootstrap.js"
FRONTEND_SRC = REPO / "algo22-terminal" / "src"


# ══════════════════════════════════════════════════════════════════════════
#  1. ONE SOURCE OF TRUTH
# ══════════════════════════════════════════════════════════════════════════

def test_the_canonical_documents_file_exists():
    assert DOCUMENTS_JS.is_file(), (
        f"{DOCUMENTS_JS.relative_to(REPO)} is the single source of truth for every "
        f"legal document and must exist."
    )


def test_the_duplicate_legal_page_is_deleted_and_never_returns():
    """src/pages/LegalPage.jsx held a SECOND, contradicting set of documents.

    It said "All purchases are final and non-refundable" while the canonical
    Refund Policy promised pro-rated service credits. Both were routed and both
    were served.
    """
    duplicate = FRONTEND_SRC / "pages" / "LegalPage.jsx"
    assert not duplicate.exists(), (
        "src/pages/LegalPage.jsx is back. It was a second, divergent copy of the "
        "legal documents that contradicted the canonical Refund Policy. Render "
        "from src/legal/documents.js instead."
    )


def test_no_component_declares_its_own_legal_prose():
    """A second copy of the text must not reappear anywhere under src/.

    Detected by looking for the distinctive phrases the documents use, in any
    file other than the canonical one. A near-copy that paraphrases would evade
    this, which is why the duplicate-file test above exists as well.
    """
    fingerprints = [
        "Dataset rejected",            # not legal text; guards against false positives
        "non-refundable",
        "Risk Disclosure",
        "no warranty of any kind",
        "not a registered investment adviser",
    ]
    # Only the refund/advice phrases should be unique to the canonical file.
    guarded = ["non-refundable", "not a registered investment adviser"]

    offenders: dict[str, list[str]] = {}
    for pattern in ("*.jsx", "*.js"):
        for path in FRONTEND_SRC.rglob(pattern):
            if path == DOCUMENTS_JS:
                continue
            # Comments stripped for the same reason as _documents_text: a file
            # may legitimately EXPLAIN that the prose moved without carrying it.
            text = _strip_js_comments(
                path.read_text(encoding="utf-8", errors="replace")
            )
            hits = [p for p in guarded if p in text]
            if hits:
                offenders[str(path.relative_to(REPO))] = hits

    assert not offenders, (
        "legal prose found outside src/legal/documents.js, which is how the two "
        f"divergent copies happened: {offenders}"
    )


def test_the_legal_route_component_renders_from_the_canonical_source():
    page = FRONTEND_SRC / "components" / "legal" / "LegalPage.jsx"
    text = page.read_text(encoding="utf-8", errors="replace")
    assert "legal/documents" in text, (
        "components/legal/LegalPage.jsx must import the documents rather than "
        "declaring them."
    )
    assert "const legalContent" not in text, (
        "components/legal/LegalPage.jsx has an inline legalContent object again. "
        "That is the duplication this finding was about."
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. THE VERSION STRINGS AGREE
# ══════════════════════════════════════════════════════════════════════════

def _js_const(path: pathlib.Path, name: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"export const {name} = '([^']+)'", text)
    assert match, f"could not find `export const {name}` in {path.name}"
    return match.group(1)


def test_frontend_and_backend_legal_version_agree():
    """The one invariant that makes an acceptance record meaningful.

    The browser says which version it displayed; the server validates that claim
    against its own constant. If the two drift, either valid acceptances are
    refused or the recorded version names text the other side never had.
    """
    from backend_app.core.legal_documents import LEGAL_VERSION as backend_version

    frontend_version = _js_const(DOCUMENTS_JS, "LEGAL_VERSION")
    assert frontend_version == backend_version, (
        f"LEGAL_VERSION disagrees: documents.js has {frontend_version!r}, "
        f"core/legal_documents.py has {backend_version!r}. Bump both together."
    )


def test_frontend_and_backend_effective_date_agree():
    from backend_app.core.legal_documents import LEGAL_EFFECTIVE_DATE as backend_date

    assert _js_const(DOCUMENTS_JS, "LEGAL_EFFECTIVE_DATE") == backend_date


def test_the_required_document_set_agrees():
    from backend_app.core.legal_documents import REQUIRED_DOCUMENTS

    text = DOCUMENTS_JS.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"SIGNUP_REQUIRED_DOCUMENTS = \[([^\]]+)\]", text)
    assert match, "could not find SIGNUP_REQUIRED_DOCUMENTS in documents.js"
    frontend = {d.strip().strip("'\"") for d in match.group(1).split(",") if d.strip()}
    assert frontend == set(REQUIRED_DOCUMENTS), (
        f"required documents disagree: documents.js has {sorted(frontend)}, "
        f"core/legal_documents.py has {sorted(REQUIRED_DOCUMENTS)}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. THE THREE FALSE STATEMENTS STAY GONE
# ══════════════════════════════════════════════════════════════════════════

def _strip_js_comments(source: str) -> str:
    """Remove block and line comments.

    NECESSARY, not fastidious: documents.js's header deliberately QUOTES each of
    the three false statements in order to record what was wrong and why it was
    wrong. A naive substring search over the raw file therefore finds the very
    strings these tests assert are gone, and would fail forever on a correct
    file. The assertions are about the DOCUMENT PROSE, so the commentary is
    stripped before searching.
    """
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


def _documents_text() -> str:
    """The document prose only, with explanatory comments removed."""
    return _strip_js_comments(
        DOCUMENTS_JS.read_text(encoding="utf-8", errors="replace")
    )


def test_the_terms_no_longer_claim_a_user_can_activate_live_trading():
    """FALSE: core/live_execution_gate.py refuses unless the PLATFORM is in live
    mode, the enable flag is true, AND the tenant is allowlisted. A user cannot
    activate live trading; only a deployment change can.
    """
    text = _documents_text()
    assert "live trading requires explicit activation per strategy" not in text, (
        "the Terms claim a user can activate live trading per strategy. "
        "assert_platform_live_trading_enabled makes that false."
    )


def test_the_privacy_policy_no_longer_claims_read_only_keys_are_enforced():
    """FALSE, and backwards: routers/exchange.py refuses WITHDRAWAL permission
    and requires TRADE permission. A read-only key cannot place orders at all.
    """
    text = _documents_text()
    assert "Read-only API permissions are enforced by default" not in text, (
        "the Privacy Policy claims read-only keys are enforced. The code requires "
        "trade permission and refuses only withdrawal."
    )
    assert "withdrawal" in text.lower(), (
        "the corrected statement should say withdrawal permission is refused."
    )


def test_the_privacy_policy_no_longer_denies_third_party_tracking():
    """FALSE: bootstrap.js loads Google Analytics and Microsoft Clarity."""
    text = _documents_text()
    assert "We do not use third-party tracking or advertising cookies" not in text, (
        "the Privacy Policy denies third-party tracking while bootstrap.js loads "
        "Google Analytics and Microsoft Clarity."
    )
    for service in ("Google Analytics", "Microsoft Clarity", "Sentry", "Razorpay"):
        assert service in text, f"{service} is used but is not disclosed"


def test_the_refund_policy_no_longer_promises_unimplemented_service_credits():
    """The old policy offered pro-rated service credits after a 72-hour outage.

    No refund or credit code exists anywhere in the backend -- no proration, no
    credit issuance, no gateway refund call. A promise nothing implements is
    worse than no promise.
    """
    text = _documents_text()
    assert "72 continuous hours" not in text, (
        "the Refund Policy still promises credits after 72 hours of downtime, "
        "which nothing implements."
    )


def test_gst_is_described_as_inclusive():
    """Razorpay Standard Checkout charges the exact order amount and no GST
    computation exists in subscription_engine.py, so exclusive pricing would be
    a false statement AND would trip the SEC-02 order/payment amount guard.
    """
    text = _documents_text()
    assert "INCLUSIVE OF GOODS AND SERVICES TAX" in text or "INCLUDE GST" in text, (
        "prices must be described as GST-inclusive while no GST computation exists."
    )


def test_the_documents_declare_no_custody():
    """The strongest factual defence the platform has, and it must be stated."""
    text = _documents_text()
    assert "never take custody" in text.lower() or "do not hold" in text.lower()


# ══════════════════════════════════════════════════════════════════════════
#  4. CONSENT GATES ANALYTICS
# ══════════════════════════════════════════════════════════════════════════

def test_analytics_are_not_loaded_without_consent():
    """GA and Clarity used to load on first paint, before any interaction."""
    text = BOOTSTRAP_JS.read_text(encoding="utf-8", errors="replace")
    assert "hasAnalyticsConsent" in text, (
        "bootstrap.js must consult consent before loading analytics."
    )
    # The injection must live in a function that consent guards, not at top level.
    assert "function loadAnalytics" in text
    gate = text.index("hasAnalyticsConsent()")
    injection = text.index("googletagmanager.com")
    assert injection < gate or "if (hasAnalyticsConsent())" in text, (
        "the analytics injection must be reachable only behind a consent check."
    )


def test_consent_defaults_to_denied():
    text = CONSENT_JS.read_text(encoding="utf-8", errors="replace")
    assert "=== ConsentState.GRANTED" in text, (
        "hasAnalyticsConsent must test for an explicit grant. Treating an absent "
        "decision as permission is the behaviour being removed."
    )


def test_sentry_is_not_consent_gated():
    """Error diagnostics are relied on under legitimate interest and declared as
    such. Gating crash reporting behind a banner loses the crashes of exactly the
    visitors who never answer it.
    """
    text = BOOTSTRAP_JS.read_text(encoding="utf-8", errors="replace")
    init_sentry = text.index("initSentry(vars.VITE_SENTRY_DSN")
    init_analytics = text.index("initAnalytics(vars.VITE_GA_TRACKING_ID")
    assert init_sentry < init_analytics


# ══════════════════════════════════════════════════════════════════════════
#  5. SERVER-SIDE VALIDATION OF AN ACCEPTANCE
# ══════════════════════════════════════════════════════════════════════════

def test_an_unpublished_version_is_refused():
    from backend_app.core.legal_documents import (LEGAL_VERSION,
                                                  LegalAcceptanceRefused,
                                                  validate_version)

    assert validate_version(LEGAL_VERSION) == LEGAL_VERSION
    with pytest.raises(LegalAcceptanceRefused) as exc:
        validate_version("1999-01-01")
    assert exc.value.code == "LEGAL_VERSION_UNKNOWN"
    # The refusal must name the current version so a stale client can act.
    assert LEGAL_VERSION in exc.value.message


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2026-13", "20261023", None])
def test_a_malformed_version_is_refused(bad):
    from backend_app.core.legal_documents import (LegalAcceptanceRefused,
                                                  validate_version)

    with pytest.raises(LegalAcceptanceRefused) as exc:
        validate_version(bad)
    assert exc.value.code == "LEGAL_VERSION_MALFORMED"


def test_partial_acceptance_is_refused_not_recorded():
    """The signup flow shows all four documents together. A request naming three
    did not come from that flow, and storing it as though it covered all four --
    or as a partial the rest of the system has no concept of -- is wrong.
    """
    from backend_app.core.legal_documents import (REQUIRED_DOCUMENTS,
                                                  LegalAcceptanceRefused,
                                                  validate_documents)

    assert validate_documents(sorted(REQUIRED_DOCUMENTS)) == REQUIRED_DOCUMENTS

    with pytest.raises(LegalAcceptanceRefused) as exc:
        validate_documents(["terms", "privacy"])
    assert exc.value.code == "LEGAL_DOCUMENTS_INCOMPLETE"
    assert "missing" in exc.value.message


def test_an_unrecognised_document_id_is_refused():
    from backend_app.core.legal_documents import (REQUIRED_DOCUMENTS,
                                                  LegalAcceptanceRefused,
                                                  validate_documents)

    with pytest.raises(LegalAcceptanceRefused) as exc:
        validate_documents(sorted(REQUIRED_DOCUMENTS) + ["something_else"])
    assert exc.value.code == "LEGAL_DOCUMENTS_INCOMPLETE"
    assert "unrecognised" in exc.value.message


def test_a_user_with_no_record_is_not_current():
    """Correct for all 182 pre-existing accounts: none has a record, so none can
    be shown to have accepted anything.
    """
    from backend_app.core.legal_documents import (LEGAL_VERSION,
                                                  is_acceptance_current)

    assert is_acceptance_current(None) is False
    assert is_acceptance_current("") is False
    assert is_acceptance_current("2026-01-01") is False
    assert is_acceptance_current(LEGAL_VERSION) is True


# ══════════════════════════════════════════════════════════════════════════
#  6. THE MIGRATION
# ══════════════════════════════════════════════════════════════════════════

def test_the_migration_exists_and_is_append_only():
    migration = REPO / "backend_app" / "migrations" / "052_legal_acceptance.sql"
    assert migration.is_file()
    sql = migration.read_text(encoding="utf-8", errors="replace")

    assert "CREATE TABLE IF NOT EXISTS public.legal_acceptances" in sql
    # Append-only must be enforced, not merely intended.
    assert "BEFORE UPDATE OR DELETE" in sql, (
        "an acceptance is a historical fact; the table must refuse mutation."
    )
    # One acceptance per user per version, so repeat clicks are idempotent.
    assert "uq_legal_acceptances_user_version" in sql
    # The browser must not be able to write its own acceptance.
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in sql
    assert "GRANT SELECT ON public.legal_acceptances TO authenticated" in sql


def test_there_is_no_browser_insert_policy():
    migration = REPO / "backend_app" / "migrations" / "052_legal_acceptance.sql"
    sql = migration.read_text(encoding="utf-8", errors="replace")
    assert "FOR INSERT TO authenticated" not in sql, (
        "an acceptance the accepting party can write unaided is not evidence."
    )
    assert "FOR ALL TO authenticated" not in sql


# ══════════════════════════════════════════════════════════════════════════
#  7. THE DOCUMENTS' PROMISES ARE ACTUALLY WIRED
#
#  A policy that promises a control which does not exist is the defect this
#  finding was about, in a new form. Each test below pairs a sentence in the
#  documents with the code that has to exist for it to be true.
# ══════════════════════════════════════════════════════════════════════════

APP_JSX = FRONTEND_SRC / "App.jsx"
FOOTER_JSX = FRONTEND_SRC / "components" / "landing" / "Footer.jsx"
AUTH_PAGE = FRONTEND_SRC / "pages" / "AuthPage.jsx"


def test_the_consent_banner_is_mounted():
    """Privacy section 6: analytics "load ONLY AFTER you consent".

    consent.js can gate loading, but nothing is gated if the visitor is never
    asked, so the banner has to be in the tree.
    """
    text = APP_JSX.read_text(encoding="utf-8", errors="replace")
    assert "CookieConsent" in text, "the consent banner is not mounted in App.jsx"
    assert "<CookieConsent />" in text


def test_the_cookie_settings_control_is_mounted_in_the_footer():
    """Privacy section 6 promises withdrawal "at any time from the cookie
    settings link in the footer". A banner shown once cannot deliver that.
    """
    text = FOOTER_JSX.read_text(encoding="utf-8", errors="replace")
    assert "CookieSettingsLink" in text, (
        "the Privacy Policy promises a cookie settings link in the footer; it "
        "must actually be there."
    )


def test_the_reacceptance_gate_is_mounted():
    """Terms section 14: "we will ask you to accept the new version before you
    continue using the Platform, and we will record that acceptance".

    Signup covers new accounts only. All 182 existing accounts have no record,
    so without this the table is accurate about new users and silent about every
    existing one.
    """
    text = APP_JSX.read_text(encoding="utf-8", errors="replace")
    assert "ReacceptanceGate" in text
    assert "<ReacceptanceGate />" in text


def test_signup_actually_posts_the_acceptance():
    """The original defect: the checkbox gated the form and was discarded."""
    text = AUTH_PAGE.read_text(encoding="utf-8", errors="replace")
    assert "api.legal" in text, (
        "the signup flow must record the acceptance, not just gate on a checkbox."
    )
    assert "LEGAL_VERSION" in text, (
        "the recorded acceptance must name the version that was displayed."
    )
    # It has to happen where a session exists. The password step signs out, so
    # the only valid point is after OTP verification.
    accept_at = text.index("api.legal")
    token_at = text.index('sessionStorage.setItem("token"')
    assert token_at < accept_at, (
        "the acceptance must be recorded AFTER a session exists; the endpoint "
        "requires authentication and the password step deliberately signs out."
    )


def test_the_signup_checkbox_is_still_required():
    """The client-side guard was never the problem; discarding it was. It should
    still refuse to submit without agreement, as well as now recording it.
    """
    text = AUTH_PAGE.read_text(encoding="utf-8", errors="replace")
    assert "agreedToPolicies" in text
    assert "You must agree to the Terms" in text


def test_the_api_module_is_exported():
    index = (FRONTEND_SRC / "api" / "index.js").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "legalApi" in index
    assert "legal: legalApi" in index


def test_the_footer_disclaimer_is_single_sourced_and_corrected():
    """It carried the SAME false implication as the Terms, in a second place.

    "Paper trading mode is enabled by default" reads as "live trading is a
    switch you can flip", when live trading is operator-granted per account.
    And "all execution decisions are made by the user" is the opposite of the
    central risk: the user picks the strategy, the strategy places the orders.
    """
    text = FOOTER_JSX.read_text(encoding="utf-8", errors="replace")
    assert "SUMMARY_DISCLAIMER" in text, (
        "the footer risk paragraph must be single-sourced from documents.js."
    )
    stripped = _strip_js_comments(text)
    assert "Paper trading mode is enabled by" not in stripped, (
        "the footer still implies live trading is a user-selectable mode."
    )
    assert "all execution decisions are made by the user" not in stripped, (
        "the footer still claims the user makes every execution decision, which "
        "is false for an automated trading platform."
    )


def test_the_summary_disclaimer_names_the_automation_risk():
    text = _documents_text()
    assert "without" in text and "asking you again" in text, (
        "the summary must convey that a started strategy places orders without "
        "asking again -- the single most important risk on this platform."
    )


# ══════════════════════════════════════════════════════════════════════════
#  8. ENTITY IDENTIFIERS ARE REAL, NOT PLACEHOLDERS
#
#  A placeholder is obviously unfinished. A WRONG statutory identifier looks
#  finished, which is worse -- so these assert the values are present AND
#  structurally valid, including the GSTIN's check digit.
# ══════════════════════════════════════════════════════════════════════════

_CP = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _entity_field(name: str) -> str:
    text = DOCUMENTS_JS.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^\s*{name}:\s*'([^']+)'", text, re.MULTILINE)
    assert m, f"could not find ENTITY.{name} in documents.js"
    return m.group(1)


def test_no_placeholders_remain_in_the_published_documents():
    """These render verbatim to users, so a leftover is a visible defect."""
    text = _documents_text()
    leftovers = re.findall(r"\[[A-Z][A-Z ]*(?:REQUIRED|NAME)\]", text)
    assert not leftovers, f"placeholder(s) still in the documents: {set(leftovers)}"


def test_the_legal_name_is_a_real_company_name():
    name = _entity_field("legalName")
    assert "[" not in name and "]" not in name
    assert name.endswith("Private Limited"), (
        f"ENTITY.legalName must match the declared company form; got {name!r}"
    )
    assert name.count("Private Limited") == 1, "the suffix is duplicated"


def test_the_cin_is_structurally_valid_and_internally_consistent():
    cin = _entity_field("cin")
    m = re.fullmatch(r"([UL])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})", cin)
    assert m, f"CIN does not match the 21-character structure: {cin!r}"
    _listing, _industry, state, year, ownership, _reg = m.groups()
    assert len(cin) == 21
    # The state in the CIN must agree with the registered address, and the
    # ownership code with the company form. Those are the two fields that can be
    # cross-checked against something else in this file.
    assert state == "UP", f"CIN state {state!r} but the address says Uttar Pradesh"
    assert "Uttar Pradesh" in _entity_field("address")
    assert ownership == "PTC", (
        f"CIN ownership code {ownership!r} but ENTITY.form declares a Private "
        f"Limited Company, which is PTC"
    )
    assert 1900 <= int(year) <= 2100


def test_the_gstin_check_digit_verifies():
    """The one identifier here that can be proven wrong by arithmetic."""
    gstin = _entity_field("gstin")
    assert len(gstin) == 15, f"a GSTIN is 15 characters; got {len(gstin)}"
    m = re.fullmatch(r"(\d{2})([A-Z]{5}\d{4}[A-Z])(\d|[A-Z])(Z)([0-9A-Z])", gstin)
    assert m, f"GSTIN does not match the expected structure: {gstin!r}"
    state_code, pan, _entity, _z, given = m.groups()

    assert state_code == "09", (
        f"GSTIN state code {state_code!r} is not 09 (Uttar Pradesh), which the "
        f"registered address declares"
    )
    # PAN's 4th character encodes holder type; 'C' is Company.
    assert pan[3] == "C", (
        f"PAN holder type {pan[3]!r} is not 'C' (Company), which a Private "
        f"Limited must be"
    )

    factor, total, mod = 2, 0, 36
    for ch in reversed(gstin[:14]):
        addend = factor * _CP.index(ch)
        factor = 1 if factor == 2 else 2
        total += (addend // mod) + (addend % mod)
    expected = _CP[(mod - (total % mod)) % mod]
    assert given == expected, (
        f"GSTIN check digit is wrong: computed {expected!r}, document says {given!r}"
    )


def test_the_address_carries_a_pin_code():
    """An address without one is not deliverable, and the E-Commerce Rules expect
    a reachable address."""
    address = _entity_field("address")
    assert re.search(r"\b\d{6}\b", address), (
        f"the registered address has no six-digit PIN code: {address!r}"
    )
    assert _entity_field("pinCode") == re.search(r"\b(\d{6})\b", address).group(1), (
        "ENTITY.pinCode disagrees with the PIN embedded in ENTITY.address"
    )


def test_the_cin_and_gstin_are_actually_published():
    """Defined-but-never-rendered is how they sat before. The Consumer Protection
    (E-Commerce) Rules expect a GST-registered seller to publish its GSTIN."""
    text = _documents_text()
    assert _entity_field("cin") in text, "the CIN is defined but never rendered"
    assert _entity_field("gstin") in text, "the GSTIN is defined but never rendered"


# ══════════════════════════════════════════════════════════════════════════
#  9. THE TWO REPRESENTATIONS ARE ENFORCED, NOT JUST WORDED
# ══════════════════════════════════════════════════════════════════════════

def test_a_restricted_jurisdiction_is_refused():
    from backend_app.core.restricted_jurisdictions import (JurisdictionRefused,
                                                           assert_permitted)

    for code in ("IR", "KP", "CU", "SY"):
        with pytest.raises(JurisdictionRefused) as exc:
            assert_permitted(code)
        assert exc.value.code == "JURISDICTION_RESTRICTED"
        assert exc.value.country_code == code


def test_a_permitted_jurisdiction_is_normalised_and_allowed():
    from backend_app.core.restricted_jurisdictions import assert_permitted

    assert assert_permitted("in") == "IN"
    assert assert_permitted(" gb ") == "GB"
    assert assert_permitted("US") == "US"


@pytest.mark.parametrize("bad", ["", "   ", "IND", "i", "12", "I1", None])
def test_a_malformed_country_is_refused_as_malformed_not_as_restricted(bad):
    """Conflating the two would report a typo as a sanctions hit."""
    from backend_app.core.restricted_jurisdictions import (JurisdictionMalformed,
                                                           assert_permitted,
                                                           is_restricted)

    with pytest.raises(JurisdictionMalformed):
        assert_permitted(bad)
    assert is_restricted(bad) is False


def test_the_restricted_list_is_documented_as_a_declaration_not_screening():
    """The honesty requirement. Describing a declared-country gate as sanctions
    screening would be the same class of false statement this finding is about."""
    source = (
        REPO / "backend_app" / "core" / "restricted_jurisdictions.py"
    ).read_text(encoding="utf-8")
    assert "NOT sanctions screening" in source or "IS NOT SANCTIONS SCREENING" in source
    assert "DELIBERATELY_NOT_BLOCKED" in source, (
        "jurisdictions deliberately left unblocked should be recorded so the "
        "omission reads as a decision rather than an oversight"
    )


def test_signup_requires_both_representations_server_side():
    router = (REPO / "backend_app" / "routers" / "legal.py").read_text(
        encoding="utf-8"
    )
    assert "AGE_NOT_CONFIRMED" in router, (
        "a signup must be refused without the age attestation"
    )
    assert "assert_permitted" in router, (
        "a signup must be refused for a restricted declared country"
    )
    # Required at signup, optional on re-acceptance -- re-demanding a country
    # declaration when prompting about new TEXT would lock a user out.
    assert 'context == "signup"' in router


def test_the_signup_form_collects_both():
    text = AUTH_PAGE.read_text(encoding="utf-8", errors="replace")
    assert "confirmedAge" in text, "the signup form must collect the age attestation"
    assert "declaredCountry" in text, "the signup form must collect the country"
    # Neither may be pre-filled: a defaulted country declares something on the
    # user's behalf, which is not a representation.
    assert "useState(false)" in text
    assert 'useState("")' in text


def test_the_migration_keeps_the_new_columns_nullable():
    """A NOT NULL with a backfill would manufacture a legal representation the
    user never made, which is worse than an honest NULL."""
    migration = (
        REPO / "backend_app" / "migrations"
        / "053_legal_acceptance_age_and_jurisdiction.sql"
    )
    assert migration.is_file()
    sql = migration.read_text(encoding="utf-8", errors="replace")
    assert "ADD COLUMN IF NOT EXISTS age_confirmed BOOLEAN" in sql
    assert "ADD COLUMN IF NOT EXISTS declared_country TEXT" in sql
    assert "NOT NULL" not in sql.split("SECTION 1")[1].split("SECTION 2")[0], (
        "the new columns must be nullable; see the migration header"
    )
    # Shape only in the database; the restricted LIST lives in Python so counsel
    # can change it without a migration invalidating historical rows.
    assert "chk_legal_acceptances_declared_country_shape" in sql
    for code in ("'IR'", "'KP'", "'CU'", "'SY'"):
        assert code not in sql, (
            "the restricted list must NOT be encoded in a CHECK constraint -- it "
            "is a legal input that changes, and baking it in would make every "
            "revision a schema change"
        )
