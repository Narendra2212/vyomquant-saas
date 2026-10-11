"""Make git say exactly why the hunk is rejected, and prove the old side byte-for-byte."""
import hashlib
import os
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
WF = ".github/workflows/01-pr-check.yml"
PRIV = os.path.abspath("staging/p11b15_private.index")
out = []

# 1. Does the patch's recorded pre-image hash match PR #6's blob?
patch = open("staging/p11b15_proposed.patch", encoding="utf-8").read()
for line in patch.splitlines():
    if line.startswith("index ") and "100644" in line:
        out.append("patch index line: %s" % line)

blob_sha = subprocess.run(["git", "rev-parse", f"{PR6}:{WF}"],
                          capture_output=True).stdout.decode().strip()
out.append("PR#6 blob sha for workflow: %s" % blob_sha)
out.append("  patch pre-image abbrev should prefix it: %s"
           % blob_sha.startswith("57e2cee8"))

# 2. Verbose apply, workflow section only.
start = patch.index("diff --git a/%s" % WF)
nxt = patch.find("diff --git a/tests", start)
wf_only = patch[start:nxt if nxt > 0 else len(patch)]
open("staging/p11b15_wf_only.patch", "w", encoding="utf-8", newline="").write(wf_only)

if os.path.exists(PRIV):
    os.remove(PRIV)
env = dict(os.environ, GIT_INDEX_FILE=PRIV)
subprocess.run(["git", "read-tree", PR6], capture_output=True, env=env)
r = subprocess.run(["git", "apply", "--cached", "--check", "-v",
                    "staging/p11b15_wf_only.patch"], capture_output=True, env=env)
out.append("")
out.append("verbose apply (workflow only): rc=%d" % r.returncode)
for line in (r.stderr.decode(errors="replace")).splitlines():
    out.append("  %s" % line)

# 3. Reconstruct the pre-image from the patch and diff it against the blob.
blob = subprocess.run(["git", "cat-file", "blob", blob_sha],
                      capture_output=True).stdout.decode("utf-8")
pre = []
in_hunk = False
for line in wf_only.splitlines():
    if line.startswith("@@"):
        in_hunk = True
        continue
    if not in_hunk:
        continue
    if line.startswith(" ") or line.startswith("-"):
        pre.append(line[1:])

out.append("")
out.append("pre-image context lines reconstructed from the patch: %d" % len(pre))
blob_lines = blob.splitlines()
out.append("first hunk starts at patch line 213 -> blob lines 212..219:")
for i in range(211, 220):
    if i < len(blob_lines):
        out.append("  blob[%d] = %r" % (i + 1, blob_lines[i]))
out.append("")
out.append("patch pre-image lines for that hunk:")
for l in pre[:8]:
    out.append("  %r" % l)

if os.path.exists(PRIV):
    os.remove(PRIV)

text = "\n".join(out)
open("staging/p11b15_verbose.txt", "w", encoding="utf-8").write(text)
print(text)
