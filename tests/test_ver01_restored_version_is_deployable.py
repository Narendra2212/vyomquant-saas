"""VER-01: a restored version could never be deployed.

``StrategyService.restore_version`` inserted a bare row - old blueprint only, no compiled_plan, no
dag_hash, validation_state UNVALIDATED, a hand-built ``v{major}.{minor+1}`` label - so deploying it
answered 409 "validation_state is 'UNVALIDATED' (must be 'VALID')". The deploy refusal for a STOPPED
version tells the user to "restore this one", which therefore led nowhere (found in production on
2026-11-11). A restore must take the one save path: compile, then persist READY/non-draft when there
is no model block, exactly like a builder save.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


import backend_app.backend.strategy_service as ss


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, {}

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def execute(self):
        self.db.calls.append((self.table, self.op, self.payload, dict(self.filters)))
        if self.table == "strategy_versions" and self.op == "select":
            return SimpleNamespace(data=[self.db.old_row])
        return SimpleNamespace(data=[])


class _DB:
    def __init__(self, old_row):
        self.old_row, self.calls = old_row, []

    def table(self, name):
        return _Q(self, name)


def _run(monkeypatch, ml_nodes=()):
    old = {"id": "old", "version": "v1.0", "blueprint": {"nodes": [], "edges": []},
           "graph_json": {"g": 1}, "lifecycle_state": "STOPPED"}
    db = _DB(old)
    svc = ss.StrategyService.__new__(ss.StrategyService)
    svc._get_supabase = lambda user: db

    async def _owns(sb, user, sid):
        return True

    svc._owns_strategy = _owns
    seen = {}

    async def _create_version(user, strategy_id, graph, **kw):
        seen.update(kw, graph=graph)
        return {"version": {"id": "new", "version": "v2.0"}, "lifecycle_state": kw["lifecycle_state"],
                "validation_state": "VALID"}

    svc.create_version = _create_version
    compiled = SimpleNamespace(plan=SimpleNamespace(ml_nodes=ml_nodes))
    monkeypatch.setattr(ss, "compile_version", lambda graph, registry: compiled)
    import backend_app.backend.strategy_dag.schema as schema
    monkeypatch.setattr(schema, "load_graph", lambda src: ("parsed", src))
    row = asyncio.run(svc.restore_version({"id": "u"}, "s-1", "v1.0"))
    return row, seen, db, compiled


def test_restore_compiles_and_saves_a_ready_non_draft_version(monkeypatch):
    row, seen, db, compiled = _run(monkeypatch)
    assert seen["compiled"] is compiled, "the restored graph must go through the compiler"
    assert seen["graph"] == ("parsed", {"g": 1}), "graph_json is the canonical source"
    assert seen["lifecycle_state"] == "READY"
    assert seen["is_draft"] is False
    assert seen["make_current"] is True
    assert "version" not in seen, "the label comes from create_version, never v{major}.{minor+1}"
    assert row["version"] == "v2.0" and row["restored_from"] == "v1.0"


def test_restore_never_inserts_an_uncompiled_row(monkeypatch):
    _, _, db, _ = _run(monkeypatch)
    assert not [c for c in db.calls if c[1] == "insert"], "bare INSERT bypasses compilation"
    prov = [c for c in db.calls if c[1] == "update" and c[2] == {"restored_from": "v1.0"}]
    assert prov and prov[0][3] == {"id": "new"}, "provenance is written on the new row only"


def test_a_model_block_restores_as_a_validated_draft(monkeypatch):
    _, seen, _, _ = _run(monkeypatch, ml_nodes=("n-9",))
    assert seen["lifecycle_state"] == "VALIDATED" and seen["is_draft"] is True
