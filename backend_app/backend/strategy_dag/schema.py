"""
backend/strategy_dag/schema.py - Canonical DAG model (schema_version 2).

The single graph schema shared by the frontend serializer, the API contract, the
validator, the compiler, the runtime, the backtester and the database. Wire format is
JSON and the same field names are used at every layer, so no layer needs a translation
table and no layer defines its own DTO.

Exposes
-------
PortType, BlockCategory, ValidationState, Port, NodeSpec, EdgeSpec, StrategyGraph
compute_dag_hash(graph)      identity hash, 16 hex chars, ``ui`` excluded
new_node_id() / new_edge_id() stable ULID-based identifiers, minted once
parse_v2(raw)                 strict schema_version 2 parse
load_graph(row)               read-time load of a stored row, v1 or v2
migrate_v1_to_v2(raw)         read-time, non-destructive v1 -> v2 migration

Notes
-----
* There is deliberately **no** ``exchange`` field anywhere in the envelope (SB-06).
  Market identity lives in the DATA node's params; exchange identity lives only in the
  deployment binding. An ``exchange`` key present on an inbound payload is dropped, never
  carried into the canonical graph.
* ``ui`` is presentation-only and excluded from ``compute_dag_hash``, so moving a node on
  the canvas does not change strategy identity.
* Module import pulls only the standard library: no database handle, no HTTP client, no
  FastAPI import. The migration path lazily imports the legacy ``NodeType`` vocabulary
  from ``backend_app.core.models.pydantic_models`` (the single source of the legacy type
  map) *inside* the call, so importing this module - the compiler and validator path -
  stays standard-library only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

#: The schema version emitted by this design.
CURRENT_SCHEMA_VERSION = 2

#: Versions that can be loaded at all. Version 1 is everything currently persisted
#: (``buy_logic._nodes`` / ``_edges``, five-value ``NodeType``, no ports) and is migrated
#: at read time by ``migrate_v1_to_v2``.
SUPPORTED_SCHEMA_VERSIONS: Tuple[int, ...] = (1, 2)

#: Field that must never appear in the canonical envelope (SB-06).
FORBIDDEN_ENVELOPE_FIELDS: Tuple[str, ...] = ("exchange",)

#: Parameter keys a node may never carry, whatever a client sends (SB-06,
#: Requirement 12.1). Two kinds of thing, one rule:
#:
#: * **Exchange identity.** A saved strategy describes trading logic only; the venue is a
#:   deployment binding, so ``exchange`` / ``exchange_id`` on a node is meaningless at
#:   best and, once persisted, is a market nobody chose.
#: * **Credential material.** ``api_key``, ``secret``, ``passphrase`` and friends live in
#:   the credential vault, addressed by exchange account id, and are resolved only inside
#:   the execution process. A strategy row is readable through the API, exported with a
#:   clone and echoed in a validation report, so a key that reached ``params`` would be a
#:   credential leak with a long tail.
#:
#: No published block declares any of these keys, so nothing legitimate is dropped: a node
#: carrying one is a stale client or a probe. Dropping is structural, in the one place
#: every canonical parse goes through, so ``graph_json`` and ``compiled_plan`` cannot carry
#: one even if a caller forgets to filter. ``exchange_account_id`` is deliberately absent:
#: it is a reference to a binding, not a credential.
FORBIDDEN_PARAM_FIELDS: Tuple[str, ...] = (
    "exchange",
    "exchange_id",
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "secret_key",
    "passphrase",
    "password",
    "private_key",
    "credentials",
)

#: Lowercased, for the membership test. Node params are matched case-insensitively so
#: ``apiKey`` - the ccxt spelling - is caught alongside ``api_key``.
_FORBIDDEN_PARAM_KEYS = frozenset(FORBIDDEN_PARAM_FIELDS)


def is_forbidden_param(key: Any) -> bool:
    """True when ``key`` names exchange identity or credential material."""
    return str(key).strip().lower() in _FORBIDDEN_PARAM_KEYS


#: The two forbidden keys whose value is safe to quote back to the author: a venue id is
#: not a secret, and naming it is what makes the warning actionable.
_QUOTABLE_FORBIDDEN_KEYS = frozenset({"exchange", "exchange_id"})

#: What a dropped credential's value is reported as. Never the value itself: a migration
#: report is persisted as ``validation_report`` and returned to the client, so echoing a
#: secret there would move the leak rather than close it.
REDACTED = "<redacted>"


def redact_forbidden_value(key: Any, value: Any) -> Any:
    """The value to report for a dropped ``key`` - the venue, or ``"<redacted>"``."""
    if str(key).strip().lower() in _QUOTABLE_FORBIDDEN_KEYS:
        return value
    return REDACTED


def strip_forbidden_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """``params`` without any exchange-identity or credential key (Requirement 12.1).

    Postcondition
        No key of the result matches :data:`FORBIDDEN_PARAM_FIELDS` case-insensitively,
        and every other key keeps its value unchanged.
    """
    return {
        key: value for key, value in params.items() if not is_forbidden_param(key)
    }


def find_forbidden_params(params: Any) -> List[str]:
    """The forbidden parameter keys present on ``params``, for reporting.

    Parsing already drops them; this lets a caller *say* that it happened rather than
    change the graph silently.
    """
    if not isinstance(params, Mapping):
        return []
    return [str(key) for key in params if is_forbidden_param(key)]


class GraphParseError(ValueError):
    """Raised when a wire payload cannot be read as a canonical graph."""


class UnsupportedSchemaVersion(GraphParseError):
    """Raised when a stored graph declares a schema version this build cannot load."""

    code = "UNSUPPORTED_SCHEMA_VERSION"

    def __init__(self, version: Any):
        self.version = version
        super().__init__(
            f"Unsupported graph schema_version {version!r}; "
            f"supported versions are {list(SUPPORTED_SCHEMA_VERSIONS)}"
        )


# ---------------------------------------------------------------------------
# Canonical enums
# ---------------------------------------------------------------------------


class PortType(str, Enum):
    """The port type vocabulary. Legality is decided by these contracts."""

    OHLCV_FRAME = "OHLCV_FRAME"        # full candle frame: symbol, ts, o,h,l,c,v
    PRICE_SERIES = "PRICE_SERIES"      # a single price-derived series (close, hl2, ...)
    SCALAR_SERIES = "SCALAR_SERIES"    # numeric series in arbitrary units
    BOOLEAN_SERIES = "BOOLEAN_SERIES"  # per-bar truth series
    FEATURE_MATRIX = "FEATURE_MATRIX"  # aligned 2-D matrix + column names + warmup offset
    PREDICTION = "PREDICTION"          # model output series + confidence
    SIGNAL = "SIGNAL"                  # normalised intent strength in [-1, 1]
    TRADE_INTENT = "TRADE_INTENT"      # action payload accepted by the execution layer
    SCALAR = "SCALAR"                  # single constant value

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


class BlockCategory(str, Enum):
    """The seven canonical block categories."""

    DATA = "DATA"
    INDICATOR = "INDICATOR"
    MATH = "MATH"
    LOGIC = "LOGIC"
    FEATURE_ENGINEERING = "FEATURE_ENGINEERING"
    ML_DL = "ML_DL"
    ACTION = "ACTION"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


class ValidationState(str, Enum):
    """Validation state carried by the envelope and persisted on the version row."""

    UNVALIDATED = "UNVALIDATED"
    VALID = "VALID"
    INVALID = "INVALID"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


def _coerce_enum(enum_cls, value: Any, field_name: str):
    """Resolve a wire string onto an enum member, or raise ``GraphParseError``."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            try:
                return enum_cls(value.strip().upper())
            except ValueError:
                pass
    permitted = ", ".join(member.value for member in enum_cls)
    raise GraphParseError(
        f"Invalid {field_name} {value!r}; permitted values are: {permitted}"
    )


