#!/usr/bin/env python3
"""PHASE 38 / PROTOTYPE ONLY -- evidence-reuse licensing + residual confirmations.

Phase 38 section 14 permits reusing a prior result only when every relevant input
is unchanged, verified by hash. This records the hash of each file whose finding is
being carried forward, and re-confirms the small number of claims that are cheap to
check directly. Reads only.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import socket
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent


def blob(rel: str) -> str:
    p = REPO / rel
    if not p.exists():
        return "ABSENT"
    data = p.read_bytes()
    head = b"blob %d\0" % len(data)
    return hashlib.sha1(head + data).hexdigest()


def git_blob(rel: str) -> str:
    r = subprocess.run(["git", "hash-object", rel], cwd=str(REPO),
                       capture_output=True)
    return r.stdout.decode().strip() or "ERR"


def grep(rel: str, pattern: str, flags=0):
    p = REPO / rel
    if not p.exists():
        return None
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace")
                             .splitlines(), 1):
        if re.search(pattern, line, flags):
            out.append((i, line.strip()))
    return out


def head(title):
    print("")
    print("=" * 94)
    print(title)
    print("=" * 94)


head("A. Hash provenance for findings carried forward from earlier phases")
CARRIED = [
    ("staging/phase18_release_gate.py", "5c70dbbfb882036badade2ff01b4bf687595ff4e",
     "verifier job-id vs display-name mismatch (9/9)"),
    ("staging/phase31_workflow_invariants.py", None, "the five invariants"),
    ("tests/property/test_tenant_isolation_matrix.py",
     "a60cd00c4558b386de61e045b08bf9b5ebfaf69d", "4 of 150 cells"),
    (".github/workflows/06-frontend-deploy.yml",
     "285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9", "frontend publishes with needs:=0"),
    ("staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md",
     "7e238620bc22f0ad246f0ebc373b7032c6b28ef0", "authoritative ledger"),
]
print("")
print("  %-56s %-9s %s" % ("file", "match", "finding"))
print("  %-56s %-9s %s" % ("-" * 56, "-" * 9, "-" * 24))
for rel, expected, label in CARRIED:
    actual = git_blob(rel)
    if expected is None:
        state = "n/a"
    elif actual == expected:
        state = "UNCHANGED"
    else:
        state = "CHANGED!"
    print("  %-56s %-9s %s" % (rel, state, label))
    if expected and actual != expected:
        print("      expected %s" % expected)
        print("      actual   %s" % actual)

head("B. Worker lifecycle -- are the two services registered?")
main_py = None
for cand in ("backend_app/main.py", "main.py", "backend_app/backend/main.py"):
    if (REPO / cand).exists():
        main_py = cand
        break
print("")
print("  application entrypoint: %s" % main_py)
for symbol in ("ExchangeReconciliationService", "MarketplaceExpiryWorker"):
    hits = grep(main_py, symbol) if main_py else None
    n = len(hits) if hits else 0
    print("    %-34s occurrences in entrypoint: %d  -> %s"
          % (symbol, n, "REGISTERED" if n else "NOT REGISTERED"))

head("C. Vault integration")
for rel, pat, label in (
    ("backend_app/core/state.py", r"safe_import\(", "safe_import calls"),
    ("backend_app/backend/portfolio_cache_updater.py", r"key_manager", "key_manager import"),
):
    hits = grep(rel, pat)
    print("")
    print("  %s -- %s" % (rel, label))
    if hits is None:
        print("     file ABSENT")
    elif not hits:
        print("     no match")
    else:
        for ln, text in hits[:6]:
            print("     :%-5d %s" % (ln, text[:96]))
km = REPO / "backend_app/backend/key_manager.py"
print("")
print("  backend_app/backend/key_manager.py exists: %s" % km.exists())

head("D. retryable flag -- is it used in control flow?")
total = 0
ctrl = 0
for path in (REPO / "backend_app").rglob("*.py"):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for line in text.splitlines():
        if "retryable" not in line:
            continue
        total += 1
        if re.search(r"\bif\b.*retryable|while\b.*retryable|retryable\s*(==|!=|is)\b", line):
            ctrl += 1
print("")
print("  lines mentioning `retryable` in backend_app : %d" % total)
print("  of those, used in a branch condition        : %d" % ctrl)
if total and not ctrl:
    print("  => the flag is recorded but never decides anything.")

head("E. Terraform roots and state posture")
roots = sorted({p.parent for p in REPO.rglob("*.tf")
                if ".terraform" not in p.parts and ".venv" not in p.parts})
print("")
for root in roots:
    rel = root.relative_to(REPO).as_posix()
    backend = None
    for tf in sorted(root.glob("*.tf")):
        text = tf.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'backend\s+"s3"\s*\{(.*?)\}', text, re.S)
        if m:
            backend = (tf.name, m.group(1))
            break
    print("  %s" % rel)
    if not backend:
        print("      backend: NONE -> local state")
    else:
        fname, body = backend
        fields = dict(re.findall(r"(\w+)\s*=\s*\"([^\"]+)\"", body))
        print("      backend s3 in %s" % fname)
        for key in ("bucket", "key", "region"):
            print("        %-16s %s" % (key, fields.get(key, "(absent)")))
        for key in ("encrypt", "dynamodb_table"):
            present = re.search(r"\b%s\s*=" % key, body) is not None
            print("        %-16s %s" % (key, "present" if present else "ABSENT"))

head("F. Service availability right now")
print("")
for label, port in (("Redis", 6379), ("PostgreSQL (51815)", 51815),
                    ("PostgreSQL (51805)", 51805), ("PostgreSQL (5432)", 5432)):
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        state = "OPEN"
    except OSError:
        state = "CLOSED"
    finally:
        s.close()
    print("  %-22s %s" % (label, state))
print("")
print("  Database-backed tenant isolation evidence therefore cannot be produced")
print("  this phase. Recorded as BLOCKED -- TOOL/SERVICE ABSENT, not as passing.")
