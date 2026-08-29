"""Canonical Strategy DAG package.

One schema, shared by the frontend serializer, the API contract, the validator, the
compiler, the runtime, the backtester and the database. The same field names are used at
every layer, so no layer needs a translation table and there are no per-layer DTOs.

Modules
-------
``schema.py``
    ``PortType``, ``BlockCategory``, ``ValidationState``, ``Port``, ``NodeSpec``,
    ``EdgeSpec``, ``StrategyGraph``, ``compute_dag_hash``, node/edge id minting.

``block_specs.py``
    ``MATH_SPECS``, ``LOGIC_SPECS``, ``DATA_SPECS`` and ``action_specs()`` - the
    descriptors for the four categories no engine module owns.

``registry.py``
    ``ParamSpec``, ``BlockDescriptor``, ``BlockRegistry``, ``build_registry``,
    ``get_registry``, ``get(block_id)`` - the backend-authoritative block catalogue.

``validator.py``
    ``is_edge_legal`` (rules R1-R8), ``find_cycle`` (iterative) and ``validate``, the
    collect-all stage pipeline that recomputes ports, edge types, state and hash
    server-side.

``plan.py``
    ``CompiledPlan`` (``dag_hash`` is a readable field, ``to_dict`` is the only
    serialization path) and ``compute_warmup``, which composes warmups along a path.

Purity contract
---------------
Every entry point in this package takes a graph (and, later, a registry) and returns a
value. No database handle, no HTTP client, no FastAPI import, no execution or credential
module import. That is precisely what made the router-embedded compiler unusable from
``dag_worker``, the backtester and training jobs.

Following the convention of ``backend_app/backend/__init__.py``, no runtime imports are
performed here. Import directly from the submodules:

    from backend_app.backend.strategy_dag.schema import StrategyGraph, compute_dag_hash
"""

__all__ = []
