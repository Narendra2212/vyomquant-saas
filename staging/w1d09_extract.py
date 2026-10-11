"""Phase 11B-W1D-09. READ-ONLY. Extract the dimension facts from CURRENT source.

The brief requires the specification be built on the current repository, not on an earlier
report. Every figure in the deliverable's matrix therefore comes from importing the live
modules and parsing the live SQL here -- not from W1D-05's published table.

Imported under a poisoned PG environment so an accidental connection would be obvious.
"""

from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import re
import sys
import time

os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGPORT", "1")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")
os.environ["AERORA_TEST_DATABASE_URL"] = ""

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")
sys.path.insert(0, str(ROOT))

t0 = time.time()
from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm  # noqa: E402
from scripts.verify_schema_drift import ADVISORY_DIMENSIONS, _as_tuples  # noqa: E402
import scripts.verify_schema_drift as drift  # noqa: E402
import scripts.verify_provisioning as prov  # noqa: E402

IMPORT_S = time.time() - t0


def strip_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", l) for l in sql.splitlines())


def split_top_level(segment: str) -> list[str]:
    """Split on commas at paren depth 0, outside single-quoted literals."""
    out, buf, depth, in_str = [], [], 0, False
    i = 0
    while i < len(segment):
        ch = segment[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(segment) and segment[i + 1] == "'":
                    buf.append(segment[i + 1])
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def find_at_depth0(sql: str, keyword: str) -> int:
    depth, in_str, i = 0, False, 0
    kw = keyword.lower()
    low = sql.lower()
    while i < len(sql):
        ch = sql[i]
        if in_str:
            if ch == "'":
                in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and low.startswith(kw, i):
            return i
        i += 1
    return -1


def select_columns(sql: str) -> list[str]:
    s = strip_comments(sql)
    sel = find_at_depth0(s, "select")
    frm = find_at_depth0(s, "from")
    if sel < 0 or frm < 0 or frm < sel:
        return []
    return split_top_level(s[sel + len("select"): frm])


def order_by_terms(sql: str) -> list[str] | None:
    s = strip_comments(sql)
    ob = find_at_depth0(s, "order by")
    if ob < 0:
        return None
    tail = s[ob + len("order by"):]
    for stop in ("limit", "offset"):
        p = find_at_depth0(tail, stop)
        if p >= 0:
            tail = tail[:p]
    return [t.strip().rstrip(";") for t in split_top_level(tail)]


def main() -> int:
    print("=== PROVENANCE ===")
    print(f"  SNAPSHOT_QUERIES defined in : {prov.__file__}")
    print(f"  compare() defined in        : {drift.__file__}")
    print(f"  identity: drift.SNAPSHOT_QUERIES is prov.SNAPSHOT_QUERIES = "
          f"{drift.SNAPSHOT_QUERIES is prov.SNAPSHOT_QUERIES}")
    print(f"  dimensions                  : {len(SNAPSHOT_QUERIES)}")
    print(f"  ADVISORY_DIMENSIONS         : {sorted(ADVISORY_DIMENSIONS)}")
    print(f"  import time (poisoned PG)   : {IMPORT_S:.3f}s -> no connection at import")
    print(f"  _norm                       : {_norm.__doc__ or '(no docstring)'}")
    print(f"  _as_tuples                  : {(_as_tuples.__doc__ or '').strip()[:90]}")
    print()

    print("=== TEST-LOCAL DECLARATION (NOT production) ===")
    spec = importlib.util.spec_from_file_location(
        "w1d05_guards", str(ROOT / "tests" / "test_snapshot_dimension_invariants.py")
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        tl_key = dict(getattr(mod, "COMPARISON_KEY", {}))
        whole = getattr(mod, "WHOLE_ROW", None)
        print(f"  loaded from tests/test_snapshot_dimension_invariants.py")
        print(f"  WHOLE_ROW sentinel = {whole!r}")
        print(f"  COMPARISON_KEY entries = {len(tl_key)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  could not load: {type(exc).__name__}: {exc}")
        tl_key, whole = {}, None
    print()

    print("=== PER-DIMENSION FACTS, parsed from the live SQL ===")
    print(f"  {'dimension':<30} {'cols':>4} {'key':>10} {'attrs':>5} {'ORDER BY terms':>14}  advisory")
    print("  " + "-" * 92)
    rows = []
    for name in SNAPSHOT_QUERIES:
        sql = SNAPSHOT_QUERIES[name]
        cols = select_columns(sql)
        ob = order_by_terms(sql)
        k = tl_key.get(name, "<undeclared>")
        kw = 0 if k == whole else (k if isinstance(k, int) else None)
        attrs = (len(cols) - kw) if isinstance(kw, int) and k != whole else (0 if k == whole else None)
        rows.append((name, len(cols), k, attrs, len(ob) if ob is not None else None, name in ADVISORY_DIMENSIONS, cols, ob))
        print(f"  {name:<30} {len(cols):>4} {str(k):>10} {str(attrs):>5} "
              f"{str(len(ob)) if ob is not None else 'NONE':>14}  "
              f"{'advisory' if name in ADVISORY_DIMENSIONS else ''}")
    print("  " + "-" * 92)
    keyed = [r for r in rows if isinstance(r[2], int)]
    whole_rows = [r for r in rows if r[2] == whole]
    print(f"  dimensions={len(rows)}  keyed={len(keyed)}  whole-row={len(whole_rows)}  "
          f"undeclared={len([r for r in rows if r[2] == '<undeclared>'])}")
    print(f"  keyed dimensions with >=1 attribute column (W1C-02 applies): "
          f"{len([r for r in keyed if r[3] and r[3] > 0])}")
    print()

    print("=== COLUMN AND ORDER BY DETAIL (for the evidence matrix) ===")
    for name, ncols, k, attrs, nob, adv, cols, ob in rows:
        print(f"  --- {name}  (key={k}, cols={ncols}, attrs={attrs}{', advisory' if adv else ''})")
        for i, c in enumerate(cols):
            tag = "KEY " if isinstance(k, int) and i < k else ("ROW " if k == whole else "attr")
            print(f"        [{i}] {tag} {re.sub(r'\\s+', ' ', c)[:92]}")
        if ob is None:
            print(f"        ORDER BY: *** NONE AT DEPTH 0 ***")
        else:
            print(f"        ORDER BY ({len(ob)}): {[re.sub(r'\\s+', ' ', t)[:40] for t in ob]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
