"""Determine which serialisation of `git ls-files --stage` reproduces the A14 digest.

Read-only. Resolves whether the A25 exit mismatch is a real content change or a
difference in how this session joined the lines.
"""
import hashlib
import subprocess

TARGET = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"

raw = subprocess.run(
    ["git", "ls-files", "--stage"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
).stdout

lines = [l for l in raw.splitlines() if l]
srt = sorted(lines)

variants = {
    "sorted,   LF join, trailing LF": "\n".join(srt) + "\n",
    "sorted,   LF join, no trailing": "\n".join(srt),
    "sorted,   CRLF join, trail CRLF": "\r\n".join(srt) + "\r\n",
    "sorted,   CRLF join, no trail": "\r\n".join(srt),
    "unsorted, LF join, trailing LF": "\n".join(lines) + "\n",
    "unsorted, LF join, no trailing": "\n".join(lines),
    "raw stdout verbatim": raw,
    "sorted, each line + LF": "".join(l + "\n" for l in srt),
}

out = []
out.append(f"target digest : {TARGET}")
out.append(f"entries       : {len(lines)}")
out.append(f"already sorted: {lines == srt}")
out.append("")
hit = None
for name, payload in variants.items():
    d = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    mark = "  <== MATCH" if d == TARGET else ""
    if d == TARGET:
        hit = name
    out.append(f"  {d}  {name}{mark}")

out.append("")
out.append(f"RESULT: {'reproduced by -> ' + hit if hit else 'NO VARIANT REPRODUCES THE TARGET'}")

text = "\n".join(out) + "\n"
with open("staging/p11b25_digest_probe.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)

# ──────────────────────────────────────────────────────────────────────────────
# Round 2. The entry script (staging/p11b25_rebase.py L31-33) used:
#     h = sha256(); for l in sorted(...splitlines()): h.update(l.encode())
# i.e. sorted lines concatenated with NO separator. That variant was missing
# from round 1, which is why nothing matched.
# ──────────────────────────────────────────────────────────────────────────────
import hashlib as _h
import subprocess as _s

_raw = _s.run(["git", "ls-files", "--stage"], capture_output=True, text=True,
              encoding="utf-8", errors="replace").stdout

_h2 = _h.sha256()
for _l in sorted(_raw.splitlines()):
    _h2.update(_l.encode("utf-8"))
_entry_method = _h2.hexdigest()

_h3 = _h.sha256()
for _l in _raw.splitlines():
    _h3.update(_l.encode("utf-8"))
_entry_unsorted = _h3.hexdigest()

_r2 = [
    "",
    "=" * 78,
    "ROUND 2 — entry-script serialisation (sorted, concatenated, no separator)",
    "=" * 78,
    f"  {_entry_method}  sorted, no separator   {'<== MATCHES A24/A25-entry TARGET' if _entry_method == TARGET else ''}",
    f"  {_entry_unsorted}  unsorted, no separator {'<== MATCH' if _entry_unsorted == TARGET else ''}",
    "",
    "CROSS-CHECK against W1D11 §7.1 'Digest A' (sha256, raw `git ls-files -s`):",
    f"  recorded by that session : 98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704",
    f"  reproduced here          : {_h.sha256(chr(10).join([l for l in _raw.splitlines() if l]).encode()).hexdigest()}",
    "",
    f"VERDICT: staged content {'UNCHANGED (two independent digest methods both reproduce)' if _entry_method == TARGET else 'NEEDS FURTHER INVESTIGATION'}",
]
_text2 = "\n".join(_r2) + "\n"
with open("staging/p11b25_digest_probe.txt", "a", encoding="utf-8") as _fh:
    _fh.write(_text2)
print(_text2)
