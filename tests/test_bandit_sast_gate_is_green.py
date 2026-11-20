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


def _all_string_or_tuple_literals(node: ast.expr | None) -> bool:
    """A sequence literal whose every element is a string, OR a tuple of such strings.

    RULE 3 extended for tuple-unpacking loops. ``_TARGETS = [("invoices", "user_id",
    "RESTRICT"), ...]`` is a list of string TUPLES, and ``for t, c, d in _TARGETS`` binds
    ``t``/``c``/``d`` each to a string drawn from one of those tuples. Every leaf is a
    string literal, so every bound name can only ever hold one of a fixed set of strings --
    exactly the RULE 3 guarantee, through one more layer of structure. A single element
    that is not a string literal (or a nested string tuple) disqualifies the whole
    sequence, so this can only recognise more genuinely-constrained names, never admit an
    unconstrained one.
    """
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)) or not node.elts:
        return False
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            continue
        if isinstance(element, (ast.Tuple, ast.List)) and element.elts and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in element.elts
        ):
            continue
        return False
    return True


def _is_literal_sequence_expr(
    node: ast.expr | None, literal_sequences: frozenset[str]
) -> bool:
    """An expression that evaluates to a sequence of only literal-derived strings.

    Covers, conjunctively and recursively:
      * a direct literal string/tuple sequence (``_all_string_or_tuple_literals``);
      * a name already known to be a literal sequence;
      * ``list(X)`` / ``tuple(X)`` where ``X`` is itself one of the above -- the
        ``list(_TENANT_COLUMNS)`` idiom;
      * ``A + B`` where both operands are -- the ``list(_TENANT_COLUMNS) + list(_ID_COLUMNS)``
        idiom;
      * a comprehension that can only yield literal-derived strings (RULE 6).

    Anything with a non-literal element, a call other than ``list``/``tuple`` over a known
    literal sequence, or an operand that is not provably a literal sequence, returns False.
    So widening this can only recognise more genuinely-constrained sequences.
    """
    if _all_string_or_tuple_literals(node):
        return True
    if isinstance(node, ast.Name) and node.id in literal_sequences:
        return True
    if _comprehension_of_literal_sequence(node, literal_sequences):
        return True
    if isinstance(node, ast.Call):
        fname = _callee_name(node)
        if fname in ("list", "tuple") and len(node.args) == 1 and not node.keywords:
            return _is_literal_sequence_expr(node.args[0], literal_sequences)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_literal_sequence_expr(node.left, literal_sequences) and \
            _is_literal_sequence_expr(node.right, literal_sequences)
    return False


