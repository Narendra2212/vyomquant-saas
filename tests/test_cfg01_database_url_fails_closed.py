"""tests/test_cfg01_database_url_fails_closed.py

CFG-01 (P3) -- ``backend_app/core/database_pool.py`` used to silently substitute a
different database for the configured one, in two separate places:

1. ``DATABASE_URL`` unset, trading mode not paper/live (e.g. the default "safe" mode,
   or no trading mode declared at all) -> fell back to a SQLite file
   (``sqlite:///./algo22.db``) unconditionally, including in production. Every read and
   write would land in an ephemeral, per-container file no other process or replica
   shares, with no error anywhere.
2. ``DATABASE_URL`` set to a Supabase project URL (``https://...``, not a Postgres DSN
   -- a misconfiguration) and ``SUPABASE_DB_URL`` unset -> fell back to
   ``postgresql://postgres:postgres@localhost:5432/postgres``, a well-known local
   superuser DSN, silently connecting under a completely different identity to
   whatever (if anything) is listening there.

THE FIX
-------
``resolve_database_url`` is the pure decision function both substitutions now go
through (mirroring ``core/rate_limit.py::resolve_rate_limit_config``'s own
"frozen, testable decision" pattern). Both substitutions are now gated on
``local_state_is_acceptable()`` (``core/runtime_env.py`` -- the same shared judgement
``redis_manager`` and ``rate_limit`` already use: "is there a second process to
coordinate with?"), raising instead outside a declared local/test environment. The
pre-existing paper/live refusal (keyed on the TRADING mode, not the deployment
environment) is preserved unchanged and checked first.

``local_allowed``/``env_name`` are injectable so every case below is asserted without
reloading the module or touching the real process environment -- importing
``database_pool`` itself eagerly creates a live engine at the bottom of the module
(pre-existing, unrelated to this fix), which this file deliberately does not disturb by
re-importing it under different conditions.
"""
from __future__ import annotations

import pytest

from backend_app.core.database_pool import (
    LOCAL_POSTGRES_FALLBACK_URL,
    SQLITE_FALLBACK_URL,
    resolve_database_url,
)


class TestTheSqliteFallback:
    def test_refuses_outside_a_local_environment_with_no_database_url(self):
        with pytest.raises(RuntimeError, match="DATABASE_URL must be set"):
            resolve_database_url(
                env_mode="safe",
                database_url=None,
                supabase_db_url=None,
                local_allowed=False,
                env_name="production",
            )

    def test_still_refuses_in_paper_mode_even_inside_a_local_environment(self):
        """NEGATIVE CONTROL: the pre-existing trading-mode refusal must not be
        weakened by the new environment-based one -- paper/live always refuses a
        missing DATABASE_URL, local environment or not.
        """
        with pytest.raises(RuntimeError, match="paper/live mode"):
            resolve_database_url(
                env_mode="paper",
                database_url=None,
                supabase_db_url=None,
                local_allowed=True,
                env_name="development",
            )

    def test_still_refuses_in_live_mode(self):
        with pytest.raises(RuntimeError, match="paper/live mode"):
            resolve_database_url(
                env_mode="live",
                database_url=None,
                supabase_db_url=None,
                local_allowed=True,
                env_name="development",
            )

    def test_falls_back_to_sqlite_only_inside_a_declared_local_environment(self):
        result = resolve_database_url(
            env_mode="safe",
            database_url=None,
            supabase_db_url=None,
            local_allowed=True,
            env_name="development",
        )
        assert result == SQLITE_FALLBACK_URL


class TestTheLocalPostgresFallback:
    def test_refuses_outside_a_local_environment_when_supabase_url_is_misconfigured(self):
        with pytest.raises(RuntimeError, match="postgres:postgres@localhost"):
            resolve_database_url(
                env_mode="safe",
                database_url="https://myproject.supabase.co",
                supabase_db_url=None,
                local_allowed=False,
                env_name="production",
            )

    def test_falls_back_to_local_postgres_only_inside_a_declared_local_environment(self):
        result = resolve_database_url(
            env_mode="safe",
            database_url="https://myproject.supabase.co",
            supabase_db_url=None,
            local_allowed=True,
            env_name="development",
        )
        assert result == LOCAL_POSTGRES_FALLBACK_URL

    def test_a_configured_supabase_db_url_is_used_instead_of_any_fallback(self):
        """NEGATIVE CONTROL: when the real DSN IS configured, it must win regardless of
        environment -- this fix narrows the SILENT SUBSTITUTION, not legitimate use.
        """
        result = resolve_database_url(
            env_mode="safe",
            database_url="https://myproject.supabase.co",
            supabase_db_url="postgresql://real:creds@real-host:5432/real_db",
            local_allowed=False,
            env_name="production",
        )
        assert result == "postgresql://real:creds@real-host:5432/real_db"


def test_a_properly_configured_database_url_passes_through_unchanged():
    """NEGATIVE CONTROL: the common, correct case -- nothing to substitute at all."""
    result = resolve_database_url(
        env_mode="live",
        database_url="postgresql://real:creds@prod-host:5432/prod_db",
        supabase_db_url=None,
        local_allowed=False,
        env_name="production",
    )
    assert result == "postgresql://real:creds@prod-host:5432/prod_db"


def test_the_default_arguments_read_the_real_process_state():
    """``local_allowed``/``env_name`` default to the real
    ``local_state_is_acceptable``/``deployment_environment`` when not injected -- this
    is what makes the module-level call at the bottom of database_pool.py behave
    identically to before this refactor for a real process.
    """
    import inspect

    sig = inspect.signature(resolve_database_url)
    assert sig.parameters["local_allowed"].default is None
    assert sig.parameters["env_name"].default is None
