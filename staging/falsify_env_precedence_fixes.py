"""Falsification: do the new precedence tests actually DETECT the defect they name?

A passing test proves nothing about a fix unless the test fails when the fix is absent.
Each case below reinstates the PRE-FIX predicate at the real call site -- the bare
``DEV_MODE`` read, or the trading-mode default -- and asserts that the old behaviour comes
back. If any case reports NOT_DETECTED, the corresponding test in
``tests/test_environment_precedence_is_centralised.py`` would pass with the defect present
and is worthless.

Patched at the CALL SITE's own reference, not inside ``runtime_env``, so the thing under
test is "does this site ask the right question" rather than "does the rule work".

Run:  .venv\\Scripts\\python.exe staging\\falsify_env_precedence_fixes.py
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["REDIS_URL"] = ""
os.environ["DATABASE_URL"] = ""

import backend_app.main as main_module  # noqa: E402
from backend_app.core.safety_config import get_vyomquant_mode  # noqa: E402
from backend_app.routers import library as library_router  # noqa: E402
from backend_app.routers import referral as referral_router  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(case: str, detected: bool, detail: str) -> None:
    RESULTS.append((case, detected, detail))
    print(f"  {'DETECTED    ' if detected else 'NOT_DETECTED'}  {case}")
    print(f"                  {detail}")


def pre_fix_dev_mode_only() -> bool:
    """Exactly what both routers used to ask: DEV_MODE, and never ENV."""
    return os.getenv("DEV_MODE", "false").lower() == "true"


def case_library_missing_key() -> None:
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    library_router._service_client = None

    original = library_router.local_fallbacks_are_acceptable
    library_router.local_fallbacks_are_acceptable = pre_fix_dev_mode_only
    try:
        value = library_router._get_service_client()
        raised = None
    except Exception as exc:  # noqa: BLE001
        value, raised = None, exc
    finally:
        library_router.local_fallbacks_are_acceptable = original
        library_router._service_client = None

    detected = raised is None and value is None
    record(
        "library._get_service_client, missing service-role key",
        detected,
        f"pre-fix predicate -> {'returned None (the defect)' if detected else f'raised {raised!r}'}",
    )


def case_referral_missing_key() -> None:
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    referral_router._service_client = None

    original = referral_router.local_fallbacks_are_acceptable
    referral_router.local_fallbacks_are_acceptable = pre_fix_dev_mode_only
    try:
        value = referral_router._get_service_client()
        raised = None
    except Exception as exc:  # noqa: BLE001
        value, raised = None, exc
    finally:
        referral_router.local_fallbacks_are_acceptable = original
        referral_router._service_client = None

    detected = raised is None and value is None
    record(
        "referral._get_service_client, missing service-role key",
        detected,
        f"pre-fix predicate -> {'returned None (the defect)' if detected else f'raised {raised!r}'}",
    )


def case_library_unconstructable() -> None:
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "not-a-real-key"
    library_router._service_client = None

    def explode(*_a, **_k):
        raise ValueError("Invalid API key")

    original_predicate = library_router.local_fallbacks_are_acceptable
    original_create = library_router.create_client
    library_router.local_fallbacks_are_acceptable = pre_fix_dev_mode_only
    library_router.create_client = explode
    try:
        value = library_router._get_service_client()
        raised = None
    except Exception as exc:  # noqa: BLE001
        value, raised = None, exc
    finally:
        library_router.local_fallbacks_are_acceptable = original_predicate
        library_router.create_client = original_create
        library_router._service_client = None
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    detected = raised is None and value is None
    record(
        "library._get_service_client, create_client raises",
        detected,
        f"pre-fix predicate -> {'returned None (the defect)' if detected else f'raised {raised!r}'}",
    )


def case_build_service_client_contract() -> None:
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    library_router._service_client = None

    original = library_router.local_fallbacks_are_acceptable
    library_router.local_fallbacks_are_acceptable = pre_fix_dev_mode_only
    try:
        value = library_router._build_service_client()
        raised = None
    except Exception as exc:  # noqa: BLE001
        value, raised = None, exc
    finally:
        library_router.local_fallbacks_are_acceptable = original
        library_router._service_client = None

    detected = raised is None and value is None
    record(
        "library._build_service_client, documented to raise",
        detected,
        f"pre-fix predicate -> {'returned None (the defect)' if detected else f'raised {raised!r}'}",
    )


def case_docs_url_from_trading_mode() -> None:
    """The pre-fix expression, restored verbatim at the call site."""
    os.environ.pop("ENV", None)
    os.environ.pop("ENVIRONMENT", None)
    os.environ.pop("ENABLE_DOCS", None)
    os.environ["VYOMQUANT_MODE"] = "paper"

    def pre_fix_docs_allowed() -> bool:
        is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in (
            "production",
            "prod",
            "live",
        )
        return not is_prod

    original = main_module._interactive_docs_allowed
    main_module._interactive_docs_allowed = pre_fix_docs_allowed
    try:
        docs = main_module._get_docs_url()
        redoc = main_module._get_redoc_url()
    finally:
        main_module._interactive_docs_allowed = original

    detected = docs == "/docs" and redoc == "/redoc"
    record(
        "main._get_docs_url, ENV unset + VYOMQUANT_MODE=paper",
        detected,
        f"pre-fix predicate -> docs={docs!r} redoc={redoc!r}",
    )


def case_docs_url_declared_production() -> None:
    """``ENV=staging`` -- served pre-fix, withheld post-fix."""
    os.environ["ENV"] = "staging"
    os.environ.pop("ENABLE_DOCS", None)

    def pre_fix_docs_allowed() -> bool:
        is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in (
            "production",
            "prod",
            "live",
        )
        return not is_prod

    original = main_module._interactive_docs_allowed
    main_module._interactive_docs_allowed = pre_fix_docs_allowed
    try:
        docs = main_module._get_docs_url()
    finally:
        main_module._interactive_docs_allowed = original

    detected = docs == "/docs"
    record(
        "main._get_docs_url, ENV=staging",
        detected,
        f"pre-fix predicate -> docs={docs!r}",
    )


def main() -> int:
    print("Reinstating each PRE-FIX predicate at its real call site:\n")
    case_library_missing_key()
    case_referral_missing_key()
    case_library_unconstructable()
    case_build_service_client_contract()
    case_docs_url_from_trading_mode()
    case_docs_url_declared_production()

    detected = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nVERDICT: {detected}/{len(RESULTS)} defects reproduced by removing the fix")
    print(
        "FALSIFICATION="
        + ("ALL_DETECTED" if detected == len(RESULTS) else "INCOMPLETE")
    )
    return 0 if detected == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
