"""Phase 11B-A8. READ-ONLY.

C3, done properly. The first attempt compared `git show` output (decoded by the system
locale, cp437/cp1252) against a file read as UTF-8, and "found" a difference at line 17
that was purely a decoding artefact. That is the same mistake that produced A7's wrong
failure tally. Compare RAW BYTES only: `git cat-file blob` captured without text mode.

Question: is tests/test_quota01_exchange_slot_is_atomic.py identical between the working
tree and the three D5' branches once line endings are normalised? A12 claims yes ("byte
diff was CRLF only"). My drift table shows 41804 vs 40937 bytes, which would imply no.
"""

from __future__ import annotations

import hashlib
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

REVS = [
    ("PR#8", "a624ce89b16005b0c73197005d6d0672737dad65"),
    ("PR#7", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#6", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
]

PATHS = [
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
]


def blob(rev: str, path: str) -> bytes | None:
    r = subprocess.run(
        ["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True
    )
    return r.stdout if r.returncode == 0 else None


def norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def main() -> int:
    for path in PATHS:
        disk = open(f"{REPO}\\" + path.replace("/", "\\"), "rb").read()
        dn = norm(disk)
        print(f"=== {path} ===")
        print(f"  worktree   raw={len(disk):>6}  norm={len(dn):>6}  lines={dn.count(10):>4}  sha={sha(dn)}")
        for label, rev in REVS:
            b = blob(rev, path)
            if b is None:
                print(f"  {label:<10} absent at this rev")
                continue
            bn = norm(b)
            same = bn == dn
            flag = "IDENTICAL" if same else "DIFFERS"
            print(
                f"  {label:<10} raw={len(b):>6}  norm={len(bn):>6}  lines={bn.count(10):>4}  "
                f"sha={sha(bn)}  {flag}"
            )
            if not same:
                a, c = bn.split(b"\n"), dn.split(b"\n")
                shown = 0
                for i in range(max(len(a), len(c))):
                    x = a[i] if i < len(a) else b"<absent>"
                    y = c[i] if i < len(c) else b"<absent>"
                    if x != y:
                        print(f"      line {i+1}:")
                        print(f"        branch  : {x[:110]!r}")
                        print(f"        worktree: {y[:110]!r}")
                        shown += 1
                        if shown >= 3:
                            print("      ... further differences not shown")
                            break
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
