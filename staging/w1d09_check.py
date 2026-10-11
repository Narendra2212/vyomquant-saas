"""Phase 11B-W1D-09. READ-ONLY. Confirm the matrix covers all 16 live dimensions."""

from __future__ import annotations

import io
import os
import pathlib
import re
import sys

os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")
sys.path.insert(0, str(ROOT))

from scripts.verify_provisioning import SNAPSHOT_QUERIES  # noqa: E402

REPORT = ROOT / "staging" / "PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md"


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    print(f"live dimensions: {len(SNAPSHOT_QUERIES)}")

    backtick = "`"
    missing = [d for d in SNAPSHOT_QUERIES if f"{backtick}{d}{backtick}" not in text]
    print(f"dimensions absent from the report: {missing or 'none'}")

    start = text.index("## 5. Dimension-by-dimension")
    end = text.index("## 6.")
    section = text[start:end]
    rows = [l for l in section.splitlines() if re.match(r"^\| \d+ \|", l)]
    print(f"matrix rows: {len(rows)}")
    print(f"matrix covers all {len(SNAPSHOT_QUERIES)}: {len(rows) == len(SNAPSHOT_QUERIES)}")

    named = []
    for l in rows:
        m = re.search(rf"{backtick}([a-z_]+){backtick}", l)
        if m:
            named.append(m.group(1))
    unknown = [n for n in named if n not in SNAPSHOT_QUERIES]
    notlisted = [d for d in SNAPSHOT_QUERIES if d not in named]
    print(f"matrix names not in SNAPSHOT_QUERIES: {unknown or 'none'}")
    print(f"live dimensions missing a matrix row : {notlisted or 'none'}")

    # every row must carry an explicit uniqueness verdict
    no_verdict = [l[:40] for l in rows if ("**No**" not in l and "n/a" not in l)]
    print(f"matrix rows lacking a uniqueness verdict: {no_verdict or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
