"""tests/test_sec06_no_hardcoded_supabase_jwt_in_scripts.py

SEC-06 (P3) -- six scripts under ``scripts/`` hardcoded the same real Supabase anon JWT
for this project (and, in one of them, the project URL via an unconditional
``os.environ[...] = ...``), committed to git:
``verify_authenticated_production.py``, ``battery_20_samples.py``,
``run_production_browser_forensic_audit.py``, ``verify_production_browser_final.py``,
``generate_browser_console_baseline.py``, ``test_currency_repro.py``. All six now read
``SUPABASE_ANON_KEY`` from the environment and exit with a clear message if it is not
set, rather than carrying the credential in the file.

This file proves two things, generically rather than one script at a time so a seventh
script that reintroduces the pattern is caught the same way:

  1. no file under ``scripts/`` contains a Supabase-shaped JWT literal (``eyJ...`` --
     base64url for ``{"...``, which is what every JWT's header starts with) anywhere in
     its source;
  2. each of the six named scripts actually reads ``SUPABASE_ANON_KEY`` from the
     environment and refuses to run without it, exercised end to end by executing each
     script as a subprocess with the variable unset.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO / "scripts"

# A JWT's header is always `{"alg":...` or `{"typ":...` base64url-encoded, which always
# starts `eyJ`. Matching that prefix (not the specific leaked key) is what makes this
# check catch ANY hardcoded Supabase/JWT-shaped token, not just the one this finding
# named.
_JWT_LITERAL = re.compile(r"""['"]eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+['"]""")

_FIXED_SCRIPTS = [
    "verify_authenticated_production.py",
    "battery_20_samples.py",
    "run_production_browser_forensic_audit.py",
    "verify_production_browser_final.py",
    "generate_browser_console_baseline.py",
    "test_currency_repro.py",
]


def test_the_six_named_scripts_exist_and_were_the_ones_fixed():
    for name in _FIXED_SCRIPTS:
        assert (SCRIPTS_DIR / name).is_file(), f"{name} is missing -- update this test's file list"


@pytest.mark.parametrize("path", sorted(SCRIPTS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_no_script_contains_a_hardcoded_jwt_literal(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _JWT_LITERAL.search(text)
    assert match is None, f"{path.name} contains what looks like a hardcoded JWT: {match.group()[:40]}..."


@pytest.mark.parametrize("name", _FIXED_SCRIPTS)
def test_each_fixed_script_refuses_to_run_without_the_env_var(name):
    env = {k: v for k, v in os.environ.items() if k != "SUPABASE_ANON_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", f"import runpy; runpy.run_path({str(SCRIPTS_DIR / name)!r}, run_name='__main__')"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"{name} must refuse to run when SUPABASE_ANON_KEY is unset, not silently "
        f"proceed with a baked-in credential"
    )
    combined = result.stdout + result.stderr
    if "ModuleNotFoundError: No module named 'playwright'" in combined:
        # Three of these scripts import playwright at module level, ahead of anything
        # this fix touches. Where playwright is not installed, the process still exits
        # non-zero (asserted above) but for an unrelated, environment-specific reason --
        # this is not evidence either way about the env-var guard, so it is not treated
        # as a pass OR a fail for that guard specifically.
        pytest.skip(f"{name} requires the playwright package, not installed here")
    assert "SUPABASE_ANON_KEY" in combined


@pytest.mark.parametrize("name", _FIXED_SCRIPTS)
def test_the_fix_reads_os_environ_for_the_key_rather_than_hardcoding_it(name):
    """Static, not executed: every one of these scripts makes a real network call to a
    live production endpoint shortly after the env-var check, so proving "the env var is
    actually used" by running them with a fake key set would mean this test suite
    issuing real requests against production infrastructure on every run. The refusal
    test above already proves the hardcoded fallback is gone end-to-end (no key in the
    environment -> no way to proceed at all); this proves, from source, that the
    replacement reads ``SUPABASE_ANON_KEY`` from ``os.environ`` rather than any other
    hardcoded source.
    """
    text = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    assert "os.environ.get('SUPABASE_ANON_KEY')" in text or 'os.environ.get("SUPABASE_ANON_KEY")' in text