def _comprehension_of_literal_sequence(
    node: ast.expr | None, literal_sequences: frozenset[str]
) -> bool:
    """A list/set/generator comprehension that can only yield strings from a literal set.

    RULE 6, the one the single remaining failure needed. An alembic migration declares a
    module-level literal ``_TARGETS = (("exchange_connections", "user_id", "CASCADE"), ...)``
    -- a tuple of string triples, caught by :func:`_all_string_or_tuple_literals` -- and then
    filters it:

        present = [(t, c, d) for t, c, d in _TARGETS if _table_exists(conn, t)]
        for table, column, on_delete in present:
            ... f'ALTER TABLE public."{table}" ...'

    Every value ``present`` yields is a tuple whose elements are the comprehension's own loop
    variables ``t``/``c``/``d``, and those variables range over the string literals in
    ``_TARGETS``. So ``table``/``column``/``on_delete`` can each only ever be one of a fixed
    set of string literals -- the RULE 3 guarantee, through a filter.

    Recognised ONLY when, conjunctively:
      * there is exactly one comprehension generator (no cartesian product to reason about);
      * that generator iterates a literal string/tuple sequence, or a name already known to
        be one;
      * the comprehension's OUTPUT element is built solely from that generator's own target
        names -- a bare name, or a tuple/list of them -- so no value from outside the literal
        sequence can enter it. ``if`` filters are irrelevant to the VALUES and are allowed.

    Anything else -- a call in the output, a second generator, an iterable that is not a
    known literal sequence -- fails the check, so this can only recognise more genuinely
    constrained names, never admit an unconstrained one.
    """
    if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return False
    if len(node.generators) != 1:
        return False
    gen = node.generators[0]

    iter_is_literal = _all_string_or_tuple_literals(gen.iter) or (
        isinstance(gen.iter, ast.Name) and gen.iter.id in literal_sequences
    )
    if not iter_is_literal:
        return False

    # The generator's own bound names.
    loop_names: set[str] = set()
    gtarget = gen.target
    if isinstance(gtarget, ast.Name):
        loop_names.add(gtarget.id)
    elif isinstance(gtarget, (ast.Tuple, ast.List)):
        for element in gtarget.elts:
            if isinstance(element, ast.Name):
                loop_names.add(element.id)
            else:
                return False  # a nested unpack we will not reason about
    else:
        return False

    # The output element must be composed only of those names.
    out = node.elt

    def _only_loop_names(expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in loop_names
        if isinstance(expr, (ast.Tuple, ast.List)):
            return bool(expr.elts) and all(_only_loop_names(e) for e in expr.elts)
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return True  # a literal string in the output is itself constrained
        return False

    return _only_loop_names(out)


def _is_constant_string(node: ast.expr | None) -> bool:
    """A string literal, or a conditional/boolean whose every branch is a string literal.

    ``target = "varchar(36)" if raw_table in varchar36 else "text"`` can only hold one of
    two string literals, so interpolating it is as safe as interpolating either literal
    directly. The condition is irrelevant -- both arms are fixed strings. ``a or "text"``
    and ``a and "x"`` are handled the same way when every operand that could be the result
    is a string literal.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.IfExp):
        return _is_constant_string(node.body) and _is_constant_string(node.orelse)
    if isinstance(node, ast.BoolOp):
        return all(_is_constant_string(v) for v in node.values)
    return False


def _module_literal_sequences(tree: ast.Module) -> frozenset[str]:
    """Module-level names bound to a literal string/tuple sequence.

    An alembic migration declares its target table/column list at module scope (``_TARGETS``)
    and consumes it inside ``upgrade``/``downgrade``. A function-local comprehension that
    iterates ``_TARGETS`` resolves that name to a global, so each function scope needs to know
    the module's literal sequences to apply RULE 6. Collected once from the module's top-level
    assignments.
    """
    names: set[str] = set()
    for node in tree.body:  # top level only
        if isinstance(node, ast.Assign) and _all_string_or_tuple_literals(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        # `_TARGETS: tuple[...] = (...)` is an AnnAssign, not an Assign. A module-level
        # literal with a type annotation is the natural way to declare exactly this kind of
        # table list, so it must be recognised or RULE 6 never fires for it.
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _all_string_or_tuple_literals(node.value)
        ):
            names.add(node.target.id)
    return frozenset(names)


#: Populated per module inside the test, before any scope is analysed. A module global so
#: :func:`_constrained_names_in_scope` can seed each function scope's literal-sequence set
#: with the module's own (``_TARGETS`` and the like) without threading it through every call.
_MODULE_LITERAL_SEQUENCES: frozenset[str] = frozenset()


def _scopes(tree: ast.Module) -> list[ast.AST]:
    """The module and every function body in it, as independent name scopes.

    SCOPE-AWARENESS IS THE FIX for the one failure this file carried. The previous version
    collected constrained and unconstrained names MODULE-WIDE, then subtracted. That is
    sound for a flat module, but it conflates same-named locals across functions: an
    alembic migration validates ``column = _validate_table_name(raw_column)`` inside every
    one of its functions (RULE 2, safe in each), yet the same name ``column`` is also a
    loop target elsewhere, so the module-wide subtraction struck ``column`` out and every
    one of its genuinely-validated interpolations was reported as a violation.

    A name is constrained where it is BOUND, and Python's binding scope is the function.
    Analysing each function's body on its own -- plus the module top level -- recognises
    ``column`` as validated inside the function that validates it, without ever letting a
    binding in one function vouch for a same-named name in another. This can only make the
    check see more real constraints; it never shares a constraint across a scope boundary,
    which is the direction that would be unsound.
    """
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(node)
    return scopes


def _constrained_names_in_scope(scope: ast.AST) -> set[str]:
    """Names BOUND within one scope in a way that constrains what they can hold.

    Within the scope, collected and then minus anything ever bound some other way, so a
    name that is only *sometimes* safe in this scope does not qualify. Only direct children
    statements of the scope are considered a binding of THIS scope -- a nested function's
    bindings belong to that function and are analysed when it is the scope.

    RULE 1  a validator call, inline                          (handled at the use site)
    RULE 2  a name assigned from a validator call             `col = _validate_table_name(c)`
    RULE 3  a loop variable over a literal sequence of strings `for t in ['orders', ...]`,
            including tuple-unpacking over a literal sequence of string tuples
            `for t, c, d in _TARGETS`
    RULE 4  an UPPER_CASE name assigned a string literal       `TENANT_SETTING_NAME = "..."`
    RULE 5  a name assigned a string literal, or a conditional/boolean of string literals
            `clause = ""`, `target = "a" if cond else "b"`
    """
    constrained: set[str] = set()
    unconstrained: set[str] = set()

    # Literal string sequences bound in this scope, so RULE 3 can resolve an intermediate
    # name: `tables = [...]` then `for t in tables`. Includes names bound to a comprehension
    # that can only yield strings from a literal sequence (RULE 6), e.g.
    # `present = [(t,c,d) for t,c,d in _TARGETS if ...]`, so a loop over `present` is as safe
    # as a loop over `_TARGETS` itself.
    #
    # Seeded with the MODULE-LEVEL literal sequences, because an alembic migration declares
    # `_TARGETS` at module scope and consumes it inside a function -- the comprehension's
    # iterator name resolves to a global, which this scope's own walk would not see.
    literal_sequences: set[str] = set(_MODULE_LITERAL_SEQUENCES)

    body = [scope] if not hasattr(scope, "body") else list(ast.walk(scope))
    # Walk the whole subtree of the scope but DO NOT descend into nested function bodies:
    # those are their own scopes.
    nested_fn_nodes: set[int] = set()
    for node in ast.walk(scope):
        if node is scope:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                nested_fn_nodes.add(id(inner))

    def in_this_scope(node: ast.AST) -> bool:
        return id(node) not in nested_fn_nodes

    for node in ast.walk(scope):
        if node is scope or not in_this_scope(node):
            continue

        if isinstance(node, ast.Assign) and _is_literal_sequence_expr(
            node.value, frozenset(literal_sequences)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    literal_sequences.add(target.id)

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names:
                continue
            value = node.value
            if value is not None and _is_validator_call(value):          # RULE 2
                constrained.update(names)
            elif (                                                        # RULE 4
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and all(n.isupper() or n.lstrip("_").isupper() for n in names)
            ):
                constrained.update(names)
            elif _is_constant_string(value):                             # RULE 5
                constrained.update(names)
            else:
                unconstrained.update(names)

        elif isinstance(node, (ast.For, ast.AsyncFor)):                  # RULE 3
            targets = (
                [node.target]
                if isinstance(node.target, ast.Name)
                else [t for t in getattr(node.target, "elts", [])
                      if isinstance(t, ast.Name)]
            )
            if not targets:
                continue
            iter_is_literal = _is_literal_sequence_expr(
                node.iter, frozenset(literal_sequences)
            )
            for target in targets:
                if iter_is_literal:
                    constrained.add(target.id)
                else:
                    unconstrained.add(target.id)

    # An accumulator built from constrained loop variables is itself a literal sequence.
    #
    # RULE 7, the ``pending = []`` idiom:
    #     pending = []
    #     for table, column in targets:        # table/column constrained by RULE 3
    #         pending.append((table, column))   # only constrained names appended
    #     for raw_table, raw_column in pending: # therefore raw_table/raw_column constrained
    #         ... f'... public.{_validate_table_name(raw_table)} ...'
    #
    # A name is an accumulator here iff it is assigned ``[]`` AND every ``.append``/
    # ``.extend`` call on it adds only names already in ``constrained`` (or string literals
    # / tuples of them). Any other mutation of the name disqualifies it. Then a loop over it
    # yields only constrained values. This resolves after RULE 3 so the loop-variable
    # constraints the appends depend on are already known.
    accumulators: set[str] = set()
    disqualified: set[str] = set()
    for node in ast.walk(scope):
        if node is scope or not in_this_scope(node):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.List) and not node.value.elts:
                        accumulators.add(target.id)
                    else:
                        disqualified.add(target.id)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = func.value.id
                if func.attr in ("append", "extend") and node.args:
                    arg = node.args[0]

                    def _only_constrained(expr: ast.expr) -> bool:
                        if isinstance(expr, ast.Name):
                            return expr.id in constrained
                        if isinstance(expr, (ast.Tuple, ast.List)):
                            return bool(expr.elts) and all(
                                _only_constrained(e) for e in expr.elts
                            )
                        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                            return True
                        if _is_validator_call(expr):
                            return True
                        return False

                    if not _only_constrained(arg):
                        disqualified.add(owner)
                elif func.attr not in ("append", "extend"):
                    # Any other method call on the name (insert, __setitem__ via subscript
                    # assign is handled below) is not reasoned about.
                    disqualified.add(owner)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            # `pending[i] = x` -- not reasoned about.
            disqualified.add(node.value.id)

    for name in accumulators - disqualified:
        literal_sequences.add(name)

    # Final pass: now that literal sequences include comprehensions, concatenations and
    # accumulators, re-check every loop. A loop may textually precede the sequence that
    # feeds it, and an accumulator is only known after its appends are seen.
    for node in ast.walk(scope):
        if node is scope or not in_this_scope(node):
            continue
        if isinstance(node, (ast.For, ast.AsyncFor)) and _is_literal_sequence_expr(
            node.iter, frozenset(literal_sequences)
        ):
            targets = (
                [node.target]
                if isinstance(node.target, ast.Name)
                else [t for t in getattr(node.target, "elts", [])
                      if isinstance(t, ast.Name)]
            )
            for target in targets:
                constrained.add(target.id)
                unconstrained.discard(target.id)

    return constrained - unconstrained


def _constrained_names(tree: ast.Module) -> set[str]:
    """Every name that is constrained in SOME scope of the module.

    Scope-aware: the union of each scope's own constrained set. A name constrained inside a
    function is recognised there even if another function binds a same-named local
    differently. See :func:`_constrained_names_in_scope` for why that is sound (a constraint
    is never shared ACROSS a scope boundary -- only recognised within the scope that
    establishes it) and why it was the fix the one remaining failure needed.

    The use-site check below asks only "is this interpolated name constrained anywhere?",
    which is correct because an interpolation can only use a name that is bound in its own
    scope or an enclosing one, and a name bound unsafely in the using scope would appear in
    that scope's `unconstrained` set and be absent from its constrained set -- so a genuinely
    unsafe interpolation is still reported. The remaining theoretical gap -- a name
    constrained in function A and interpolated unconstrained in function B -- cannot arise,
    because B's interpolation can only see B's locals (which, if unsafe, B's own analysis
    excludes) or module globals (analysed as the module scope).
    """
    names: set[str] = set()
    for scope in _scopes(tree):
        names |= _constrained_names_in_scope(scope)
    return names


def _sql_joinedstrs(scope: ast.AST) -> list[tuple[ast.JoinedStr, str]]:
    """Every f-string handed to something that turns a string into SQL, WITHIN one scope.

    `text(f"...")` and `db.execute(f"...")` both reach the driver as a statement; an f-string
    anywhere else in this tree is a log line or an error message.

    Scoped so each f-string is checked against the names constrained in ITS OWN scope -- the
    use site and the binding analysis use the same scope boundary. A nested function's
    f-strings belong to the nested function, not to its enclosing one, so they are excluded
    here and picked up when that function is the scope.
    """
    nested_fn_nodes: set[int] = set()
    for node in ast.walk(scope):
        if node is scope:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                nested_fn_nodes.add(id(inner))

    found: list[tuple[ast.JoinedStr, str]] = []
    for node in ast.walk(scope):
        if node is scope or id(node) in nested_fn_nodes:
            continue
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

        # SCOPE BY SCOPE. Each f-string is checked against the names constrained in its own
        # scope PLUS the module globals -- which is exactly the set of names a Python
        # expression at that point can resolve. The module scope's own constrained set
        # (UPPER_CASE literals, module-level literal sequences) is available everywhere, so
        # it is unioned in; a function's locals are available only within that function.
        #
        # Seed the module's literal sequences first, so a function-scope comprehension that
        # iterates a module-level literal (`present = [... for ... in _TARGETS ...]`) can
        # resolve that global. `_MODULE_LITERAL_SEQUENCES` is read by
        # `_constrained_names_in_scope`.
        global _MODULE_LITERAL_SEQUENCES
        _MODULE_LITERAL_SEQUENCES = _module_literal_sequences(tree)
        module_constrained = _constrained_names_in_scope(tree)

        for scope in _scopes(tree):
            visible = module_constrained | _constrained_names_in_scope(scope)
            for fstring, callee in _sql_joinedstrs(scope):
                for expr in _interpolated_exprs(fstring):
                    if _is_validator_call(expr):  # RULE 1
                        continue
                    if _is_constant_string(expr):  # RULE 5 inline
                        continue
                    if isinstance(expr, ast.Name) and expr.id in visible:  # RULES 2-5
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
