"""Dummy module for orchestration_safety_guarantees to fix imports.

These are no-argument placeholder collaborators. Consumers in this package
(``lease_manager``, ``heartbeat_manager``, ``worker_registry``,
``orphan_recovery_manager``) instantiate them in ``__init__`` and hold the
reference; the real guarantee logic has not been implemented yet.

Every name referenced by a sibling module must exist here, otherwise the
importing module - and everything that depends on it - becomes unimportable.
"""


class SplitBrainPreventionGuarantee:
    pass


class AtomicTransactionGuarantee:
    pass


class DeterministicAssignmentGuarantee:
    pass


class OperationIsolationGuarantee:
    pass


class ReplaySafeReassignmentManager:
    pass
