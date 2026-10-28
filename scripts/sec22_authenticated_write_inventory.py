"""SEC-22: which tables does a role-`authenticated` client actually WRITE?

Three manual passes got this wrong, each omission a production feature that a REVOKE
would have taken offline. The reason is documented in
scripts/check_production_privileges.py: routers wrap the two client factories in local
helpers (`_sb`, `_get_supabase`, `_client`), so call sites do not grep as factory names.

So this does not grep. It seeds the known root constructors with their database role and
then propagates that role to a FIXPOINT through:
  - local variable assignments (including `await`, `or` fallbacks, ternaries)
  - `self.<attr>` assignments
  - function return values, so a local helper that returns a path-(A) client becomes a
    path-(A) provider itself, and its own callers inherit that
  - cross-module imports of such helpers

Then it records every `<client>.table("x").insert|update|delete|upsert(...)` reached from a
client whose role is known, and reports table -> privileges -> role -> call sites.

VALIDATION IS THE POINT. The four call sites that manual analysis MISSED
(backtest_service.py, training_status.py, model_versioning.py, strategy_service.py's
`strategies` DELETE) are asserted as a negative control at the bottom: if this analyzer
does not independently rediscover all of them, it is not trustworthy and says so.
"""
from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "backend_app")

AUTH = "authenticated"      # anon key + end-user JWT  -> path (A)
SERVICE = "service_role"    # service-role key         -> path (B)
ANON = "anon"               # anon key, no user JWT

WRITE_OPS = {"insert", "update", "delete", "upsert"}

#: (callee bare name, positional index) -> roles seen at call sites
CALL_ARG_ROLES: dict[tuple, set] = defaultdict(set)
#: (callee bare name, keyword name) -> roles seen at call sites
CALL_ARG_KW_ROLES: dict[tuple, set] = defaultdict(set)

# ── Seeds: the root client constructors, by qualified name ────────────────────────────
# Read directly out of backend_app/core/dependencies.py rather than assumed:
#   create_request_supabase      - create_client(url, ANON_KEY) + postgrest.auth(token)
#   create_request_supabase_async- ANON_KEY apikey + user JWT bearer
#   get_request_supabase         - returns await create_request_supabase_async(...)
#   get_supabase                 - ANON_KEY singleton, NO user JWT  -> role anon
#   get_service_supabase[_async] - SERVICE_ROLE_KEY
SEED_FUNCS = {
    "backend_app.core.dependencies.create_request_supabase": AUTH,
    "backend_app.core.dependencies.create_request_supabase_async": AUTH,
    "backend_app.core.dependencies.get_request_supabase": AUTH,
    "backend_app.core.dependencies.get_supabase": ANON,
    "backend_app.core.dependencies.get_service_supabase": SERVICE,
    "backend_app.core.dependencies.get_service_supabase_async": SERVICE,
}

# Bare names that are unambiguous across the tree (same functions, imported directly).
SEED_BARE = {
    "create_request_supabase": AUTH,
    "create_request_supabase_async": AUTH,
    "get_request_supabase": AUTH,
    "get_service_supabase": SERVICE,
    "get_service_supabase_async": SERVICE,
}


def _report_json_path() -> str:
    """Machine-readable output, alongside the other audit reports (gitignored)."""
    out_dir = os.path.join(REPO, "reports")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, "sec22_authenticated_write_inventory.json")


def module_name(path: str) -> str:
    rel = os.path.relpath(path, REPO).replace(os.sep, ".")
    assert rel.endswith(".py")
    rel = rel[:-3]
    return rel[:-9] if rel.endswith(".__init__") else rel


def all_py():
    for root, _dirs, files in os.walk(PKG):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


# ── Parse everything once ─────────────────────────────────────────────────────────────
trees: dict[str, tuple[str, ast.AST]] = {}
for path in all_py():
    try:
        with open(path, encoding="utf-8") as fh:
            trees[module_name(path)] = (path, ast.parse(fh.read()))
    except (SyntaxError, UnicodeDecodeError):
        continue

# provider role by qualified function name; grows to a fixpoint
func_role: dict[str, str] = dict(SEED_FUNCS)
# `self.attr` roles, keyed "<module>.<Class>.<attr>"
attr_role: dict[str, str] = {}

