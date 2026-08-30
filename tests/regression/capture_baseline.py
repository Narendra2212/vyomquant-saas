"""Requirement 25 / 17.12 pre-change regression baseline capture.

Run this against the **unmodified** system::

    python -m tests.regression.capture_baseline

It writes one JSON file per named path and per surface into
``tests/regression/baseline/``. It never writes a single combined file: Requirement 25.7
asks for each named path and surface to be verified *individually*, and one file per
capture is what makes a later failure name itself.

WHY THE BASELINE RECORDS SHAPES RATHER THAN VALUES
    ``tests/regression/test_baseline_unchanged.py`` re-runs every capture in this module
    and compares it to the stored file. A baseline that recorded ``order_id`` or
    ``created_at`` *values* would fail on the second run for reasons that have nothing to
    do with a regression. So every capture records a **key set and a type map** — which is
    exactly the unit Requirements 17.12 and 25.4 are written in ("no existing response
    field is removed, renamed, retyped or given a changed meaning").

    One capture is a deliberate exception and records exact numbers, because the
    requirement is about the numbers themselves: ``risk_utilisation.json``. Requirement
    25.5's "without changing its reported semantics" is a claim about the arithmetic of
    ``routers/risk.py`` lines 300–311 and 355–360, so that capture builds a paper account
    by value and pins every figure the two endpoints compute from it, including which rung
    of the risk ladder a 60 % utilisation lands on.

    The paper accounting invariants are *not* captured here — they are Requirement 18's,
    and ``tests/test_paper_trading_lifecycle.py`` already asserts them.

HOW EACH CLASS OF CAPTURE IS TAKEN
    Live paths (``live_order_path``, ``credential_vault``, ``live_runtime``,
        ``signal_generation``, ``signal_trace_live``) are **executed**, against recording
        doubles standing in for the database, Redis and the exchange. What is recorded is
        the ordered collaborator call sequence and the resulting record shape — the two
        things a refactor of these paths would disturb.

    Paper and risk endpoints are **executed** through ``TestClient`` against the real
        in-memory service, because they need no external system.

    Billing, authentication and the seventeen Requirement 25.4 surfaces are recorded as a
        **route contract**: the resolved FastAPI route for every API call the surface
        makes, its authorisation dependencies, its declared parameters, and the response
        key set and type map read out of the handler's own ``return`` statements. Register,
        sign-in and the token/role/AAL2 decision tables are additionally executed against
        recording doubles, so the parts that can be run are run.

        The route contract is not a second-best stand-in for a live call. These handlers
        reach Supabase, Stripe and Razorpay; a "live" capture of them against absent
        third-party systems would record a 503 and would assert nothing about the response
        fields Requirement 25.2 is protecting. The handler's own ``return`` dictionaries
        are where those fields are defined, so that is where they are read.

NOTHING IN THIS MODULE WRITES TO A REAL SYSTEM
    No capture opens a socket, no capture reads a real credential, and no captured value
    is a secret: the credential-vault capture records the *shape* of the Redis key and the
    ordered call sequence, never a plaintext or a ciphertext.
"""

from __future__ import annotations

import os

# ══════════════════════════════════════════════════════════════════════════
#  ENVIRONMENT — set before any backend_app import, exactly as tests/conftest.py does.
#  Importing backend_app.main with a real DATABASE_URL or REDIS_URL would make the
#  capture depend on whatever happens to be running on this machine.
# ══════════════════════════════════════════════════════════════════════════

_CAPTURE_ENV = {
    "ENV": "testing",
    "REDIS_URL": "",
    "DEV_MODE": "true",
    "JWT_SECRET": "dev-secret-change-in-production",
    "SUPABASE_JWT_SECRET": "dev-secret-change-in-production",
    "DEFAULT_EXCHANGE": "binance",
    "VYOMQUANT_MODE": "safe",
    "DATABASE_URL": "",
}

for _name, _value in _CAPTURE_ENV.items():
    os.environ[_name] = _value

#: Deterministic, non-secret material for the credential-vault capture. Passed to
#: ``CredentialVault(...)`` as arguments rather than exported to the environment: importing
#: this module must not change the key any *other* test in the session would derive.
_VAULT_KEY = "regression-baseline-master-material-0123456789abcdef"
_VAULT_SALT = "regression-baseline-deployment-salt"

import ast  # noqa: E402
import asyncio  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import textwrap  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = Path(__file__).resolve().parent / "baseline"
FRONTEND_SRC = REPO_ROOT / "algo22-terminal" / "src"

#: The one user identity every capture acts as. Fixed so the capture is reproducible.
CAPTURE_USER_ID = "00000000-0000-4000-8000-00000000cafe"
CAPTURE_TENANT_ID = CAPTURE_USER_ID


# ══════════════════════════════════════════════════════════════════════════
#  SHAPE VOCABULARY
#
#  One type name per JSON value, recursively. ``{"list_of": ...}`` rather than a bare list
#  so that a type map is always a dict-or-string and a diff can address any part of it by
#  key path.
# ══════════════════════════════════════════════════════════════════════════


def shape_of(value: Any) -> Any:
    """The type map of ``value``: the same structure with every leaf replaced by its type."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, dict):
        return {str(key): shape_of(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        seen: List[Any] = []
        for item in value:
            item_shape = shape_of(item)
            if item_shape not in seen:
                seen.append(item_shape)
        if not seen:
            return {"list_of": "empty"}
        if len(seen) == 1:
            return {"list_of": seen[0]}
        return {"list_of_one_of": sorted(seen, key=lambda s: json.dumps(s, sort_keys=True))}
    return type(value).__name__


def key_paths(value: Any, prefix: str = "") -> List[str]:
    """Every addressable key path in ``value``, sorted. A list element is spelled ``[]``."""
    paths: List[str] = []
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths.extend(key_paths(value[key], path))
    elif isinstance(value, (list, tuple)):
        for item in value:
            for path in key_paths(item, f"{prefix}[]"):
                if path not in paths:
                    paths.append(path)
    return sorted(set(paths))


def response_shape(payload: Any) -> Dict[str, Any]:
    """The unit every response capture is recorded in."""
    return {"keys": key_paths(payload), "type_map": shape_of(payload)}


def _run(coro: Any) -> Any:
    """Drive one coroutine to completion on a private loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _error_of(exc: BaseException) -> Dict[str, Any]:
    """An exception recorded by type and code, never by message.

    A message carries identifiers, paths and formatted numbers that would make the
    baseline fail on its own second run. The type and the module's own error ``code`` are
    the parts of a raised failure that are contractual.
    """
    recorded: Dict[str, Any] = {"error_type": type(exc).__name__}
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        recorded["code"] = code
    return recorded


# ══════════════════════════════════════════════════════════════════════════
#  HANDLER SOURCE READING
#
#  A FastAPI handler in this repository declares its response by returning a dict
#  literal. That literal is the definition of the response field set, so it is read from
#  the source rather than guessed.
# ══════════════════════════════════════════════════════════════════════════

_TYPE_OF_CALL = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "len": "int",
    "list": "list",
    "dict": "dict",
    "sorted": "list",
    "round": "float",
    "isoformat": "str",
    "upper": "str",
    "lower": "str",
    "strip": "str",
    "join": "str",
}


def _inferred_type(node: ast.AST) -> Any:
    """The type an expression in a ``return`` dict evaluates to, as far as it is readable."""
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        return type(value).__name__
    if isinstance(node, ast.JoinedStr):
        return "str"
    if isinstance(node, ast.Dict):
        return _dict_type_map(node)
    if isinstance(node, (ast.List, ast.Tuple, ast.ListComp)):
        return "list"
    if isinstance(node, (ast.DictComp,)):
        return "dict"
    if isinstance(node, ast.Compare):
        return "bool"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return "bool"
    if isinstance(node, ast.BoolOp):
        alternatives = [_inferred_type(v) for v in node.values]
        return _union(alternatives)
    if isinstance(node, ast.IfExp):
        return _union([_inferred_type(node.body), _inferred_type(node.orelse)])
    if isinstance(node, ast.Await):
        return _inferred_type(node.value)
    if isinstance(node, ast.Call):
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in _TYPE_OF_CALL:
            return _TYPE_OF_CALL[name]
        return "computed"
    return "computed"


def _union(alternatives: Sequence[Any]) -> Any:
    unique: List[Any] = []
    for alternative in alternatives:
        if alternative not in unique:
            unique.append(alternative)
    if len(unique) == 1:
        return unique[0]
    return {"one_of": sorted(unique, key=lambda s: json.dumps(s, sort_keys=True))}


def _dict_type_map(node: ast.Dict) -> Any:
    """The type map of a dict literal. A non-literal key makes the whole dict opaque."""
    mapping: Dict[str, Any] = {}
    for key, value in zip(node.keys, node.values):
        if key is None:  # ``{**other}``
            return "dict_with_spread"
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            mapping[key.value] = _inferred_type(value)
        else:
            return "dict_with_computed_key"
    return {name: mapping[name] for name in sorted(mapping)}


def _handler_definition(function: Callable[..., Any]) -> Optional[ast.AST]:
    target = inspect.unwrap(function)
    try:
        source = textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError):
        return None
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _own_returns(definition: ast.AST) -> List[ast.Return]:
    """Every ``return`` belonging to this function, excluding nested function bodies."""
    returns: List[ast.Return] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                returns.append(child)
            walk(child)

    walk(definition)
    return returns


def _raised_status_codes(definition: ast.AST) -> List[Any]:
    """Every ``HTTPException(status_code=...)`` this handler raises, by code and error name."""
    raised: List[Any] = []
    for node in ast.walk(definition):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "HTTPException":
            continue
        entry: Dict[str, Any] = {}
        for keyword in node.exc.keywords:
            if keyword.arg == "status_code":
                if isinstance(keyword.value, ast.Constant):
                    entry["status_code"] = keyword.value.value
                elif isinstance(keyword.value, (ast.Attribute, ast.Name)):
                    # ``status.HTTP_401_UNAUTHORIZED`` rather than a literal. Recorded as
                    # written, because the name is what a reader and a diff both need.
                    entry["status_code"] = ast.unparse(keyword.value)
            if keyword.arg == "detail":
                entry["detail_type"] = _inferred_type(keyword.value)
                if isinstance(keyword.value, ast.Dict):
                    for key, value in zip(keyword.value.keys, keyword.value.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "error"
                            and isinstance(value, ast.Constant)
                        ):
                            entry["error_code"] = value.value
        if entry and entry not in raised:
            raised.append(entry)
    return sorted(raised, key=lambda e: json.dumps(e, sort_keys=True))


