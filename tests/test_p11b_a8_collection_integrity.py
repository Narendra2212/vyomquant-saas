"""No single test file may abort collection for the whole repository.

PHASE 11B-A8 — regression coverage for P11-H15 / W1A-01 / W1B-02.

THE DEFECT THIS LOCKS DOWN
--------------------------
`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` loaded
`staging/probe_pg_find.py` by path at MODULE scope. When that file went missing the
`FileNotFoundError` was raised during collection rather than inside a test, pytest
answered with `Interrupted: 1 error during collection`, and it discarded the entire
session -- so ~14.4k unrelated tests became unrunnable because one tooling script was
absent. Recorded as P11-H15 (`staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md`), as W1A-01
(`staging/PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md`) and as W1B-02
(`staging/PHASE11B_W1B_BASELINE.md`).

W1B fixed the one occurrence with a conditional module-level skip, and raised W1B-02 to
say the shape is structural rather than a one-off: `staging/` is not a package, so
by-path loading at module scope is the established pattern across the P10H/I/J suites,
and any of them would abort collection the same way if its target vanished. W1B
recommended "the same conditional-skip guard shape wherever a suite loads a `staging/`
path at import time" but had no way to enforce it. This file is that enforcement.

THE INVARIANT
-------------
For every module-scope load of a path under `tests/**`, either

  * the target exists, or
  * the module guards itself with `pytest.skip(..., allow_module_level=True)` BEFORE the
    load, so the outcome is a named skip instead of a session-wide abort.

A guarded skip is NOT a pass, and this file does not claim otherwise -- it asserts only
that one missing file cannot take the suite down. `_module_scope_loads_of_missing_paths`
reports every guarded case so the suspended coverage stays visible rather than silent.

WHY AST AND NOT A pytest RUN
----------------------------
The direct check is `pytest --collect-only` over `tests/`, which takes ~2 minutes here
and, being the thing under test, cannot run inside itself. AST parsing reaches the same
property in about a second, imports nothing, and -- unlike a collection run, which stops
at the FIRST error -- reports every offender in one pass.

Derived from `staging/probe_p11b_w1b_import_time_paths.py` (W1B, read-only), with one
correction: that probe counted loads inside test bodies as collection-fatal, which they
are not. Only genuinely module-scope loads abort collection, so only those are asserted
on here; in-body loads are reported separately as deferred.

STRICTLY READ-ONLY. Parses text and stats paths. Imports no repository code, starts no
server, contacts no database, writes no file.
"""

from __future__ import annotations

import ast
import functools
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"

#: The by-path loader helpers these suites use, plus the stdlib call underneath them.
LOADER_CALLS = {"spec_from_file_location", "_load", "_load_module", "load_module_by_path"}
#: Reading a source file for an AST/contract assertion fails the same way a load does.
READ_CALLS = {"read_text", "read_bytes", "open"}


def _const_paths(tree: ast.Module, repo: pathlib.Path = REPO) -> dict[str, pathlib.Path]:
    """Module-level names bound to a concrete path.

    Handles the two shapes used across these suites::

        REPO = pathlib.Path(__file__).resolve().parents[1]
        BOOTSTRAP_PATH = REPO / "staging" / "bootstrap_pg.py"

    Anything else resolves to nothing and is skipped rather than guessed at -- a name
    this function cannot resolve is not reported as an offender.
    """
    env: dict[str, pathlib.Path] = {}

    def resolve(node: ast.expr) -> pathlib.Path | None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = resolve(node.left)
            if left is None:
                return None
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
                return left / node.right.value
            return None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        return None

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        src = ast.unparse(stmt.value)
        # `pathlib.Path(__file__).resolve().parents[N]` is the repository root in every
        # one of these files; parents[1] from tests/ and from staging/ both land there.
        if "parents[" in src and "__file__" in src:
            env[target.id] = repo
            continue
        got = resolve(stmt.value)
        if got is not None:
            env[target.id] = got
    return env


