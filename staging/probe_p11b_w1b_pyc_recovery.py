"""Recover what the two MISSING `staging/` modules actually contained, from their `.pyc`.

PHASE 11B / WORKSTREAM 1B — read-only evidence gathering for the collection blocker.

WHY THIS EXISTS
---------------
`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` aborts collection because
`staging/probe_pg_find.py` is absent, and `git log --all` shows it was NEVER committed, so
there is no version to check out. The W1B brief forbids recreating it from its filename
alone.

But `staging/__pycache__/probe_pg_find.cpython-312.pyc` and
`staging/__pycache__/pg_keepalive_r1.cpython-312.pyc` both survive. A `.pyc` is not source
-- comments and exact formatting are gone -- but it is a primary record of the module that
really ran on this machine: the loader's header carries the source file's size and mtime,
and the marshalled code object carries every name, signature, default, docstring and
constant.

So this probe answers three questions with evidence rather than inference:

  1. DID the source exist, and how big was it? (pyc header)
  2. WHAT was its public surface? (module-level defs, their signatures and docstrings)
  3. Does that surface MATCH what the test file demands, or only overlap with it?

STRICTLY READ-ONLY. It opens two `.pyc` files, unmarshals them, and prints. It imports
nothing from the repository, executes no recovered code, starts no server, contacts no
database, and writes no file.
"""

from __future__ import annotations

import datetime as _dt
import dis
import hashlib
import importlib.util
import marshal
import pathlib
import struct
import sys
import types

REPO = pathlib.Path(__file__).resolve().parents[1]
CACHE = REPO / "staging" / "__pycache__"

TARGETS = [
    ("probe_pg_find", CACHE / "probe_pg_find.cpython-312.pyc",
     REPO / "staging" / "probe_pg_find.py"),
    ("pg_keepalive_r1", CACHE / "pg_keepalive_r1.cpython-312.pyc",
     REPO / "staging" / "pg_keepalive_r1.py"),
]


def _hdr(raw: bytes) -> dict:
    """Decode the 16-byte PEP 552 header."""
    magic, flags = struct.unpack("<4sI", raw[:8])
    out = {
        "magic": magic.hex(),
        "magic_matches_running_interpreter": magic == importlib.util.MAGIC_NUMBER,
        "flags": flags,
        "mode": "hash-based" if flags & 0b1 else "timestamp",
    }
    if flags & 0b1:
        out["source_hash"] = raw[8:16].hex()
        out["check_source"] = bool(flags & 0b10)
    else:
        mtime, size = struct.unpack("<II", raw[8:16])
        out["source_mtime_epoch"] = mtime
        out["source_mtime"] = _dt.datetime.fromtimestamp(mtime).isoformat(sep=" ")
        out["source_size_bytes"] = size
    return out


def _sig(code: types.CodeType) -> str:
    n = code.co_argcount
    nk = code.co_kwonlyargcount
    names = list(code.co_varnames[: n + nk])
    pos, kw = names[:n], names[n:]
    parts = list(pos)
    flags = code.co_flags
    star = None
    idx = n + nk
    if flags & 0x04:                      # CO_VARARGS
        star = code.co_varnames[idx]
        idx += 1
    if star:
        parts.append("*" + star)
    elif kw:
        parts.append("*")
    parts.extend(kw)
    if flags & 0x08:                      # CO_VARKEYWORDS
        parts.append("**" + code.co_varnames[idx])
    return f"{code.co_name}({', '.join(parts)})"


def _doc(code: types.CodeType) -> str | None:
    """A function/class docstring is co_consts[0] when present."""
    if code.co_consts and isinstance(code.co_consts[0], str):
        return code.co_consts[0]
    return None


def _first(text: str | None) -> str:
    if not text:
        return "-"
    return text.strip().splitlines()[0][:96]


