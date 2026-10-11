"""Phase 10 / P-23 task: prove no audit JSON mirror was modified in this task.

The P-23 draw-order task is explicitly forbidden from touching the audit mirrors. The prior
(TEST-05) task edited exactly one of the four - full_audit_results.json - so the expected
state is: full_audit at resolved=58/open=25, the other three at resolved=57/open=26, and the
only structural differences between full_audit and a sibling being the five TEST-05 ones.

Any other difference means this task changed a mirror. Exits non-zero if so.

Usage: python staging/check_audit_mirrors_untouched.py
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List, Tuple

FILES = (
    "full_audit_results.json",
    "marketplace_audit.json",
    "money_flow_audit.json",
    "trading_audit.json",
)

EXPECTED_TEST05_DIFFS = {
    ("CHANGED", "/corrections[21]/why"),
    ("CHANGED", "/corrections[21]/fix"),
    ("CHANGED", "/corrections[21]/lesson"),
    ("REMOVED_IN_EDITED", "/open/TEST-05"),
    ("ADDED_IN_EDITED", "/resolved/TEST-05"),
}


def block(name: str) -> Any:
    return json.load(open("staging/" + name, encoding="utf-8"))["remediation"]


def diffs(x: Any, y: Any, p: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            if k not in x:
                out.append(("REMOVED_IN_EDITED", p + "/" + k))
            elif k not in y:
                out.append(("ADDED_IN_EDITED", p + "/" + k))
            else:
                out += diffs(x[k], y[k], p + "/" + k)
    elif isinstance(x, list) and isinstance(y, list):
        if len(x) != len(y):
            out.append(("LEN", p))
        else:
            for i, (u, v) in enumerate(zip(x, y)):
                out += diffs(u, v, p + "[" + str(i) + "]")
    elif x != y:
        out.append(("CHANGED", p))
    return out


def main() -> int:
    bad = []
    blocks = {}
    for name in FILES:
        b = block(name)
        blocks[name] = b
        h = hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()[:12]
        print(f"  {name:28s} resolved={len(b['resolved']):3d} "
              f"open={len(b['open']):3d}  nonce={b.get('nonce')}  hash={h}")

    mirrors = [n for n in FILES if n != "full_audit_results.json"]
    mirror_hashes = {
        hashlib.sha256(json.dumps(blocks[n], sort_keys=True).encode()).hexdigest()
        for n in mirrors
    }
    print(f"\n  the three untouched mirrors are identical to each other: "
          f"{len(mirror_hashes) == 1}")
    if len(mirror_hashes) != 1:
        bad.append("the three mirrors have diverged from each other")

    for n in mirrors:
        d = set(diffs(blocks["full_audit_results.json"], blocks[n]))
        extra = d - EXPECTED_TEST05_DIFFS
        missing = EXPECTED_TEST05_DIFFS - d
        print(f"  full_audit vs {n:28s} diffs={len(d)} "
              f"unexpected={len(extra)} missing={len(missing)}")
        if extra:
            bad.append(f"{n}: unexpected differences {sorted(extra)}")
        if missing:
            bad.append(f"{n}: expected TEST-05 differences absent {sorted(missing)}")

    print()
    if bad:
        print("AUDIT MIRRORS WERE MODIFIED OR DIVERGED:")
        for item in bad:
            print(f"  - {item}")
        return 1
    print("NO AUDIT JSON MIRROR WAS MODIFIED IN THIS TASK: confirmed.")
    print("  (full_audit_results.json still carries exactly the five TEST-05 differences")
    print("   made in the prior task, and nothing else.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
