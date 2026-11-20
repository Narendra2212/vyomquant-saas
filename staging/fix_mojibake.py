"""Repair UTF-8 text that was decoded as cp1252 and re-encoded as UTF-8.

PowerShell's `Set-Content -Encoding utf8` round-trip mangled the em dashes and other
non-ASCII punctuation in one document: a UTF-8 em dash (E2 80 94) read as three cp1252
characters and written back as nine bytes, which renders as `â€"`.

The repair is the inverse: encode the mangled text as cp1252 to recover the original
bytes, then decode those bytes as UTF-8. Applied only to the specific sequences, so a
file containing legitimate `Ã` text is not touched.

    python staging/fix_mojibake.py <file> [<file> ...]
"""

from __future__ import annotations

import pathlib
import sys

#: The marker that says a file has been through the bad round-trip. `â` followed by a
#: character that cp1252 maps from the 0x80-0x9F range is not something this project's
#: prose contains legitimately.
MARKER = "\u00e2"

#: A first attempt at this file hardcoded `"\u00e2\u0080\u0094"` for a mangled em dash.
#: That is wrong: cp1252 maps byte 0x80 to U+20AC (the euro sign), not to U+0080, so the
#: sequence to look for is `â€”` = U+00E2 U+20AC U+201D. Rather than enumerate the
#: mappings by hand and get them wrong a second time, the repair is now the exact inverse
#: of the corruption -- encode as cp1252 to recover the original bytes, decode those as
#: UTF-8 -- which handles every character without a table.


#: Characters this project's prose actually uses that are outside ASCII. The mangled form
#: of each is DERIVED rather than typed, which is the whole point: typing it by hand got
#: the codepoints wrong once already.
INTERESTING = (
    "\u2014",  # em dash
    "\u2013",  # en dash
    "\u2018", "\u2019",  # single quotes
    "\u201c", "\u201d",  # double quotes
    "\u2192",  # right arrow
    "\u00a0",  # non-breaking space
    "\u2026",  # ellipsis
    "\u00f7", "\u00d7",
)


def _mangled(char: str) -> str | None:
    """What `char` looks like after UTF-8 bytes were decoded as cp1252.

    Exactly the corruption being undone, computed rather than guessed. Returns None for
    characters cp1252 cannot represent the bytes of, which simply means that character
    cannot have been mangled this way.
    """
    try:
        return char.encode("utf-8").decode("cp1252")
    except UnicodeDecodeError:
        return None


def repair(text: str) -> str | None:
    """Return the repaired text, or None if there was nothing to repair.

    A WHOLE-FILE cp1252 round-trip was the first attempt and it does not work here: the
    file is MIXED -- some em dashes are mangled and some are correct -- and it also
    contains characters cp1252 cannot encode at all, so `text.encode("cp1252")` raises
    and the repair silently did nothing. Targeted replacement handles the mixed case,
    which is the realistic one when only some edits went through PowerShell.
    """
    if MARKER not in text:
        return None
    out = text
    for char in INTERESTING:
        bad = _mangled(char)
        if bad and bad in out:
            out = out.replace(bad, char)
    return out if out != text else None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    repaired_files = 0
    for name in sys.argv[1:]:
        path = pathlib.Path(name)
        if not path.exists():
            print(f"missing: {path}")
            return 1
        text = path.read_text(encoding="utf-8")
        fixed = repair(text)
        if fixed is None:
            print(f"{path}: nothing to repair")
            continue
        path.write_text(fixed, encoding="utf-8")
        before = text.count(MARKER)
        after = fixed.count(MARKER)
        print(f"{path}: repaired -- occurrences of {MARKER!r} {before} -> {after}")
        repaired_files += 1

    print(f"files repaired: {repaired_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
