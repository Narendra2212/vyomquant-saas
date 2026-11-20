"""Phase 2 inspection: is the slot-lock implementation isolable from concurrent work?

Read-only. Writes a report to staging/p11b13_phase2.txt.
"""
import re
import subprocess

FILES = [
    "backend_app/core/subscription_dependencies.py",
    "backend_app/routers/exchange.py",
]

SLOT_SYMBOLS = [
    "exchange_connection_slot_guard",
    "_slot_lock_key",
    "_acquire_slot_lock",
    "_release_slot_lock",
    "check_exchange_connection_slot",
]

out = []


def sh(args):
    r = subprocess.run(args, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace")


out.append("=" * 78)
out.append("PHASE 2 - SLOT-LOCK DEPENDENCY ISOLATION ANALYSIS")
out.append("=" * 78)

out.append("")
out.append("--- git status (XY) ---")
out.append(sh(["git", "status", "--porcelain", "--"] + FILES).rstrip())

out.append("")
out.append("--- unstaged numstat (index -> worktree) ---")
out.append(sh(["git", "diff", "--numstat", "--"] + FILES).rstrip() or "(none)")

out.append("")
out.append("--- staged numstat (HEAD -> index) ---")
out.append(sh(["git", "diff", "--cached", "--numstat", "--"] + FILES).rstrip() or "(none)")

out.append("")
out.append("--- total vs HEAD (HEAD -> worktree) ---")
out.append(sh(["git", "diff", "HEAD", "--numstat", "--"] + FILES).rstrip() or "(none)")

for f in FILES:
    out.append("")
    out.append("=" * 78)
    out.append(f)
    out.append("=" * 78)

    head = sh(["git", "show", "HEAD:" + f])
    try:
        work = open(f, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        out.append("  cannot read worktree copy: %s" % exc)
        continue

    out.append("  lines: HEAD=%d  worktree=%d" % (
        len(head.splitlines()), len(work.splitlines())))
    out.append("  slot-lock symbols:")
    for sym in SLOT_SYMBOLS:
        pat = r"\b" + re.escape(sym) + r"\b"
        out.append("    %-34s HEAD=%-7s worktree=%s" % (
            sym,
            "PRESENT" if re.search(pat, head) else "absent",
            "PRESENT" if re.search(pat, work) else "absent",
        ))

    # Classify every changed hunk as slot-lock-related or not.
    diff = sh(["git", "diff", "-U0", "HEAD", "--", f])
    hunks = []
    cur = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            if cur:
                hunks.append(cur)
            cur = {"header": line, "body": []}
        elif cur is not None and line[:1] in "+-" and not line.startswith(("+++", "---")):
            cur["body"].append(line)
    if cur:
        hunks.append(cur)

    related, unrelated = [], []
    for h in hunks:
        text = "\n".join(h["body"])
        tag = related if any(s in text for s in SLOT_SYMBOLS) else unrelated
        tag.append(h)

    out.append("")
    out.append("  hunks vs HEAD: %d total | %d mention a slot-lock symbol | %d do NOT"
               % (len(hunks), len(related), len(unrelated)))
    out.append("")
    out.append("  HUNKS NOT MENTIONING ANY SLOT-LOCK SYMBOL (concurrent work):")
    if not unrelated:
        out.append("    (none)")
    for h in unrelated:
        first = next((b for b in h["body"] if b[1:].strip()), "")
        out.append("    %-28s %s" % (h["header"][:28], first.strip()[:90]))

open("staging/p11b13_phase2.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
