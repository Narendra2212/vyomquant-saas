"""Print the FAILURES section of the Phase 6 full run verbatim.

`--tb=line` reports the line where the exception was RAISED, which for an integration-style
test is often inside `backend_app/` rather than in the test file - so grepping for the test's
own name finds only the summary line and not the cause. Printing the section in order, beside
the ordered FAILED ids, pairs them up.
"""
from __future__ import annotations

import pathlib
import re

RAW = pathlib.Path(__file__).resolve().parent / "fullreg_raw_FULLREG_P6.txt"

text = RAW.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

start = next(
    (i for i, line in enumerate(lines) if re.match(r"^=+ FAILURES =+$", line.strip())),
    None,
)
end = next(
    (
        i
        for i, line in enumerate(lines)
        if "short test summary info" in line
    ),
    len(lines),
)

print(f"FAILURES section: lines {start} .. {end}")
if start is None:
    print("NO FAILURES SECTION FOUND")
else:
    for line in lines[start:end]:
        if line.strip():
            print(f"  {line}")

print("\nFAILED ids, in the order pytest listed them:")
for line in lines[end:]:
    if line.startswith("FAILED "):
        print(f"  {line}")