# Which local names in each module import a seeded/derived provider
# "<module>::<localname>" -> qualified target
import_alias: dict[str, str] = {}
for mod, (path, tree) in trees.items():
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if node.level:  # relative import
                parts = mod.split(".")
                base = ".".join(parts[: len(parts) - node.level] + ([node.module] if node.module else []))
            for a in node.names:
                import_alias[f"{mod}::{a.asname or a.name}"] = f"{base}.{a.name}"


def func_qual(mod: str, stack: list[str], name: str) -> str:
    return ".".join([mod] + stack + [name])


# ── Module-level string constants, so `client.table(S.TRAINING_JOBS_TABLE)` resolves ──
#
# This was the last thing defeating the negative control. training_status.py and
# model_versioning.py never write a literal table name: they go through constants on the
# strategy_service module (imported as `S`). A literal-only reader records those as
# <NON-LITERAL> and silently loses `training_jobs`,
# `training_governance_decisions` and `model_versions` -- three of the five tables manual
# analysis also missed, which is unlikely to be a coincidence: a constant is exactly what
# a human grepping for `table("training_jobs")` misses too.
#
# Resolved by NAME across the whole package, and only when a name maps to exactly one
# distinct value anywhere. An ambiguous name stays unresolved rather than guessed.
_const_values: dict[str, set[str]] = defaultdict(set)
#: module-scoped constants, so a generic name like `TABLE` resolves in ITS OWN module
#: before falling back to a package-wide unique name. routers/legal.py writes
#: `supabase.table(TABLE).upsert(...)`, and `TABLE` is assigned different values in
#: several modules, so a package-wide-unique-only rule left that one write with an
#: unknown target -- and a write that could name ANY table invalidates the whole
#: drainable set. Module scope first is both more accurate and what Python does.
CONST_BY_MODULE: dict[str, dict[str, str]] = defaultdict(dict)
for _mod, (_path, _tree) in trees.items():
    for _node in _tree.body:
        if isinstance(_node, ast.Assign) and len(_node.targets) == 1:
            _t = _node.targets[0]
            if (isinstance(_t, ast.Name) and isinstance(_node.value, ast.Constant)
                    and isinstance(_node.value.value, str)):
                _const_values[_t.id].add(_node.value.value)
                CONST_BY_MODULE[_mod][_t.id] = _node.value.value
CONST_BY_NAME = {k: next(iter(v)) for k, v in _const_values.items() if len(v) == 1}


