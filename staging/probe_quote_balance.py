"""Find where single-quote pairing goes wrong in a .sql file after comment stripping.

Written because `tests/test_tenant02_fee_records_rls_postgres.py::_executable_sql` blanked
real SQL, which means quote pairing shifted somewhere. Guessing at the cause from a
truncated assertion message is slower than measuring it.
"""
from __future__ import annotations

import pathlib
import re
import sys

LINE = re.compile(r"--[^\n]*")
BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)

path = pathlib.Path(sys.argv[1])
raw = path.read_text(encoding="utf-8")
# Blank comments but keep line structure so reported line numbers match the file.
text = BLOCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), raw)
text = LINE.sub(lambda m: " " * len(m.group(0)), text)

print(f"FILE={path}")
print(f"BYTES={len(raw)}")

total = 0
for lineno, line in enumerate(text.splitlines(), start=1):
    n = line.count("'")
    if n:
        total += n
        print(f"L{lineno:<5} quotes={n:<3} {line.strip()[:110]}")
print(f"TOTAL_QUOTES_AFTER_COMMENT_STRIP={total}  (odd total means an unbalanced literal)")

# Walk it the way the stripper does and report the offset where a literal is left open.
i = 0
n = len(text)
depth_open_at = None
while i < n:
    if text[i] == "'":
        start = i
        i += 1
        closed = False
        while i < n:
            if text[i] == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                i += 1
                closed = True
                break
            i += 1
        if not closed:
            depth_open_at = start
            break
    else:
        i += 1
if depth_open_at is not None:
    line = text.count("\n", 0, depth_open_at) + 1
    print(f"UNCLOSED_LITERAL_OPENS_AT offset={depth_open_at} line={line}")
    print(f"CONTEXT={raw[max(0, depth_open_at - 120):depth_open_at + 120]!r}")
else:
    print("ALL_LITERALS_CLOSE")

# Dollar-quoted bodies: count them and their quotes.
for m in re.finditer(r"\$\$", text):
    pass
print(f"DOLLAR_MARKERS={len(re.findall(r'[$][$]', text))}")
