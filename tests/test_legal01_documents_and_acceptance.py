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


# ══════════════════════════════════════════════════════════════════════════
#  10. GDPR OBLIGATIONS THAT FOLLOW FROM ACCEPTING EU/UK CUSTOMERS
#
#  The owner's decision is to accept customers globally INCLUDING the EU and
#  UK, which makes GDPR and UK GDPR apply in full rather than optionally.
#  These assert the obligations that are code or documents -- and, for the one
#  that is an appointment rather than code, that the gap stays VISIBLE.
# ══════════════════════════════════════════════════════════════════════════

ROPA = REPO / "docs" / "legal" / "RECORDS_OF_PROCESSING.md"


def test_the_article_27_gap_is_visible_rather_than_omitted():
    """Article 27 requires an EU representative for a non-EU controller that
    offers services to EU data subjects OR monitors their behaviour. Both apply
    here: global signups, plus Google Analytics and Microsoft Clarity.

    It is an APPOINTMENT, not code, so it cannot be satisfied in this repository.
    What this test protects is that the gap is stated in the document a user
    actually reads, instead of the section being quietly left out -- an omitted
    representative reads as "not required", which is the opposite of true.

    WHEN THE REPRESENTATIVES ARE APPOINTED, this test should be inverted to
    assert the real contact details are present and the placeholder is gone.
    """
    text = _documents_text()
    assert "Article 27" in text, (
        "the Privacy Policy must address Article 27 now that EU/UK users are in "
        "scope"
    )
    assert "NOT YET APPOINTED" in text, (
        "the Article 27 representatives are not appointed. While that is true, "
        "the documents must SAY so rather than omitting the section."
    )
    # Both jurisdictions, because the UK requires its own.
    assert "EU representative" in text
    assert "UK representative" in text


def test_the_privacy_policy_commits_to_the_72_hour_breach_notification():
    """Article 33: notify the supervisory authority within 72 hours where the
    breach is likely to risk rights and freedoms."""
    text = _documents_text()
    assert "72 HOURS" in text or "72 hours" in text
    assert "supervisory authority" in text.lower()


def test_the_privacy_policy_states_the_one_month_response_period():
    """Article 12(3): respond without undue delay and within one month,
    extendable by two further months for complex requests. The earlier wording
    said '30 days', which is close but is not the regulation's period."""
    text = _documents_text()
    assert "WITHIN ONE MONTH" in text, (
        "Article 12(3)'s period is one month, not 30 days"
    )
    assert "two further months" in text, (
        "the extension right should be stated, since relying on it without "
        "having disclosed it is worse than not having it"
    )


def test_the_privacy_policy_addresses_automated_decision_making():
    """Article 22. The answer here is 'we do not', which is worth saying on a
    platform where software places orders -- a reader could reasonably assume
    otherwise."""
    text = _documents_text()
    assert "automated means" in text.lower()


def test_records_of_processing_activities_exist():
    """Article 30 requires a controller to maintain these and produce them on
    request. This is a hard obligation the moment EU/UK users are accepted."""
    assert ROPA.is_file(), (
        "Article 30 requires Records of Processing Activities; "
        f"{ROPA.relative_to(REPO)} is missing"
    )
    text = ROPA.read_text(encoding="utf-8", errors="replace")

    # The identifiers must agree with the published documents, or the ROPA
    # describes a different company than the Terms do.
    assert _entity_field("legalName") in text
    assert _entity_field("cin") in text
    assert _entity_field("gstin") in text

    # Every processor named in the Privacy Policy must appear in the ROPA.
    for processor in (
        "Supabase", "Amazon Web Services", "Razorpay", "Stripe",
        "Sentry", "Google Analytics", "Microsoft Clarity",
    ):
        assert processor in text, f"{processor} is in the Policy but not the ROPA"

    # Article 30(1) content requirements.
    for required in ("Purpose", "Lawful basis", "Retention", "Recipients"):
        assert required in text, f"the ROPA does not record {required.lower()}"