# ---------------------------------------------------------------------------
# Identifier minting (ULID)
# ---------------------------------------------------------------------------

NODE_ID_PREFIX = "n_"
EDGE_ID_PREFIX = "e_"

# Crockford base32, the ULID alphabet: no I, L, O or U.
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_TIME_CHARS = 10          # 48 bits of milliseconds
_ULID_RANDOM_CHARS = 16        # 80 bits of randomness
_ULID_LENGTH = _ULID_TIME_CHARS + _ULID_RANDOM_CHARS
_ULID_RANDOM_MAX = (1 << 80) - 1
_ULID_TIME_MAX = (1 << 48) - 1

ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
NODE_ID_PATTERN = re.compile(r"^n_[0-9A-HJKMNP-TV-Z]{26}$")
EDGE_ID_PATTERN = re.compile(r"^e_[0-9A-HJKMNP-TV-Z]{26}$")


def _encode_crockford(value: int, length: int) -> str:
    """Encode ``value`` as a fixed-length Crockford base32 string, most significant first."""
    if value < 0:
        raise ValueError("cannot encode a negative value")
    out = [""] * length
    for position in range(length - 1, -1, -1):
        out[position] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


class _MonotonicUlidFactory:
    """Thread-safe, monotonic ULID source.

    Within one millisecond the random component is incremented rather than redrawn, so
    two ids minted in the same millisecond are distinct and ordered. A backwards clock
    step never produces a duplicate or a lower id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_random = 0

    def new(self) -> str:
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms > self._last_ms:
                self._last_ms = now_ms
                self._last_random = secrets.randbits(80)
            else:
                # Same millisecond, or the clock moved backwards: stay monotonic.
                if self._last_random >= _ULID_RANDOM_MAX:
                    self._last_ms += 1
                    self._last_random = secrets.randbits(80)
                else:
                    self._last_random += 1
            if self._last_ms > _ULID_TIME_MAX:  # pragma: no cover - year 10889
                raise RuntimeError("ULID timestamp overflow")
            return (
                _encode_crockford(self._last_ms, _ULID_TIME_CHARS)
                + _encode_crockford(self._last_random, _ULID_RANDOM_CHARS)
            )


_ULID_FACTORY = _MonotonicUlidFactory()


def generate_ulid() -> str:
    """Return a new 26-character Crockford base32 ULID."""
    return _ULID_FACTORY.new()


def new_node_id() -> str:
    """Mint a node identifier: ``n_`` + ULID.

    Minted once at node creation. Node ids are never reused and never renumbered: edges,
    execution order, model bindings, traces and audit records all key on them.
    """
    return NODE_ID_PREFIX + generate_ulid()


def new_edge_id() -> str:
    """Mint an edge identifier: ``e_`` + ULID."""
    return EDGE_ID_PREFIX + generate_ulid()


def is_minted_node_id(value: Any) -> bool:
    """True when ``value`` is a node id minted by :func:`new_node_id`.

    Legacy ids (``n_data_1`` and similar) are still valid graph ids - they are preserved
    verbatim on load and never renumbered - so this predicate is informational only.
    """
    return isinstance(value, str) and bool(NODE_ID_PATTERN.match(value))


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@dataclass
class Port:
    """A single input or output port on a node.

    Wire shape::

        { "port": "series", "type": "PRICE_SERIES", "required": true }

    ``required`` / ``variadic`` are omitted from the wire form when false and
    ``description`` when empty, which keeps the payload small without losing information.
    """

    name: str
    type: PortType
    required: bool = False
    variadic: bool = False          # AND/OR/SUM accept 2..N
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"port": self.name, "type": self.type.value}
        if self.required:
            out["required"] = True
        if self.variadic:
            out["variadic"] = True
        if self.description:
            out["description"] = self.description
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "Port":
        if not isinstance(data, dict):
            raise GraphParseError(f"Port must be an object, got {type(data).__name__}")
        # ``port`` is the canonical wire key; ``name`` is accepted as an alias so a Port
        # serialized from an internal descriptor also parses.
        name = data.get("port", data.get("name"))
        if not isinstance(name, str) or not name:
            raise GraphParseError("Port is missing a non-empty 'port' name")
        if "type" not in data:
            raise GraphParseError(f"Port '{name}' is missing 'type'")
        return cls(
            name=name,
            type=_coerce_enum(PortType, data["type"], f"port type for port '{name}'"),
            required=bool(data.get("required", False)),
            variadic=bool(data.get("variadic", False)),
            description=str(data.get("description") or ""),
        )


def _parse_ports(raw: Any, owner: str, kind: str) -> List[Port]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GraphParseError(f"Node '{owner}' field '{kind}' must be a list")
    return [Port.from_dict(item) for item in raw]


# ---------------------------------------------------------------------------
# NodeSpec
# ---------------------------------------------------------------------------


@dataclass
class NodeSpec:
    """One block instance on the canvas.

    ``block_id`` and ``category`` come from the registry descriptor. The frontend copies
    them; it never invents them and it never derives them from the display label (SB-05).

    ``inputs`` / ``outputs`` are the *resolved* port list: equal to the descriptor for
    fixed-arity blocks, the instantiated ports for variadic ones. The validator re-derives
    them from the descriptor and rejects any mismatch, so a tampered client cannot widen
    its own contract.
    """

    id: str
    block_id: str
    category: BlockCategory
    params: Dict[str, Any] = field(default_factory=dict)
    inputs: List[Port] = field(default_factory=list)
    outputs: List[Port] = field(default_factory=list)
    ui: Dict[str, Any] = field(default_factory=dict)   # presentation only, excluded from hash

    @classmethod
    def create(
        cls,
        block_id: str,
        category: BlockCategory,
        params: Optional[Dict[str, Any]] = None,
        inputs: Optional[Iterable[Port]] = None,
        outputs: Optional[Iterable[Port]] = None,
        ui: Optional[Dict[str, Any]] = None,
    ) -> "NodeSpec":
        """Create a node, minting its identifier once."""
        return cls(
            id=new_node_id(),
            block_id=block_id,
            category=category,
            params=dict(params or {}),
            inputs=list(inputs or []),
            outputs=list(outputs or []),
            ui=dict(ui or {}),
        )

    def port(self, name: str, *, direction: str = "input") -> Optional[Port]:
        """Return the named port, or ``None`` when the node does not declare it."""
        ports = self.inputs if direction == "input" else self.outputs
        for candidate in ports:
            if candidate.name == name:
                return candidate
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "block_id": self.block_id,
            "category": self.category.value,
            "params": dict(self.params),
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "ui": dict(self.ui),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "NodeSpec":
        if not isinstance(data, dict):
            raise GraphParseError(f"Node must be an object, got {type(data).__name__}")
        node_id = data.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise GraphParseError("Node is missing a non-empty 'id'")
        block_id = data.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise GraphParseError(f"Node '{node_id}' is missing a non-empty 'block_id'")
        if "category" not in data:
            raise GraphParseError(f"Node '{node_id}' is missing 'category'")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise GraphParseError(f"Node '{node_id}' field 'params' must be an object")
        ui = data.get("ui") or {}
        if not isinstance(ui, dict):
            raise GraphParseError(f"Node '{node_id}' field 'ui' must be an object")
        return cls(
            id=node_id,
            block_id=block_id,
            category=_coerce_enum(
                BlockCategory, data["category"], f"category for node '{node_id}'"
            ),
            # SB-06, Requirement 12.1: exchange identity and credential material are
            # dropped here, on the one path every canonical parse takes, so no downstream
            # consumer has to remember to filter and neither ``graph_json`` nor
            # ``compiled_plan`` can carry one. The v1 migration path drops the same keys
            # with a reported warning; here the validator's PARAM_UNKNOWN already tells
            # the author about a param the block does not declare.
            params=strip_forbidden_params(params),
            inputs=_parse_ports(data.get("inputs"), node_id, "inputs"),
            outputs=_parse_ports(data.get("outputs"), node_id, "outputs"),
            ui=dict(ui),
        )


# ---------------------------------------------------------------------------
# EdgeSpec
# ---------------------------------------------------------------------------


@dataclass
class EdgeSpec:
    """A port-addressed connection between two nodes.

    ``type`` is advisory - it is recomputed at validation from the source port descriptor.
    It exists so error messages and the UI can explain a rejection without a registry
    lookup.
    """

    id: str
    source: str
    source_port: str
    target: str
    target_port: str
    type: Optional[PortType] = None

    @classmethod
    def create(
        cls,
        source: str,
        source_port: str,
        target: str,
        target_port: str,
        type: Optional[PortType] = None,
    ) -> "EdgeSpec":
        """Create an edge, minting its identifier once."""
        return cls(
            id=new_edge_id(),
            source=source,
            source_port=source_port,
            target=target,
            target_port=target_port,
            type=type,
        )

    @property
    def address(self) -> str:
        """The port-addressed form used by the identity hash and error messages."""
        return f"{self.source}.{self.source_port}->{self.target}.{self.target_port}"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "source_port": self.source_port,
            "target": self.target,
            "target_port": self.target_port,
        }
        if self.type is not None:
            out["type"] = self.type.value
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "EdgeSpec":
        if not isinstance(data, dict):
            raise GraphParseError(f"Edge must be an object, got {type(data).__name__}")
        edge_id = data.get("id")
        if not isinstance(edge_id, str) or not edge_id:
            raise GraphParseError("Edge is missing a non-empty 'id'")
        values: Dict[str, str] = {}
        for key in ("source", "source_port", "target", "target_port"):
            value = data.get(key)
            if not isinstance(value, str) or not value:
                raise GraphParseError(
                    f"Edge '{edge_id}' is missing a non-empty '{key}'; "
                    "every edge is port-addressed on both ends"
                )
            values[key] = value
        raw_type = data.get("type")
        return cls(
            id=edge_id,
            source=values["source"],
            source_port=values["source_port"],
            target=values["target"],
            target_port=values["target_port"],
            type=(
                None
                if raw_type in (None, "")
                else _coerce_enum(PortType, raw_type, f"type for edge '{edge_id}'")
            ),
        )


# ---------------------------------------------------------------------------
# StrategyGraph
# ---------------------------------------------------------------------------


@dataclass
class StrategyGraph:
    """The canonical graph envelope.

    There is deliberately no ``exchange`` field (SB-06). The ``compiled`` block of the
    wire envelope is compiler output, not graph state: it is ignored on parse and never
    emitted here, so a client cannot supply its own hash or execution order.
    """

    schema_version: int = CURRENT_SCHEMA_VERSION
    strategy_id: str = ""
    version: str = ""
    name: str = ""
    nodes: List[NodeSpec] = field(default_factory=list)
    edges: List[EdgeSpec] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    validation_state: ValidationState = ValidationState.UNVALIDATED

    # -- lookup helpers ----------------------------------------------------
    def node(self, node_id: str) -> Optional[NodeSpec]:
        for candidate in self.nodes:
            if candidate.id == node_id:
                return candidate
        return None

    def node_index(self) -> Dict[str, NodeSpec]:
        return {node.id: node for node in self.nodes}

    def nodes_in_category(self, category: BlockCategory) -> List[NodeSpec]:
        return [node for node in self.nodes if node.category == category]

    # -- wire format -------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "name": self.name,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "metadata": dict(self.metadata),
            "validation_state": self.validation_state.value,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "StrategyGraph":
        """Parse a schema_version 2 envelope.

        Raises ``UnsupportedSchemaVersion`` for anything other than version 2; version 1
        rows are routed through ``migrate_v1_to_v2`` before reaching this parser.
        """
        if not isinstance(data, dict):
            raise GraphParseError(
                f"Graph envelope must be an object, got {type(data).__name__}"
            )

        raw_version = data.get("schema_version")
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise UnsupportedSchemaVersion(raw_version)
        if raw_version != CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(raw_version)

        # Forbidden envelope fields (``exchange``) are structurally dropped: the dataclass
        # has no such field, so nothing carries them into the canonical graph. Callers that
        # want to *report* the attempt use ``find_forbidden_fields`` (SB-06).
        raw_nodes = data.get("nodes") or []
        if not isinstance(raw_nodes, list):
            raise GraphParseError("Graph field 'nodes' must be a list")
        raw_edges = data.get("edges") or []
        if not isinstance(raw_edges, list):
            raise GraphParseError("Graph field 'edges' must be a list")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise GraphParseError("Graph field 'metadata' must be an object")

        return cls(
            schema_version=raw_version,
            strategy_id=str(data.get("strategy_id") or ""),
            version=str(data.get("version") or ""),
            name=str(data.get("name") or ""),
            nodes=[NodeSpec.from_dict(item) for item in raw_nodes],
            edges=[EdgeSpec.from_dict(item) for item in raw_edges],
            metadata=dict(metadata),
            validation_state=_coerce_enum(
                ValidationState,
                data.get("validation_state") or ValidationState.UNVALIDATED.value,
                "validation_state",
            ),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "StrategyGraph":
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise GraphParseError(f"Graph payload is not valid JSON: {exc}") from exc
        return cls.from_dict(decoded)

    @property
    def dag_hash(self) -> str:
        """Convenience accessor; the authoritative value is recomputed on compile."""
        return compute_dag_hash(self)


def parse_v2(raw: Any) -> StrategyGraph:
    """Strict schema_version 2 parse (the ``parse_v2`` step of ``load_graph``)."""
    return StrategyGraph.from_dict(raw)


def find_forbidden_fields(raw: Any) -> List[str]:
    """Return the forbidden envelope fields present on an inbound payload (SB-06).

    Parsing already drops them structurally. This helper lets a caller *report* the
    attempt - a client that still sends ``exchange`` is out of date - instead of dropping
    it silently.
    """
    if not isinstance(raw, dict):
        return []
    return [name for name in FORBIDDEN_ENVELOPE_FIELDS if name in raw]


# ---------------------------------------------------------------------------
# Identity hash
# ---------------------------------------------------------------------------

#: Non-finite floats have no JSON representation; they are mapped to these stable
#: sentinels so a hash can still be computed deterministically.
_NAN_SENTINEL = "NaN"
_POS_INF_SENTINEL = "Infinity"
_NEG_INF_SENTINEL = "-Infinity"


def _normalize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        # bool before int matters: True is not 1 for identity purposes, and json emits
        # ``true`` for bool and ``1`` for int, so the two never collide.
        return value
    if isinstance(value, float):
        if value != value:
            return _NAN_SENTINEL
        if value == float("inf"):
            return _POS_INF_SENTINEL
        if value == float("-inf"):
            return _NEG_INF_SENTINEL
        if value.is_integer() and abs(value) < 2 ** 53:
            # 21 and 21.0 are the same parameter value.
            return int(value)
        return value
    return str(value)


def canonicalize(value: Any) -> Any:
    """Return a JSON-safe, order-independent, numerically normalised copy of ``value``.

    Mapping keys are coerced to strings and sorted, tuples become lists, sets become
    sorted lists, integral floats become ints and non-finite floats become stable
    sentinels. The result is safe to feed to ``json.dumps(..., allow_nan=False)``.
    """
    if isinstance(value, dict):
        items = sorted(((str(key), item) for key, item in value.items()), key=lambda kv: kv[0])
        return {key: canonicalize(item) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json.dumps(canonicalize(item), sort_keys=True) for item in value))
    return _normalize_scalar(value)


def compute_dag_hash(graph: StrategyGraph) -> str:
    """Compute the identity hash of ``graph``: first 16 hex chars of sha256.

    Preconditions
        The graph passed structural validation (unique node ids, all edge endpoints
        resolve). This function does not re-check that; it hashes what it is given.

    Postconditions
        Deterministic across processes and platforms. Identical for graphs differing only
        in ``ui``. Different for any change to the node set, block ids, params,
        categories, port wiring or schema version.

    Loop invariants
        Iteration is over a total order (node id; then the edge address tuple), so the
        accumulated lists are independent of input ordering.

    Contrast with the deleted ``CompiledDAG.compute_hash``, which hashed only node ids and
    ``source->target`` strings: it was blind to parameter changes and to port rewiring, so
    two materially different strategies could share a hash.
    """
    canonical_nodes: List[Dict[str, Any]] = []
    for node in sorted(graph.nodes, key=lambda item: item.id):
        canonical_nodes.append(
            {
                "id": node.id,
                "block_id": node.block_id,
                "category": (
                    node.category.value
                    if isinstance(node.category, BlockCategory)
                    else str(node.category)
                ),
                "params": canonicalize(node.params),
                # node.ui deliberately EXCLUDED
            }
        )

    canonical_edges: List[str] = [
        f"{edge.source}.{edge.source_port}->{edge.target}.{edge.target_port}"
        for edge in sorted(
            graph.edges,
            key=lambda item: (item.source, item.source_port, item.target, item.target_port),
        )
    ]

    payload = json.dumps(
        {
            "schema_version": graph.schema_version,
            "nodes": canonical_nodes,
            "edges": canonical_edges,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schema versioning and migration (design.md "Schema versioning and migration")
# ---------------------------------------------------------------------------
#
# Version 1 is everything currently persisted: ``buy_logic._nodes`` /
# ``buy_logic._edges``, the five-value ``NodeType`` vocabulary, one implicit port per
# node. Version 2 is the canonical model above.
#
# The migration is a **read** operation. It deep-copies its input, never writes back to
# the stored row and never mutates the dict it was handed; the v1 row is rewritten only
# when the author saves a new version, so a rollback loses nothing (Requirement 1.9).
#
# Nothing is guessed. A node whose block cannot be resolved against the registry is
# *marked*: the load succeeds, the graph comes back ``INVALID`` and an
# ``UNRESOLVED_BLOCK`` entry names the node (Requirement 1.10). A node never reaches the
# compiler carrying an invented port contract.

#: ``metadata`` key holding the migration report of a graph produced by
#: :func:`migrate_v1_to_v2`. Absent on graphs parsed straight from version 2.
MIGRATION_METADATA_KEY = "migration"

#: Legacy container keys, as written by ``routers/strategies.py``.
LEGACY_NODES_KEY = "_nodes"
LEGACY_EDGES_KEY = "_edges"
LEGACY_DAG_VERSION_KEY = "_dag_version"
LEGACY_DAG_SCHEMA_VERSION_KEY = "_dag_schema_version"

#: The DATA block a version 1 ``input`` / ``market_data`` node becomes. Version 1 DATA
#: nodes carried no block-identifying field at all, so this is the one substitution the
#: design mandates by name - and it is applied *only* to the DATA category, never as a
#: catch-all for a node whose block cannot be identified.
DEFAULT_DATA_BLOCK_ID = "ohlcv_feed"

#: Version 1 node keys the migration consumes structurally. They are never copied into
#: ``params``: ``indicator`` / ``action`` / ``operator`` become the ``block_id``, and the
#: rest are canonical fields in their own right. ``model_id`` is deliberately *absent*
#: from this set: it is a block_id candidate *and* the reference to the trained artifact
#: a version 1 row was bound to, so it is also kept in ``params`` rather than consumed.
_V1_STRUCTURAL_KEYS: frozenset = frozenset(
    {
        "id",
        "type",
        "node_type",
        "params",
        "inputs",
        "outputs",
        "ui",
        "block_id",
        "category",
        "indicator",
        "action",
        "operator",
    }
)

#: Version 1 node keys that are presentation only. They land in ``ui``, which is excluded
#: from the identity hash, so a migrated graph does not change identity when the canvas is
#: rearranged (Requirement 1.8).
_V1_UI_KEYS: Tuple[str, ...] = (
    "position",
    "x",
    "y",
    "label",
    "collapsed",
    "width",
    "height",
    "color",
    "style",
    "note",
    "selected",
    "dragging",
)

#: Explicit port identity as emitted by the three version 1 frontend formats. When
#: present it is honoured; it is never invented (SB-05).
_V1_SOURCE_PORT_KEYS: Tuple[str, ...] = ("source_port", "sourcePort", "sourceHandle")
_V1_TARGET_PORT_KEYS: Tuple[str, ...] = ("target_port", "targetPort", "targetHandle")

# -- migration issue codes --------------------------------------------------

#: The registry holds no descriptor for a node's block (Requirement 1.10).
CODE_UNRESOLVED_BLOCK = "UNRESOLVED_BLOCK"
#: A version 1 node carried no block-identifying field and is not a DATA node.
CODE_UNIDENTIFIED_BLOCK = "UNIDENTIFIED_BLOCK"
#: A version 1 node's ``type`` is outside the legacy vocabulary, so no canonical
#: category can be derived from it.
CODE_UNMAPPED_NODE_TYPE = "UNMAPPED_NODE_TYPE"
#: No descriptor source was available at all, so no block could be resolved.
CODE_REGISTRY_UNAVAILABLE = "REGISTRY_UNAVAILABLE"
#: An edge endpoint names a node that is not in the graph.
CODE_UNRESOLVED_EDGE_ENDPOINT = "UNRESOLVED_EDGE_ENDPOINT"
#: An edge's ports could not be resolved without guessing.
CODE_UNRESOLVED_EDGE_PORTS = "UNRESOLVED_EDGE_PORTS"
#: The legacy-mapped category disagreed with the registry descriptor; the registry wins.
CODE_CATEGORY_REMAPPED = "CATEGORY_REMAPPED"
#: An ``exchange`` key was present on a version 1 payload and was dropped (SB-06).
CODE_EXCHANGE_FIELD_DROPPED = "EXCHANGE_FIELD_DROPPED"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

#: Category assigned to a node whose category could not be resolved from either its
#: legacy ``type`` or a registry descriptor. Such a node always carries an error marker
#: and the graph is always ``INVALID``, so this value can never reach an executable plan.
#: MATH is used because it is inert in both directions: it cannot source market data and
#: it cannot emit a trade intent.
_UNPLACEABLE_CATEGORY = BlockCategory.MATH


class DescriptorLookup:
    """Structural type of a descriptor source: ``get(block_id) -> descriptor | None``.

    Satisfied by the block registry (``strategy_dag/registry.py``) and by any narrower
    mapping of ``block_id`` to an object exposing ``inputs`` / ``outputs`` (and,
    optionally, ``category`` and ``block_id``). Declared as a plain class rather than a
    ``Protocol`` so this module keeps working on the interpreter versions the backend
    targets.
    """

    def get(self, block_id: str) -> Any:  # pragma: no cover - interface only
        raise NotImplementedError


#: A descriptor source: an object with ``get()``, a mapping, or a plain callable.
Resolver = Any

_DEFAULT_RESOLVER_SENTINEL = object()
_default_resolver_cache: Any = _DEFAULT_RESOLVER_SENTINEL


def reset_default_resolver() -> None:
    """Forget the cached default descriptor source.

    The default source is discovered once and memoised. Tests that install or remove the
    registry module call this to force rediscovery.
    """
    global _default_resolver_cache
    _default_resolver_cache = _DEFAULT_RESOLVER_SENTINEL


def _discover_default_resolver() -> Optional[Callable[[str], Any]]:
    """Resolve the backend-authoritative registry lazily, or ``None`` when absent.

    This is the seam onto ``strategy_dag/registry.py``. It is a lazy import on purpose:
    the migration must not force registry assembly at module import time, and this module
    must keep importing with only the standard library. When the registry is not present,
    the migration reports ``REGISTRY_UNAVAILABLE`` and marks every node - it does not
    fabricate descriptors.
    """
    try:
        from backend_app.backend.strategy_dag import registry as _registry
    except Exception:  # noqa: BLE001 - an absent or broken registry is a reported state
        return None

    getter = getattr(_registry, "get", None)
    if callable(getter):
        return getter

    builder = getattr(_registry, "build_registry", None)
    if callable(builder):
        try:
            built = builder()
        except Exception:  # noqa: BLE001 - reported, never raised through a read path
            return None
        return _as_lookup(built)
    return None


def _as_lookup(resolver: Resolver) -> Optional[Callable[[str], Any]]:
    """Normalise any accepted descriptor source onto ``lookup(block_id)``."""
    if resolver is None:
        return None
    getter = getattr(resolver, "get", None)
    if callable(getter):
        return lambda block_id: getter(block_id)
    if callable(resolver):
        return resolver
    return None


def _resolve_lookup(resolver: Resolver) -> Optional[Callable[[str], Any]]:
    global _default_resolver_cache
    if resolver is not None:
        return _as_lookup(resolver)
    if _default_resolver_cache is _DEFAULT_RESOLVER_SENTINEL:
        _default_resolver_cache = _discover_default_resolver()
    return _as_lookup(_default_resolver_cache)


def make_issue(
    code: str,
    severity: str,
    message: str,
    *,
    node_id: Optional[str] = None,
    edge_id: Optional[str] = None,
    field_name: Optional[str] = None,
    expected: Any = None,
    actual: Any = None,
    fix_hint: str = "",
) -> Dict[str, Any]:
    """Build one structured migration issue.

    The key set is the structured error contract the validator emits, so a caller can
    fold these entries straight into a Validation_Report without translating them.
    """
    return {
        "code": code,
        "severity": severity,
        "node_id": node_id,
        "edge_id": edge_id,
        "field": field_name,
        "message": message,
        "expected": expected,
        "actual": actual,
        "fix_hint": fix_hint,
    }


def migration_report(graph: StrategyGraph) -> Dict[str, Any]:
    """The migration report of ``graph``, or an empty report when it was not migrated."""
    report = graph.metadata.get(MIGRATION_METADATA_KEY)
    return report if isinstance(report, dict) else {}


def migration_issues(graph: StrategyGraph) -> List[Dict[str, Any]]:
    """Every issue recorded while migrating ``graph``, in discovery order."""
    issues = migration_report(graph).get("issues")
    return list(issues) if isinstance(issues, list) else []


def migration_errors(graph: StrategyGraph) -> List[Dict[str, Any]]:
    """The error-severity subset of :func:`migration_issues`."""
    return [i for i in migration_issues(graph) if i.get("severity") == SEVERITY_ERROR]


def unresolved_node_ids(graph: StrategyGraph) -> List[str]:
    """Ids of nodes whose block could not be resolved, so their ports are unknown."""
    marked = migration_report(graph).get("unresolved_nodes")
    return list(marked) if isinstance(marked, list) else []


def is_node_unresolved(graph: StrategyGraph, node_id: str) -> bool:
    """True when ``node_id`` was marked unresolved by the migration."""
    return node_id in unresolved_node_ids(graph)


# -- legacy vocabulary ------------------------------------------------------


def map_legacy_type(raw_type: Any) -> Optional[BlockCategory]:
    """Map a version 1 node ``type`` onto a canonical :class:`BlockCategory`.

    The legacy vocabulary is not restated here. It is read from
    ``backend_app.core.models.pydantic_models`` (``LEGACY_NODE_TYPE_MAP`` and
    ``NodeType.canonical``), which is its single source of truth. Returns ``None`` for a
    value outside that vocabulary - the caller marks the node rather than guessing.
    """
    if isinstance(raw_type, BlockCategory):
        return raw_type
    if raw_type is None:
        return None
    text = str(raw_type).strip().lower()
    if not text:
        return None

    # Lazy: keeps module import standard-library only.
    from backend_app.core.models.pydantic_models import (  # noqa: PLC0415
        LEGACY_NODE_TYPE_MAP,
        NodeType,
    )

    member = LEGACY_NODE_TYPE_MAP.get(text)
    if member is None:
        try:
            member = NodeType(text)
        except ValueError:
            return None
    member = member.canonical
    try:
        return BlockCategory[member.name]
    except KeyError:  # pragma: no cover - the two vocabularies are name-identical
        return None


def _adapt_port(raw: Any, owner: str, kind: str) -> Port:
    """Adapt a registry descriptor port onto the canonical :class:`Port`.

    Descriptor ports are declared by the engines that own them (``IndicatorSpec``,
    ``FeatureSpec``, ``ModelSpec``), each with its own frozen dataclass. They all carry
    ``name`` / ``type`` / ``required`` / ``variadic`` / ``description``, so one adapter
    covers every source without any of them having to know about this module.
    """
    if isinstance(raw, Port):
        return raw
    if isinstance(raw, Mapping):
        return Port.from_dict(dict(raw))
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        return Port.from_dict(to_dict())
    name = getattr(raw, "name", None) or getattr(raw, "port", None)
    if not isinstance(name, str) or not name:
        raise GraphParseError(
            f"Descriptor for node '{owner}' declares an unnamed {kind} port"
        )
    port_type = getattr(raw, "type", None)
    if port_type is None:
        raise GraphParseError(
            f"Descriptor port '{name}' of node '{owner}' declares no type"
        )
    return Port(
        name=name,
        type=_coerce_enum(PortType, port_type, f"port type for port '{name}'"),
        required=bool(getattr(raw, "required", False)),
        variadic=bool(getattr(raw, "variadic", False)),
        description=str(getattr(raw, "description", "") or ""),
    )


def _descriptor_ports(descriptor: Any, owner: str, kind: str) -> List[Port]:
    raw_ports = getattr(descriptor, kind, None)
    if raw_ports is None and isinstance(descriptor, Mapping):
        raw_ports = descriptor.get(kind)
    if raw_ports is None:
        return []
    return [_adapt_port(item, owner, kind) for item in raw_ports]


def _descriptor_category(descriptor: Any) -> Optional[BlockCategory]:
    raw = getattr(descriptor, "category", None)
    if raw is None and isinstance(descriptor, Mapping):
        raw = descriptor.get("category")
    if raw is None:
        return None
    try:
        return _coerce_enum(BlockCategory, raw, "descriptor category")
    except GraphParseError:
        return None


def _descriptor_block_id(descriptor: Any, fallback: str) -> str:
    raw = getattr(descriptor, "block_id", None)
    if raw is None and isinstance(descriptor, Mapping):
        raw = descriptor.get("block_id")
    return raw if isinstance(raw, str) and raw else fallback


def _lookup_descriptor(
    lookup: Optional[Callable[[str], Any]], block_id: str
) -> Tuple[Any, str]:
    """Return ``(descriptor, resolved_block_id)``; descriptor is ``None`` when unknown.

    The lookup is tried verbatim and then lower-cased, because registry block ids are
    lower-case snake_case while version 1 stored operator tokens upper-cased (``"AND"``).
    When the lower-cased form resolves, the descriptor's own id becomes the node's
    ``block_id`` - the registry is authoritative about its own naming.
    """
    if lookup is None:
        return None, block_id
    for candidate in (block_id, block_id.strip().lower()):
        try:
            descriptor = lookup(candidate)
        except Exception:  # noqa: BLE001 - a broken lookup marks, never crashes a read
            return None, block_id
        if descriptor is not None:
            return descriptor, _descriptor_block_id(descriptor, candidate)
    return None, block_id


def _legacy_block_id(node: Mapping[str, Any], category: Optional[BlockCategory]) -> Optional[str]:
    """The block a version 1 node names, or ``None`` when it names none.

    Order is the design's: ``indicator``, ``model_id``, ``action``, ``operator``. The
    ``ohlcv_feed`` substitution is applied only to the DATA category; for every other
    category a node that names no block is marked, never defaulted onto a data feed.
    """
    for key in ("indicator", "model_id", "action", "operator"):
        value = node.get(key)
        if value is None:
            continue
        text = getattr(value, "value", value)
        text = str(text).strip()
        if text:
            return text
    if category is BlockCategory.DATA:
        return DEFAULT_DATA_BLOCK_ID
    return None


def _legacy_scalar_fields(
    node: Mapping[str, Any], issues: List[Dict[str, Any]], node_id: str
) -> Dict[str, Any]:
    """The version 1 fields that are really parameters, keyed as parameters.

    Version 1 spread a node's configuration over named columns (``symbol``,
    ``timeframe``, ``confidence_threshold``, ``order_type``, ``amount``, ...) instead of
    ``params``. Everything that is not structural, not presentation and not forbidden is
    carried across, so nothing a user configured is dropped (SB-05).
    """
    merged: Dict[str, Any] = {}
    for key, value in node.items():
        if key in _V1_STRUCTURAL_KEYS or key in _V1_UI_KEYS:
            continue
        if is_forbidden_param(key):
            issues.append(
                make_issue(
                    CODE_EXCHANGE_FIELD_DROPPED,
                    SEVERITY_WARNING,
                    f"Node '{node_id}' carried '{key}'; exchange identity and credential "
                    "material are bound at deployment time and are not part of a "
                    "strategy.",
                    node_id=node_id,
                    field_name=key,
                    expected=None,
                    actual=redact_forbidden_value(key, value),
                    fix_hint=(
                        "Select the exchange account when deploying this version; the "
                        "graph itself stays exchange-agnostic."
                    ),
                )
            )
            continue
        if value is None:
            continue
        merged[key] = value
    return merged


def _legacy_ui_fields(node: Mapping[str, Any]) -> Dict[str, Any]:
    ui = node.get("ui")
    resolved: Dict[str, Any] = dict(ui) if isinstance(ui, Mapping) else {}
    for key in _V1_UI_KEYS:
        if key in node and node[key] is not None:
            resolved.setdefault(key, node[key])
    return resolved


# -- node migration ---------------------------------------------------------


def _migrate_node(
    raw_node: Any,
    index: int,
    lookup: Optional[Callable[[str], Any]],
    issues: List[Dict[str, Any]],
    unresolved: List[str],
) -> NodeSpec:
    if not isinstance(raw_node, Mapping):
        raise GraphParseError(
            f"Version 1 node at position {index} must be an object, "
            f"got {type(raw_node).__name__}"
        )

    node_id = raw_node.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise GraphParseError(
            f"Version 1 node at position {index} is missing a non-empty 'id'; "
            "node identity cannot be reconstructed and must not be invented"
        )

    raw_type = raw_node.get("type", raw_node.get("node_type"))
    legacy_category = map_legacy_type(raw_type)

    params = _legacy_scalar_fields(raw_node, issues, node_id)
    declared_params = raw_node.get("params")
    if declared_params is not None and not isinstance(declared_params, Mapping):
        raise GraphParseError(f"Node '{node_id}' field 'params' must be an object")
    for key, value in dict(declared_params or {}).items():
        if is_forbidden_param(key):
            issues.append(
                make_issue(
                    CODE_EXCHANGE_FIELD_DROPPED,
                    SEVERITY_WARNING,
                    f"Node '{node_id}' carried params['{key}']; exchange identity and "
                    "credential material are bound at deployment time and are not part "
                    "of a strategy.",
                    node_id=node_id,
                    field_name=f"params.{key}",
                    expected=None,
                    actual=redact_forbidden_value(key, value),
                    fix_hint=(
                        "Select the exchange account when deploying this version; the "
                        "graph itself stays exchange-agnostic."
                    ),
                )
            )
            continue
        # An explicit params entry is the more specific form: it wins over the
        # same key spread across a legacy column.
        params[key] = value

    block_id = _legacy_block_id(raw_node, legacy_category)
    descriptor: Any = None
    if block_id is None:
        issues.append(
            make_issue(
                CODE_UNIDENTIFIED_BLOCK,
                SEVERITY_ERROR,
                f"Node '{node_id}' names no block: it carries none of 'indicator', "
                "'model_id', 'action' or 'operator', and its category is not DATA.",
                node_id=node_id,
                field_name="block_id",
                expected="one of: indicator, model_id, action, operator",
                actual=None,
                fix_hint=(
                    "Open this node in the builder and pick a block from the palette; "
                    "the stored version cannot say which block was intended."
                ),
            )
        )
        block_id = ""
    else:
        descriptor, block_id = _lookup_descriptor(lookup, block_id)

    inputs: List[Port] = []
    outputs: List[Port] = []
    category = legacy_category

    if descriptor is None:
        if block_id:
            unresolved.append(node_id)
            issues.append(
                make_issue(
                    CODE_UNRESOLVED_BLOCK,
                    SEVERITY_ERROR,
                    f"Node '{node_id}' references block '{block_id}', which the block "
                    "registry does not publish.",
                    node_id=node_id,
                    field_name="block_id",
                    expected="a block_id published by the block registry",
                    actual=block_id,
                    fix_hint=(
                        f"Replace node '{node_id}' with a block from the current "
                        "palette, or restore the build that published "
                        f"'{block_id}'."
                    ),
                )
            )
    else:
        inputs = _descriptor_ports(descriptor, node_id, "inputs")
        outputs = _descriptor_ports(descriptor, node_id, "outputs")
        descriptor_category = _descriptor_category(descriptor)
        if descriptor_category is not None:
            if legacy_category is not None and descriptor_category is not legacy_category:
                issues.append(
                    make_issue(
                        CODE_CATEGORY_REMAPPED,
                        SEVERITY_WARNING,
                        f"Node '{node_id}' was stored as '{raw_type}' but block "
                        f"'{block_id}' is a {descriptor_category.value} block; the "
                        "registry decides the category.",
                        node_id=node_id,
                        field_name="category",
                        expected=descriptor_category.value,
                        actual=legacy_category.value,
                        fix_hint="No action needed; the category was corrected on load.",
                    )
                )
            category = descriptor_category

    if category is None:
        category = _UNPLACEABLE_CATEGORY
        issues.append(
            make_issue(
                CODE_UNMAPPED_NODE_TYPE,
                SEVERITY_ERROR,
                f"Node '{node_id}' declares type {raw_type!r}, which is outside the "
                "schema version 1 vocabulary, and its block resolved no category.",
                node_id=node_id,
                field_name="type",
                expected="one of the schema version 1 node types",
                actual=raw_type,
                fix_hint=(
                    f"Replace node '{node_id}' with a block from the current palette; "
                    "its category cannot be derived and must not be guessed."
                ),
            )
        )

    return NodeSpec(
        id=node_id,
        block_id=block_id,
        category=category,
        params=params,
        inputs=inputs,
        outputs=outputs,
        ui=_legacy_ui_fields(raw_node),
    )


# -- edge migration ---------------------------------------------------------


def _explicit_port(raw_edge: Mapping[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = raw_edge.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _sole_output_port(node: NodeSpec) -> Tuple[Optional[str], str]:
    """The single output port of a version 1 node, or a reason it cannot be chosen."""
    if not node.outputs:
        return None, f"node '{node.id}' has no resolved output port"
    if len(node.outputs) > 1:
        names = ", ".join(port.name for port in node.outputs)
        return None, (
            f"node '{node.id}' publishes several output ports ({names}) and the stored "
            "edge names none"
        )
    return node.outputs[0].name, ""


def _next_free_input_port(node: NodeSpec, occupied: Dict[str, int]) -> Tuple[Optional[str], str]:
    """The first input port of ``node`` not already taken, honouring variadic ports."""
    if not node.inputs:
        return None, f"node '{node.id}' has no resolved input port"
    for port in node.inputs:
        used = occupied.get(port.name, 0)
        if port.variadic or used == 0:
            return port.name, ""
    names = ", ".join(port.name for port in node.inputs)
    return None, (
        f"every input port of node '{node.id}' is already connected ({names}) and the "
        "stored edge names none"
    )


def _migrate_edges(
    raw_edges: Sequence[Any],
    nodes_by_id: Dict[str, NodeSpec],
    issues: List[Dict[str, Any]],
) -> Tuple[List[EdgeSpec], List[Dict[str, Any]]]:
    edges: List[EdgeSpec] = []
    unmigrated: List[Dict[str, Any]] = []
    occupied: Dict[str, Dict[str, int]] = {}

    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, Mapping):
            raise GraphParseError(
                f"Version 1 edge at position {index} must be an object, "
                f"got {type(raw_edge).__name__}"
            )
        edge_id = raw_edge.get("id")
        source = raw_edge.get("source")
        target = raw_edge.get("target")
        if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
            raise GraphParseError(
                f"Version 1 edge at position {index} is missing a non-empty "
                "'source' or 'target'"
            )
        if not isinstance(edge_id, str) or not edge_id:
            edge_id = _migrated_edge_id(source, target, index)

        source_node = nodes_by_id.get(source)
        target_node = nodes_by_id.get(target)
        missing = [
            name
            for name, node in (("source", source_node), ("target", target_node))
            if node is None
        ]
        if missing:
            unmigrated.append(dict(raw_edge))
            issues.append(
                make_issue(
                    CODE_UNRESOLVED_EDGE_ENDPOINT,
                    SEVERITY_ERROR,
                    f"Edge '{edge_id}' references a node that is not in the graph "
                    f"({', '.join(missing)}).",
                    edge_id=edge_id,
                    field_name=missing[0],
                    expected="a node id present in the graph",
                    actual={"source": source, "target": target},
                    fix_hint="Redraw this connection between two existing blocks.",
                )
            )
            continue

        source_port = _explicit_port(raw_edge, _V1_SOURCE_PORT_KEYS)
        source_reason = ""
        if source_port is None:
            source_port, source_reason = _sole_output_port(source_node)

        target_port = _explicit_port(raw_edge, _V1_TARGET_PORT_KEYS)
        target_reason = ""
        if target_port is None:
            target_port, target_reason = _next_free_input_port(
                target_node, occupied.setdefault(target, {})
            )

        if source_port is None or target_port is None:
            unmigrated.append(dict(raw_edge))
            reason = "; ".join(part for part in (source_reason, target_reason) if part)
            issues.append(
                make_issue(
                    CODE_UNRESOLVED_EDGE_PORTS,
                    SEVERITY_ERROR,
                    f"Edge '{edge_id}' from '{source}' to '{target}' could not be "
                    f"port-addressed: {reason}.",
                    edge_id=edge_id,
                    field_name="source_port" if source_port is None else "target_port",
                    expected="a port published by the block registry for this node",
                    actual={"source": source, "target": target},
                    fix_hint=(
                        "Redraw this connection in the builder, choosing the ports "
                        "explicitly; the stored version does not record them."
                    ),
                )
            )
            continue

        occupied.setdefault(target, {})
        occupied[target][target_port] = occupied[target].get(target_port, 0) + 1

        source_port_spec = source_node.port(source_port, direction="output")
        edges.append(
            EdgeSpec(
                id=edge_id,
                source=source,
                source_port=source_port,
                target=target,
                target_port=target_port,
                type=source_port_spec.type if source_port_spec is not None else None,
            )
        )

    return edges, unmigrated


def _migrated_edge_id(source: str, target: str, index: int) -> str:
    """A stable id for a version 1 edge that carried none.

    Derived from the endpoints and the edge's position, so reloading the same row twice
    yields the same id instead of churning a fresh ULID on every read. Edge ids are not
    part of the identity hash, which addresses edges by their ports.
    """
    digest = hashlib.sha256(f"{index}|{source}|{target}".encode("utf-8")).hexdigest()
    return f"{EDGE_ID_PREFIX}mig_{digest[:16]}"


# -- migration entry points -------------------------------------------------


def normalize_schema_version(value: Any) -> int:
    """Return the declared schema version, or raise :class:`UnsupportedSchemaVersion`.

    ``None`` means version 1: the earliest rows predate the field entirely. Anything that
    is not one of the supported integers - including a float, a numeric string or a
    boolean - is rejected naming the declared value (Requirement 1.11).
    """
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsupportedSchemaVersion(value)
    if value not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(value)
    return value


def _raw_node_list(raw: Mapping[str, Any]) -> List[Any]:
    for key in ("nodes", LEGACY_NODES_KEY):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, list):
                raise GraphParseError(f"Graph field '{key}' must be a list")
            return list(value)
    return []


def _raw_edge_list(raw: Mapping[str, Any]) -> List[Any]:
    for key in ("edges", LEGACY_EDGES_KEY):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, list):
                raise GraphParseError(f"Graph field '{key}' must be a list")
            return list(value)
    return []


def migrate_v1_to_v2(raw: Any, *, resolver: Resolver = None) -> StrategyGraph:
    """Migrate a schema version 1 payload to a canonical version 2 graph, at read time.

    Parameters
        ``raw``
            The stored version 1 payload: ``nodes`` and ``edges`` lists, with or without a
            ``schema_version`` of 1. Never mutated - the function works on a deep copy.
        ``resolver``
            Descriptor source used to resolve ports and categories: an object with
            ``get(block_id)``, a mapping, or a callable. Defaults to the backend block
            registry, discovered lazily. When no source is available every block is
            marked ``UNRESOLVED_BLOCK`` and the graph reports ``REGISTRY_UNAVAILABLE``.

    Preconditions
        ``raw`` deserializes as a JSON object with ``nodes`` and ``edges`` lists.

    Postconditions
        The result is a version 2 :class:`StrategyGraph`. Unresolvable blocks are marked,
        never guessed. The migration is read-only: neither ``raw`` nor the stored row is
        written, so the version 1 row is rewritten only when the author saves a new
        version and a rollback loses nothing. Any error-severity marker leaves
        ``validation_state`` at ``INVALID``; otherwise the graph is ``UNVALIDATED``,
        because a migration is not a validation.

    Loop invariants
        Each processed node either carries a registry-resolved port set or is marked
        unresolved; no node reaches the compiler with an invented contract. Each edge is
        either port-addressed on both ends or recorded in the report as unmigrated with an
        error naming it; no edge is silently dropped and no port name is invented.
    """
    if not isinstance(raw, Mapping):
        raise GraphParseError(
            f"Version 1 graph payload must be an object, got {type(raw).__name__}"
        )

    working: Dict[str, Any] = copy.deepcopy(dict(raw))
    declared = normalize_schema_version(working.get("schema_version"))
    if declared != 1:
        raise GraphParseError(
            f"migrate_v1_to_v2 was handed a schema_version {declared} payload; "
            "use load_graph() to route a stored row, or parse_v2() for a version 2 graph"
        )

    lookup = _resolve_lookup(resolver)
    issues: List[Dict[str, Any]] = []
    unresolved: List[str] = []

    for name in find_forbidden_fields(working):
        issues.append(
            make_issue(
                CODE_EXCHANGE_FIELD_DROPPED,
                SEVERITY_WARNING,
                f"The stored graph carried '{name}' in its envelope; exchange identity "
                "is bound at deployment time and is not part of a strategy.",
                field_name=name,
                expected=None,
                actual=working.get(name),
                fix_hint=(
                    "Select the exchange account when deploying this version; the graph "
                    "itself stays exchange-agnostic."
                ),
            )
        )

    if lookup is None:
        issues.append(
            make_issue(
                CODE_REGISTRY_UNAVAILABLE,
                SEVERITY_ERROR,
                "No block registry was available, so no block could be resolved and no "
                "port contract could be recovered.",
                expected="an assembled block registry",
                actual=None,
                fix_hint=(
                    "Reload this strategy once the block registry is serving; the stored "
                    "version is untouched."
                ),
            )
        )

    nodes = [
        _migrate_node(raw_node, index, lookup, issues, unresolved)
        for index, raw_node in enumerate(_raw_node_list(working))
    ]
    nodes_by_id: Dict[str, NodeSpec] = {}
    for node in nodes:
        nodes_by_id.setdefault(node.id, node)

    edges, unmigrated_edges = _migrate_edges(_raw_edge_list(working), nodes_by_id, issues)

    metadata = working.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    for name in FORBIDDEN_ENVELOPE_FIELDS:
        metadata.pop(name, None)
    metadata[MIGRATION_METADATA_KEY] = {
        "from_schema_version": 1,
        "to_schema_version": CURRENT_SCHEMA_VERSION,
        "read_only": True,
        "registry_available": lookup is not None,
        "unresolved_nodes": list(unresolved),
        "unmigrated_edges": unmigrated_edges,
        "issues": issues,
    }

    has_error = any(issue["severity"] == SEVERITY_ERROR for issue in issues)
    return StrategyGraph(
        schema_version=CURRENT_SCHEMA_VERSION,
        strategy_id=str(working.get("strategy_id") or ""),
        version=str(working.get("version") or ""),
        name=str(working.get("name") or ""),
        nodes=nodes,
        edges=edges,
        metadata=metadata,
        validation_state=(
            ValidationState.INVALID if has_error else ValidationState.UNVALIDATED
        ),
    )


def _row_field(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, Mapping):
            if name in row and row[name] is not None:
                return row[name]
        else:
            value = getattr(row, name, None)
            if value is not None:
                return value
    return None


def _decode_json_field(value: Any, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise GraphParseError(f"Row field '{field_name}' is not valid JSON: {exc}") from exc
    return value


def legacy_extract(buy_logic: Any, row: Any = None) -> Dict[str, Any]:
    """Lift a version 1 graph out of a strategy row's ``buy_logic`` blob.

    Version 1 stored the DAG inside ``buy_logic`` under ``_nodes`` / ``_edges``, with
    ``_dag_version`` and ``_dag_schema_version`` alongside. The blob is read, never
    written: the returned payload is a fresh dict.
    """
    decoded = _decode_json_field(buy_logic, "buy_logic")
    if not isinstance(decoded, Mapping):
        raise GraphParseError(
            "Row carries no canonical graph and no version 1 'buy_logic' object"
        )
    if LEGACY_NODES_KEY not in decoded and LEGACY_EDGES_KEY not in decoded:
        raise GraphParseError(
            "Row 'buy_logic' holds no '_nodes' / '_edges'; there is no graph to load"
        )
    extracted: Dict[str, Any] = {
        "schema_version": decoded.get(LEGACY_DAG_SCHEMA_VERSION_KEY),
        "nodes": copy.deepcopy(decoded.get(LEGACY_NODES_KEY) or []),
        "edges": copy.deepcopy(decoded.get(LEGACY_EDGES_KEY) or []),
    }
    if row is not None:
        strategy_id = _row_field(row, "strategy_id", "id")
        name = _row_field(row, "name")
        version = _row_field(row, "version", "dag_version")
        if strategy_id is not None:
            extracted["strategy_id"] = str(strategy_id)
        if name is not None:
            extracted["name"] = str(name)
        if version is not None:
            extracted["version"] = str(version)
    if extracted.get("version") is None:
        legacy_version = decoded.get(LEGACY_DAG_VERSION_KEY)
        if legacy_version is not None:
            extracted["version"] = str(legacy_version)
    return extracted


def _graph_shaped_blueprint(row: Any) -> Optional[Dict[str, Any]]:
    """``row['blueprint']`` when it is itself a graph payload, else ``None``.

    The last fallback, and only for the shape that genuinely is a graph.
    ``strategy_versions.blueprint`` is ``NOT NULL`` in the pre-canonical schema, so
    ``StrategyService._insert_version_row`` writes the author's submitted graph there on
    **every** save and writes ``graph_json`` only once migration 004 part 1
    (``backend_app/migrations/004_strategy_builder_canonical.sql``) has been applied. Until
    it is, a version row carries its graph in ``blueprint`` and its plan in the legacy
    ``execution_graph`` column - which is exactly the pair
    ``strategy_compiler._PLAN_COLUMNS`` documents itself as reading, and which no version
    consumer could reach before this fallback existed, because the graph read raised first
    and turned "the columns are not there yet" into a hard refusal for every consumer of
    every degraded row (Requirements 22.3, 22.5).

    Gated on ``nodes`` / ``edges`` rather than accepted blindly: ``blueprint`` also holds
    the genuinely legacy entry/exit-condition blob, which is not a graph and must keep
    reporting itself as one that cannot be loaded rather than being half-parsed.
    """
    blueprint = _row_field(row, "blueprint")
    if blueprint is None:
        return None
    decoded = _decode_json_field(blueprint, "blueprint")
    if not isinstance(decoded, Mapping):
        return None
    if "nodes" not in decoded and "edges" not in decoded:
        return None
    return copy.deepcopy(dict(decoded))


def extract_raw_graph(row: Any) -> Dict[str, Any]:
    """Return the raw graph payload carried by ``row``, without mutating ``row``.

    Accepts what the persistence layer actually holds, in the design's order:
    ``graph_json`` first, then the version 1 ``buy_logic`` blob, then a row that is already
    a graph payload (``nodes`` / ``edges`` at the top level, as the strategies router
    flattens it), and last a graph-shaped ``blueprint`` - the column a version row keeps its
    graph in until migration 004 part 1 is applied. See
    :func:`_graph_shaped_blueprint` for why that fallback is last and why it is gated.
    """
    if row is None:
        raise GraphParseError("Cannot load a graph from a null row")

    graph_json = _decode_json_field(_row_field(row, "graph_json", "graph"), "graph_json")
    if graph_json is not None:
        if not isinstance(graph_json, Mapping):
            raise GraphParseError(
                f"Row field 'graph_json' must be an object, got {type(graph_json).__name__}"
            )
        return copy.deepcopy(dict(graph_json))

    buy_logic = _row_field(row, "buy_logic")
    if buy_logic is not None:
        try:
            return legacy_extract(buy_logic, row)
        except GraphParseError:
            if _row_field(row, "nodes") is None and _graph_shaped_blueprint(row) is None:
                raise

    if isinstance(row, Mapping) and ("nodes" in row or "edges" in row):
        payload = copy.deepcopy(dict(row))
        if payload.get("schema_version") is None:
            declared = payload.get("dag_schema_version")
            if declared is not None:
                payload["schema_version"] = declared
        return payload

    blueprint_graph = _graph_shaped_blueprint(row)
    if blueprint_graph is not None:
        # No log line here on purpose: this module is pure in, pure out (``design.md`` ->
        # Module map). ``strategy_compiler.load_plan`` is the version-consumer seam and it
        # emits the degradation warning naming the unapplied migration.
        return blueprint_graph

    raise GraphParseError(
        "Row carries no canonical graph: no 'graph_json', no version 1 'buy_logic', "
        "no top-level 'nodes' and no graph-shaped 'blueprint'"
    )


def load_graph(row: Any, *, resolver: Resolver = None) -> StrategyGraph:
    """Load a stored strategy row as a canonical version 2 graph.

    Version 1 rows are migrated at read time and the stored record is left exactly as it
    was (Requirement 1.9). Version 2 rows are parsed straight through. Any other declared
    version is rejected with :class:`UnsupportedSchemaVersion`, which names the declared
    value (Requirement 1.11).
    """
    raw = extract_raw_graph(row)
    if normalize_schema_version(raw.get("schema_version")) == 1:
        return migrate_v1_to_v2(raw, resolver=resolver)
    return parse_v2(raw)
