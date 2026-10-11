"""Show what `_app_cache_url()` (F2) resolves to, and that the old choice disagreed.

No server, no network: `get_redis_url` is a pure function of its module's constants and
redis-py's `parse_url` is pure parsing. Run one case per subprocess so the module-level
`REDIS_URL` capture happens for real rather than being patched after the fact.

Run:  python staging/p11b12_url_derivation.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

CHILD = r"""
import json, os, sys
sys.path.insert(0, %(root)r)
from urllib.parse import urlsplit
from redis.connection import parse_url
from backend_app.backend.redis_manager import REDIS_DB_CACHE, get_redis_url

derived = get_redis_url(REDIS_DB_CACHE)
opt_in = os.environ.get("AERORA_TEST_REDIS_URL", "")
out = {
    "REDIS_URL_env": os.environ.get("REDIS_URL", ""),
    "REDIS_DB_CACHE": REDIS_DB_CACHE,
    "derived_app_cache_url": derived,
    "derived_resolves_to": parse_url(derived),
    "derived_host": (urlsplit(derived).hostname or "").lower(),
    "opt_in_url": opt_in,
    "opt_in_resolves_to": parse_url(opt_in) if opt_in else None,
}
print(json.dumps(out, default=str))
"""


def case(name, env):
    child_env = dict(os.environ)
    child_env.pop("AERORA_TEST_REDIS_URL", None)
    child_env.update(env)
    r = subprocess.run(
        [sys.executable, "-c", CHILD % {"root": ROOT}],
        capture_output=True, env=child_env, cwd=ROOT,
    )
    print("=" * 78)
    print(name)
    print("=" * 78)
    if r.returncode != 0:
        print("FAILED rc=%s" % r.returncode)
        print(r.stderr.decode(errors="replace")[-1500:])
        return
    d = json.loads(r.stdout.decode())
    app_db = d["derived_resolves_to"].get("db")
    opt_db = (d["opt_in_resolves_to"] or {}).get("db")
    print(f"  REDIS_URL (env)            : {d['REDIS_URL_env']!r}")
    print(f"  REDIS_DB_CACHE             : {d['REDIS_DB_CACHE']}")
    print(f"  derived app cache URL      : {d['derived_app_cache_url']!r}")
    print(f"  -> redis-py resolves db    : {app_db}   host={d['derived_host']}")
    print(f"  opt-in AERORA_TEST_REDIS_URL: {d['opt_in_url']!r}")
    print(f"  -> redis-py resolves db    : {opt_db}")
    if opt_db is not None:
        verdict = "AGREE" if app_db == opt_db else "DISAGREE"
        print(f"  KEYSPACE: {verdict}  (app db={app_db}, opt-in db={opt_db})")
    print()


if __name__ == "__main__":
    # The shape the CI job actually presents (clean-log lines 957-958), with conftest's
    # unconditional blanking of REDIS_URL applied -- i.e. what the test process sees.
    case(
        "A. CI shape, REDIS_URL blanked by tests/conftest.py (the real test process)",
        {"REDIS_URL": "", "AERORA_TEST_REDIS_URL": "redis://localhost:6379/15"},
    )
    # The same job, if the backend module were imported before conftest blanked it.
    case(
        "B. CI shape, REDIS_URL still visible at import",
        {"REDIS_URL": "redis://localhost:6379",
         "AERORA_TEST_REDIS_URL": "redis://localhost:6379/15"},
    )
    # A URL that explicitly names a database: the override discards it anyway.
    case(
        "C. REDIS_URL explicitly names /15 -- the override still wins",
        {"REDIS_URL": "redis://localhost:6379/15",
         "AERORA_TEST_REDIS_URL": "redis://localhost:6379/15"},
    )
    # A deployment-shaped host, to show what the loopback guard in `_app_cache_url` is for.
    case(
        "D. non-loopback REDIS_HOST (what the loopback guard refuses)",
        {"REDIS_URL": "", "REDIS_HOST": "redis.internal.example",
         "AERORA_TEST_REDIS_URL": "redis://localhost:6379/15"},
    )