def _decorators(definition: ast.AST) -> List[str]:
    """The decorator names as written, so a lost rate limit is visible in the diff."""
    rendered: List[str] = []
    for decorator in getattr(definition, "decorator_list", []):
        try:
            rendered.append(ast.unparse(decorator))
        except Exception:  # noqa: BLE001 - a decorator we cannot render is recorded as such
            rendered.append("<unrenderable>")
    return rendered


def handler_contract(function: Callable[..., Any]) -> Dict[str, Any]:
    """What this handler declares: its parameters, its guards and its response fields."""
    definition = _handler_definition(function)
    target = inspect.unwrap(function)
    contract: Dict[str, Any] = {
        "handler": getattr(target, "__name__", "<unknown>"),
        "module": getattr(target, "__module__", "<unknown>"),
    }
    if definition is None:
        contract["source_readable"] = False
        return contract

    contract["decorators"] = _decorators(definition)
    contract["parameters"] = _declared_parameters(target)
    contract["returns"] = [
        _inferred_type(node.value) if node.value is not None else "null"
        for node in _own_returns(definition)
    ]
    contract["raises"] = _raised_status_codes(definition)
    return contract


def _declared_parameters(function: Callable[..., Any]) -> Dict[str, Any]:
    """Parameter name to annotation, with the dependency-injected ones named as such."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return {}
    declared: Dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        annotation = parameter.annotation
        rendered = (
            getattr(annotation, "__name__", None)
            or (str(annotation) if annotation is not inspect.Parameter.empty else "untyped")
        )
        default = parameter.default
        entry: Dict[str, Any] = {"annotation": str(rendered)}
        depends = getattr(default, "dependency", None)
        if depends is not None:
            # A dependency can be a function or a callable instance (``HTTPBearer()``).
            # An instance's ``repr`` carries its memory address, which would make this
            # capture differ on every run, so the class name is used instead.
            entry["depends_on"] = getattr(depends, "__name__", None) or type(depends).__name__
        elif default is not inspect.Parameter.empty:
            entry["has_default"] = True
        declared[name] = entry
    return {name: declared[name] for name in sorted(declared)}


# ══════════════════════════════════════════════════════════════════════════
#  ROUTE RESOLUTION
# ══════════════════════════════════════════════════════════════════════════

_ROUTE_INDEX: Optional[Dict[Tuple[str, str], List[Any]]] = None


def _app() -> Any:
    from backend_app.main import app

    return app


def _normalised(path: str) -> str:
    """``/api/risk/strategy-limits/{strategy_id}`` and ``/…/{strategyId}`` agree here.

    A path parameter's *name* is the frontend's business and the backend's independently;
    only its position addresses a route. The query string addresses no route at all.
    """
    return re.sub(r"\{[^}]*\}", "{}", _resolvable(path).rstrip("/") or "/")


def route_index() -> Dict[Tuple[str, str], List[Any]]:
    global _ROUTE_INDEX
    if _ROUTE_INDEX is None:
        index: Dict[Tuple[str, str], List[Any]] = {}
        for route in _app().routes:
            path = getattr(route, "path", None)
            if not path:
                continue
            for method in sorted(getattr(route, "methods", None) or []):
                index.setdefault((method, _normalised(path)), []).append(route)
        _ROUTE_INDEX = index
    return _ROUTE_INDEX


def route_contract(method: str, path: str) -> Dict[str, Any]:
    """The contract of one API call, resolved against the running application."""
    method = method.upper()
    contract: Dict[str, Any] = {"method": method, "path": path}
    routes = route_index().get((method, _normalised(path)), [])
    if not routes:
        # A frontend call with no backend route is a fact about the unmodified system and
        # is recorded as one rather than being silently dropped.
        contract["resolved"] = False
        return contract

    contract["resolved"] = True
    handlers = []
    for route in routes:
        entry: Dict[str, Any] = {"route_path": getattr(route, "path", None)}
        status_code = getattr(route, "status_code", None)
        if status_code is not None:
            entry["status_code"] = status_code
        response_model = getattr(route, "response_model", None)
        if response_model is not None:
            entry["response_model"] = getattr(response_model, "__name__", str(response_model))
            fields = getattr(response_model, "model_fields", None)
            if isinstance(fields, dict):
                entry["response_model_fields"] = {
                    name: str(getattr(field.annotation, "__name__", field.annotation))
                    for name, field in sorted(fields.items())
                }
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            entry.update(handler_contract(endpoint))
        handlers.append(entry)

    # Two routes can answer the same path (``/api/strategies`` is registered by two
    # routers). Both are recorded, in a stable order, because which one shadows the other
    # is precisely what task 13 changes.
    contract["handlers"] = sorted(handlers, key=lambda h: json.dumps(h, sort_keys=True))
    contract["handler_count"] = len(handlers)
    return contract


# ══════════════════════════════════════════════════════════════════════════
#  FRONTEND API CALL EXTRACTION
#
#  Requirement 25.4 asks for "every API call the surface makes". The surface's own source
#  is the only authority on that, so the list is derived from it rather than maintained by
#  hand beside it.
# ══════════════════════════════════════════════════════════════════════════

_VERB_OF_HELPER = {
    "get": "GET",
    "publicGet": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "del": "DELETE",
}

_METHOD_DEFINITION = re.compile(r"\n {2}(\w+):\s*(?:async\s*)?\(")
_HELPER_CALL_START = re.compile(r"\b(get|publicGet|post|put|patch|del)\(\s*")
_API_USAGE = re.compile(r"\bapi\.(\w+)\.(\w+)\s*\(")
_ENDPOINTS_USAGE = re.compile(r"\bendpoints\.(\w+)\.(\w+)\s*\(")
_MODULE_USAGE = re.compile(r"\b(\w+)Api\.(\w+)\s*\(")
_SUPABASE_AUTH_CALL = re.compile(r"\bsupabase\.auth\.((?:mfa\.)?\w+)\s*\(")

#: ``api.<name>`` to the module file that defines it, read from ``src/api/index.js``.
_API_MODULE_BINDING = re.compile(r"^\s{2}(\w+):\s*(\w+)Api,\s*$", re.MULTILINE)

#: A ``${…}`` hole that names one value, as opposed to one that computes a fragment.
_SIMPLE_HOLE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")


def _skip_js_expression(source: str, index: int) -> int:
    """Index just past the ``}`` closing a ``${`` at ``index`` (which points at ``{``).

    Brace counting alone is not enough: ``${status ? `?status=${status}` : ''}`` nests a
    template literal, its own ``${}`` and two quote styles inside the expression.
    """
    depth = 0
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char in "'\"`":
            _, index = _read_js_literal(source, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def _read_js_literal(source: str, index: int) -> Tuple[Optional[str], int]:
    """Read one JS string literal starting at ``index``. Returns its text and the next index.

    A template literal's ``${…}`` holes are preserved verbatim so ``_template`` can decide
    which of them names a value and which computes a fragment.
    """
    if index >= len(source) or source[index] not in "'\"`":
        return None, index
    quote = source[index]
    index += 1
    out: List[str] = []
    while index < len(source):
        char = source[index]
        if char == "\\":
            out.append(source[index : index + 2])
            index += 2
            continue
        if quote == "`" and char == "$" and source[index + 1 : index + 2] == "{":
            end = _skip_js_expression(source, index + 1)
            out.append(source[index:end])
            index = end
            continue
        if char == quote:
            return "".join(out), index + 1
        out.append(char)
        index += 1
    return "".join(out), index


def _template(path: str) -> str:
    """``/x/${id}?a=${b}`` becomes ``/x/{id}?a={b}`` — a stable, comparable template.

    A hole that names one value keeps its name. A hole that computes a URL *fragment* —
    ``${status ? `?status=${status}` : ''}`` — becomes ``{expr}``: its text is a whole
    conditional and would make the template unreadable and brittle to reformatting.
    """
    out: List[str] = []
    index = 0
    while index < len(path):
        if path.startswith("${", index):
            end = _skip_js_expression(path, index + 1)
            inner = path[index + 2 : end - 1].strip()
            out.append("{" + inner + "}" if _SIMPLE_HOLE.match(inner) else "{expr}")
            index = end
            continue
        out.append(path[index])
        index += 1
    return "".join(out)


_CONCATENATION = re.compile(r"\s*\+\s*")


def _read_concatenated_path(source: str, index: int) -> Tuple[Optional[str], int]:
    """``\\`/a/${x}\\` + \\`/b/${y}/c\\``` read as one path. ``previewNode`` is written that way."""
    literal, index = _read_js_literal(source, index)
    if literal is None:
        return None, index
    parts = [literal]
    while True:
        joiner = _CONCATENATION.match(source, index)
        if joiner is None:
            break
        following, after = _read_js_literal(source, joiner.end())
        if following is None:
            break
        parts.append(following)
        index = after
    return "".join(parts), index


def _helper_calls(body: str, *, only_api_paths: bool = False) -> List[Dict[str, str]]:
    """Every ``get``/``post``/``put``/``del`` call in ``body``, with its path template.

    Three shapes occur in this codebase and all three are read:

    * one literal — ``get('/api/paper/account')``;
    * two literals concatenated — ``post(`/a/${id}` + `/nodes/${n}/preview`, body)``;
    * a literal chosen inside the argument — ``get(currency ? `…?c=${currency}` : '…')``,
      where every ``/api/`` literal in the argument list is a path the call can take.
    """
    calls: List[Dict[str, str]] = []
    seen: List[Dict[str, str]] = []
    for match in _HELPER_CALL_START.finditer(body):
        method = _VERB_OF_HELPER[match.group(1)]
        candidates: List[str] = []
        literal, _ = _read_concatenated_path(body, match.end())
        if literal is not None:
            candidates.append(literal)
        else:
            # The first argument is an expression. Every ``/api/`` literal inside the call's
            # argument list is a path this call can request, so all of them are recorded.
            end = _skip_js_arguments(body, match.end())
            index = match.end()
            while index < end:
                if body[index] in "'\"`":
                    nested, index = _read_js_literal(body, index)
                    if nested and nested.startswith("/api/"):
                        candidates.append(nested)
                    continue
                index += 1
        for candidate in candidates:
            if only_api_paths and not candidate.startswith("/api/"):
                continue
            call = {"method": method, "path_template": _template(candidate)}
            if call not in seen:
                seen.append(call)
                calls.append(call)
    return calls


def _skip_js_arguments(source: str, index: int) -> int:
    """Index of the ``)`` closing the argument list whose contents start at ``index``."""
    depth = 1
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            _, index = _read_js_literal(source, index)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return index


def _resolvable(path: str) -> str:
    """The part of a path template that addresses a route.

    A query string addresses no route, and neither does a ``{expr}`` hole spliced into the
    middle of a segment — both are dropped. A ``{name}`` hole that *is* a whole segment is
    a path parameter and is kept.
    """
    without_query = path.split("?", 1)[0]
    return re.sub(r"(?<!/)\{[^}]*\}", "", without_query)


def api_module_index() -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    """``{api key: {method name: [{verb, path_template}, …]}}`` for every module."""
    index_source = (FRONTEND_SRC / "api" / "index.js").read_text(encoding="utf-8")
    bindings = {key: module for key, module in _API_MODULE_BINDING.findall(index_source)}

    modules: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    for key, module_name in sorted(bindings.items()):
        module_path = FRONTEND_SRC / "api" / "modules" / f"{module_name}.js"
        if not module_path.exists():
            modules[key] = {}
            continue
        source = module_path.read_text(encoding="utf-8")
        matches = list(_METHOD_DEFINITION.finditer(source))
        methods: Dict[str, List[Dict[str, str]]] = {}
        for position, match in enumerate(matches):
            start = match.end()
            end = matches[position + 1].start() if position + 1 < len(matches) else len(source)
            # Recorded even when no literal path is found: a method whose URL is built by a
            # helper (``dataQualityApi.forStrategy``) still exists, and reporting it as
            # "no such method" would be wrong. Its empty binding is the honest answer.
            methods[match.group(1)] = _helper_calls(source[start:end])
        modules[key] = {"module_file": f"api/modules/{module_name}.js", **methods}
    return modules


def surface_api_calls(relative_files: Sequence[str]) -> Dict[str, Any]:
    """Every API call the named surface files make, resolved to a backend route."""
    modules = api_module_index()
    module_by_suffix = {
        key.lower(): key for key in modules
    }  # ``portfolioApi`` -> ``portfolio``

    calls: List[Dict[str, Any]] = []
    unresolved_usages: List[Dict[str, str]] = []
    sources: Dict[str, str] = {}
    for relative in relative_files:
        sources[relative] = (FRONTEND_SRC / relative).read_text(encoding="utf-8")

    usages: List[Tuple[str, str]] = []
    for source in sources.values():
        usages.extend(_API_USAGE.findall(source))
        # ``endpoints`` is the legacy alias for the same object and is still the idiom on
        # several surfaces (``Backtester.jsx`` uses it exclusively).
        usages.extend(_ENDPOINTS_USAGE.findall(source))
        for module_name, method in _MODULE_USAGE.findall(source):
            key = module_by_suffix.get(module_name.lower())
            if key:
                usages.append((key, method))

    for module_key, method in sorted(set(usages)):
        module = modules.get(module_key)
        if module is None:
            unresolved_usages.append({"usage": f"api.{module_key}.{method}", "reason": "no such api module"})
            continue
        if method not in module:
            unresolved_usages.append(
                {"usage": f"api.{module_key}.{method}", "reason": "no such method in module"}
            )
            continue
        bound = module[method]
        if not bound:
            calls.append(
                {
                    "via": f"api.{module_key}.{method}",
                    "path_template": "<built at runtime>",
                    "method": "UNKNOWN",
                    "resolved": False,
                }
            )
            continue
        for call in bound:
            if not isinstance(call, dict):
                continue
            calls.append(
                {
                    "via": f"api.{module_key}.{method}",
                    "path_template": call["path_template"],
                    **route_contract(call["method"], call["path_template"]),
                }
            )

    direct: List[Dict[str, Any]] = []
    for source in sources.values():
        for call in _helper_calls(source, only_api_paths=True):
            direct.append(
                {
                    "via": "apiClient direct",
                    "path_template": call["path_template"],
                    **route_contract(call["method"], call["path_template"]),
                }
            )

    # One surface can reach the same route from two files, and a module method can be both
    # bound and called directly. The set of calls is what the requirement asks for, so a
    # repeat is collapsed rather than counted twice.
    everything: List[Dict[str, Any]] = []
    for entry in calls + direct:
        if entry not in everything:
            everything.append(entry)
    everything.sort(key=lambda c: (c["method"], c["path_template"], c["via"]))

    # The authentication, sign-up and sign-in surfaces talk to Supabase Auth directly
    # rather than through the platform's API, so their call set is empty above. Those calls
    # are the surface's behaviour and are recorded too, or Requirement 25.4 would be
    # verified against nothing for three of its seventeen surfaces.
    supabase_calls = sorted(
        {call for source in sources.values() for call in _SUPABASE_AUTH_CALL.findall(source)}
    )

    return {
        "source_files": sorted(relative_files),
        "api_call_count": len(everything),
        "api_calls": everything,
        "supabase_auth_calls": supabase_calls,
        "unresolved_usages": sorted(
            unresolved_usages, key=lambda u: json.dumps(u, sort_keys=True)
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
#  1. LIVE ORDER PATH — core/execution_engine.execute_with_idempotency
# ══════════════════════════════════════════════════════════════════════════


class _CallLog:
    """An ordered record of which collaborator was called with which argument names.

    Argument *names* and argument *shapes*, never argument values: the values carry a
    tenant id, a price and a timestamp, and none of those is the contract.
    """

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    def record(self, name: str, **detail: Any) -> None:
        entry: Dict[str, Any] = {"call": name}
        entry.update({key: detail[key] for key in sorted(detail)})
        self.entries.append(entry)

    def as_json(self) -> List[Dict[str, Any]]:
        return self.entries


def capture_live_order_path() -> Dict[str, Any]:
    """The collaborator call sequence and result shape of ``execute_with_idempotency``.

    Requirement 25.1. The engine's four collaborators — the kill switch, the strategy
    check, the ``execution_records`` repository and the trade execution itself — are
    replaced by recording doubles, so what is captured is the *order the engine calls
    them in* and the *shape of what it returns*, for the representative intent and for
    each guard that short-circuits it.
    """
    from uuid import UUID

    from backend_app.core import execution_engine as engine_module
    from backend_app.core.models.execution_record import ExecutionStatus

    tenant = UUID("11111111-1111-4111-8111-111111111111")
    intent = {
        "tenant_id": tenant,
        "strategy_id": "strategy-baseline",
        "symbol": "BTCUSDT",
        "side": "buy",
        "size": Decimal("0.25"),
        "price": Decimal("61234.50"),
        "source": "bot_runner",
    }

    scenarios: List[Dict[str, Any]] = []

    def build_engine(log: _CallLog) -> Any:
        engine = engine_module.ExecutionEngine.__new__(engine_module.ExecutionEngine)
        engine.exchange_executor = None

        def validate(tenant_id: Any, strategy_id: str) -> bool:
            log.record("ExecutionEngine._validate_strategy_exists", returns="bool")
            return True

        async def execute_internal(**kwargs: Any) -> Tuple[bool, Any]:
            log.record(
                "ExecutionEngine._execute_trade_internal",
                arguments=sorted(kwargs),
            )
            return True, {"order_id": "ord-baseline", "filled_size": "0.25"}

        engine._validate_strategy_exists = validate  # type: ignore[attr-defined]
        engine._execute_trade_internal = execute_internal  # type: ignore[attr-defined]
        return engine

    class _Repo:
        def __init__(self, log: _CallLog, *, action: str) -> None:
            self._log = log
            self._action = action

        def check_idempotent_execution(self, **kwargs: Any) -> Tuple[str, str, Any]:
            self._log.record(
                "ExecutionRecordRepository.check_idempotent_execution",
                arguments=sorted(kwargs),
                returns_action=self._action,
            )
            existing = {"order_id": "ord-existing"} if self._action == "skip_return_result" else None
            return "exec-baseline", self._action, existing

        def claim_execution(self, execution_id: str, tenant_id: Any) -> Tuple[bool, Any]:
            self._log.record("ExecutionRecordRepository.claim_execution", returns="tuple[bool, record]")
            return True, {"execution_id": execution_id}

        def update_status(self, **kwargs: Any) -> None:
            self._log.record(
                "ExecutionRecordRepository.update_status",
                arguments=sorted(kwargs),
                status=getattr(kwargs.get("status"), "value", str(kwargs.get("status"))),
            )

    def run(*, action: str, kill_switch_active: bool, source: str) -> Dict[str, Any]:
        log = _CallLog()

        class _Session:
            def close(self) -> None:
                log.record("SessionLocal.close")

        class _KillSwitch:
            async def is_active(self) -> bool:
                log.record("GlobalKillSwitch.is_active", returns="bool")
                return kill_switch_active

        def session_local() -> Any:
            log.record("SessionLocal")
            return _Session()

        def repo_factory(_session: Any) -> Any:
            log.record("ExecutionRecordRepository")
            return _Repo(log, action=action)

        class _Metrics:
            def record_success(self, tenant_id: str) -> None:
                log.record("execution_metrics.record_success")

        import backend_app.core.global_safety as global_safety

        originals = {
            "SessionLocal": engine_module.SessionLocal,
            "ExecutionRecordRepository": engine_module.ExecutionRecordRepository,
            "execution_metrics": engine_module.execution_metrics,
            "get_global_kill_switch": global_safety.get_global_kill_switch,
        }
        engine_module.SessionLocal = session_local
        engine_module.ExecutionRecordRepository = repo_factory
        engine_module.execution_metrics = _Metrics()
        global_safety.get_global_kill_switch = lambda: _KillSwitch()
        try:
            engine = build_engine(log)
            payload = dict(intent)
            payload["source"] = source
            result = _run(engine.execute_with_idempotency(**payload))
        finally:
            engine_module.SessionLocal = originals["SessionLocal"]
            engine_module.ExecutionRecordRepository = originals["ExecutionRecordRepository"]
            engine_module.execution_metrics = originals["execution_metrics"]
            global_safety.get_global_kill_switch = originals["get_global_kill_switch"]

        return {
            "collaborator_sequence": log.as_json(),
            "result_status": result.get("status"),
            "result": response_shape(result),
        }

    scenarios.append({"scenario": "representative_intent_completes", **run(
        action="proceed", kill_switch_active=False, source="bot_runner"
    )})
    scenarios.append({"scenario": "already_completed_returns_cached", **run(
        action="skip_return_result", kill_switch_active=False, source="bot_runner"
    )})
    scenarios.append({"scenario": "already_executing_is_skipped", **run(
        action="skip_already_running", kill_switch_active=False, source="bot_runner"
    )})
    scenarios.append({"scenario": "global_kill_switch_blocks", **run(
        action="proceed", kill_switch_active=True, source="bot_runner"
    )})
    scenarios.append({"scenario": "non_bot_runner_source_blocked", **run(
        action="proceed", kill_switch_active=False, source="api"
    )})

    return {
        "capture": "live_order_path",
        "requirement": "25.1",
        "subject": "backend_app/core/execution_engine.py::ExecutionEngine.execute_with_idempotency",
        "intent": {
            "symbol": intent["symbol"],
            "side": intent["side"],
            "size": str(intent["size"]),
            "price": str(intent["price"]),
            "source": intent["source"],
        },
        "execution_statuses_available": sorted(
            member.value for member in ExecutionStatus
        ),
        "scenarios": scenarios,
        "contract": handler_contract(engine_module.ExecutionEngine.execute_with_idempotency),
    }


# ══════════════════════════════════════════════════════════════════════════
#  2. CREDENTIAL VAULT — the read/decrypt sequence for a connection fetch
# ══════════════════════════════════════════════════════════════════════════


def capture_credential_vault() -> Dict[str, Any]:
    """The read/decrypt call sequence of ``CredentialVault.get_credential``.

    Requirement 25.1. Redis is a recording in-memory double. Nothing captured here is a
    secret: the Redis key is recorded as a *template* with its four segments named, and
    the plaintext and ciphertext are recorded only by whether the round trip returned the
    value that went in.
    """
    from backend_app.core import credential_vault as vault_module
    from backend_app.core.credential_vault import CredentialType

    log = _CallLog()
    store: Dict[str, str] = {}

    def _key_template(key: str) -> str:
        """A Redis key with its identifier segments replaced by their names.

        The identifiers are a tenant id, a user id and a SHA-256 of the three of them —
        none of them belongs in a committed baseline, and none of them is the contract.
        The *shape* of the key is.
        """
        parts = key.split(":")
        named = {
            "tenant": "{tenant_id}",
            "user": "{user_id}",
            "credential": "{credential_id}",
            "credential_access_log": "{log_id}",
        }
        rendered: List[str] = []
        index = 0
        while index < len(parts):
            label = parts[index]
            rendered.append(label)
            if label in named and index + 1 < len(parts):
                rendered.append(named[label])
                index += 2
                continue
            index += 1
        return ":".join(rendered)

    class _Redis:
        """A recording in-memory stand-in for every Redis call the vault makes."""

        def __init__(self) -> None:
            self.audit_log: List[str] = []

        async def get(self, key: str) -> Optional[str]:
            log.record("redis_manager.get", key_template=_key_template(key))
            return store.get(key)

        async def setex(self, key: str, ttl: int, value: str) -> None:
            log.record("redis_manager.setex", key_template=_key_template(key), ttl_seconds=ttl)
            store[key] = value

        async def delete(self, key: str) -> None:
            log.record("redis_manager.delete", key_template=_key_template(key))
            store.pop(key, None)

        async def lpush(self, key: str, value: str) -> None:
            log.record("redis_manager.lpush", key_template=_key_template(key))
            self.audit_log.insert(0, value)

        async def keys(self, pattern: str) -> List[str]:
            log.record("redis_manager.keys", pattern_template=_key_template(pattern))
            return [key for key in sorted(store) if key.startswith(pattern.rstrip("*"))]

    redis_double = _Redis()
    original_redis = vault_module.redis_manager
    vault_module.redis_manager = redis_double  # type: ignore[assignment]
    try:
        vault = vault_module.CredentialVault(encryption_key=_VAULT_KEY, salt=_VAULT_SALT)

        credential_id = vault._generate_credential_id(
            CAPTURE_USER_ID, "binance", CredentialType.API_KEY
        )
        salt = "ZmFrZS1zYWx0LWZvci1jYXB0dXJl"
        secret = "baseline-plaintext-not-a-real-credential"
        record = vault_module.ExchangeCredential(
            credential_id=credential_id,
            user_id=CAPTURE_USER_ID,
            tenant_id=CAPTURE_TENANT_ID,
            exchange_id="binance",
            credential_type=CredentialType.API_KEY,
            encrypted_value=vault._encrypt(secret, salt),
            salt=salt,
        )
        stored_row = record.to_dict()
        key = f"tenant:{CAPTURE_TENANT_ID}:user:{CAPTURE_USER_ID}:credential:{credential_id}"
        store[key] = json.dumps(stored_row)

        log.record("--- fetch by the owning tenant ---")
        owner_value = _run(
            vault.get_credential(
                user_id=CAPTURE_USER_ID,
                tenant_id=CAPTURE_TENANT_ID,
                exchange_id="binance",
                credential_type=CredentialType.API_KEY,
            )
        )

        log.record("--- fetch by another tenant ---")
        foreign_value = _run(
            vault.get_credential(
                user_id=CAPTURE_USER_ID,
                tenant_id="22222222-2222-4222-8222-222222222222",
                exchange_id="binance",
                credential_type=CredentialType.API_KEY,
            )
        )

        log.record("--- fetch of an absent credential ---")
        absent_value = _run(
            vault.get_credential(
                user_id=CAPTURE_USER_ID,
                tenant_id=CAPTURE_TENANT_ID,
                exchange_id="kraken",
                credential_type=CredentialType.API_KEY,
            )
        )

        after = json.loads(store[key])

        # The tenant check inside get_credential cannot be reached through the key alone —
        # the key is tenant-scoped, so a foreign tenant's read misses. It is reached only
        # when a row's *stored* tenant disagrees with the key it is filed under, which is
        # the state a mis-scoped write would leave behind. Seeded here so the second
        # isolation control and its audit record are captured too.
        log.record("--- fetch of a row whose stored tenant disagrees with its key ---")
        store[key] = json.dumps({**stored_row, "tenant_id": "not-the-owning-tenant"})
        mismatched_value = _run(
            vault.get_credential(
                user_id=CAPTURE_USER_ID,
                tenant_id=CAPTURE_TENANT_ID,
                exchange_id="binance",
                credential_type=CredentialType.API_KEY,
            )
        )

        credential_id_is_derived = credential_id == vault._generate_credential_id(
            CAPTURE_USER_ID, "binance", CredentialType.API_KEY
        )
        audit_rows = [json.loads(row) for row in redis_double.audit_log]
        audit_actions = [
            {"action": row.get("action"), "success": row.get("success")}
            for row in reversed(audit_rows)
        ]
        audit_leaks_no_secret = not any(
            secret in row or record.encrypted_value in row
            for row in redis_double.audit_log
        )
    finally:
        vault_module.redis_manager = original_redis  # type: ignore[assignment]

    return {
        "capture": "credential_vault",
        "requirement": "25.1",
        "subject": "backend_app/core/credential_vault.py::CredentialVault.get_credential",
        "credential_types": sorted(member.value for member in CredentialType),
        "stored_row": response_shape(stored_row),
        "redis_key_template": "tenant:{tenant_id}:user:{user_id}:credential:{credential_id}",
        "credential_id_is_a_pure_derivation": credential_id_is_derived,
        "collaborator_sequence": log.as_json(),
        "outcomes": {
            "owning_tenant_gets_the_plaintext_back": owner_value == secret,
            "foreign_tenant_gets": shape_of(foreign_value),
            "absent_credential_gets": shape_of(absent_value),
            "tenant_mismatch_gets": shape_of(mismatched_value),
            "a_refusal_is_indistinguishable_from_an_absence": (
                foreign_value == absent_value == mismatched_value
            ),
        },
        "access_tracking_after_a_read": {
            "access_count": after.get("access_count"),
            "last_accessed_is_set": after.get("last_accessed") is not None,
        },
        "access_log_row": response_shape(audit_rows[0]) if audit_rows else None,
        "access_log_sequence": audit_actions,
        "access_log_carries_no_plaintext_and_no_ciphertext": audit_leaks_no_secret,
        "get_credential_contract": handler_contract(
            vault_module.CredentialVault.get_credential
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
#  3–5. LIVE RUNTIME, SIGNAL GENERATION, SIGNAL TRACE
# ══════════════════════════════════════════════════════════════════════════

#: A ``strategy_deployments`` row for a LIVE deployment, deliberately carrying credentials
#: it must not leak — the containment claim is that a credential *cannot* travel even when
#: the input holds one.
_LIVE_DEPLOYMENT = {
    "id": "dep-baseline",
    "user_id": CAPTURE_USER_ID,
    "strategy_id": "strategy-baseline",
    "version": "v3",
    "version_id": "version-baseline",
    "exchange_account_id": "account-baseline",
    "exchange_id": "kraken",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "mode": "live",
    "worker_id": "worker-baseline",
    "api_key": "NOT-A-REAL-KEY-0001",
    "api_secret": "NOT-A-REAL-SECRET-0001",
    "passphrase": "NOT-A-REAL-PASSPHRASE",
    "access_token": "NOT-A-REAL-TOKEN",
}

_ACTION_OUTPUT = {
    "decision": "BUY",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "quantity": 0.25,
    "price": 61234.5,
    "bar_time": "2024-05-01T12:00:00+00:00",
    "source_node_ids": ["action-1"],
    "closure_ready": True,
    "node_closure": {
        "data-1": {"node_type": "DATA", "ready": True, "value": 61234.5},
        "rsi-1": {"node_type": "INDICATOR", "ready": True, "value": 28.4},
        "logic-1": {"node_type": "LOGIC", "ready": True, "value": True},
    },
    "risk_validation": {
        "passed": True,
        "reason": "within limits",
        "position_size": 0.25,
        "capital": 10000.0,
        "exposure": 0.15,
        "expected_loss": 120.0,
        "expected_reward": 380.0,
        "drawdown_check": True,
        "evaluated_at": "2024-05-01T12:00:01+00:00",
    },
    "ml_inference": {
        "model_id": "model-baseline",
        "model_version": "3",
        "prediction": "BUY",
        "confidence": 0.81,
        "probabilities": {"BUY": 0.81, "SELL": 0.19},
    },
}

_CAPTURE_INSTANT = "2024-05-01T12:00:00+00:00"
_CAPTURE_SIGNAL_ID = "5f3b9c2e-0000-4000-8000-0000000000aa"


class _RecordingPostgrest:
    """The smallest PostgREST double that answers what the signal path asks of it."""

    def __init__(self, log: _CallLog) -> None:
        self._log = log
        self._table: Optional[str] = None
        self._pending: Optional[Tuple[str, Any]] = None
        self.inserted: List[Tuple[str, Any]] = []
        self.updated: List[Tuple[str, Any]] = []

    def table(self, name: str) -> "_RecordingPostgrest":
        self._table = name
        return self

    def select(self, columns: str) -> "_RecordingPostgrest":
        self._pending = ("select", columns)
        return self

    def insert(self, payload: Any) -> "_RecordingPostgrest":
        self._pending = ("insert", payload)
        return self

    def update(self, payload: Any) -> "_RecordingPostgrest":
        self._pending = ("update", payload)
        return self

    # The filter and modifier vocabulary the signal path chains onto a query. Each is a
    # no-op that returns the builder, because what is being captured is which table was
    # written and with which columns, not which rows a real server would have matched.
    def eq(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def neq(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def is_(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def in_(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def gte(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def lte(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def single(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def maybe_single(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def limit(self, *_args: Any) -> "_RecordingPostgrest":
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_RecordingPostgrest":
        return self

    def execute(self) -> Any:
        kind, payload = self._pending or ("select", "*")
        table = self._table or "<unset>"
        if kind == "select":
            self._log.record("postgrest.select", table=table, columns=str(payload))
            return type("Result", (), {"data": [], "error": None})()
        if kind == "insert":
            self._log.record(
                "postgrest.insert",
                table=table,
                columns=sorted(payload) if isinstance(payload, dict) else "non-dict",
            )
            self.inserted.append((table, payload))
            return type("Result", (), {"data": [dict(payload)], "error": None})()
        self._log.record(
            "postgrest.update",
            table=table,
            columns=sorted(payload) if isinstance(payload, dict) else "non-dict",
        )
        self.updated.append((table, payload))
        return type("Result", (), {"data": [{"id": _CAPTURE_SIGNAL_ID}], "error": None})()


def _minted_signal(service: Any) -> Any:
    from datetime import datetime, timezone

    return service.mint_signal(
        _LIVE_DEPLOYMENT,
        _ACTION_OUTPUT,
        now=datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        signal_id=_CAPTURE_SIGNAL_ID,
    )


def capture_signal_generation() -> Dict[str, Any]:
    """The generated ``Signal`` record's field set and the row it writes.

    Requirement 25.1. ``mint_signal`` is pure and is called with a fixed instant and a
    fixed id, so the record is captured by value as well as by type. ``generate_signal``
    is driven against a recording PostgREST double, so the INSERT it issues is captured
    column by column.
    """
    from dataclasses import fields as dataclass_fields

    from backend_app.backend import signal_service as service

    service.reset_signal_lifecycle_column_support()
    try:
        signal = _minted_signal(service)
        log = _CallLog()
        client = _RecordingPostgrest(log)
        generated = _run(
            service.generate_signal(
                _LIVE_DEPLOYMENT,
                _ACTION_OUTPUT,
                sb=client,
                now=None,
                signal_id=_CAPTURE_SIGNAL_ID,
            )
        )

        refusals: Dict[str, Any] = {}
        for name, output in (
            ("hold_is_not_actionable", {**_ACTION_OUTPUT, "decision": "HOLD"}),
            ("unsized_decision_is_refused", {k: v for k, v in _ACTION_OUTPUT.items() if k != "quantity"}),
        ):
            try:
                service.mint_signal(_LIVE_DEPLOYMENT, output)
            except BaseException as exc:  # noqa: BLE001 - the refusal is the capture
                refusals[name] = _error_of(exc)
            else:
                refusals[name] = {"error_type": None}

        credential_markers = [
            _LIVE_DEPLOYMENT["api_key"],
            _LIVE_DEPLOYMENT["api_secret"],
            _LIVE_DEPLOYMENT["passphrase"],
            _LIVE_DEPLOYMENT["access_token"],
        ]
        serialised = json.dumps(signal.to_row(include_lifecycle_columns=True), default=str)
    finally:
        service.reset_signal_lifecycle_column_support()

    return {
        "capture": "signal_generation",
        "requirement": "25.1",
        "subject": "backend_app/backend/signal_service.py::mint_signal, generate_signal",
        "record_fields": sorted(f.name for f in dataclass_fields(signal)),
        "record_type_map": shape_of(
            {f.name: getattr(signal, f.name) for f in dataclass_fields(signal)}
        ),
        "record_values": {
            "decision": signal.decision,
            "signal_type": signal.signal_type,
            "side": signal.side,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "mode": signal.mode,
            "quantity": signal.quantity,
            "closure_ready": signal.closure_ready,
            "risk_passed": signal.risk_passed,
            "order_lifecycle_state": signal.order_lifecycle_state.value,
            "idempotency_key": signal.idempotency_key,
            "generated_at": signal.generated_at,
            "order_id": signal.order_id,
            "execution_id": signal.execution_id,
        },
        "idempotency_key_is_derived_from_the_id_alone": signal.idempotency_key
        == f"signal:{signal.id}",
        "signals_row_with_lifecycle_columns": sorted(
            signal.to_row(include_lifecycle_columns=True)
        ),
        "signals_row_without_lifecycle_columns": sorted(
            signal.to_row(include_lifecycle_columns=False)
        ),
        "lifecycle_columns": sorted(service.SIGNAL_LIFECYCLE_COLUMNS),
        "lifecycle_migration": service.SIGNAL_LIFECYCLE_MIGRATION,
        "initial_state": service.INITIAL_ORDER_LIFECYCLE_STATE.value,
        "actionable_decisions": sorted(service.ACTIONABLE_DECISIONS),
        "entry_decisions": sorted(service.ENTRY_DECISIONS),
        "exit_decisions": sorted(service.EXIT_DECISIONS),
        "no_credential_reaches_the_record": not any(
            marker in serialised for marker in credential_markers
        ),
        "generate_signal_collaborator_sequence": log.as_json(),
        "generate_signal_returns_state": generated.order_lifecycle_state.value,
        "refusals": refusals,
        "contract": handler_contract(service.generate_signal),
    }


def capture_signal_trace_live() -> Dict[str, Any]:
    """The recorded ``signals`` row and the lifecycle event sequence for a LIVE signal.

    Requirement 25.1. Driven through ``submit_signal`` against a recording PostgREST
    double, so the sequence captured is the sequence of writes a live signal actually
    produces: the ``signals`` INSERT, the genesis transition, then one appended
    transition row per state change.
    """
    from backend_app.backend import signal_service as service
    from backend_app.backend.order_lifecycle_state import OrderLifecycleState

    service.reset_signal_lifecycle_column_support()
    log = _CallLog()
    client = _RecordingPostgrest(log)
    try:
        signal = _run(
            service.generate_signal(
                _LIVE_DEPLOYMENT,
                _ACTION_OUTPUT,
                sb=client,
                signal_id=_CAPTURE_SIGNAL_ID,
            )
        )
        log.record("--- anchor the GENERATED genesis row ---")
        anchored = _run(service.anchor_generated_transition(client, signal))
    finally:
        service.reset_signal_lifecycle_column_support()

    signals_rows = [payload for table, payload in client.inserted if table == "signals"]
    transition_rows = [
        payload
        for table, payload in client.inserted
        if table == service.ORDER_LIFECYCLE_TRANSITIONS_TABLE
    ]

    return {
        "capture": "signal_trace_live",
        "requirement": "25.1",
        "subject": "backend_app/backend/signal_service.py — the LIVE recording path",
        "signals_table": "signals",
        "signals_row": response_shape(signals_rows[0]) if signals_rows else None,
        "signals_row_columns": sorted(signals_rows[0]) if signals_rows else [],
        # Today the environment travels inside ``market_info`` and there is no
        # ``execution_environment`` column. Requirement 23.1 adds one additively, so this
        # pair of facts is what the later assertion is measured against.
        "mode_recorded_at": (
            "market_info.mode"
            if signals_rows and "mode" in (signals_rows[0].get("market_info") or {})
            else "absent"
        ),
        "mode_value": (
            (signals_rows[0].get("market_info") or {}).get("mode") if signals_rows else None
        ),
        "execution_environment_column_present": bool(
            signals_rows and "execution_environment" in signals_rows[0]
        ),
        "event_table": service.ORDER_LIFECYCLE_TRANSITIONS_TABLE,
        "event_row_columns": sorted(transition_rows[0]) if transition_rows else [],
        "event_row": response_shape(transition_rows[0]) if transition_rows else None,
        "event_sequence": [
            {
                "from_state": payload.get("from_state"),
                "to_state": payload.get("to_state"),
            }
            for payload in transition_rows
        ],
        "genesis_row_was_written": bool(anchored),
        "signal_events_table": service.SIGNAL_EVENTS_TABLE,
        "manual_reconciliation_event": service.MANUAL_RECONCILIATION_EVENT,
        "order_lifecycle_states": [state.value for state in OrderLifecycleState],
        "collaborator_sequence": log.as_json(),
    }


def capture_live_runtime() -> Dict[str, Any]:
    """The live runtime's ACTION-node adapter, closure gate and outcome vocabulary.

    Requirement 25.1. Every function here is pure, so each is called for real and its
    verdict recorded by value.
    """
    from backend_app.backend import signal_service as service
    from backend_app.backend.feed_state import FeedState

    class _TradeIntent:
        """``dag_engine.TradeIntent``'s named reads, as the runtime produces them."""

        def __init__(self, **fields: Any) -> None:
            for name, value in fields.items():
                setattr(self, name, value)

    entry_intent = _TradeIntent(
        node_id="action-1",
        side="buy",
        symbol="BTC/USDT",
        quantity=0.25,
        quantity_type="base_units",
        price=61234.5,
        order_intent="entry",
    )
    exit_intent = _TradeIntent(
        node_id="action-2",
        side=None,
        symbol="BTC/USDT",
        quantity=1.0,
        quantity_type="percent_of_position",
        price=61234.5,
        order_intent="exit",
    )

    adapted_entry = service.action_node_output(entry_intent)
    adapted_exit = service.action_node_output(exit_intent)

    ready = service.closure_readiness(_ACTION_OUTPUT)
    not_ready = service.closure_readiness({**_ACTION_OUTPUT, "closure_ready": False})
    no_evidence = service.closure_readiness(
        {k: v for k, v in _ACTION_OUTPUT.items() if k not in ("closure_ready", "node_closure")}
    )

    return {
        "capture": "live_runtime",
        "requirement": "25.1",
        "subject": "backend_app/backend/signal_service.py::action_node_output, closure_readiness, LiveSignalPath",
        "outcome_vocabulary": sorted(
            value
            for name, value in vars(service).items()
            if name.startswith("OUTCOME_") and isinstance(value, str)
        ),
        "feed_states": sorted(member.value for member in FeedState),
        "adapter": {
            "entry_intent": {
                "keys": sorted(adapted_entry),
                "type_map": shape_of(adapted_entry),
                "decision": adapted_entry.get("decision"),
                "quantity": adapted_entry.get("quantity"),
                "sizing_intention": shape_of(adapted_entry.get("sizing_intention")),
            },
            "exit_intent_with_no_side": {
                "keys": sorted(adapted_exit),
                "decision": adapted_exit.get("decision"),
                "quantity_is_absent_when_sized_as_a_percentage": "quantity"
                not in adapted_exit,
                "sizing_intention": shape_of(adapted_exit.get("sizing_intention")),
            },
        },
        "closure_gate": {
            "reported_ready": ready.ready,
            "reported_not_ready": not_ready.ready,
            "no_evidence": no_evidence.ready,
        },
        "execution_outcome_fields": sorted(
            field.name for field in service.ExecutionOutcome.__dataclass_fields__.values()
        ),
        "risk_verdict_fields": sorted(
            field.name for field in service.RiskVerdict.__dataclass_fields__.values()
        ),
        "live_signal_path_operations": sorted(
            name
            for name, member in vars(service.LiveSignalPath).items()
            if callable(member) and not name.startswith("__")
        ),
        "contract": handler_contract(service.action_node_output),
    }


# ══════════════════════════════════════════════════════════════════════════
#  6. BILLING (Requirement 25.2)
# ══════════════════════════════════════════════════════════════════════════

#: Each Requirement 25.2 path, and the routes that serve it. Named explicitly because the
#: requirement names the *paths*, not the endpoints, and one path is served by more than
#: one route.
_BILLING_PATHS: Dict[str, Dict[str, Any]] = {
    "billing_plan_checkout": {
        "requirement": "25.2 — plan checkout",
        "calls": [
            ("GET", "/api/billing/plans"),
            ("GET", "/api/billing/plan"),
            ("POST", "/api/billing/checkout"),
        ],
        "frontend": ["getPlans", "createCheckout"],
    },
    "billing_entitlements": {
        "requirement": "25.2 — entitlement resolution",
        "calls": [("GET", "/api/billing/entitlements")],
        "frontend": ["getEntitlements"],
    },
    "billing_invoices": {
        "requirement": "25.2 — invoice retrieval",
        "calls": [("GET", "/api/billing/invoices")],
        "frontend": ["getInvoices"],
    },
    "billing_payment_methods": {
        "requirement": "25.2 — payment-method management",
        "calls": [
            ("GET", "/api/billing/payment-methods"),
            ("POST", "/api/billing/payment-methods"),
            ("DELETE", "/api/billing/payment-methods/{method_id}"),
        ],
        "frontend": ["getPaymentMethods", "deletePaymentMethod"],
    },
    "billing_currency": {
        "requirement": "25.2 — currency selection",
        "calls": [
            ("GET", "/api/billing/currency"),
            ("POST", "/api/billing/currency"),
        ],
        "frontend": ["getCurrency", "setCurrency"],
    },
    "billing_portal": {
        "requirement": "25.2 — portal",
        "calls": [("POST", "/api/billing/portal")],
        "frontend": ["openPortal"],
    },
    "billing_cancel": {
        "requirement": "25.2 — cancel",
        "calls": [("POST", "/api/billing/cancel")],
        "frontend": ["cancelSubscription"],
    },
    "billing_resume": {
        "requirement": "25.2 — resume",
        "calls": [("POST", "/api/billing/resume")],
        "frontend": ["resumeSubscription"],
    },
}


def _billing_capture(name: str) -> Callable[[], Dict[str, Any]]:
    spec = _BILLING_PATHS[name]

    def capture() -> Dict[str, Any]:
        modules = api_module_index()
        billing_module = modules.get("billing", {})
        return {
            "capture": name,
            "requirement": spec["requirement"],
            "routes": [route_contract(method, path) for method, path in spec["calls"]],
            "frontend_bindings": {
                method: billing_module.get(method, "MISSING")
                for method in sorted(spec["frontend"])
            },
        }

    capture.__name__ = f"capture_{name}"
    capture.__doc__ = f"{spec['requirement']} — the route contract of the {name} path."
    return capture


# ══════════════════════════════════════════════════════════════════════════
#  7. AUTHENTICATION (Requirement 25.3)
# ══════════════════════════════════════════════════════════════════════════


class _RecordingSupabaseAuth:
    def __init__(self, log: _CallLog, *, with_session: bool, raises: bool) -> None:
        self._log = log
        self._with_session = with_session
        self._raises = raises

    def _result(self, call: str, payload: Any) -> Any:
        self._log.record(call, argument_keys=sorted(payload) if isinstance(payload, dict) else [])
        if self._raises:
            raise RuntimeError("supabase refused")
        user = type("User", (), {"id": CAPTURE_USER_ID, "email": _CAPTURE_EMAIL})()
        session = (
            type("Session", (), {"access_token": "baseline.access.token"})()
            if self._with_session
            else None
        )
        return type("Response", (), {"user": user, "session": session})()

    def sign_up(self, payload: Any) -> Any:
        return self._result("supabase.auth.sign_up", payload)

    def sign_in_with_password(self, payload: Any) -> Any:
        return self._result("supabase.auth.sign_in_with_password", payload)


class _RecordingSupabase:
    def __init__(self, log: _CallLog, *, with_session: bool, raises: bool = False) -> None:
        self.auth = _RecordingSupabaseAuth(log, with_session=with_session, raises=raises)


def _auth_client(*, with_session: bool, raises: bool = False) -> Tuple[Any, _CallLog]:
    """A ``TestClient`` whose only overridden dependency is the Supabase client."""
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_supabase

    app = _app()
    log = _CallLog()
    app.dependency_overrides[get_supabase] = lambda: _RecordingSupabase(
        log, with_session=with_session, raises=raises
    )
    return TestClient(app, raise_server_exceptions=False), log


def _clear_overrides() -> None:
    _app().dependency_overrides.clear()


#: ``example.com`` rather than ``example.test``: ``EmailStr`` rejects reserved TLDs, so a
#: ``.test`` address never reaches the handler and would capture a 422 instead of the path.
_CAPTURE_EMAIL = "baseline@example.com"

_REGISTRATION_BODY = {
    "email": _CAPTURE_EMAIL,
    "password": "Baseline-Passphrase-9!",
    "username": "baseline",
    "phone_number": None,
    "referral_code": None,
}


def capture_auth_register() -> Dict[str, Any]:
    """``POST /api/auth/register`` — both confirmation branches. Requirement 25.3."""
    outcomes: Dict[str, Any] = {}
    for name, with_session in (("session_returned", True), ("verification_required", False)):
        client, log = _auth_client(with_session=with_session)
        try:
            response = client.post("/api/auth/register", json=_REGISTRATION_BODY)
        finally:
            _clear_overrides()
        outcomes[name] = {
            "status_code": response.status_code,
            **response_shape(response.json()),
            "verification_required": response.json().get("verification_required"),
            "collaborator_sequence": log.as_json(),
        }

    client, log = _auth_client(with_session=False, raises=True)
    try:
        rejected = client.post("/api/auth/register", json=_REGISTRATION_BODY)
    finally:
        _clear_overrides()
    outcomes["provider_refused"] = {
        "status_code": rejected.status_code,
        "keys": key_paths(rejected.json()),
    }

    client, _ = _auth_client(with_session=True)
    try:
        invalid = client.post(
            "/api/auth/register", json={**_REGISTRATION_BODY, "email": "not-an-email"}
        )
    finally:
        _clear_overrides()
    outcomes["invalid_email_is_rejected_before_the_provider"] = {
        "status_code": invalid.status_code
    }

    return {
        "capture": "auth_register",
        "requirement": "25.3 — registration",
        "route": route_contract("POST", "/api/auth/register"),
        "request_fields": sorted(_REGISTRATION_BODY),
        "outcomes": outcomes,
    }


def capture_auth_signin() -> Dict[str, Any]:
    """``POST /api/auth/login`` and the sign-out/identity pair. Requirement 25.3."""
    body = {"email": _CAPTURE_EMAIL, "password": "Baseline-Passphrase-9!"}
    outcomes: Dict[str, Any] = {}

    client, log = _auth_client(with_session=True)
    try:
        accepted = client.post("/api/auth/login", json=body)
    finally:
        _clear_overrides()
    outcomes["credentials_accepted"] = {
        "status_code": accepted.status_code,
        **response_shape(accepted.json()),
        "collaborator_sequence": log.as_json(),
    }

    client, log = _auth_client(with_session=False)
    try:
        no_session = client.post("/api/auth/login", json=body)
    finally:
        _clear_overrides()
    outcomes["no_session_returned"] = {
        "status_code": no_session.status_code,
        "keys": key_paths(no_session.json()),
        "collaborator_sequence": log.as_json(),
    }

    client, _ = _auth_client(with_session=True)
    try:
        unauthenticated = client.get("/api/auth/me")
    finally:
        _clear_overrides()
    outcomes["identity_without_a_bearer_token"] = {
        "status_code": unauthenticated.status_code,
        "keys": key_paths(unauthenticated.json()),
    }

    return {
        "capture": "auth_signin",
        "requirement": "25.3 — sign-in",
        "routes": [
            route_contract("POST", "/api/auth/login"),
            route_contract("POST", "/api/auth/google"),
            route_contract("POST", "/api/auth/signout"),
            route_contract("GET", "/api/auth/me"),
        ],
        "request_fields": sorted(body),
        "outcomes": outcomes,
    }


def capture_auth_token() -> Dict[str, Any]:
    """Token decoding and the identity dict every handler is handed. Requirement 25.3."""
    import time

    import jwt

    from backend_app.core import auth_middleware

    secret = _CAPTURE_ENV["SUPABASE_JWT_SECRET"]
    issued_at = 1_700_000_000
    claims = {
        "sub": CAPTURE_USER_ID,
        "email": _CAPTURE_EMAIL,
        "role": "authenticated",
        "aud": "authenticated",
        # ``decode_token_local`` verifies against the Supabase ES256 JWKS first and only
        # falls back to the HS256 test secret for a token that claims this issuer. Anything
        # else is refused outright, which is the control this capture pins.
        "iss": "algo22-test",
        "iat": issued_at,
        "exp": int(time.time()) + 3600,
        "app_metadata": {"role": "user", "tenant_id": CAPTURE_TENANT_ID},
        "user_metadata": {"username": "baseline"},
    }
    token = jwt.encode(claims, secret, algorithm="HS256")
    decoded = auth_middleware.decode_token_local(token)

    verdicts: Dict[str, Any] = {}
    for name, mutation in (
        ("unsigned_none_algorithm", jwt.encode(claims, key="", algorithm="none")),
        ("wrong_secret", jwt.encode(claims, "not-the-secret", algorithm="HS256")),
        ("expired", jwt.encode({**claims, "exp": issued_at + 1}, secret, algorithm="HS256")),
        ("foreign_issuer", jwt.encode({**claims, "iss": "somebody-else"}, secret, algorithm="HS256")),
        ("wrong_audience", jwt.encode({**claims, "aud": "anon"}, secret, algorithm="HS256")),
        ("not_a_token", "not-a-token"),
    ):
        try:
            auth_middleware.decode_token_local(mutation)
        except BaseException as exc:  # noqa: BLE001 - the refusal is the capture
            verdicts[name] = _error_of(exc)
        else:
            verdicts[name] = {"error_type": None, "accepted": True}

    # The identity dict get_current_user builds. Captured by projecting the same claim
    # reads the dependency performs, so the field set is pinned without the Supabase
    # freeze-check round trip the dependency also makes.
    identity = {
        "id": decoded.get("sub"),
        "email": decoded.get("email", ""),
        "tenant_id": decoded.get("tenant_id")
        or decoded.get("app_metadata", {}).get("tenant_id")
        or decoded.get("sub"),
        "access_token": token,
        "role": decoded.get("role", "authenticated"),
        "app_metadata": decoded.get("app_metadata", {}),
    }

    return {
        "capture": "auth_token",
        "requirement": "25.3 — token handling",
        "subject": "backend_app/core/auth_middleware.py::decode_token_local, "
        "backend_app/core/dependencies.py::get_current_user",
        "accepted_claims": sorted(decoded),
        "claim_type_map": shape_of({k: decoded[k] for k in sorted(decoded)}),
        "identity_fields": sorted(identity),
        "identity_type_map": shape_of(identity),
        "tenant_id_falls_back_to_the_subject": identity["tenant_id"] == CAPTURE_USER_ID,
        "rejections": verdicts,
        "ws_ticket_route": route_contract("POST", "/api/auth/ws-ticket"),
        "get_current_user_contract": handler_contract(
            __import__(
                "backend_app.core.dependencies", fromlist=["get_current_user"]
            ).get_current_user
        ),
    }


_TWOFA_MFA_CALL = re.compile(r"supabase\.auth\.mfa\.(\w+)\(")


def capture_auth_2fa() -> Dict[str, Any]:
    """Two-factor enrolment: the AAL2 gate, and the enrolment call set. Requirement 25.3.

    Enrolment itself is a Supabase Auth MFA flow driven from the browser, so the backend
    half of the contract is ``require_aal2`` — which is executed here against every claim
    shape — and the frontend half is the set of ``supabase.auth.mfa.*`` calls the 2FA
    surface makes, read out of its source.
    """
    from fastapi import HTTPException

    from backend_app.core.dependencies import require_aal2

    def verdict(user: Dict[str, Any]) -> Dict[str, Any]:
        try:
            _run(require_aal2(user))
        except HTTPException as exc:
            return {"allowed": False, "status_code": exc.status_code}
        return {"allowed": True}

    surface_sources = {
        "pages/TwoFA.jsx": (FRONTEND_SRC / "pages" / "TwoFA.jsx").read_text(encoding="utf-8"),
        "pages/Wizard.jsx": (FRONTEND_SRC / "pages" / "Wizard.jsx").read_text(encoding="utf-8"),
    }
    mfa_calls = sorted(
        {call for source in surface_sources.values() for call in _TWOFA_MFA_CALL.findall(source)}
    )

    return {
        "capture": "auth_2fa",
        "requirement": "25.3 — two-factor enrolment",
        "subject": "backend_app/core/dependencies.py::require_aal2 + the 2FA surface",
        "aal2_gate": {
            "aal2_in_app_metadata": verdict({"id": CAPTURE_USER_ID, "app_metadata": {"aal": "aal2"}}),
            "aal2_at_top_level": verdict({"id": CAPTURE_USER_ID, "aal": "aal2"}),
            "aal1": verdict({"id": CAPTURE_USER_ID, "app_metadata": {"aal": "aal1"}}),
            "no_aal_claim_at_all": verdict({"id": CAPTURE_USER_ID}),
            "aal2_in_user_metadata_is_not_trusted": verdict(
                {"id": CAPTURE_USER_ID, "user_metadata": {"aal": "aal2"}}
            ),
        },
        "frontend_mfa_calls": mfa_calls,
        "frontend_sources": sorted(surface_sources),
    }


def capture_auth_admin_role() -> Dict[str, Any]:
    """Administrative role resolution. Requirement 25.3.

    ``get_admin_user`` and ``get_operator_user`` are pure functions of the identity dict,
    so the whole decision table is executed rather than described. The row that matters
    most is ``user_metadata`` — writable by any authenticated user through the browser SDK
    — being refused.
    """
    from fastapi import HTTPException

    from backend_app.core.dependencies import get_admin_user, get_operator_user

    def verdict(dependency: Callable[..., Any], user: Dict[str, Any]) -> Dict[str, Any]:
        try:
            _run(dependency(user))
        except HTTPException as exc:
            return {"allowed": False, "status_code": exc.status_code}
        return {"allowed": True}

    identities = {
        "app_metadata_admin": {"app_metadata": {"role": "admin"}},
        "app_metadata_support": {"app_metadata": {"role": "support"}},
        "app_metadata_operator": {"app_metadata": {"role": "operator"}},
        "app_metadata_user": {"app_metadata": {"role": "user"}},
        "no_app_metadata": {},
        "user_metadata_admin": {"user_metadata": {"role": "admin"}, "app_metadata": {}},
        "top_level_role_admin": {"role": "admin", "app_metadata": {}},
    }

    return {
        "capture": "auth_admin_role",
        "requirement": "25.3 — administrative role resolution",
        "subject": "backend_app/core/dependencies.py::get_admin_user, get_operator_user",
        "get_admin_user": {
            name: verdict(get_admin_user, {"id": CAPTURE_USER_ID, **identity})
            for name, identity in sorted(identities.items())
        },
        "get_operator_user": {
            name: verdict(get_operator_user, {"id": CAPTURE_USER_ID, **identity})
            for name, identity in sorted(identities.items())
        },
        "admin_routes": [
            route_contract("GET", "/api/admin/users"),
            route_contract("POST", "/api/admin/users/{user_id}/status"),
            route_contract("POST", "/api/admin/kill-all"),
            route_contract("GET", "/api/library/admin/pending"),
            route_contract("PATCH", "/api/library/admin/{library_id}"),
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
#  8. THE SEVENTEEN REQUIREMENT 25.4 SURFACES
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 25.4's surface names, each bound to the frontend source that *is* the
#: surface. Every API call the surface makes is then read out of that source.
_SURFACES: Dict[str, List[str]] = {
    "authentication": ["pages/AuthPage.jsx", "pages/TwoFA.jsx", "pages/UpdatePasswordPage.jsx"],
    "sign_up": ["pages/AuthPage.jsx", "pages/Wizard.jsx"],
    "sign_in": ["pages/AuthPage.jsx"],
    "dashboard": ["pages/Dashboard.jsx"],
    "strategy_builder": ["pages/StrategyBuilder.jsx"],
    "strategies": ["pages/Strategies.jsx", "pages/StrategyDetail.jsx"],
    "backtester": ["pages/Backtester.jsx"],
    "signal_trace": ["pages/SignalTrace.jsx"],
    "exchange_connection": ["pages/ExchangeManager.jsx"],
    "risk_settings": ["pages/RiskSettings.jsx"],
    "portfolio": ["pages/Portfolio.jsx"],
    "trade_history": ["pages/TradeHistory.jsx"],
    "billing": ["pages/Billing.jsx"],
    "notifications": ["components/NotificationCenter.jsx"],
    "profile": ["pages/Profile.jsx"],
    "security_logs": ["pages/SecurityLogs.jsx"],
    "support": ["components/SupportCenter.jsx"],
}


def _surface_capture(surface: str) -> Callable[[], Dict[str, Any]]:
    files = _SURFACES[surface]

    def capture() -> Dict[str, Any]:
        return {
            "capture": f"surface_{surface}",
            "requirement": "25.4",
            "surface": surface,
            **surface_api_calls(files),
        }

    capture.__name__ = f"capture_surface_{surface}"
    capture.__doc__ = (
        f"Requirement 25.4 — every API call the {surface.replace('_', ' ')} surface makes, "
        f"with each call's response key set and type map."
    )
    return capture


# ══════════════════════════════════════════════════════════════════════════
#  9. RISK UTILISATION (Requirement 25.5)
# ══════════════════════════════════════════════════════════════════════════


def _known_paper_account(user_id: str) -> Any:
    """A paper account with numbers chosen so every branch of the arithmetic is exercised.

    * ``realized_pnl = -300`` against a ``max_daily_loss`` of 500 puts loss utilisation at
      60 % — the WARNING boundary — so the threshold ladder is pinned, not just the ratio.
    * two open positions against a ``max_positions`` of 10 put position utilisation at
      20 %.
    * a locked balance makes ``margin_ratio`` and ``free_margin`` non-trivial.
    """
    from backend_app.backend.paper_trading_service import get_paper_trading_service

    service = get_paper_trading_service()
    service.reset_account(user_id, capital=100_000.0)
    account = service.get_or_create_account(user_id)
    account["realized_pnl"] = "-300.00"
    account["available_balance"] = "70000.00"
    account["locked_balance"] = "10000.00"
    service._positions[str(user_id)] = {
        "BTC-USDT": {
            "symbol": "BTC-USDT",
            "side": "long",
            "size": "0.25",
            "entry_price": "60000.00",
            "current_price": "61000.00",
            "unrealized_pnl": "250.00",
            "created_at": "2024-05-01T12:00:00+00:00",
            "updated_at": "2024-05-01T12:00:00+00:00",
        },
        "ETH-USDT": {
            "symbol": "ETH-USDT",
            "side": "short",
            "size": "2.0",
            "entry_price": "3500.00",
            "current_price": "3400.00",
            "unrealized_pnl": "200.00",
            "created_at": "2024-05-01T12:00:00+00:00",
            "updated_at": "2024-05-01T12:00:00+00:00",
        },
    }
    return service


def _authenticated_client(user_id: str) -> Any:
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user

    app = _app()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "sub": user_id,
        "email": _CAPTURE_EMAIL,
        "tenant_id": user_id,
        "access_token": "",
        "role": "authenticated",
        "app_metadata": {},
    }
    return TestClient(app, raise_server_exceptions=False)


def capture_risk_utilisation() -> Dict[str, Any]:
    """The exact numeric semantics of ``routers/risk.py`` lines 300–311 and 355–360.

    Requirement 25.5. This is the one capture that records numbers rather than types,
    because "without changing its reported semantics" is a claim about the numbers. The
    account is built by value, so every figure below is reproducible.
    """
    user_id = f"{CAPTURE_USER_ID}-risk"
    service = _known_paper_account(user_id)
    from backend_app.routers.risk import get_user_risk_settings_store

    settings = get_user_risk_settings_store(user_id)
    client = _authenticated_client(user_id)
    try:
        status = client.get("/api/risk/status")
        margin = client.get("/api/risk/margin-health")
    finally:
        _clear_overrides()

    status_body = status.json()
    margin_body = margin.json()
    account = service.get_or_create_account(user_id)

    return {
        "capture": "risk_utilisation",
        "requirement": "25.5",
        "subject": "backend_app/routers/risk.py::get_risk_status (lines 300-311), "
        "get_margin_health (lines 355-360)",
        "known_account": {
            "initial_capital": account["initial_capital"],
            "available_balance": account["available_balance"],
            "locked_balance": account["locked_balance"],
            "total_equity": account["total_equity"],
            "realized_pnl": account["realized_pnl"],
            "unrealized_pnl": account["unrealized_pnl"],
            "open_position_count": len(service.get_positions(user_id)),
        },
        "risk_settings": {
            "max_daily_loss": settings["max_daily_loss"],
            "max_positions": settings["max_positions"],
            "max_leverage": settings["max_leverage"],
            "circuit_breaker_armed": settings["circuit_breaker_armed"],
        },
        "status_endpoint": {
            "status_code": status.status_code,
            **response_shape(status_body),
            "values": {
                "status": status_body.get("status"),
                "kill_switch_active": status_body.get("kill_switch_active"),
                "daily_loss": status_body.get("daily_loss"),
                "positions": status_body.get("positions"),
                "leverage": status_body.get("leverage"),
                "drawdown_pct": status_body.get("drawdown_pct"),
                "circuit_breaker_armed": status_body.get("circuit_breaker_armed"),
            },
        },
        "margin_health_endpoint": {
            "status_code": margin.status_code,
            **response_shape(margin_body),
            "values": {
                key: margin_body.get(key)
                for key in ("margin_ratio", "free_margin", "risk_score", "currency")
            },
        },
        "semantics": {
            "realized_loss_is_the_negated_floor_of_realized_pnl_at_zero": True,
            "loss_utilisation_pct": "round(realized_loss / max_daily_loss * 100, 2), 0.0 when max_daily_loss <= 0",
            "position_utilisation_pct": "round(open_position_count / max_positions * 100, 2), 0.0 when max_positions <= 0",
            "risk_level_ladder": [
                "BLOCKED when the kill switch is active",
                "BLOCKED at utilisation >= 100",
                "CRITICAL at utilisation >= 85",
                "WARNING at utilisation >= 60",
                "SAFE otherwise",
            ],
            "margin_ratio": "round(locked_balance / total_equity * 100, 2), 0.0 when total_equity <= 0",
            "free_margin": "round(available_balance / total_equity * 100, 2), 100.0 when total_equity <= 0",
            "risk_score": "min(100, int(margin_ratio * 0.8 + (100 - free_margin) * 0.2))",
            "open_position_count_source": "len(paper_service.get_positions(user_id))",
        },
        "routes": [
            route_contract("GET", "/api/risk/status"),
            route_contract("GET", "/api/risk/margin-health"),
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
#  10. THE SIX EXISTING /api/paper/* ENDPOINTS (Requirement 17.12)
# ══════════════════════════════════════════════════════════════════════════

_PAPER_ENDPOINTS: List[Tuple[str, str]] = [
    ("GET", "/api/paper/account"),
    ("GET", "/api/paper/positions"),
    ("GET", "/api/paper/orders"),
    ("GET", "/api/paper/trades"),
    ("GET", "/api/paper/summary"),
    ("POST", "/api/paper/account/reset"),
]


def capture_paper_api_shape() -> Dict[str, Any]:
    """The key sets and value types of the six existing ``/api/paper/*`` endpoints.

    Requirement 17.12. The account is seeded with two fills and one resting limit order
    first, so ``positions``, ``orders`` and ``trades`` are captured with elements in them
    — an empty list would freeze no element shape at all, and the element shape is the
    part a later change is most likely to disturb.
    """
    from backend_app.backend.paper_trading_service import get_paper_trading_service

    user_id = f"{CAPTURE_USER_ID}-paper"
    service = get_paper_trading_service()
    service.reset_account(user_id, capital=100_000.0)

    # An open long, a partial close (which realises PnL and writes a trade), and one
    # resting limit order.
    _run(service.place_order(user_id=user_id, symbol="BTC-USDT", side="buy",
                             order_type="market", quantity=0.5, price=60_000.0))
    _run(service.place_order(user_id=user_id, symbol="BTC-USDT", side="sell",
                             order_type="market", quantity=0.2, price=61_000.0))
    _run(service.place_order(user_id=user_id, symbol="ETH-USDT", side="buy",
                             order_type="limit", quantity=1.0, price=3_000.0))

    client = _authenticated_client(user_id)
    captured: Dict[str, Any] = {}
    try:
        for method, path in _PAPER_ENDPOINTS:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json={"capital": 100_000.0})
            body = response.json()
            captured[f"{method} {path}"] = {
                "status_code": response.status_code,
                **response_shape(body),
                "top_level_keys": sorted(body) if isinstance(body, dict) else "non-object",
            }

        # A filled order and a resting limit order do not carry the same fields, so the
        # unfiltered ``/orders`` shape is a union. Captured per status as well, so a
        # removal from either variant names itself instead of hiding inside the union.
        orders_by_status: Dict[str, Any] = {}
        for status in ("OPEN", "FILLED", "CANCELLED"):
            body = client.get(f"/api/paper/orders?status={status}").json()
            orders_by_status[status] = response_shape(body)
    finally:
        _clear_overrides()

    return {
        "capture": "paper_api_shape",
        "requirement": "17.12",
        "subject": "backend_app/routers/paper_trading.py — the six endpoints api.paper consumes",
        "endpoints": captured,
        "orders_by_status": orders_by_status,
        "order_statuses": sorted(
            member.value
            for member in __import__(
                "backend_app.backend.paper_trading_service",
                fromlist=["PaperOrderStatus"],
            ).PaperOrderStatus
        ),
        "routes": [route_contract(method, path) for method, path in _PAPER_ENDPOINTS],
        "frontend_bindings": {
            name: calls
            for name, calls in sorted(api_module_index().get("paper", {}).items())
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  THE REGISTRY
# ══════════════════════════════════════════════════════════════════════════

CAPTURES: Dict[str, Callable[[], Dict[str, Any]]] = {
    # Live paths — Requirement 25.1
    "live_order_path": capture_live_order_path,
    "credential_vault": capture_credential_vault,
    "live_runtime": capture_live_runtime,
    "signal_generation": capture_signal_generation,
    "signal_trace_live": capture_signal_trace_live,
    # Authentication — Requirement 25.3
    "auth_register": capture_auth_register,
    "auth_signin": capture_auth_signin,
    "auth_token": capture_auth_token,
    "auth_2fa": capture_auth_2fa,
    "auth_admin_role": capture_auth_admin_role,
    # Risk utilisation — Requirement 25.5
    "risk_utilisation": capture_risk_utilisation,
    # Paper API shape — Requirement 17.12
    "paper_api_shape": capture_paper_api_shape,
}

# Billing — Requirement 25.2
for _name in _BILLING_PATHS:
    CAPTURES[_name] = _billing_capture(_name)

# The seventeen surfaces — Requirement 25.4
for _surface in _SURFACES:
    CAPTURES[f"surface_{_surface}"] = _surface_capture(_surface)


def capture(name: str) -> Dict[str, Any]:
    """Take one capture by baseline file name."""
    if name not in CAPTURES:
        raise KeyError(f"No such capture: {name}. Known: {sorted(CAPTURES)}")
    return CAPTURES[name]()


def baseline_path(name: str) -> Path:
    return BASELINE_DIR / f"{name}.json"


def read_baseline(name: str) -> Dict[str, Any]:
    return json.loads(baseline_path(name).read_text(encoding="utf-8"))


def write_baseline(name: str, payload: Dict[str, Any]) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    path = baseline_path(name)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    names = list(argv) if argv else sorted(CAPTURES)
    failures: List[str] = []
    for name in names:
        try:
            payload = capture(name)
        except Exception as exc:  # noqa: BLE001 - report every capture, not just the first
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"FAILED  {name}: {type(exc).__name__}: {exc}")
            continue
        path = write_baseline(name, payload)
        print(f"wrote   {path.relative_to(REPO_ROOT)}")
    if failures:
        print(f"\n{len(failures)} capture(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"\n{len(names)} baseline file(s) written to {BASELINE_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
