"""Detail the slot-lock hunks and check whether the non-slot hunks serve them."""
import re
import subprocess

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


SYMS = ["exchange_connection_slot_guard", "_slot_lock_key",
        "_acquire_slot_lock", "_release_slot_lock"]

# --- subscription_dependencies.py: what are the two non-slot hunks? ---
out.append("=" * 78)
out.append("subscription_dependencies.py - the two non-slot-lock hunks, verbatim")
out.append("=" * 78)
d = sh(["git", "diff", "-U1", "HEAD", "--", "backend_app/core/subscription_dependencies.py"])
cur = None
for line in d.splitlines():
    if line.startswith("@@"):
        cur = line
        body = []
    elif cur and line[:1] in "+-" and not line.startswith(("+++", "---")):
        body.append(line)
        if len(body) < 12 and not any(s in line for s in SYMS):
            out.append("  %s  %s" % (cur[:30], line[:100]))

# Does the slot-lock code actually use asyncio / random?
work = open("backend_app/core/subscription_dependencies.py",
            encoding="utf-8", errors="replace").read()
start = work.find("def _slot_lock_key")
end = work.find("\nasync def check_metered", start)
block = work[start:end if end > start else start + 14000]
out.append("")
out.append("  Does the slot-lock block reference the newly imported names?")
for name in ("asyncio.", "random."):
    out.append("    %-10s used in slot-lock block: %s" % (name, name in block))

# --- exchange.py: the three slot-lock hunks ---
out.append("")
out.append("=" * 78)
out.append("routers/exchange.py - hunks that DO mention a slot-lock symbol")
out.append("=" * 78)
d2 = sh(["git", "diff", "-U0", "HEAD", "--", "backend_app/routers/exchange.py"])
cur, body = None, []
hunks = []
for line in d2.splitlines():
    if line.startswith("@@"):
        if cur:
            hunks.append((cur, body))
        cur, body = line, []
    elif cur and line[:1] in "+-" and not line.startswith(("+++", "---")):
        body.append(line)
if cur:
    hunks.append((cur, body))
for header, body in hunks:
    text = "\n".join(body)
    if any(s in text for s in SYMS):
        out.append("")
        out.append("  " + header[:60])
        for b in body[:14]:
            out.append("    " + b[:108])

# --- which other workstreams are named in exchange.py's worktree copy? ---
out.append("")
out.append("=" * 78)
out.append("routers/exchange.py - other workstream markers present in the worktree copy")
out.append("=" * 78)
wk = open("backend_app/routers/exchange.py", encoding="utf-8", errors="replace").read()
for tag in sorted(set(re.findall(r"\b(?:AUTH|CONN|QUOTA|BUG|API|KILL)-\d+", wk))):
    out.append("    %-10s occurrences: %d" % (tag, len(re.findall(re.escape(tag), wk))))

open("staging/p11b13_hunks.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