def _module_scope_statements(tree: ast.Module):
    """Statements that execute at import, descending into `if` / `try` / `with` only.

    A load inside a function or class body does not run at import, so it cannot abort
    collection. A load inside a module-level `if` or `try` very much does -- and the
    guard pattern under test is itself a module-level `if`, so this has to descend.
    """
    stack = list(tree.body)
    while stack:
        stmt = stack.pop()
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(stmt, field, None) or [])


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _path_arguments(call: ast.Call, env: dict[str, pathlib.Path]) -> list[pathlib.Path]:
    """Resolved paths this call is pointed at, from its args, kwargs or receiver."""
    found: list[pathlib.Path] = []
    candidates = list(call.args) + [kw.value for kw in call.keywords]
    if isinstance(call.func, ast.Attribute):
        candidates.append(call.func.value)
    for arg in candidates:
        if isinstance(arg, ast.Name) and arg.id in env:
            found.append(env[arg.id])
        elif isinstance(arg, ast.BinOp):
            text = ast.unparse(arg)
            for name, base in env.items():
                if text.startswith(name + " /"):
                    tail = [
                        node.value
                        for node in ast.walk(arg)
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    ]
                    found.append(base.joinpath(*tail))
                    break
    return found


def _module_level_guard_lineno(tree: ast.Module) -> int | None:
    """Line of a module-level `pytest.skip(..., allow_module_level=True)`, if any.

    `allow_module_level=True` is required: a plain `pytest.skip()` at module scope is an
    error, not a guard, so only the real form counts.
    """
    for stmt in _module_scope_statements(tree):
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call) or _call_name(node) != "skip":
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "allow_module_level"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    return node.lineno
    return None


def _test_files() -> list[pathlib.Path]:
    return sorted(p for p in TESTS.rglob("*.py") if p.is_file())


def _scan(root: pathlib.Path, repo: pathlib.Path):
    """`(unguarded, guarded)` for every module-scope by-path load under `root`.

    Takes its roots as arguments so the detector itself can be exercised against a
    synthetic tree -- see `test_the_sweep_detects_an_unguarded_load_of_a_missing_path`.
    A gate that has never been observed failing is not a gate.
    """
    unguarded, guarded = [], []
    for path in sorted(p for p in root.rglob("*.py") if p.is_file()):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "parents[" not in source or "__file__" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        env = _const_paths(tree, repo)
        if not env:
            continue
        guard_lineno = _module_level_guard_lineno(tree)
        for stmt in _module_scope_statements(tree):
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name not in LOADER_CALLS and name not in READ_CALLS:
                    continue
                for target in _path_arguments(node, env):
                    if target.exists() or target.suffix != ".py":
                        continue
                    shown = (
                        target.relative_to(repo).as_posix()
                        if repo in target.parents
                        else target.as_posix()
                    )
                    record = (
                        f"{path.relative_to(repo).as_posix()}:{node.lineno} "
                        f"{name}() -> {shown}"
                    )
                    if guard_lineno is not None and guard_lineno < node.lineno:
                        guarded.append(record)
                    else:
                        unguarded.append(record)
    return tuple(unguarded), tuple(guarded)


@functools.lru_cache(maxsize=1)
def _parsed_candidates() -> tuple[tuple[pathlib.Path, ast.Module], ...]:
    """Parse only the files that could possibly resolve a module-level path constant.

    `_const_paths` can seed its environment one way only: a module-level assignment whose
    value contains both `parents[` and `__file__`. A file without those two substrings
    therefore yields an empty environment and contributes nothing, so parsing it is
    wasted work -- and parsing all 508 files under `tests/` costs ~16 s, which is too
    much to pay three times over for a CI integrity gate. The prefilter is a strict
    superset of the files that can produce a finding, so it changes cost and not results.

    Cached because all three tests in this file need the same sweep.
    """
    candidates = []
    for path in _test_files():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "parents[" not in source or "__file__" not in source:
            continue
        try:
            candidates.append((path, ast.parse(source)))
        except SyntaxError:
            continue
    return tuple(candidates)


@functools.lru_cache(maxsize=1)
def _module_scope_loads_of_missing_paths():
    """`_scan` over this repository's own `tests/`, cached for the three tests below."""
    return _scan(TESTS, REPO)


def test_the_sweep_actually_sees_the_suites_it_is_meant_to_police():
    """Positive control: the scan must be finding files and resolving path constants.

    Without this, every assertion below would pass vacuously the moment the resolver
    stopped understanding the `REPO / "staging" / "x.py"` shape -- a silent regression
    into a test that proves nothing.
    """
    files = _test_files()
    assert len(files) > 100, f"only {len(files)} files under tests/; the sweep is broken"

    candidates = _parsed_candidates()
    assert candidates, (
        "no file under tests/ binds a module-level path constant. The prefilter in "
        "_parsed_candidates() has stopped matching the pattern this file polices."
    )

    with_by_path_loads = 0
    for _path, tree in candidates:
        if not _const_paths(tree):
            continue
        if any(
            _call_name(node) in LOADER_CALLS
            for stmt in _module_scope_statements(tree)
            for node in ast.walk(stmt)
            if isinstance(node, ast.Call)
        ):
            with_by_path_loads += 1

    assert with_by_path_loads >= 1, (
        "the sweep found no module-scope by-path loader call anywhere under tests/. "
        "Either the P10H/I/J suites were removed, or the resolver no longer understands "
        "the pattern it exists to police."
    )


