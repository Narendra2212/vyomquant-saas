"""What would each PG-gated suite WRITE to the shared cluster? Measured without running it.

PHASE 11B / WORKSTREAM 1B — Phase 3 deliverable evidence.

WHY STATIC
----------
The question is whether it is safe to point these suites at `staging/pgdata_r1`, a shared
cluster that holds data (57 profiles, 28 orders, 32 safety_control_audit rows). Running
them to find out is the thing being decided, so the answer has to come from the source.

WHAT IT MEASURES, PER FILE
--------------------------
1. THE GATE. Which environment variable or DSN file decides whether the suite runs, and
   whether absence SKIPS (safe) or FAILS (noisy but also safe) or RUNS ANYWAY (unsafe).
2. WRITE OPERATIONS, by category, with line numbers and the tables named. SQL is found
   in string constants -- including f-strings, whose literal segments are visible in the
   AST as `JoinedStr` parts -- and classified by leading keyword, so `DELETE FROM` in a
   docstring is not counted as a write (docstrings are excluded explicitly).
3. COMMIT DISCIPLINE. `autocommit = True` / `autocommit=True` kwarg /
   `ISOLATION_LEVEL_AUTOCOMMIT` / explicit `.commit()` / `.rollback()` in a `finally`.
   This is the difference between a write that survives the test and one that does not.
4. BLAST RADIUS. Whether each destructive statement is scoped. An unqualified
   `DELETE FROM t` or any `TRUNCATE` / `DROP` / `ALTER` is unscoped and therefore
   affects rows this suite did not create.
5. ROLE SWITCHING. `SET ROLE` / `RESET ROLE` / `CREATE ROLE`. Relevant because the lane
   connects as a superuser table owner with BYPASSRLS, so only a suite that explicitly
   switches role can say anything at all about RLS.

LIMITS, STATED
--------------
Static analysis sees SQL it can read as a literal. SQL assembled at runtime from
non-literal parts, or issued through an ORM without literal SQL, is reported separately
as ORM/dynamic rather than silently scored as "no writes". A file in that bucket is
UNKNOWN, not SAFE.

STRICTLY READ-ONLY: parses files, prints. No import, no database, no execution.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

GATE_TOKENS = ("pg_dsn.txt", "POSTGRES_TEST_DSN", "TEST_DATABASE_URL", "DATABASE_URL",
               "AERORA_TEST_PG", "bootstrap_pg")

#: Leading keyword -> category. Order matters: longest/most specific first.
WRITE_PATTERNS: list[tuple[str, str]] = [
    (r"^\s*truncate\b", "TRUNCATE"),
    (r"^\s*drop\s+(table|schema|database|role|policy|function|index|type)\b", "DROP"),
    (r"^\s*create\s+(table|schema|database|role|policy|function|index|type|extension)\b",
     "CREATE"),
    (r"^\s*alter\s+(table|schema|database|role|policy|function|index|type|default)\b",
     "ALTER"),
    (r"^\s*insert\s+into\b", "INSERT"),
    (r"^\s*update\s+\S", "UPDATE"),
    (r"^\s*delete\s+from\b", "DELETE"),
    (r"^\s*(grant|revoke)\b", "GRANT/REVOKE"),
    (r"^\s*(set\s+role|reset\s+role)\b", "SET ROLE"),
    (r"^\s*select\s+.*\bfor\s+update\b", "SELECT FOR UPDATE"),
    (r"^\s*(begin|commit|rollback|savepoint|release)\b", "TXN CONTROL"),
    (r"^\s*copy\b", "COPY"),
]

DESTRUCTIVE = {"TRUNCATE", "DROP", "ALTER", "GRANT/REVOKE"}
MUTATING = {"INSERT", "UPDATE", "DELETE", "COPY", "CREATE"} | DESTRUCTIVE

TABLE_RE = re.compile(
    r"\b(?:from|into|update|table|join)\s+"
    r"((?:(?:public|auth|storage)\s*\.\s*)?\"?[a-zA-Z_][a-zA-Z0-9_]*\"?)",
    re.IGNORECASE)


def _sql_literals(tree: ast.Module) -> list[tuple[int, str]]:
    """Every string literal that is NOT a docstring, with its line number.

    f-strings contribute their literal segments: those are the parts that carry the
    verb, and the interpolated parts are table/column names the verb acts on.
    """
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(
                    body[0].value.value, str):
                docstring_nodes.add(id(body[0].value))

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_nodes:
                continue
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            parts = [v.value for v in node.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if parts:
                out.append((node.lineno, " <?> ".join(parts)))
    return out


def _classify(text: str) -> str | None:
    probe = text.strip().lower()
    if len(probe) < 4:
        return None
    for pattern, label in WRITE_PATTERNS:
        if re.search(pattern, probe):
            return label
    return None


#: SQL keywords and bare schema names the table regex picks up as false positives. An
#: f-string like `f'DELETE FROM public."{table}"'` leaves the literal segment
#: `DELETE FROM public."` behind, so `public` is matched as if it were the table; and
#: `DELETE FROM t WHERE ... IF ...` can leave a keyword in the capture. Reported as a
#: stop-list rather than silently filtered, because a real table called `public` would
#: be excluded too -- there is none here (checked against the 89 live table names).
TABLE_STOPWORDS = {
    "select", "where", "values", "set", "and", "or", "on", "if", "with", "as",
    "public", "auth", "storage", "only", "exists", "not", "case", "when", "then",
    "else", "end", "null", "true", "false", "table", "into", "from", "update",
    "delete", "insert", "join", "using", "returning", "conflict", "do", "nothing",
}


def _tables(text: str) -> list[str]:
    found = []
    for match in TABLE_RE.finditer(text):
        name = match.group(1).replace('"', "").replace(" ", "").lower()
        if name in TABLE_STOPWORDS:
            continue
        if name.endswith("."):
            continue
        if name not in found:
            found.append(name)
    return found


def _is_scoped(text: str, category: str) -> bool:
    """Does this statement limit itself to rows the suite owns?"""
    low = text.lower()
    if category in ("TRUNCATE", "DROP", "ALTER", "GRANT/REVOKE", "CREATE"):
        return False                      # these are object-level by nature
    if category in ("DELETE", "UPDATE"):
        return " where " in low or low.rstrip().endswith("where")
    return True


def _commit_discipline(tree: ast.Module, src: str) -> dict:
    out = {"autocommit_true": [], "commit_calls": [], "rollback_calls": [],
           "rollback_in_finally": False, "isolation_autocommit": []}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == "autocommit"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value is True):
                    out["autocommit_true"].append(node.lineno)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "autocommit" and isinstance(kw.value, ast.Constant) \
                        and kw.value.value is True:
                    out["autocommit_true"].append(node.lineno)
            f = node.func
            if isinstance(f, ast.Attribute):
                if f.attr == "commit":
                    out["commit_calls"].append(node.lineno)
                elif f.attr == "rollback":
                    out["rollback_calls"].append(node.lineno)
                elif f.attr == "set_isolation_level":
                    out["isolation_autocommit"].append(node.lineno)
        if isinstance(node, ast.Try) and node.finalbody:
            for sub in ast.walk(ast.Module(body=node.finalbody, type_ignores=[])):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr == "rollback":
                    out["rollback_in_finally"] = True
    if "ISOLATION_LEVEL_AUTOCOMMIT" in src:
        out["isolation_autocommit"].append(-1)
    return out


def _gate(src: str, tree: ast.Module) -> dict:
    tokens = sorted({t for t in GATE_TOKENS if t in src})
    module_level_skip = False
    importorskip = False
    pytestmark_skipif = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "skip":
                for kw in node.keywords:
                    if kw.arg == "allow_module_level" and getattr(
                            kw.value, "value", False) is True:
                        module_level_skip = True
            if name == "importorskip":
                importorskip = True
            if name == "skipif":
                pytestmark_skipif = True
    return {"tokens": tokens, "module_level_skip": module_level_skip,
            "importorskip": importorskip, "skipif": pytestmark_skipif,
            "has_pytestmark": "pytestmark" in src}


def _orm_writes(src: str) -> list[str]:
    hints = []
    for token, label in [
        ("session.add(", "ORM session.add"),
        ("session.commit()", "ORM session.commit"),
        ("db.commit()", "ORM db.commit"),
        ("sa.text(", "sqlalchemy text()"),
        ("text(", "sqlalchemy text()"),
        ("session.execute(", "ORM session.execute"),
        ("session.delete(", "ORM session.delete"),
        (".bulk_save_objects", "ORM bulk_save"),
    ]:
        if token in src and label not in hints:
            hints.append(label)
    return hints


def main() -> int:
    targets = sorted(
        p for p in TESTS.glob("*.py")
        if any(t in p.read_text(encoding="utf-8", errors="replace")
               for t in GATE_TOKENS)
        or "psycopg2" in p.read_text(encoding="utf-8", errors="replace")
    )

    print(f"PG-related files under tests/ : {len(targets)}")
    print("=" * 78)

    summary: list[dict] = []

    for path in targets:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        gate = _gate(src, tree)
        commit = _commit_discipline(tree, src)

        ops: dict[str, list[tuple[int, str, bool]]] = {}
        for lineno, text in _sql_literals(tree):
            cat = _classify(text)
            if cat is None:
                continue
            ops.setdefault(cat, []).append(
                (lineno, ", ".join(_tables(text)) or "-", _is_scoped(text, cat)))

        mutating = {k: v for k, v in ops.items() if k in MUTATING}
        unscoped = [(k, ln, tb) for k, v in mutating.items()
                    for ln, tb, sc in v if not sc]
        orm = _orm_writes(src)

        persists = bool(commit["autocommit_true"] or commit["commit_calls"]
                        or commit["isolation_autocommit"])

        if not mutating and not orm:
            risk = "READ-ONLY"
        elif not persists:
            risk = "UNCOMMITTED"
        elif any(k in DESTRUCTIVE for k in mutating) or unscoped:
            risk = "DESTRUCTIVE-SHARED"
        elif mutating:
            risk = "COMMITTED-SCOPED"
        else:
            risk = "UNKNOWN-ORM"

        summary.append({
            "file": path.name, "risk": risk, "ops": mutating, "orm": orm,
            "gate": gate, "commit": commit, "unscoped": unscoped,
            "roles": ops.get("SET ROLE", []),
        })

        print(f"\n{path.name}")
        print(f"   RISK        : {risk}")
        print(f"   gate tokens : {gate['tokens'] or '(none)'}"
              f"   module-skip={gate['module_level_skip']}"
              f"  skipif={gate['skipif']}  importorskip={gate['importorskip']}")
        disc = []
        if commit["autocommit_true"]:
            disc.append(f"autocommit=True @ {commit['autocommit_true'][:6]}")
        if commit["isolation_autocommit"]:
            disc.append("ISOLATION_LEVEL_AUTOCOMMIT")
        if commit["commit_calls"]:
            disc.append(f"commit() x{len(commit['commit_calls'])}")
        if commit["rollback_calls"]:
            disc.append(f"rollback() x{len(commit['rollback_calls'])}")
        if commit["rollback_in_finally"]:
            disc.append("rollback in finally")
        print(f"   persistence : {'; '.join(disc) or 'no commit/autocommit found'}")
        if ops.get("SET ROLE"):
            print(f"   SET ROLE    : lines "
                  f"{[ln for ln, _, _ in ops['SET ROLE']]}")
        if orm:
            print(f"   ORM/dynamic : {', '.join(orm)}")
        if mutating:
            for cat in sorted(mutating):
                entries = mutating[cat]
                shown = "; ".join(
                    f"L{ln}{'' if sc else ' UNSCOPED'}->{tb}" for ln, tb, sc in
                    entries[:5])
                extra = f"  (+{len(entries) - 5} more)" if len(entries) > 5 else ""
                print(f"   {cat:14}: {len(entries):>3}  {shown}{extra}")
        else:
            print("   writes      : none found in literal SQL")

    print("\n" + "=" * 78)
    print("SUMMARY BY RISK")
    print("=" * 78)
    for label in ("DESTRUCTIVE-SHARED", "COMMITTED-SCOPED", "UNKNOWN-ORM",
                  "UNCOMMITTED", "READ-ONLY"):
        group = [s for s in summary if s["risk"] == label]
        print(f"\n{label}  ({len(group)})")
        for s in group:
            detail = ""
            if s["unscoped"]:
                cats = sorted({c for c, _, _ in s["unscoped"]})
                detail = f"   unscoped: {', '.join(cats)}"
            print(f"   {s['file']}{detail}")

    print("\n" + "=" * 78)
    print("TABLES TARGETED BY COMMITTED WRITES (shared-cluster blast radius)")
    print("=" * 78)
    tables: dict[str, set[str]] = {}
    for s in summary:
        if s["risk"] in ("DESTRUCTIVE-SHARED", "COMMITTED-SCOPED"):
            for cat, entries in s["ops"].items():
                for _, tb, _ in entries:
                    for name in [t.strip() for t in tb.split(",") if t.strip() != "-"]:
                        tables.setdefault(name, set()).add(f"{cat}")
    for name in sorted(tables):
        print(f"   {name:46} {', '.join(sorted(tables[name]))}")

    print("\n" + "=" * 78)
    print("ROLE-SWITCHING SUITES (the only ones that can test RLS on this target)")
    print("=" * 78)
    any_role = False
    for s in summary:
        if s["roles"]:
            any_role = True
            print(f"   {s['file']}: lines {[ln for ln, _, _ in s['roles']]}")
    if not any_role:
        print("   NONE -- no suite switches role, so every suite runs as the superuser")
        print("   table owner with BYPASSRLS, and none can evidence RLS enforcement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