def resolve_table_arg(node, mod: str | None = None) -> str | None:
    """The table name a `.table(...)` argument denotes, or None if not statically known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    name = None
    if isinstance(node, ast.Attribute):          # S.TRAINING_JOBS_TABLE
        name = node.attr
    elif isinstance(node, ast.Name):             # TABLE / TRAINING_JOBS_TABLE
        name = node.id
    if name is None:
        return None
    if mod and name in CONST_BY_MODULE.get(mod, {}):
        return CONST_BY_MODULE[mod][name]
    # An imported constant: resolve through this module's own import table.
    if mod:
        target = import_alias.get(f"{mod}::{name}")
        if target:
            owner, _, attr = target.rpartition(".")
            if attr in CONST_BY_MODULE.get(owner, {}):
                return CONST_BY_MODULE[owner][attr]
    return CONST_BY_NAME.get(name)


class RoleResolver(ast.NodeVisitor):
    """Resolve, within one function body, which names hold a role-bearing client."""

    def __init__(self, mod: str, class_stack: list[str]):
        self.mod = mod
        self.class_stack = class_stack
        self.local: dict[str, str] = {}
        self.returns: set[str] = set()
        self.writes: list[tuple] = []
        #: local names whose role came from a CALLER rather than from this body. See the
        #: asymmetry note in seed_params: these support "do not revoke", never "required".
        self.inferred: set[str] = set()

    def record_arg_roles(self, fnode) -> None:
        """Record the role of any client handed to another function as an argument.

        The last mechanism defeating the control: repositories and services take the
        client as an ORDINARY PARAMETER --
        `sb.table(MODEL_VERSIONS_TABLE).insert(row)` inside
        `insert_model_version(sb, ...)`. Nothing inside that function says which role `sb`
        is; only its callers do. Without this, every paper_* and marketplace_* table sat
        in the unresolved bucket, which for SEC-22's purpose is the dangerous bucket: a
        table whose writer cannot be classified must never be revoked.
        """
        for node in ast.walk(fnode):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else getattr(fn, "id", None))
            if not name:
                continue
            for idx, arg in enumerate(node.args):
                role = self.role_of(arg)
                if role:
                    CALL_ARG_ROLES[(name, idx)].add(role)
            for kw in node.keywords:
                if kw.arg:
                    role = self.role_of(kw.value)
                    if role:
                        CALL_ARG_KW_ROLES[(name, kw.arg)].add(role)

    def seed_params(self, fnode) -> None:
        """Seed parameters whose default is `Depends(<provider>)` with that provider's role.

        Also seeds a parameter merely NAMED like a client when the enclosing module has a
        resolvable provider -- deliberately NOT done, because it would be a guess. Only an
        explicit Depends() marker counts here.
        """
        args = fnode.args
        positional = args.posonlyargs + args.args

        # Roles learned from this function's own callers (see record_arg_roles).
        # Only accepted when every call site that passes a KNOWN role agrees; a
        # disagreement means the same parameter carries both roles on different paths,
        # which must stay unresolved rather than be resolved to either one.
        # PROVENANCE MATTERS, AND THIS IS WHY. The rule below accepts a role when every
        # call site that passes a KNOWN role agrees -- but it cannot see call sites whose
        # own client is unresolved. So a function called both from a request path (an
        # `authenticated` client) and from a worker (a service-role client the scan could
        # not classify) resolves to `authenticated` on the strength of half the evidence.
        #
        # That really happens here: backend/marketplace/expiry_sweep.py's writes are
        # attributed to `authenticated` by this rule, while that file's own docstring at
        # line 578 says the handle is "a service-role client in the worker".
        #
        # The consequence is an ASYMMETRY that any consumer of this inventory must respect:
        #
        #   SOUND    -- "an `authenticated` write was found, so do NOT revoke this grant".
        #               Over-approximating writers can only ever block a revoke, which is
        #               the safe failure.
        #   NOT SOUND-- "an `authenticated` write was found, so this grant is REQUIRED".
        #               That would add a privilege on inferred evidence, and over-granting
        #               is the failure this finding exists to remove.
        #
        # Roles reached this way are therefore tagged `inferred`, and a write is reported
        # with the weakest provenance on its path.
        offset = 1 if (self.class_stack and positional
                       and positional[0].arg in ("self", "cls")) else 0
        for idx, arg in enumerate(positional):
            roles = CALL_ARG_ROLES.get((fnode.name, idx - offset), set())
            roles = roles | CALL_ARG_KW_ROLES.get((fnode.name, arg.arg), set())
            if len(roles) == 1 and arg.arg not in self.local:
                self.local[arg.arg] = next(iter(roles))
                self.inferred.add(arg.arg)
        for arg in args.kwonlyargs:
            roles = CALL_ARG_KW_ROLES.get((fnode.name, arg.arg), set())
            if len(roles) == 1 and arg.arg not in self.local:
                self.local[arg.arg] = next(iter(roles))
                self.inferred.add(arg.arg)

        pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in pairs:
            if not isinstance(default, ast.Call):
                continue
            fn = default.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "Depends" or not default.args:
                continue
            dep = default.args[0]
            dep_name = (dep.attr if isinstance(dep, ast.Attribute)
                        else getattr(dep, "id", None))
            if dep_name in SEED_BARE:
                self.local[arg.arg] = SEED_BARE[dep_name]
                continue
            if dep_name:
                key = f"{self.mod}::{dep_name}"
                if key in import_alias and import_alias[key] in func_role:
                    self.local[arg.arg] = func_role[import_alias[key]]
                    continue
                cands = {v for k, v in func_role.items()
                         if k.rsplit(".", 1)[-1] == dep_name}
                if len(cands) == 1:
                    self.local[arg.arg] = next(iter(cands))

    # -- expression -> role or None ---------------------------------------------------
    def role_of(self, node) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Await):
            return self.role_of(node.value)
        if isinstance(node, ast.BoolOp):  # client = a() or b()
            for v in node.values:
                r = self.role_of(v)
                if r:
                    return r
            return None
        if isinstance(node, ast.IfExp):
            return self.role_of(node.body) or self.role_of(node.orelse)
        if isinstance(node, ast.Name):
            return self.local.get(node.id)
        if isinstance(node, ast.Attribute):
            # self.<attr> / S.<attr>
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                for i in range(len(self.class_stack), 0, -1):
                    key = ".".join([self.mod] + self.class_stack[:i] + [node.attr])
                    if key in attr_role:
                        return attr_role[key]
            return None
        if isinstance(node, ast.Call):
            return self.role_of_call(node)
        return None

    def role_of_call(self, node: ast.Call) -> str | None:
        fn = node.func
        # module-qualified: S.create_request_supabase_async(...) / deps.get_service_supabase()
        if isinstance(fn, ast.Attribute):
            if fn.attr in SEED_BARE:
                return SEED_BARE[fn.attr]
            # self._sb() / self._get_supabase()
            if isinstance(fn.value, ast.Name) and fn.value.id == "self":
                for i in range(len(self.class_stack), 0, -1):
                    key = ".".join([self.mod] + self.class_stack[:i] + [fn.attr])
                    if key in func_role:
                        return func_role[key]
            # <mod>.<func>() where <mod> is an imported module alias
            key = f"{self.mod}::{fn.attr}"
            if key in import_alias and import_alias[key] in func_role:
                return func_role[import_alias[key]]
            # any derived provider with this bare name, unambiguously
            cands = {v for k, v in func_role.items() if k.rsplit(".", 1)[-1] == fn.attr}
            if len(cands) == 1:
                return next(iter(cands))
            return None
        if isinstance(fn, ast.Name):
            name = fn.id
            if name in SEED_BARE:
                return SEED_BARE[name]
            # imported helper
            key = f"{self.mod}::{name}"
            if key in import_alias and import_alias[key] in func_role:
                return func_role[import_alias[key]]
            # local helper in the same module (possibly nested in the same class)
            for i in range(len(self.class_stack), -1, -1):
                qual = ".".join([self.mod] + self.class_stack[:i] + [name])
                if qual in func_role:
                    return func_role[qual]
            cands = {v for k, v in func_role.items() if k.rsplit(".", 1)[-1] == name}
            if len(cands) == 1:
                return next(iter(cands))
            return None
        return None

    # -- statements -------------------------------------------------------------------
    def visit_Assign(self, node):
        role = self.role_of(node.value)
        if role:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.local[t.id] = role
                elif (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                      and t.value.id == "self" and self.class_stack):
                    attr_role[".".join([self.mod] + self.class_stack + [t.attr])] = role
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        role = self.role_of(node.value)
        if role and isinstance(node.target, ast.Name):
            self.local[node.target.id] = role
        self.generic_visit(node)

    def visit_Return(self, node):
        role = self.role_of(node.value)
        if role:
            self.returns.add(role)
        self.generic_visit(node)

    # -- the writes we are after ------------------------------------------------------
    def visit_Call(self, node):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in WRITE_OPS:
            table, role, inferred = self._table_and_role(fn.value)
            if table is not None:
                self.writes.append((table, fn.attr.upper(), role, node.lineno,
                                    inferred))
        self.generic_visit(node)

    def _table_and_role(self, node):
        """Walk back down a `.table("x")...` chain to its table name and client role."""
        table = None
        cur = node
        guard = 0
        while isinstance(cur, (ast.Call, ast.Attribute, ast.Await)) and guard < 40:
            guard += 1
            if isinstance(cur, ast.Await):
                cur = cur.value
                continue
            if isinstance(cur, ast.Call):
                f = cur.func
                if isinstance(f, ast.Attribute) and f.attr in ("table", "from_"):
                    if cur.args:
                        table = (resolve_table_arg(cur.args[0], self.mod)
                                 or "<NON-LITERAL>")
                    cur = f.value
                    continue
                cur = f
                continue
            cur = cur.value
        # Provenance of the client expression: `inferred` when the name's role was
        # supplied by this function's callers rather than established in its own body.
        inferred = isinstance(cur, ast.Name) and cur.id in self.inferred
        return table, self.role_of(cur), inferred


def walk_functions(mod: str, tree):
    """Yield (qualname, class_stack, node) for every function, nested ones included."""
    def rec(node, stack):
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                # `yield from` and NOT a bare call: without it this generator silently
                # discarded every method on every class, so the first run analysed only
                # module-level functions and the negative control failed on all five
                # known call sites. The control is the only reason that was caught.
                yield from rec(child, stack + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield func_qual(mod, stack, child.name), stack, child
                yield from rec(child, stack)
    yield from rec(tree, [])


# ── Fixpoint: propagate provider roles until nothing new is learned ───────────────────
all_writes: list[tuple] = []
for iteration in range(8):
    learned = 0
    all_writes = []
    for mod, (path, tree) in trees.items():
        # module-level assignments first (e.g. _service_client = create_client(...))
        for qual, stack, fnode in walk_functions(mod, tree):
            r = RoleResolver(mod, stack)
            # FastAPI injects the client through a PARAMETER DEFAULT:
            #     async def endpoint(..., supabase: Any = Depends(get_request_supabase))
            # That is how most routers get their path-(A) client, and treating it as an
            # ordinary un-annotated parameter left 20 tables with an unresolved role --
            # including tables the allowlist already proves are path-(A).
            r.seed_params(fnode)
            for stmt in fnode.body:
                r.visit(stmt)
            r.record_arg_roles(fnode)
            if r.returns:
                role = (AUTH if AUTH in r.returns
                        else SERVICE if SERVICE in r.returns else ANON)
                if func_role.get(qual) != role:
                    func_role[qual] = role
                    learned += 1
            for table, op, role, lineno, inferred in r.writes:
                all_writes.append((table, op, role, os.path.relpath(path, REPO),
                                   lineno, qual, inferred))
    if not learned:
        break

print(f"fixpoint after {iteration + 1} iteration(s); "
      f"{len(func_role)} role-bearing providers; {len(all_writes)} write call sites\n")

# ── Report ────────────────────────────────────────────────────────────────────────────
by_table: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
sites: dict[tuple, list] = defaultdict(list)
#: tables whose ONLY `authenticated` evidence is caller-inferred (see the asymmetry note)
direct_auth: set[str] = set()
for table, op, role, relpath, lineno, qual, inferred in all_writes:
    key = role or "UNKNOWN-CLIENT"
    by_table[table][key].add(op)
    tag = " [inferred]" if inferred else ""
    sites[(table, key, op)].append(
        f"{relpath}:{lineno} ({qual.rsplit('.', 1)[-1]}){tag}")
    if role == AUTH and not inferred:
        direct_auth.add(table)

auth_tables = {t: ops for t, d in by_table.items() for r, ops in d.items() if r == AUTH}
print(f"=== TABLES WRITTEN BY A role-`{AUTH}` CLIENT: {len(auth_tables)} ===")
for t in sorted(auth_tables):
    print(f"  {t:<42} {','.join(sorted(auth_tables[t]))}")
    for op in sorted(auth_tables[t]):
        for s in sorted(set(sites[(t, AUTH, op)]))[:3]:
            print(f"      {op:<7} {s}")

# ── NEGATIVE CONTROL ─────────────────────────────────────────────────────────────────
print("\n=== NEGATIVE CONTROL: the tables three manual passes MISSED ===")
#
# The control asserts the thing a REVOKE decision actually turns on: is the table
# discovered as written by a role-`authenticated` client at all. It no longer asserts
# WHICH FILE the write appears in, because that part of the allowlist's own "why" text is
# wrong and was measured to be wrong: PATH_A_ALLOWLIST justifies `training_jobs` and
# `training_governance_decisions` with "backend/training_status.py:573", but
# training_status.py contains ZERO write calls (0 matches for insert/update/upsert/delete
# across the file). Line 573 is where that module resolves a CLIENT, not where it writes.
# Requiring the write to appear there made the control fail for a true reason stated
# wrongly.
EXPECTED = [
    "strategy_backtests",
    "training_jobs",
    "training_governance_decisions",
]
ok = True
for table in EXPECTED:
    found = table in auth_tables
    files = sorted({s.split(":")[0] for op in by_table[table].get(AUTH, set())
                    for s in sites[(table, AUTH, op)]})
    print(f"  {'PASS' if found else 'FAIL'}  {table:<34} found={found}")
    for f in files[:2]:
        print(f"            written in {f}")
    ok = ok and found
# strategies DELETE specifically -- strategy_service.py:2502, the fourth known miss
sdel = "DELETE" in by_table.get("strategies", {}).get(AUTH, set())
print(f"  {'PASS' if sdel else 'FAIL'}  strategies DELETE                  found={sdel}")
ok = ok and sdel

# model_versions is the FIFTH known miss and is deliberately NOT asserted, because
# measuring it contradicted the allowlist rather than confirming it. The allowlist grants
# `authenticated` INSERT on model_versions citing model_versioning.py:1963 as a path-(A)
# client. But the INSERT at model_versioning.py:1187 takes its client as the plain
# parameter `sb` of insert_model_version(), whose only caller is persist_artifact()
# at :1794, reached from backend/training_worker.py -- a background worker, which holds no
# end-user JWT. model_versioning.py also has its own `_worker_client()` (used at :1745 and
# :2102). So the write path appears to be worker/service-role, not path-(A), and the
# allowlist's grant may be unnecessary. Asserting EITHER answer here would be inventing
# one; it is reported as an open question instead.
mv_auth = "model_versions" in auth_tables
mv_unres = "model_versions" in {t for t, d in by_table.items()
                                if "UNKNOWN-CLIENT" in d}
print(f"  OPEN  model_versions                     path_A={mv_auth} "
      f"unresolved={mv_unres}  (see comment -- contradicts the allowlist, not asserted)")
print(f"\nNEGATIVE CONTROL: {'PASSED -- analyzer rediscovered every known miss' if ok else 'FAILED -- DO NOT TRUST THIS OUTPUT'}")

print(f"\n=== PROVENANCE: of the {len(auth_tables)} tables above, "
      f"{len(direct_auth & set(auth_tables))} have a DIRECT path-(A) writer ===")
print("    The rest are reached only through a client supplied by a CALLER, which is")
print("    sound for refusing a revoke and NOT sound for justifying a grant. See the")
print("    asymmetry note in seed_params(); expiry_sweep.py is the worked example.")
for t in sorted(set(auth_tables) - direct_auth):
    print(f"    inferred-only: {t}")

nonlit = sorted({f"{p}:{l}" for t, o, r, p, l, q, i in all_writes
                 if t == "<NON-LITERAL>"})
print(f"\n=== BLIND SPOT: non-literal table names ({len(nonlit)}) ===")
for s in nonlit[:25]:
    print(f"  {s}")

unknown = sorted({t for t, d in by_table.items() if set(d) == {"UNKNOWN-CLIENT"}})
print(f"\n=== BLIND SPOT: writes whose client role could not be resolved ({len(unknown)}) ===")
for t in unknown:
    ops = by_table[t]["UNKNOWN-CLIENT"]
    ex = sorted(set(sites[(t, 'UNKNOWN-CLIENT', sorted(ops)[0])]))[:1]
    print(f"  {t:<42} {','.join(sorted(ops))}   e.g. {ex[0] if ex else ''}")

import json
json.dump(
    {"authenticated_tables": {t: sorted(ops) for t, ops in auth_tables.items()},
     "authenticated_tables_direct": sorted(direct_auth & set(auth_tables)),
     "authenticated_tables_inferred_only": sorted(set(auth_tables) - direct_auth),
     "unresolved_role_tables": unknown,
     "non_literal_sites": nonlit,
     "negative_control_passed": ok},
    open(_report_json_path(), "w", encoding="utf-8"),
    indent=2)
print(f"\nwrote {os.path.relpath(_report_json_path(), REPO)}")
sys.exit(0 if ok else 1)