def _squash(text: str) -> str:
    """Collapse whitespace and markdown emphasis.

    These documents are prose with line wraps, blockquote prefixes and bold
    markers, so a phrase that reads as one sentence can contain '\\n> ' or '**'
    in the middle. Asserting on the raw text makes a test that fails when a
    paragraph is rewrapped, which is a test about formatting pretending to be a
    test about content.
    """
    return re.sub(r"[\s>*_`]+", " ", text).strip()


def test_the_ropa_records_its_own_gaps():
    """A ROPA that lists only what is in order is a sales document.

    A supervisory authority reading it will find the gaps anyway, and the
    difference between disclosed and discovered is the whole of good faith here.

    Asserts the OPEN gaps specifically rather than any particular gap, because
    gaps get closed -- an earlier version of this test asserted the Supabase
    region was unknown and then failed the moment that was established, which
    made a closed gap look like a regression.
    """
    text = ROPA.read_text(encoding="utf-8", errors="replace")
    squashed = _squash(text)

    assert "Known gaps" in text or "KNOWN GAPS" in text, (
        "the ROPA must have a gaps section"
    )
    # The two that are genuinely still open, and are appointments rather than code.
    assert "not appointed" in squashed.lower(), (
        "the unappointed Article 27 representatives must be recorded"
    )
    assert "DPAs" in text and "SCCs" in text, (
        "the unconfirmed processor DPAs/SCCs must be recorded"
    )
    # Closed gaps should be marked closed rather than deleted, so the record
    # shows what was done rather than only what remains.
    assert "CLOSED" in text, (
        "closed gaps should be marked CLOSED rather than removed, so the ROPA "
        "reads as a history rather than only a snapshot"
    )


def test_the_ropa_records_what_is_not_processed():
    """The absences are load-bearing: no custody underpins the PMLA position, and
    no Article 9 data underpins the DPIA screening conclusion."""
    text = ROPA.read_text(encoding="utf-8", errors="replace")
    assert "No custody" in text
    assert "No special-category data" in text or "no special-category" in text.lower()
    assert "No automated decision-making" in text


def test_the_ropa_does_not_overstate_the_copilot():
    """Migration 018 records that every copilot write is swallowed by a
    try/except, so assistant history is never persisted. A ROPA claiming to hold
    conversation data that does not exist would be inaccurate in the direction
    that looks worse for us, which is still inaccurate.
    """
    text = ROPA.read_text(encoding="utf-8", errors="replace")
    assert "never persisted" in text, (
        "the ROPA should record that copilot history is not actually stored"
    )


# ══════════════════════════════════════════════════════════════════════════
#  11. THE REMAINING GDPR ARTEFACTS, AND THE MASKING THEY SURFACED
# ══════════════════════════════════════════════════════════════════════════

RUNBOOK = REPO / "docs" / "legal" / "BREACH_RESPONSE_RUNBOOK.md"
DPIA = REPO / "docs" / "legal" / "DPIA_SCREENING.md"


def test_the_breach_runbook_exists_and_carries_the_72_hour_clock():
    """Privacy section 10 commits to 72-hour notification. A commitment with no
    procedure behind it is the exact pattern this finding was opened about -- and
    72 hours is not long enough to invent a process in.
    """
    assert RUNBOOK.is_file(), f"{RUNBOOK.relative_to(REPO)} is missing"
    text = RUNBOOK.read_text(encoding="utf-8", errors="replace")

    assert "72" in text
    # The clock starts on AWARENESS, not on completing the investigation. Getting
    # that wrong is how an organisation misses the deadline while believing it has
    # time left.
    assert "aware" in text.lower()
    assert "not when we finish investigating" in text or "AWARE, not when" in text
    # Article 33(4) phased notification: a partial notice inside 72h beats a
    # complete one after.
    assert "phase" in text.lower()
    # Must name the actual containment tooling, or it is a template rather than a
    # runbook for THIS platform.
    for tool in ("rotate_database_password.py", "rotate_jwt_secret.py",
                 "rotate_master_encryption_keys.py",
                 "audit_secrets_in_git_history.py"):
        assert tool in text, f"the runbook does not reference {tool}"


