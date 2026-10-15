"""tests/test_sec11_jwt_secret_never_logged.py

SEC-11 (P3) -- the weak-JWT-secret warning in ``backend_app/core/config.py::
validate_production_config`` used to log ``jwt_secret[:10]`` -- the first ten real
characters of whatever ``JWT_SECRET`` is set to in the environment that triggered the
warning, straight into any log sink this process ships to (which may be far less
trusted than the process environment itself: a log aggregator, a ticket someone pastes
a stack trace into, a support bundle). Naming the matched weak-secret pattern identifies
the problem exactly as precisely as the first ten characters did, without emitting any
character that was ever part of the actual secret.

``validate_production_config`` is itself dead code -- nothing calls it
(tests/test_env03_placeholder_secrets_are_invalid.py pins that as its own invariant,
with the real production check documented as ``Settings.assert_credentials_are_
production_grade``). That does not make this fix pointless: a function that logs secret
material is a landmine for whoever eventually wires a call to it believing it is a real
check, and this test exists so that if it ever is called, no secret character reaches a
log line.
"""
from __future__ import annotations

import logging

import pytest

from backend_app.core import config as config_module


@pytest.fixture
def production_env(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-value")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key-value")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "supabase-jwt-secret-value")


def test_a_weak_secret_is_never_present_in_the_warning(production_env, monkeypatch, caplog):
    # Deliberately NOT starting with the matched pattern itself, so this proves the log
    # carries no character sliced from the actual secret value -- rather than the
    # pattern name and the first ten characters coincidentally being the same string.
    distinctive_prefix = "XYZ9981726"
    weak_secret = f"{distinctive_prefix}-dev-secret-change-in-production"
    monkeypatch.setenv("JWT_SECRET", weak_secret)

    with caplog.at_level(logging.WARNING):
        config_module.validate_production_config()

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert weak_secret not in logged_text
    assert weak_secret[:10] not in logged_text
    assert distinctive_prefix not in logged_text
    # The warning must still fire and still be diagnosable -- removing the secret
    # characters must not have silently removed the warning itself.
    assert "weak development secret" in logged_text
    assert "dev-secret-change-in-production" in logged_text  # the pattern name, not a slice of the secret


@pytest.mark.parametrize("weak_secret", ["dummysecretvaluepaddedtoreachlength32chars", "testvaluepaddedtoreachlength32characters", "secretvaluepaddedtoreachlength32characters"])
def test_no_variant_of_the_matched_secret_leaks_for_other_weak_patterns(
    production_env, monkeypatch, caplog, weak_secret
):
    monkeypatch.setenv("JWT_SECRET", weak_secret)

    with caplog.at_level(logging.WARNING):
        config_module.validate_production_config()

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert weak_secret not in logged_text
    assert weak_secret[:10] not in logged_text


def test_a_strong_secret_triggers_no_weak_secret_warning_at_all(production_env, monkeypatch, caplog):
    strong_secret = "Xk7#mP9$vL2@qR5!wT8&nY3^zB6*cF4%hJ1-sD0_aG"
    monkeypatch.setenv("JWT_SECRET", strong_secret)

    with caplog.at_level(logging.WARNING):
        config_module.validate_production_config()

    logged_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "weak development secret" not in logged_text
    assert strong_secret not in logged_text
