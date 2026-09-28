"""Dummy module for deterministic_reassignment_model to fix imports.

``worker_registry`` imports ``DeterministicCapabilityAssigner`` from here and
instantiates it with no arguments. The module was missing entirely, which made
``worker_registry`` - and transitively ``orphan_recovery_manager`` - impossible
to import.

Matches the placeholder style already used by ``heartbeat_architecture`` and
``orchestration_safety_guarantees`` in this package.
"""


class DeterministicCapabilityAssigner:
    pass
