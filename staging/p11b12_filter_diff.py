"""Reduce the A12 diff to its executable lines, so the report can quote code not prose."""
raw = open("staging/p11b12_diff.txt", "rb").read()
enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
text = raw.decode(enc, errors="replace")

TRIPLE = '"""'
SKIP_PREFIXES = ("#", "*", ":", TRIPLE)

keep = []
for line in text.splitlines():
    if line.startswith("@@"):
        keep.append(line)
        continue
    if line[:1] in "+-" and not line.startswith(("+++", "---")):
        body = line[1:].strip()
        if not body:
            continue
        if body.startswith(SKIP_PREFIXES) or body.endswith(TRIPLE):
            continue
        keep.append(line)

open("staging/p11b12_diff_code.txt", "w", encoding="utf-8").write("\n".join(keep))
print(len(keep), "executable diff lines")