def test_the_sweep_detects_an_unguarded_load_of_a_missing_path(tmp_path):
    """Negative control: proof the gate below can fail.

    Builds a synthetic `tests/` tree carrying the exact shape that took this repository's
    suite down -- a module-scope `spec_from_file_location` pointed at an absent
    `staging/*.py` -- and requires the sweep to classify it as unguarded. Then adds the
    W1B guard to the same file and requires the classification to move to guarded.

    Hermetic: everything is under `tmp_path`. Nothing is written to `tests/` or
    `staging/`, and no module is imported.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "staging").mkdir()

    offender = repo / "tests" / "test_synthetic_offender.py"
    body = (
        "import importlib.util\n"
        "import pathlib\n"
        "import pytest\n"
        "\n"
        "REPO = pathlib.Path(__file__).resolve().parents[1]\n"
        "PROBE_PATH = REPO / 'staging' / 'absent_probe.py'\n"
    )
    load = (
        "\n"
        "spec = importlib.util.spec_from_file_location('x', PROBE_PATH)\n"
    )
    offender.write_text(body + load, encoding="utf-8")

    unguarded, guarded = _scan(repo / "tests", repo)
    assert len(unguarded) == 1, f"the synthetic offender was not detected: {unguarded}"
    assert "absent_probe.py" in unguarded[0]
    assert guarded == ()

    # Now guard it the way W1B did, and the same file must reclassify.
    guard = (
        "\n"
        "if not PROBE_PATH.exists():\n"
        "    pytest.skip('absent_probe.py is missing', allow_module_level=True)\n"
    )
    offender.write_text(body + guard + load, encoding="utf-8")

    unguarded, guarded = _scan(repo / "tests", repo)
    assert unguarded == (), f"a guarded load was still reported as fatal: {unguarded}"
    assert len(guarded) == 1 and "absent_probe.py" in guarded[0]

    # And once the dependency is present, neither list mentions it at all.
    (repo / "staging" / "absent_probe.py").write_text("x = 1\n", encoding="utf-8")
    assert _scan(repo / "tests", repo) == ((), ())


def test_no_test_file_aborts_collection_on_a_missing_by_path_dependency():
    """THE P11-H15 ASSERTION.

    An unguarded module-scope load of an absent path is a repository-wide outage of the
    test suite, not a local failure. The fix is the W1B guard shape -- a conditional
    `pytest.skip(..., allow_module_level=True)` naming what is missing -- not deleting
    the assertions and not `--ignore` on one lane's command line, which leaves every
    other invocation broken.
    """
    unguarded, _ = _module_scope_loads_of_missing_paths()

    assert not unguarded, (
        "these module-scope loads target files that do not exist, so pytest will report "
        "`Interrupted: N error(s) during collection` and discard the WHOLE session:\n  "
        + "\n  ".join(unguarded)
        + "\n\nGuard each one with a conditional module-level skip that names the missing "
        "file (see tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py), or restore "
        "the dependency. Do not silence it with --ignore or an addopts exclusion."
    )


def test_collection_blocker_guards_are_reported_and_not_silent():
    """A guard keeps the suite runnable; it does not make the missing file acceptable.

    Guarded cases are real suspended coverage, so they must stay visible. This test
    passes either way -- there is nothing wrong with zero guards -- but it fails if a
    guard exists whose skip message does not name the file it is standing in for, which
    is the difference between a documented suspension and a quiet mask.
    """
    _, guarded = _module_scope_loads_of_missing_paths()
    if not guarded:
        pytest.skip("no collection-blocker guard is currently active")

    for record in guarded:
        relative_path, _, remainder = record.partition(":")
        missing = remainder.rsplit("-> ", 1)[-1].strip()
        source = (REPO / relative_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        guard_lineno = _module_level_guard_lineno(tree)
        assert guard_lineno is not None
        guard_region = "\n".join(source.splitlines()[guard_lineno - 1:guard_lineno + 40])
        assert pathlib.PurePosixPath(missing).name in guard_region, (
            f"{relative_path} guards collection but its skip message never names "
            f"{missing}. A guard that does not say what is missing is a mask."
        )
