"""Phase 8 final regression counts, recalculated from the new runs and cross-checked.

IMPORTS the Phase 7 finalizer's parsers rather than copying them. `lane()`, `parse_summary`,
`parse_collected`, `parse_junit` and `parse_failed_ids` are the proven implementations that
produced the Phase 5/6/7 figures, including the junit cross-check and the convention note
about where `collected` comes from when `pytest -q` prints no "collected N items" line.
Re-implementing them here would let the two drift and would make a comparison against the
Phase 7 baseline a comparison of two different measuring instruments.

`finalize_phase7_counts.py` guards its own `main()` behind `__name__ == "__main__"`, so the
import is side-effect free apart from module constants.

THE PHASE 7 FIGURES ARE A BASELINE, NOT A TARGET. They are recorded here for the delta and
nothing more; the counts below are recalculated from FULLREG_P8 / FULLREG_P8PG.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from finalize_phase7_counts import lane  # noqa: E402

STAGING = pathlib.Path(__file__).resolve().parent
OUT = STAGING / "fullreg_phase8_counts.json"
P7 = STAGING / "fullreg_phase7_counts.json"


def normalise_encoding(nonce: str) -> str | None:
    """Rewrite a UTF-16 raw log as UTF-8 in place, so `lane()` can parse it.

    `lane()` reads with `encoding="utf-8", errors="replace"`, which is correct for the
    Phase 7 wrappers' `Add-Content -Encoding ascii` output. The Phase 8 CI-lane wrapper
    used `Tee-Object` instead - so the run could be WATCHED, because `Add-Content` holds an
    exclusive lock for the whole run - and `Tee-Object` on PowerShell 5.1 has no `-Encoding`
    parameter and defaults to UTF-16LE. Read as UTF-8 that yields NUL-interleaved text and
    every count parses as zero.

    Detected by BOM and by the NUL-byte signature rather than by which wrapper was used, so
    this is correct whichever one wrote the file. Returns a description of what it did.
    """
    raw = STAGING / f"fullreg_raw_{nonce}.txt"
    if not raw.exists():
        return None

    try:
        data = raw.read_bytes()
    except PermissionError:
        # Windows holds an exclusive lock while `Add-Content` is writing, which is itself
        # the signal that the run is still in flight. `lane()` reports that separately.
        return f"{raw.name}: LOCKED by the writing pipeline, left alone"

    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", errors="replace")
        raw.write_text(text, encoding="utf-8")
        return f"{raw.name}: NORMALISED from BOM'd utf-16 to utf-8"

    candidates = [("as-is", _segment_decode(data))]
    # A file that has ALREADY been rewritten by a buggy earlier pass holds UTF-16LE text
    # stored as UTF-8 - it reads as CJK mojibake and contains no NULs at all, so no
    # byte-level sniff can see it. Re-encoding recovers the original bytes exactly when the
    # earlier decode was lossless, which is checked rather than assumed.
    try:
        text_as_utf8 = data.decode("utf-8")
    except UnicodeDecodeError:
        text_as_utf8 = None
    if text_as_utf8 is not None and "\ufffd" not in text_as_utf8:
        candidates.append(("undo-mojibake", _segment_decode(text_as_utf8.encode("utf-16-le"))))

    # CHOSEN BY EVIDENCE, not by sniffing: the correct reading must begin with the header
    # the wrapper writes and must contain a pytest summary line. Anything else is a
    # mis-decode, however plausible its bytes looked.
    def usable(text: str) -> bool:
        return text.lstrip().startswith("START=") and (
            " passed" in text or " failed" in text
        )

    for label, text in candidates:
        if usable(text):
            if text != text_as_utf8:
                raw.write_text(text, encoding="utf-8")
                return (
                    f"{raw.name}: NORMALISED ({label}) -> utf-8, {len(text)} chars"
                )
            return f"{raw.name}: already readable, left alone"

    return (
        f"{raw.name}: COULD NOT BE DECODED into a readable pytest log by any of "
        f"{[c[0] for c in candidates]}. Counts for this lane are NOT trustworthy."
    )


def _segment_decode(data: bytes) -> str:
    """Decode a log whose regions alternate between single-byte and UTF-16LE.

    The Phase 8 CI wrapper wrote its header with `Set-Content -Encoding ascii`, piped
    pytest through `Tee-Object` (UTF-16LE, no BOM, because PowerShell 5.1's Tee-Object has
    no -Encoding parameter), and then wrote its trailer with `Add-Content -Encoding ascii`
    again. So one file contains THREE regions in two encodings, and any single whole-file
    decode mangles two of them.

    A UTF-16LE run of ASCII text is a sequence of `X\\x00` pairs, which is unambiguous
    enough to find directly.
    """
    wide = re.compile(rb"(?:[\x01-\x7f]\x00){4,}")
    out: list[str] = []
    cursor = 0
    for match in wide.finditer(data):
        if match.start() > cursor:
            out.append(data[cursor:match.start()].decode("utf-8", errors="replace"))
        out.append(match.group(0).decode("utf-16-le", errors="replace"))
        cursor = match.end()
    if cursor < len(data):
        out.append(data[cursor:].decode("utf-8", errors="replace"))
    return "".join(out)

#: Phase 7's authoritative pair, as handed to this phase. Historical baselines.
BASELINE_PHASE_7 = {
    "ci": {
        "collected": 13892, "passed": 13410, "failed": 7, "errors": 0,
        "skipped": 466, "xfailed": 6, "xpassed": 3, "duration_seconds": 1871.39,
        "nonce": "FULLREG_P7B",
    },
    "postgresql": {
        "collected": 13892, "passed": 13805, "failed": 14, "errors": 0,
        "skipped": 64, "xfailed": 6, "xpassed": 3, "duration_seconds": 1762.14,
        "nonce": "FULLREG_P7BPG",
    },
}

ACCOUNTING_CONVENTION = (
    "XFAILED AND XPASSED, AND WHY THE TWO SOURCES DISAGREE BY DESIGN. pytest's terminal "
    "summary counts xfailed and xpassed as their own categories, so the arithmetic that "
    "reconciles against `collected` is failed + passed + skipped + xfailed + xpassed. The "
    "JUnit XML does not have those categories: its `testsuite@skipped` attribute FOLDS "
    "xfailed into skipped, and its per-element count FOLDS xpassed into passed. In Phase 7 "
    "that showed as 472 = 466 + 6 on the header and 13389 = 13386 + 3 on the elements. So "
    "a direct attribute-to-summary comparison is expected to differ by exactly the xfail "
    "and xpass counts, and the cross-check accounts for that rather than treating it as a "
    "discrepancy. `pytest -q` prints no 'collected N items' line, so `collected` is taken "
    "from the junit `tests` attribute; that source is recorded on every lane so a reader "
    "can see the cross-check is not comparing a figure with itself."
)


def main() -> int:
    for nonce in ("FULLREG_P8", "FULLREG_P8PG", "FULLREG_P8PG2"):
        note = normalise_encoding(nonce)
        if note:
            print(note)

    ci = lane(
        "FULLREG_P8",
        "CI lane (AUTHORITATIVE, Phase 8)",
        "No DATABASE_URL and no AERORA_TEST_DATABASE_URL. Reproduces 01-pr-check.yml's "
        "'Run Unit Tests' job, so the figures stay comparable to the Phase 5/6/7 "
        "baselines. Every PostgreSQL-only suite SKIPS here. Run after migrations 026 and "
        "027, the KILL-01/TRADE-02 kill-switch fixes, the TRADE-01 capacity reservation "
        "and the TRADE-06 claim-predicate fix.",
    )
    pg = lane(
        "FULLREG_P8PG2",
        "PostgreSQL lane (AUTHORITATIVE, Phase 8)",
        "AERORA_TEST_DATABASE_URL set from staging/pg_dsn.txt against a local PostgreSQL "
        "16.2 with migrations 026 and 027 applied, so the PostgreSQL-only suites actually "
        "execute rather than skipping. SECOND run of this lane: FULLREG_P8PG surfaced two "
        "failures outside the Phase 7 set, one of which (the WORK-03 heartbeat freshness "
        "assertion) was a test defect FIXED after that run completed - so its figures no "
        "longer described HEAD and the lane was re-measured.",
    )
    pg_first = lane(
        "FULLREG_P8PG",
        "PostgreSQL lane (intermediate, pre-heartbeat-fix)",
        "Kept rather than discarded: this is the run that FOUND both failures outside the "
        "Phase 7 set, which is the only reason either is disposed of. Its four failures "
        "were the two retained false positives, the clock-dependent protected-logic "
        "substring collision (TEST-06, retained), and the heartbeat freshness assertion "
        "(fixed).",
    )

    record = {
        "lanes": {"ci": ci, "postgresql": pg},
        "intermediate_lanes": {"postgresql_first_run": pg_first},
        "accounting_convention": ACCOUNTING_CONVENTION,
        "why_two_lanes": (
            "Reporting only the CI lane would report a pass on tests that never ran, "
            "because that lane skips every PostgreSQL-only suite by design. Reporting "
            "only the PostgreSQL lane would not be comparable to the earlier baselines. "
            "The difference between the two IS the infrastructure-gated surface, so both "
            "result sets are preserved separately and neither is merged into the other."
        ),
        "baseline_phase_7": BASELINE_PHASE_7,
        "substrate": (
            "LOCAL PostgreSQL 16.2 via pgserver. No production database was connected to "
            "in any phase of this audit, so every database figure here is "
            "local-substrate evidence."
        ),
    }

    for key, base_key in (("ci", "ci"), ("postgresql", "postgresql")):
        current = record["lanes"][key]
        base = BASELINE_PHASE_7[base_key]
        if not current.get("collected"):
            continue
        record[f"delta_vs_phase_7_{key}_lane"] = {
            field: current[field] - base[field]
            for field in ("collected", "passed", "failed", "skipped", "xfailed", "xpassed")
            if field in current and field in base
        }

    if record["lanes"]["ci"].get("collected") and record["lanes"]["postgresql"].get("collected"):
        record["infrastructure_gated_surface"] = {
            "skipped_in_ci_lane": ci["skipped"],
            "skipped_in_postgresql_lane": pg["skipped"],
            "skips_converted_to_runs": ci["skipped"] - pg["skipped"],
            "note": "The reduction is the number of tests the CI lane could not measure "
                    "and the PostgreSQL lane did. A test that still skips in the "
                    "PostgreSQL lane needs something else - Redis, a running HTTP "
                    "server, a built frontend bundle - and those are named in the top "
                    "skip reasons.",
        }

    # Any lane still reporting failures is named explicitly, so no reader can take the
    # file as a clean bill of health.
    outstanding = {
        name: {"failed": data.get("failed"), "failed_ids": data.get("failed_ids", [])}
        for name, data in record["lanes"].items()
        if data.get("failed")
    }
    record["lanes_with_outstanding_failures"] = outstanding
    record["fully_green"] = not outstanding

    OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nWROTE {OUT}")
    print(f"FULLY_GREEN = {record['fully_green']}")
    for name, info in outstanding.items():
        print(f"  {name} lane still reports {info['failed']} failure(s)")
        for fid in info["failed_ids"]:
            print(f"    {fid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
