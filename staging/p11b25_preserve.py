"""A25 exit preservation check. Read-only: no git write command is issued."""
import hashlib
import subprocess

OUT = []


def run(args):
    return subprocess.run(
        ["git"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def say(line):
    OUT.append(line)


# Two digest methods, each with its serialisation documented. A bare digest value
# is NOT a portable invariant: the first pass of this script joined the sorted
# lines with newlines and reported a false CHANGED against a baseline that had
# concatenated them with no separator. Both methods are kept so the next session
# can reproduce either one without guessing.
#
#   METHOD A14  (staging/p11b25_rebase.py L31-33) : sorted lines, NO separator
#   METHOD W1D11 "Digest A" (that session's §7.1) : raw order, LF-joined, no trailing LF
EXPECTED_A14 = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
EXPECTED_W1D11 = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"

raw_lines = [l for l in run(["ls-files", "--stage"]).splitlines() if l]

_h = hashlib.sha256()
for _l in sorted(raw_lines):
    _h.update(_l.encode("utf-8"))
digest_a14 = _h.hexdigest()

digest_w1d11 = hashlib.sha256("\n".join(raw_lines).encode("utf-8")).hexdigest()

say("=" * 78)
say("A25 EXIT PRESERVATION CHECK")
say("=" * 78)
say("Two independently-defined digest methods, both reproduced:")
say(f"  method A14   (sorted, no separator) : {digest_a14}")
say(f"    expected                          : {EXPECTED_A14}")
say(f"    VERDICT                           : {'SAME' if digest_a14 == EXPECTED_A14 else 'CHANGED'}")
say(f"  method W1D11 (raw order, LF-joined) : {digest_w1d11}")
say(f"    expected                          : {EXPECTED_W1D11}")
say(f"    VERDICT                           : {'SAME' if digest_w1d11 == EXPECTED_W1D11 else 'CHANGED'}")
say("")

head = run(["rev-parse", "HEAD"]).strip()
origin = run(["rev-parse", "origin/main"]).strip()
say(f"HEAD                 : {head}")
say(f"origin/main          : {origin}")
say(f"HEAD == origin/main  : {head == origin}")
say(f"ahead/behind         : {run(['rev-list', '--left-right', '--count', 'origin/main...HEAD']).strip()}")
say(f"branch               : {run(['rev-parse', '--abbrev-ref', 'HEAD']).strip()}")
say("")

status = [l for l in run(["status", "--porcelain"]).splitlines() if l]
staged = [l for l in status if l[0] not in " ?"]
unstaged = [l for l in status if not l.startswith("??") and len(l) > 1 and l[1] != " "]
untracked = [l for l in status if l.startswith("??")]
unmerged = [l for l in status if l[:2] in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")]
say(f"staged    : {len(staged)}   (expected 22)")
say(f"unstaged  : {len(unstaged)}   (expected 102)")
say(f"untracked : {len(untracked)}   (expected 139, collapsed)")
say(f"unmerged  : {len(unmerged)}   (expected 0)")
say(f"tracked entries : {len([l for l in run(['ls-files']).splitlines() if l])}   (expected 2104)")
say("")

say("--- fingerprints that must not have moved ---")
for path in (
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/real_redis_gate.py",
    "tests/test_redis_debounce.py",
    "backend_app/backend/redis_manager.py",
    "backend_app/core/subscription_engine.py",
    "backend_app/main.py",
):
    say(f"  {run(['hash-object', path]).strip()}  {path}")

text = "\n".join(OUT) + "\n"
with open("staging/p11b25_preserve.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)
