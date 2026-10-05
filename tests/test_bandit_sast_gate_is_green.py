"""The Bandit SAST gate blocks every deployment, and it has never passed.

WHAT WAS MEASURED, BEFORE ANY OF THIS WAS WRITTEN
--------------------------------------------------
`.github/workflows/05-security.yml` runs, with no `|| true` and no baseline file::

    bandit -r backend_app -ll -f json -o reports/bandit-report.json

`bandit` exits 1 when it reports anything, so that step fails whenever a single MEDIUM or
HIGH finding exists. Run against the tree at 16d31982 it reports **4 findings, all MEDIUM,
all B608 (hardcoded_sql_expressions), all LOW confidence, all in one file**:

    backend_app/backend/transactional_execution_manager.py:692, 746, 792, 1144

That is not a cosmetic failure. `.github/workflows/03-deploy.yml` opens with a step named
"Require the security scan to have passed for this commit", and that step is what fails on
`03 Deploy` -- in 7 seconds, before the AWS credentials are even configured. So:

    bandit finding  ->  05 Security red  ->  03 Deploy's gate refuses  ->  NOTHING DEPLOYS

`gh run list` shows `03 Deploy` failing on every single run, each in 11-14s, always at that
same gate. The deploy pipeline has been inoperable, and the reason was four lines of SQL.

WHY THE FOUR FINDINGS ARE FALSE POSITIVES, AND WHY THAT IS CHECKABLE
--------------------------------------------------------------------
All four are f-strings interpolating `json_text("data", "<literal>", _session_dialect(db))`,
the dialect renderer added for the `json_extract`-on-PostgreSQL defect. A column expression
cannot be a bound parameter -- `data ->> 'symbol'` versus `json_extract(data, '$.symbol')` is
SQL *structure*, chosen by the session's dialect -- so interpolation there is unavoidable.

What *is* avoidable is interpolating anything untrusted, and `json_text` already refuses to:
it matches both `column` and `key` against `^[A-Za-z_][A-Za-z0-9_]*$` and raises `ValueError`
before it builds any SQL (`test_json_extract_dialect_portability.py` proves the refusal, with
`"symbol; DROP TABLE positions"` among its cases). The user-controlled values at all four
sites -- `symbol` and `tenant_id` -- are bound parameters, `:symbol` and `:tid`.

So the finding is suppressed with `# nosec B608` plus the reason, which is the mechanism
bandit provides for exactly this. Suppression is only honest if the premise is held, so this
file holds it two ways:

1. `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` runs the gate's own command,
   with the gate's own flags, scoped to that module. This is the test that FAILED FIRST.
2. `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` walks the AST of
   every module under `backend_app` and fails if any f-string that becomes a statement
   interpolates an expression that is not provably constrained. A `# nosec` comment is
   invisible to the AST, so it cannot silence this one, and it covers sites that do not exist
   yet. Writing it turned up TEN interpolation sites bandit never reported at all -- five more
   in this same module, three in an alembic RLS migration, two in `core/tenant_db_context.py`.
   All ten check out, by the four rules below, but bandit's silence was not why.

NOT FIXED BY LOOSENING THE GATE, DELIBERATELY. Dropping to `-lll`, adding `|| true` or
writing a `--baseline` would turn the one workflow that reads this codebase for injection and
command execution into decoration. The gate is correct to block; it had nothing true to say.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND_APP = REPO_ROOT / "backend_app"
GATED_MODULE = BACKEND_APP / "backend" / "transactional_execution_manager.py"

#: Functions that refuse anything but a plain identifier and raise before returning SQL, so
#: their return value is safe to interpolate. Both live in this repository and both are under
#: test: `json_text` in test_json_extract_dialect_portability.py and below,
#: `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises ValueError) at
#: transactional_execution_manager.py:37.
#:
#: TO ADD A NAME HERE you have to have read the function and confirmed it raises rather than
#: sanitising, escaping or falling back to a default. A function that quietly returns a
#: cleaned-up string on bad input belongs nowhere near this set.
SQL_SAFE_VALIDATORS = frozenset({"json_text", "_validate_table_name"})

#: Callables whose string argument reaches a database driver as a statement. An f-string
#: anywhere else under backend_app is a log line or an error message.
SQL_SINKS = frozenset({"text", "execute", "executescript", "exec_driver_sql"})


# ---------------------------------------------------------------------------
# 1. The gate's own command, on the module that failed it. FAILED FIRST.
# ---------------------------------------------------------------------------

def test_bandit_reports_nothing_in_the_module_the_gate_failed_on() -> None:
    """`bandit -ll` on this module must be silent, because `03 Deploy` is downstream of it.

    Same tool and same severity threshold as `05-security.yml`, scoped to one file so it runs
    in about a second instead of the 5m44s a full `-r backend_app` sweep takes. The AST test
    below is what covers the rest of the tree.
    """
    pytest.importorskip(
        "bandit",
        reason="bandit is installed by 05-security.yml, not by requirements-cpu.txt",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "bandit", "-ll", "-f", "json", str(GATED_MODULE)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    # bandit writes the report to stdout and its progress chatter to stderr.
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover - only on a bandit invocation failure
        pytest.fail(
            "bandit produced no JSON report, so the gate condition could not be measured.\n"
            f"exit={completed.returncode}\nstdout={completed.stdout[:2000]}\n"
            f"stderr={completed.stderr[:2000]}"
        )

    findings = report.get("results", [])
    if findings:
        rendered = "\n".join(
            f"  {f['filename']}:{f['line_number']}  {f['test_id']} "
            f"{f['issue_severity']}/{f['issue_confidence']}  {f['issue_text']}"
            for f in findings
        )
        pytest.fail(
            f"bandit reports {len(findings)} MEDIUM-or-higher finding(s) in "
            f"{GATED_MODULE.relative_to(REPO_ROOT)}.\n\n{rendered}\n\n"
            "05-security.yml runs `bandit -r backend_app -ll` with no baseline, so this "
            "fails that job; 03-deploy.yml's first step requires that job to have passed, so "
            "it also stops every deployment. Either fix the finding or suppress it at the "
            "line with `# nosec <id> - <reason>` and a reason that is true."
        )


# ---------------------------------------------------------------------------
# 2. The premise behind the suppression, over the whole tree. AST, not grep.
# ---------------------------------------------------------------------------

def _python_modules() -> list[pathlib.Path]:
    return sorted(
        p
        for p in BACKEND_APP.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _callee_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_validator_call(node: ast.AST) -> bool:
    """RULE 1: the expression is a call to a validator that raises on anything unsafe."""
    return _callee_name(node) in SQL_SAFE_VALIDATORS


def _all_string_literals(node: ast.expr | None) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return bool(node.elts) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        )
    return False


def _constrained_names(tree: ast.Module) -> set[str]:
    """Names this module binds in a way that constrains what they can hold.

    Collected module-wide and then minus anything ever bound some other way, so a name that
    is only *sometimes* safe does not qualify. Widening the collection scope can therefore
    only make the check stricter, never looser.

    RULE 2  a name assigned from a validator call             `tbl = _validate_table_name(t)`
    RULE 3  a loop variable over a literal sequence of strings `for table in ['orders', ...]`
    RULE 4  an UPPER_CASE name assigned a string literal       `TENANT_SETTING_NAME = "..."`
    """
    constrained: set[str] = set()
    unconstrained: set[str] = set()

    for node in ast.walk(tree):
        # RULE 2 and RULE 4 -- assignments.
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names:
                continue
            value = node.value
            if value is not None and _is_validator_call(value):
                constrained.update(names)
            elif (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and all(n.isupper() or n.lstrip("_").isupper() for n in names)
            ):
                constrained.update(names)
            else:
                unconstrained.update(names)

        # RULE 3 -- `for <name> in [<string literals>]`.
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                if _all_string_literals(node.iter):
                    constrained.add(node.target.id)
                elif isinstance(node.iter, ast.Name):
                    # Iterating a name: safe only if that name is itself a literal sequence,
                    # resolved in the second pass below.
                    pass
                else:
                    unconstrained.add(node.target.id)

    # Second pass for RULE 3 via an intermediate name: `tables = [...]` then `for t in tables`.
    literal_sequences = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _all_string_literals(node.value)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if (
                isinstance(node.target, ast.Name)
                and isinstance(node.iter, ast.Name)
                and node.iter.id in literal_sequences
            ):
                constrained.add(node.target.id)
                unconstrained.discard(node.target.id)

    return constrained - unconstrained


def _sql_joinedstrs(tree: ast.Module) -> list[tuple[ast.JoinedStr, str]]:
    """Every f-string handed to something that turns a string into SQL.

    `text(f"...")` and `db.execute(f"...")` both reach the driver as a statement; an f-string
    anywhere else in this tree is a log line or an error message.
    """
    found: list[tuple[ast.JoinedStr, str]] = []
    for node in ast.walk(tree):
        callee = _callee_name(node)
        if callee not in SQL_SINKS:
            continue
        assert isinstance(node, ast.Call)
        for arg in node.args:
            if isinstance(arg, ast.JoinedStr):
                found.append((arg, callee))
    return found


def _interpolated_exprs(fstring: ast.JoinedStr) -> list[ast.expr]:
    return [v.value for v in fstring.values if isinstance(v, ast.FormattedValue)]


def test_every_interpolation_into_sql_is_constrained_before_it_gets_there() -> None:
    """The invariant the `# nosec B608` suppressions rest on, checked rather than asserted.

    A `# nosec` comment is invisible to the AST, so suppressing bandit's verdict cannot
    suppress this. If a future edit interpolates a request value into a statement -- in this
    module or any other under `backend_app` -- this fails regardless of what bandit says, and
    regardless of whether bandit noticed the site at all. It did not notice ten of them.
    """
    violations: list[str] = []

    for module in _python_modules():
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue

        constrained = _constrained_names(tree)

        for fstring, callee in _sql_joinedstrs(tree):
            for expr in _interpolated_exprs(fstring):
                if _is_validator_call(expr):  # RULE 1
                    continue
                if isinstance(expr, ast.Name) and expr.id in constrained:  # RULES 2-4
                    continue
                violations.append(
                    f"{module.relative_to(REPO_ROOT)}:{getattr(expr, 'lineno', '?')} "
                    f"{callee}(f\"...\") interpolates {ast.unparse(expr)!r}"
                )

    assert not violations, (
        "SQL is being built by interpolating a value nothing constrains:\n\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nSQL structure -- a column expression, a table name -- has to be interpolated, "
        "because it cannot be a bound parameter. A VALUE can be, and must be. An interpolated "
        "expression passes here only if it is one of:\n"
        f"  1. a call to {sorted(SQL_SAFE_VALIDATORS)}, which raise on anything but an identifier\n"
        "  2. a name assigned from one of those calls\n"
        "  3. a loop variable over a literal sequence of strings\n"
        "  4. an UPPER_CASE name assigned a string literal\n"
        "If the value is request data, bind it as a parameter. If it is structure, route it "
        "through a validator that RAISES -- not one that sanitises."
    )


def test_the_validators_the_suppression_rests_on_raise_rather_than_sanitise() -> None:
    """Guard the premise directly, for both names in `SQL_SAFE_VALIDATORS`.

    The AST test treats a validator call as safe. That is only true if the validator REFUSES
    bad input instead of cleaning it up -- a sanitiser that strips quotes and returns a string
    would satisfy the AST check while still being an injection seam. So both are exercised
    with input that must raise.

    `test_json_extract_dialect_portability.py` already covers `json_text`'s `key` argument.
    Both of its arguments are interpolated, so `column` is covered here, and
    `_validate_table_name` -- which five of the ten sites depend on -- is covered at all.
    """
    from backend_app.backend import transactional_execution_manager as tem

    hostile = ("data; DROP TABLE positions", "da ta", "", "da'ta", "data)", "a--b")

    for bad in hostile:
        with pytest.raises(ValueError):
            tem.json_text(bad, "symbol", "postgresql")

    for bad in hostile:
        with pytest.raises(ValueError):
            tem._validate_table_name(bad)

    # And the validators must still accept what the call sites actually pass, or the
    # suppression would be hiding a crash rather than a false positive.
    assert tem._validate_table_name("positions") == "positions"
    assert "symbol" in tem.json_text("data", "symbol", "postgresql")
