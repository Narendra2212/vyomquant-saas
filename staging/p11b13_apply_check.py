"""Empirically test whether the exchange.py slot-guard hunks apply onto HEAD.

Uses a PRIVATE index file via GIT_INDEX_FILE. The shared .git/index is never opened for
writing; its checksum is printed before and after as proof.
"""
import hashlib
import os
import subprocess

PRIV = os.path.abspath("staging/p11b13_private.index")
SHARED = os.path.join(".git", "index")
TARGET = "backend_app/routers/exchange.py"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run(args, env=None, stdin=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, capture_output=True, env=e, input=stdin)


out = []
out.append("shared .git/index BEFORE: %s" % sha(SHARED))

if os.path.exists(PRIV):
    os.remove(PRIV)

priv_env = {"GIT_INDEX_FILE": PRIV}

# Private index == HEAD, so "does it apply to HEAD" is what gets tested.
r = run(["git", "read-tree", "HEAD"], env=priv_env)
out.append("read-tree HEAD into private index: rc=%d %s"
           % (r.returncode, r.stderr.decode(errors="replace").strip()))

# The UNSTAGED patch: index -> worktree. This is the slot-guard delta.
patch = run(["git", "diff", "--", TARGET]).stdout
out.append("unstaged patch bytes: %d" % len(patch))

r2 = run(["git", "apply", "--cached", "--recount", "--check", "-"],
         env=priv_env, stdin=patch)
out.append("")
out.append("git apply --cached --recount --check  (private index = HEAD)")
out.append("  rc = %d" % r2.returncode)
for line in (r2.stderr.decode(errors="replace") + r2.stdout.decode(errors="replace")).splitlines():
    out.append("  %s" % line)
out.append("")
out.append("  VERDICT: %s" % ("APPLIES onto HEAD" if r2.returncode == 0
                              else "DOES NOT APPLY onto HEAD -- entangled with the staged state"))

# Control: the same test for subscription_dependencies.py, which is ' M' only.
SD = "backend_app/core/subscription_dependencies.py"
patch_sd = run(["git", "diff", "--", SD]).stdout
r3 = run(["git", "apply", "--cached", "--recount", "--check", "-"],
         env=priv_env, stdin=patch_sd)
out.append("")
out.append("CONTROL - %s" % SD)
out.append("  unstaged patch bytes: %d" % len(patch_sd))
out.append("  rc = %d" % r3.returncode)
for line in (r3.stderr.decode(errors="replace")).splitlines():
    out.append("  %s" % line)
out.append("  VERDICT: %s" % ("APPLIES onto HEAD cleanly" if r3.returncode == 0
                              else "does not apply"))

if os.path.exists(PRIV):
    os.remove(PRIV)
out.append("")
out.append("private index removed: %s" % (not os.path.exists(PRIV)))
out.append("shared .git/index AFTER : %s" % sha(SHARED))

text = "\n".join(out)
open("staging/p11b13_apply_check.txt", "w", encoding="utf-8").write(text)
print(text)
