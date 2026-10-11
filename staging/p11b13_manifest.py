"""Phase 0: which version of each D5' file is in each candidate commit, and in the worktree."""
import subprocess

REFS = [
    ("main (HEAD)", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR6 base b43e990d", "b43e990dc31766b7456dad8abd8dc5705c143814"),
    ("PR6 head d07014d3", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
    ("PR7 head 49e70da2", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR8 head a624ce89", "a624ce89b16005b0c73197005d6d0672737dad65"),
]

FILES = [
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/real_redis_gate.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "backend_app/core/subscription_dependencies.py",
    "backend_app/routers/exchange.py",
    ".github/workflows/01-pr-check.yml",
]

MARKERS = {
    "tests/test_webhook_duplicate_delivery_real_redis.py": [
        ("A5 fail-not-skip", "FAIL, NOT SKIP"),
        ("A12 keyspace fix", "_prove_one_keyspace"),
    ],
    "backend_app/core/subscription_dependencies.py": [
        ("slot guard", "exchange_connection_slot_guard"),
        ("lock key", "_slot_lock_key"),
    ],
    "backend_app/routers/exchange.py": [
        ("store_keys guard", "exchange_connection_slot_guard"),
    ],
    ".github/workflows/01-pr-check.yml": [
        ("hunk1 opt-in", "AERORA_TEST_REDIS_URL"),
        ("A11 diagnostic job", "d5-redis-diagnostics"),
        ("provisioning job", "provisioning-and-isolation"),
    ],
}


def blob(ref, path):
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


def worktree(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


out = []
for path in FILES:
    out.append("=" * 90)
    out.append(path)
    out.append("=" * 90)
    rows = [(label, blob(ref, path)) for label, ref in REFS]
    rows.append(("WORKTREE", worktree(path)))
    for label, text in rows:
        if text is None:
            out.append("  %-22s ABSENT" % label)
            continue
        flags = []
        for name, needle in MARKERS.get(path, []):
            flags.append("%s=%s" % (name, "yes" if needle in text else "NO"))
        out.append("  %-22s %5d lines   %s"
                   % (label, len(text.splitlines()), "  ".join(flags)))
    out.append("")

open("staging/p11b13_manifest.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
