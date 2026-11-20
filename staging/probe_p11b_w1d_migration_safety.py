"""Statement-level safety matrix for migrations 022-025, plus two structural ledger hazards.

PHASE 11B / WORKSTREAM 1D — STRICTLY READ-ONLY. Parses SQL text and Python source.
No database connection. No file is written except this probe's own redirected stdout.

WHAT THIS ANSWERS, FROM SOURCE
------------------------------
W1C established that migrations 022-025 are applied to the live database with no row in
`public.schema_migrations`. W1D must decide what may safely be done about that, and the
decision turns on per-statement properties rather than on the migration as a whole:

  * a `CREATE TABLE IF NOT EXISTS` is safe to re-execute;
  * a bare `CREATE TABLE` is not;
  * a `REVOKE` is idempotent in effect but destructive in direction;
  * an `INSERT` without a conflict clause duplicates rows on re-execution;
  * a `DROP ... CASCADE` can remove objects a later migration created.

So every statement is classified, and the file-level verdict is the worst case among its
statements -- not an average.

TWO STRUCTURAL HAZARDS CHECKED SEPARATELY
-----------------------------------------
1. LEDGER KEY COLLISION. Both appliers record
   `name = f"{path.parent.name}/{path.name}"`. The parent directory NAME only, not the
   path. So `backend_app/migrations/X.sql` and `migrations/X.sql` both record as
   `migrations/X.sql`. If any basename exists in both directories, the ledger cannot
   distinguish them and `_apply_sql`'s `if name in already` would skip the second as
   already applied -- silently not applying it.

2. TRANSACTION ATOMICITY. Both appliers run `cur.execute(text)` and then the ledger
   INSERT inside one `with conn.cursor()` block before a single `conn.commit()`. That is
   atomic ONLY while the migration text does not commit on its own. A file containing its
   own `COMMIT;` ends the transaction mid-way, so the INSERT lands in a NEW transaction
   and an interruption between the two leaves the migration applied and unrecorded.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PRIMARY = REPO / "backend_app" / "migrations"
SECONDARY = REPO / "migrations"
TARGETS = ["022_fix_library_subscriptions_updated_at.sql",
           "023_remediate_privilege_escalation.sql",
           "024_remediate_referral_double_commission.sql",
           "025_work03_bot_ownership_claims.sql"]

#: (regex, class, re-executable?, note). Order matters; first match wins.
RULES: list[tuple[str, str, str, str]] = [
    (r"^create\s+table\s+if\s+not\s+exists", "DDL-create", "SAFE",
     "guarded by IF NOT EXISTS"),
    (r"^create\s+table\b", "DDL-create", "UNSAFE",
     "unguarded CREATE TABLE fails if the object exists"),
    (r"^create\s+(unique\s+)?index\s+if\s+not\s+exists", "DDL-index", "SAFE",
     "guarded"),
    (r"^create\s+(unique\s+)?index\b", "DDL-index", "UNSAFE", "unguarded"),
    (r"^create\s+or\s+replace\s+(function|procedure|view)", "DDL-replace", "SAFE",
     "idempotent by definition"),
    (r"^create\s+trigger\b", "DDL-trigger", "UNSAFE",
     "no IF NOT EXISTS for triggers before PG14; duplicate name errors"),
    (r"^create\s+policy\b", "DDL-policy", "UNSAFE",
     "duplicate policy name errors; usually paired with a preceding DROP POLICY"),
    (r"^create\s+type\b", "DDL-type", "UNSAFE", "duplicate type errors"),
    (r"^drop\s+policy\s+if\s+exists", "DDL-drop", "SAFE-GUARDED",
     "guarded, but REMOVES a control for the rest of the transaction"),
    (r"^drop\s+(table|schema|database)\b.*cascade", "DDL-drop", "DESTRUCTIVE",
     "CASCADE can remove objects created by LATER migrations"),
    (r"^drop\s+function\s+if\s+exists", "DDL-drop", "SAFE-GUARDED",
     "guarded; dependent objects may break if not recreated"),
    (r"^drop\s+(table|index|trigger|type|function|policy|constraint)\b", "DDL-drop",
     "DESTRUCTIVE", "unguarded drop"),
    (r"^alter\s+table\b.*\badd\s+column\s+if\s+not\s+exists", "DDL-alter", "SAFE",
     "guarded"),
    (r"^alter\s+table\b.*\bdrop\s+column\b", "DDL-alter", "DESTRUCTIVE",
     "column drop loses data"),
    (r"^alter\s+table\b.*\b(enable|disable|force|no\s+force)\s+row\s+level\s+security",
     "DDL-rls", "SAFE", "idempotent state change, but changes a security control"),
    (r"^alter\s+table\b", "DDL-alter", "CONDITIONAL",
     "depends on the specific clause; inspect"),
    (r"^alter\s+default\s+privileges\b", "GRANT", "SAFE",
     "idempotent, but alters future-object privileges"),
    (r"^(grant)\b", "GRANT", "SAFE", "idempotent; WIDENS privilege"),
    (r"^(revoke)\b", "REVOKE", "SAFE", "idempotent; NARROWS privilege"),
    (r"^insert\s+into\b.*on\s+conflict", "DML-insert", "SAFE", "conflict-guarded"),
    (r"^insert\s+into\b", "DML-insert", "UNSAFE",
     "re-execution DUPLICATES rows -- no conflict clause"),
    (r"^update\b", "DML-update", "CONDITIONAL",
     "idempotent only if the SET is a fixed value and the WHERE re-selects nothing"),
    (r"^delete\s+from\b", "DML-delete", "DESTRUCTIVE", "removes rows"),
    (r"^truncate\b", "DML-truncate", "DESTRUCTIVE", "removes all rows"),
    (r"^comment\s+on\b", "META", "SAFE", "idempotent"),
    (r"^do\s*\$\$", "PLPGSQL", "CONDITIONAL",
     "anonymous block -- re-execution safety depends on its body"),
    (r"^(begin|commit|rollback|start\s+transaction)\b", "TXN", "N/A",
     "explicit transaction control INSIDE the file -- see hazard 2"),
    (r"^(set|reset)\b", "SESSION", "SAFE", "session-local"),
    (r"^select\b", "READ", "SAFE", "read only"),
    (r"^raise\b", "DIAG", "SAFE", "diagnostic"),
]

WORST = ["DESTRUCTIVE", "UNSAFE", "CONDITIONAL", "SAFE-GUARDED", "SAFE", "N/A"]


def _strip(sql: str) -> str:
    """Remove line and block comments, preserving statement structure."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def _statements(sql: str) -> list[str]:
    """Split on `;` at depth 0, keeping `DO $$ ... $$;` blocks and 'string literals' whole.

    A naive split on `;` tears PL/pgSQL bodies apart and produces nonsense fragments,
    which would then be misclassified. BOTH quoting forms have to be tracked:

      * dollar-quoting (`$$ ... $$`, `$tag$ ... $tag$`) for PL/pgSQL bodies;
      * ORDINARY SINGLE QUOTES, including the SQL `''` escape.

    An earlier revision of this probe tracked only dollar-quoting. A `COMMENT ON INDEX
    ... IS '... one atomic decision.'` in `024` contains a `;`-free but quote-heavy
    string that still mis-split, and the probe reported a statement reading
    `only a unique index ' 'makes the check and the write one atomic decision.'` as
    UNCLASSIFIED. That is a defect in the splitter, not an odd statement in the
    migration -- and an UNCLASSIFIED row in a safety matrix is exactly the kind of noise
    that gets waved through.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    tag: str | None = None
    in_quote = False
    while i < len(sql):
        ch = sql[i]
        if tag is not None:
            if sql.startswith(tag, i):
                buf.append(tag)
                i += len(tag)
                tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_quote:
            # `''` inside a literal is an escaped quote, not the end of it.
            if ch == "'" and sql[i + 1:i + 2] == "'":
                buf.append("''")
                i += 2
                continue
            buf.append(ch)
            i += 1
            if ch == "'":
                in_quote = False
            continue
        m = re.match(r"\$([A-Za-z_]*)\$", sql[i:])
        if m:
            tag = m.group(0)
            buf.append(tag)
            i += len(tag)
            continue
        if ch == "'":
            in_quote = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _pair_guarded(stmts: list[str], index: int) -> str | None:
    """Is this `CREATE POLICY`/`CREATE TRIGGER` preceded by a matching guarded DROP?

    `CREATE POLICY` has no `IF NOT EXISTS`, so in isolation it is not re-executable. The
    idiom that makes it re-executable is a `DROP ... IF EXISTS` on the same object
    immediately before. Scoring the CREATE as UNSAFE while ignoring the DROP overstates
    the risk -- which matters here, because an overstated verdict argues for re-running a
    migration that does not need re-running.
    """
    probe = re.sub(r"\s+", " ", stmts[index].strip().lower())
    m = re.match(r"^create\s+(policy|trigger)\s+(\"[^\"]+\"|\S+)", probe)
    if not m:
        return None
    kind, obj = m.group(1), m.group(2).strip('"')
    for prior in stmts[:index]:
        p = re.sub(r"\s+", " ", prior.strip().lower())
        if re.match(rf"^drop\s+{kind}\s+if\s+exists\s+(\"?){re.escape(obj)}\1", p):
            return f"guarded by a preceding DROP {kind.upper()} IF EXISTS on {obj!r}"
    return None


def _classify(stmt: str) -> tuple[str, str, str]:
    probe = re.sub(r"\s+", " ", stmt.strip().lower())
    for pattern, kind, safety, note in RULES:
        if re.match(pattern, probe):
            return kind, safety, note
    return "UNCLASSIFIED", "CONDITIONAL", "no rule matched -- inspect by hand"


def hazard_key_collision() -> None:
    print("=" * 78)
    print("HAZARD 1 -- LEDGER KEY COLLISION")
    print("=" * 78)
    print("   Both appliers record `f\"{path.parent.name}/{path.name}\"`, i.e. the parent")
    print("   DIRECTORY NAME only. Two different files can therefore share one key.\n")
    prim = {p.name for p in PRIMARY.glob("*.sql")} if PRIMARY.exists() else set()
    sec = {p.name for p in SECONDARY.glob("*.sql")} if SECONDARY.exists() else set()
    print(f"   backend_app/migrations/*.sql : {len(prim)} files -> key 'migrations/<name>'")
    print(f"   migrations/*.sql             : {len(sec)} files -> key 'migrations/<name>'")
    clash = sorted(prim & sec)
    print(f"   COLLIDING basenames          : {len(clash)}")
    for name in clash:
        a = PRIMARY / name
        b = SECONDARY / name
        da = hashlib.sha256(a.read_bytes()).hexdigest()[:16]
        db = hashlib.sha256(b.read_bytes()).hexdigest()[:16]
        print(f"     !! {name}")
        print(f"          backend_app/migrations/  {a.stat().st_size:>7} B  sha {da}")
        print(f"          migrations/              {b.stat().st_size:>7} B  sha {db}")
        print(f"          same content: {da == db}")
    if not clash:
        print("     none today -- the hazard is latent, not realised")
    print("\n   Consequence if it were realised: `_apply_sql` tests `if name in already`")
    print("   and would SKIP the second file as 'already applied', or REFUSE on a")
    print("   checksum mismatch and exit 4. Neither outcome names the real problem.")
    print()


def hazard_atomicity() -> None:
    print("=" * 78)
    print("HAZARD 2 -- EXECUTE-THEN-RECORD ATOMICITY")
    print("=" * 78)
    print("   Both appliers do, inside ONE cursor block before ONE conn.commit():")
    print("       cur.execute(text)                       # the migration")
    print("       cur.execute('INSERT INTO ... schema_migrations ...')")
    print("       conn.commit()")
    print("   Atomic -- UNLESS the migration text commits on its own.\n")
    print(f"   {'migration':52} {'BEGIN;':>7} {'COMMIT;':>8}  atomic with ledger?")
    print("   " + "-" * 74)
    for path in sorted(PRIMARY.glob("*.sql")):
        if not re.match(r"^\d", path.name):
            continue
        text = _strip(path.read_text(encoding="utf-8", errors="replace"))
        nb = len(re.findall(r"(?im)^\s*begin\s*;", text))
        nc = len(re.findall(r"(?im)^\s*commit\s*;", text))
        verdict = "YES" if nc == 0 else "NO -- file commits itself"
        print(f"   {path.name:52} {nb:>7} {nc:>8}  {verdict}")
    print("\n   For any file with COMMIT; > 0 the ledger INSERT runs in a SEPARATE")
    print("   transaction from the DDL. An interruption between them leaves the")
    print("   migration APPLIED and UNRECORDED -- precisely the 022-025 condition.")
    print()


def main() -> int:
    print(f"repo: {REPO}")
    print("READ-ONLY: parses SQL and Python text. No database connection.\n")
    hazard_key_collision()
    hazard_atomicity()

    print("=" * 78)
    print("STATEMENT-LEVEL SAFETY MATRIX -- MIGRATIONS 022-025")
    print("=" * 78)

    summary = []
    for name in TARGETS:
        path = PRIMARY / name
        if not path.exists():
            print(f"\n{name}: ABSENT")
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        stmts = _statements(_strip(raw))

        buckets: dict[str, list[tuple[str, str, str]]] = {}
        for idx, stmt in enumerate(stmts):
            kind, safety, note = _classify(stmt)
            guard = _pair_guarded(stmts, idx)
            if guard and safety == "UNSAFE":
                safety, note = "SAFE-GUARDED", guard
            head = re.sub(r"\s+", " ", stmt.strip())[:96]
            buckets.setdefault(safety, []).append((kind, head, note))

        file_verdict = next((w for w in WORST if w in buckets), "SAFE")
        summary.append((name, len(stmts), file_verdict, buckets))

        print(f"\n{'-' * 78}")
        print(f"{name}")
        print(f"{'-' * 78}")
        print(f"   bytes {len(raw)}   statements {len(stmts)}")
        print(f"   sha256 {digest}")
        print(f"   ledger key would be: 'migrations/{name}'")
        print(f"   FILE VERDICT (worst statement): **{file_verdict}**")
        for safety in WORST:
            if safety not in buckets:
                continue
            print(f"\n     [{safety}]  {len(buckets[safety])} statement(s)")
            seen_notes = set()
            for kind, head, note in buckets[safety][:8]:
                print(f"       {kind:14} {head}")
                if note not in seen_notes:
                    print(f"       {'':14} ^ {note}")
                    seen_notes.add(note)
            if len(buckets[safety]) > 8:
                print(f"       ... and {len(buckets[safety]) - 8} more")

    print("\n" + "=" * 78)
    print("FILE-LEVEL VERDICTS")
    print("=" * 78)
    print(f"   {'migration':52} {'stmts':>6}  verdict")
    print("   " + "-" * 74)
    for name, n, verdict, _ in summary:
        print(f"   {name:52} {n:>6}  {verdict}")
    print("\n   A file is re-executable only if NO statement is DESTRUCTIVE or UNSAFE")
    print("   and every CONDITIONAL one has been inspected and cleared by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
