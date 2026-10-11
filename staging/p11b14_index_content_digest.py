"""Content-only digest of the shared index.

The raw `.git/index` byte checksum includes per-entry stat data (mtime, ctime, inode,
size), so git rewrites it whenever it refreshes those caches -- which `git status` and
`git diff` both do. A changed byte checksum therefore does NOT prove staged content moved.

This hashes only (mode, blob sha, stage, path) for every entry, which is the thing that
actually matters when the question is "did another workstream's staged work change?".
Read-only: `git ls-files --stage` does not write.
"""
import hashlib
import subprocess

raw = subprocess.run(["git", "ls-files", "--stage"], capture_output=True).stdout
lines = sorted(raw.decode("utf-8", errors="replace").splitlines())

h = hashlib.sha256()
for line in lines:
    h.update(line.encode("utf-8"))
digest = h.hexdigest()

file_sha = hashlib.sha256(open(".git/index", "rb").read()).hexdigest()

out = [
    "shared index -- two different measurements",
    "",
    "  raw file checksum (includes stat data) : %s" % file_sha,
    "  CONTENT digest (mode/sha/stage/path)   : %s" % digest,
    "",
    "  tracked entries in index : %d" % len(lines),
    "",
    "Use the CONTENT digest to decide whether concurrent staged work changed.",
    "Use the raw checksum only to prove THIS session did not write to the index",
    "within a single uninterrupted stretch of work.",
]
text = "\n".join(out)
open("staging/p11b14_index_digest.txt", "w", encoding="utf-8").write(text)
print(text)
