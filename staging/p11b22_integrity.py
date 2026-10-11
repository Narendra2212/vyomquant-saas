"""A22 Stage 5: compare exit state with the entry snapshot."""
import hashlib
import subprocess
from pathlib import Path

ENTRY_STAGED = Path("staging/p11b22_entry_staged.txt").read_text(encoding="utf-8")
ENTRY_STATUS = Path("staging/p11b22_entry_status.txt").read_text(encoding="utf-8")
A20_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def digest_of(text):
    h = hashlib.sha256()
    for l in sorted(text.splitlines()):
        h.update(l.encode("utf-8"))
    return h.hexdigest()


out.append("=" * 100)
out.append("STAGE 5. ENTRY vs EXIT")
out.append("=" * 100)
now_staged = t(["git", "ls-files", "--stage"])
out.append("  index CONTENT digest   entry=%s" % digest_of(ENTRY_STAGED)[:32])
out.append("                          exit=%s  %s"
           % (digest_of(now_staged)[:32],
              "SAME" if digest_of(now_staged) == digest_of(ENTRY_STAGED) else ">> CHANGED"))
out.append("  matches A20 digest: %s" % (digest_of(now_staged) == A20_DIGEST))
out.append("  HEAD: %s" % t(["git", "rev-parse", "HEAD"]).strip())
out.append("  branch: %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())

entry = {l[3:]: l[:2] for l in ENTRY_STATUS.splitlines() if l.strip()}
now = {l[3:]: l[:2] for l in t(["git", "status", "--porcelain"]).splitlines() if l.strip()}

added = sorted(set(now) - set(entry))
removed = sorted(set(entry) - set(now))
changed = sorted(p for p in set(now) & set(entry) if now[p] != entry[p])

out.append("")
out.append("  porcelain entries  entry=%d  exit=%d" % (len(entry), len(now)))
out.append("")
out.append("  NEWLY APPEARING (%d):" % len(added))
for p in added:
    out.append("    %-5s %s" % (now[p], p))
out.append("")
out.append("  DISAPPEARED (%d):" % len(removed))
for p in removed:
    out.append("    %-5s %s" % (entry[p], p))
if not removed:
    out.append("    (none -- no pre-existing work lost)")
out.append("")
out.append("  STATUS CHANGED (%d):" % len(changed))
for p in changed:
    out.append("    %s -> %s   %s" % (entry[p], now[p], p))
if not changed:
    out.append("    (none)")

out.append("")
out.append("=" * 100)
out.append("FILES THIS TASK TOUCHED")
out.append("=" * 100)
out.append("  tests/test_redis_debounce.py:")
out.append("    %s" % t(["git", "diff", "--numstat", "--", "tests/test_redis_debounce.py"]).strip())
out.append("    status: %s" % (t(["git", "status", "--porcelain", "--",
                                  "tests/test_redis_debounce.py"]).rstrip() or "(clean)"))
out.append("")
out.append("  FORBIDDEN FILES -- must be untouched by this task:")
for p in ("tests/conftest.py", "backend_app/core/cache/redis_manager.py",
          "backend_app/backend/redis_manager.py", "backend_app/core/subscription_engine.py",
          "tests/test_quota01_exchange_slot_is_atomic.py",
          "tests/test_webhook_duplicate_delivery_real_redis.py",
          "tests/test_quota01_metered_reservation_is_hard.py",
          ".github/workflows/01-pr-check.yml", "pytest.ini"):
    st = t(["git", "status", "--porcelain", "--", p]).rstrip()
    e = entry.get(p, "  ")
    n = now.get(p, "  ")
    out.append("    %-54s entry=%-5s exit=%-5s %s"
               % (p, e, n, "UNCHANGED BY ME" if e == n else ">> I CHANGED IT"))

out.append("")
out.append("  blob identities of the D5' files (must match A20):")
for p, want in (("tests/test_quota01_exchange_slot_is_atomic.py", "2d7ccbbb"),
                ("tests/test_quota01_metered_reservation_is_hard.py", "7baff0b6"),
                ("tests/test_webhook_duplicate_delivery_real_redis.py", "3ffbccde")):
    h = t(["git", "hash-object", p]).strip()
    out.append("    %-54s %s  %s" % (p, h[:8], "SAME" if h.startswith(want) else ">> CHANGED"))

open("staging/p11b22_integrity.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