def test_the_runbook_states_that_losing_data_is_also_a_breach():
    """VAULT-01 is a live example: two customers' credentials are unreadable
    because a key was lost. Irrecoverable loss is a breach even though nobody
    gained access, and a runbook that only imagines intrusions would miss it.
    """
    text = RUNBOOK.read_text(encoding="utf-8", errors="replace")
    assert "VAULT-01" in text
    assert "SEC-01" in text, (
        "the runbook should cite the platform's own incidents rather than "
        "hypotheticals"
    )
    assert "irrecoverab" in text.lower() or "Losing data" in text


def test_the_runbook_records_that_article_27_blocks_its_own_notification_step():
    """Without an appointed representative there is no designated route to an
    EEA/UK supervisory authority. A runbook that did not say so would read as
    complete while containing a hole at the one step with a legal deadline.
    """
    # Squashed: the phrase sits inside a markdown blockquote and wraps across
    # lines, so the raw text contains '\n> ' and '**' in the middle of it.
    squashed = _squash(RUNBOOK.read_text(encoding="utf-8", errors="replace"))
    assert "Article 27" in squashed
    assert "not yet appointed" in squashed.lower()
    assert "blocking" in squashed.lower()


def test_the_dpia_screening_exists_and_reaches_a_conclusion():
    """A screening that hedges is not a screening. It must answer the one
    question -- is a DPIA required -- and say why."""
    assert DPIA.is_file(), f"{DPIA.relative_to(REPO)} is missing"
    text = DPIA.read_text(encoding="utf-8", errors="replace")

    # All three Article 35(3) limbs must be addressed individually, not waved at.
    assert text.count("Not met.") >= 3, (
        "each of the three Article 35(3) triggers must be assessed separately"
    )
    assert "NOT MANDATORY" in text, "the screening must reach a conclusion"
    # And it must not oversell that conclusion.
    assert "borderline" in text.lower(), (
        "the screening should say the result is borderline rather than presenting "
        "a one-and-a-half-out-of-nine score as comfortable"
    )
    assert "not legal advice" in text.lower()


def test_the_dpia_screening_does_not_claim_financial_data_is_special_category():
    """A real trap. Financial data is sensitive in the ordinary sense but is NOT
    an Article 9 special category, and conflating the two would produce a wrong
    mandatory-DPIA conclusion in the cautious-looking direction.
    """
    text = DPIA.read_text(encoding="utf-8", errors="replace")
    assert "not a special category under Article 9" in text


def test_clarity_masks_the_authenticated_area():
    """Found by writing the DPIA screening, and fixed in the same change.

    Clarity's default masking covers form inputs but not page text. On a trading
    platform the text is the sensitive part: balances, position sizes, P&L,
    strategy logic. Applied at the single <main> landmark so every authenticated
    route is masked by default rather than per page.
    """
    app = APP_JSX.read_text(encoding="utf-8", errors="replace")
    assert 'data-clarity-mask="true"' in app, (
        "authenticated pages must be masked from Clarity session replay"
    )
    # It has to be on the shell's <main>, not on some leaf component, or a new
    # page added later is unmasked by default.
    main_at = app.index("id=\"main-content\"")
    mask_at = app.index('data-clarity-mask="true"')
    assert abs(mask_at - main_at) < 2000, (
        "the mask attribute should sit on the authenticated <main> landmark so it "
        "covers every route inside the shell"
    )


def test_the_privacy_policy_discloses_the_masking():
    """A protection the user is not told about cannot reassure them, and claiming
    it without doing it would be the false-statement problem again. Both halves
    have to hold.
    """
    text = _documents_text()
    assert "masked" in text.lower()
    assert "balances" in text.lower(), (
        "the Policy should say specifically what is not captured, since 'we mask "
        "sensitive data' is the kind of claim a reader cannot check"
    )


def test_the_ropa_records_the_supabase_region():
    """A ROPA that cannot say where the primary database sits is missing one of
    the things a ROPA is for. The repository only ever held a placeholder, but
    the running system's own pooler hostname carried the answer.
    """
    text = ROPA.read_text(encoding="utf-8", errors="replace")
    assert "ap-southeast-1" in text
    assert "Singapore" in text
    assert "region not yet confirmed" not in text, (
        "the region gap is closed and the ROPA should no longer claim otherwise"
    )