def _module_constants(code: types.CodeType) -> list[tuple[str, object]]:
    """Module-level `NAME = <literal>` pairs, read off the bytecode.

    Only literal stores are recoverable this way, which is exactly what matters here:
    thresholds, default directory names and marker tuples.
    """
    found: list[tuple[str, object]] = []
    pending: object = None
    have = False
    for ins in dis.get_instructions(code):
        if ins.opname in ("LOAD_CONST",):
            pending, have = ins.argval, True
        elif ins.opname == "STORE_NAME" and have:
            if not isinstance(pending, types.CodeType):
                found.append((ins.argval, pending))
            pending, have = None, False
        elif ins.opname in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_MAP", "CALL",
                            "BINARY_OP", "LOAD_NAME", "LOAD_ATTR"):
            have = False
    return found


def _report(label: str, pyc: pathlib.Path, src: pathlib.Path) -> None:
    print("=" * 78)
    print(f"MODULE  {label}")
    print("=" * 78)
    print(f"  source path          {src.relative_to(REPO)}")
    print(f"  source present now   {src.exists()}")
    print(f"  bytecode path        {pyc.relative_to(REPO)}")
    if not pyc.exists():
        print("  bytecode present     False  -- nothing to recover")
        return
    raw = pyc.read_bytes()
    print(f"  bytecode present     True ({len(raw)} bytes)")
    print(f"  bytecode sha256      {hashlib.sha256(raw).hexdigest()}")
    stat = pyc.stat()
    print(f"  bytecode mtime       {_dt.datetime.fromtimestamp(stat.st_mtime)}")

    h = _hdr(raw)
    print("  -- pyc header --")
    for k, v in h.items():
        print(f"     {k:38} {v}")

    try:
        code = marshal.loads(raw[16:])
    except Exception as exc:                                  # pragma: no cover
        print(f"  !! cannot unmarshal: {type(exc).__name__}: {exc}")
        print("     (the bytecode was written by a different interpreter version)")
        return

    print(f"  -- module docstring ({len(_doc(code) or '')} chars) --")
    for line in (_doc(code) or "").splitlines():
        print(f"     | {line}")

    consts = _module_constants(code)
    print(f"  -- module-level literal constants ({len(consts)}) --")
    for name, value in consts:
        if name == "__doc__":
            continue
        print(f"     {name:34} = {value!r}")

    children = [c for c in code.co_consts if isinstance(c, types.CodeType)]
    print(f"  -- module-level defs ({len(children)}) --")
    for child in children:
        print(f"     {_sig(child)}")
        print(f"         doc: {_first(_doc(child))}")

    print(f"  -- names referenced ({len(code.co_names)}) --")
    print("     " + ", ".join(sorted(set(code.co_names))))

    nested = []
    for child in children:
        for g in child.co_consts:
            if isinstance(g, types.CodeType):
                nested.append((child.co_name, g))
    if nested:
        print(f"  -- nested defs ({len(nested)}) --")
        for parent, g in nested:
            print(f"     {parent}.{_sig(g)}")

    # Every string literal anywhere in the module, which is where the refusal messages,
    # SQL and CLI help text live.
    strings: set[str] = set()

    def walk(c: types.CodeType) -> None:
        for k in c.co_consts:
            if isinstance(k, str):
                strings.add(k)
            elif isinstance(k, types.CodeType):
                walk(k)
            elif isinstance(k, tuple):
                for item in k:
                    if isinstance(item, str):
                        strings.add(item)
    walk(code)
    interesting = sorted(s for s in strings if len(s) > 3 and "\n" not in s)
    print(f"  -- single-line string literals ({len(interesting)}) --")
    for s in interesting:
        print(f"     {s!r}")
    print()


def main() -> int:
    print(f"interpreter          {sys.version.split()[0]}")
    print(f"MAGIC_NUMBER         {importlib.util.MAGIC_NUMBER.hex()}")
    print(f"repo                 {REPO}")
    print()
    for label, pyc, src in TARGETS:
        _report(label, pyc, src)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
