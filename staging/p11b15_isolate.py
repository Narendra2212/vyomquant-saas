"""Isolate why the patch was rejected: whitespace policy, or the '#' preamble?"""
import hashlib
import os
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PRIV = os.path.abspath("staging/p11b15_private.index")
patch = open("staging/p11b15_proposed.patch", "rb").read()
stripped = b"\n".join(l for l in patch.split(b"\n") if not l.startswith(b"#")) + b"\n"
open("staging/p11b15_proposed_clean.patch", "wb").write(stripped)

out = []
raw0 = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()


def trial(label, data, extra):
    if os.path.exists(PRIV):
        os.remove(PRIV)
    env = dict(os.environ, GIT_INDEX_FILE=PRIV)
    subprocess.run(["git", "read-tree", PR6], capture_output=True, env=env)
    r = subprocess.run(["git", "apply", "--cached", "--check"] + extra + ["-"],
                       capture_output=True, input=data, env=env)
    out.append("")
    out.append("%-52s rc=%d" % (label, r.returncode))
    for line in (r.stderr.decode(errors="replace")).splitlines():
        out.append("    %s" % line)
    if os.path.exists(PRIV):
        os.remove(PRIV)
    return r.returncode


trial("A. as emitted, --whitespace=error", patch, ["--whitespace=error"])
trial("B. as emitted, default whitespace", patch, [])
trial("C. '#' preamble stripped, --whitespace=error", stripped, ["--whitespace=error"])
rc_d = trial("D. '#' preamble stripped, --whitespace=nowarn", stripped, ["--whitespace=nowarn"])

out.append("")
out.append("CONCLUSION")
if rc_d == 0:
    out.append("  The proposed CHANGE is applicable. The rejection was caused by how the")
    out.append("  patch file was emitted, not by its content.")
else:
    out.append("  The change itself still does not apply; investigate further.")

out.append("")
out.append("  shared .git/index raw before: %s" % raw0)
out.append("  shared .git/index raw after : %s"
           % hashlib.sha256(open(".git/index", "rb").read()).hexdigest())

text = "\n".join(out)
open("staging/p11b15_isolate.txt", "w", encoding="utf-8").write(text)
print(text)
