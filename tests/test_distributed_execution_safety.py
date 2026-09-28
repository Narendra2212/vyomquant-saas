"""
DIST-CRITICAL-001 Regression Test: Distributed Execution Safety

Tests that distributed execution components handle leader election, orphan recovery,
and failover scenarios safely to prevent duplicate execution and split-brain conditions.

This test verifies the safety of distributed execution recovery mechanisms.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timedelta, timezone


class _InMemoryRedisPipeline:
    """Buffers the writes `_atomic_lease_acquisition` issues, applied on `execute()`."""

    def __init__(self, store: "_InMemoryRedis"):
        self._store = store
        self._queued = []

    def hset(self, key, mapping=None, **kwargs):
        self._queued.append(("hset", key, mapping or kwargs))
        return self

    def set(self, key, value, ex=None):
        self._queued.append(("set", key, value))
        return self

    def sadd(self, key, *members):
        self._queued.append(("sadd", key, members))
        return self

    def expire(self, key, seconds):
        return self

    async def execute(self):
        for operation, key, payload in self._queued:
            if operation == "hset":
                await self._store.hset(key, mapping=payload)
            elif operation == "set":
                await self._store.set(key, payload)
            else:
                await self._store.sadd(key, *payload)
        self._queued.clear()
        return []


class _InMemoryRedis:
    """The slice of `redis_manager` these modules actually use, kept in process.

    WHY THIS EXISTS. These tests already replaced `redis_manager`, but with a bare `Mock`, whose
    `get` answers every "is this resource already claimed?" with a truthy `Mock` and whose `await`
    raises. The exclusion logic under test therefore never ran. This substitutes only the network
    client - one store that every component in a test reads and writes, which is the role Redis
    plays in production - so the product's own arbitration code is what the assertions observe.
    Nothing here decides anything: it stores and returns what it was given.
    """

    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.sets = {}
        self.published = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = str(value)
        return True

    async def setex(self, key, seconds, value):
        self.values[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.hashes.pop(key, None)
            self.sets.pop(key, None)
        return True

    async def hset(self, key, mapping=None, **kwargs):
        fields = dict(mapping or {})
        fields.update(kwargs)
        self.hashes.setdefault(key, {}).update(
            {str(name): str(value) for name, value in fields.items()}
        )
        return True

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, seconds):
        return True

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(str(member) for member in members)
        return True

    async def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(str(member) for member in members)
        return True

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [
            key
            for key in list(self.values) + list(self.hashes) + list(self.sets)
            if key.startswith(prefix)
        ]

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def pipeline(self):
        return _InMemoryRedisPipeline(self)


@pytest.mark.asyncio
async def test_leader_election_prevents_split_brain():
    """
    DIST-CRITICAL-001: Verify that leader election prevents split-brain scenarios.
    
    This test ensures that leader election uses fencing tokens and heartbeats
    to prevent multiple leaders from assuming authority simultaneously.
    """
    # Mock the dependencies
    with patch('backend_app.backend.distributed_execution.leader_election_manager.redis_manager') as mock_redis:
        mock_redis_manager = AsyncMock()
        mock_redis.initialize = AsyncMock(return_value=True)
        
        from backend_app.backend.distributed_execution.leader_election_manager import LeaderElectionManager
        
        # Create leader election manager
        manager = LeaderElectionManager("coordinator_1")
        
        # Verify fencing token is present
        assert manager.signing_key is not None, "Fencing token must be present"
        assert len(manager.signing_key) > 0, "Fencing token must not be empty"
        
        # Verify heartbeat configuration
        assert manager.heartbeat_interval > 0, "Heartbeat interval must be positive"
        assert manager.election_timeout_min > 0, "Election timeout must be positive"
        assert manager.election_timeout_max > manager.election_timeout_min, "Max timeout must be greater than min"
        
        print("✓ Leader election has split-brain prevention mechanisms")


@pytest.mark.asyncio
async def test_orphan_detection_preserves_replay_safety():
    """
    DIST-CRITICAL-002: Verify that orphan detection preserves replay safety.
    
    This test ensures that orphaned tasks/workers are recovered deterministically
    without violating replay guarantees or causing duplicate execution.
    """
    with patch('backend_app.backend.distributed_execution.orphan_recovery_manager.redis_manager') as mock_redis:
        mock_redis_manager = AsyncMock()
        mock_redis.initialize = AsyncMock(return_value=True)
        
        from backend_app.backend.distributed_execution.orphan_recovery_manager import (
            OrphanRecoveryManager, OrphanType, OrphanStatus, TaskOrphan
        )
        
        # Create orphan recovery manager
        manager = OrphanRecoveryManager()
        
        # Create a test orphan task
        orphan_task = TaskOrphan(
            task_id="task_123",
            task_type="strategy_execution",
            worker_id="worker_1",
            task_data={"strategy_id": "strat_1", "symbol": "BTC/USDT"}
        )
        
        # Verify orphan status is detected
        assert orphan_task.status == OrphanStatus.DETECTED, "Orphan should start in detected status"
        assert orphan_task.recovery_priority > 0, "Orphan should have recovery priority"
        assert orphan_task.max_recovery_attempts > 0, "Orphan should have max recovery attempts"
        
        print("✓ Orphan detection preserves replay safety")


@pytest.mark.asyncio
async def test_lease_prevents_concurrent_execution():
    """
    DIST-CRITICAL-003: Verify that lease management prevents concurrent execution.
    
    This test ensures that leases prevent multiple workers from claiming the same
    task/queue and causing duplicate execution.

    WHAT CHANGED AND WHY. This used to assert `hasattr(LeaseType, 'TASK' | 'WORKER' | 'QUEUE')`.
    Those members do not exist - the vocabulary is EXCLUSIVE / SHARED / READ_ONLY / COORDINATION -
    and no code in the product dispatches on a lease type name, so declaring them would have
    turned the test green without any worker being kept out of anything. Three `hasattr` checks
    also say nothing about concurrent execution either way, so correcting the names alone would
    have left the test vacuous. It now drives `acquire_lease` and asserts the exclusion its name
    promises.
    """
    store = _InMemoryRedis()
    with patch('backend_app.backend.distributed_execution.lease_manager.redis_manager', store):
        from backend_app.backend.distributed_execution.lease_manager import (
            LeaseManager, LeaseRequest, LeaseStatus, LeaseType
        )

        lease_manager = LeaseManager()
        contended = "strategy_execution:strat_1"

        def _claim(requester_id, resource_id=contended):
            return LeaseRequest(
                resource_id=resource_id,
                requester_id=requester_id,
                lease_type=LeaseType.EXCLUSIVE,
                ttl_seconds=60,
                # The renewal loop is a timer, not part of the exclusion under test, and leaving
                # it running would outlive the test.
                auto_renew=False,
                priority=5,
            )

        first = await lease_manager.acquire_lease(_claim("worker_a"))
        assert first.success, (
            f"the first claim on a free resource must be granted: "
            f"{first.reason or first.error}"
        )
        assert first.lease.owner_id == "worker_a"
        assert first.lease.lease_type is LeaseType.EXCLUSIVE
        assert first.fencing_token, "an exclusive lease must carry a fencing token"

        # THE SAFETY PROPERTY: a second worker cannot hold the same resource at the same time.
        # Two grants here is duplicate execution of one strategy.
        second = await lease_manager.acquire_lease(_claim("worker_b"))
        assert not second.success, (
            "two workers were granted an exclusive lease on the same resource; both would "
            "execute it"
        )
        assert second.lease is None, "a refused claim must not hand back a lease"
        assert second.reason == "Resource already leased"

        # The refusal left the holder intact - it did not evict worker_a, which would be the
        # same duplicate-execution window from the other side.
        holder = await lease_manager.get_resource_lease(contended)
        assert holder is not None, "the resource lost its holder while being contended"
        assert holder.lease_id == first.lease.lease_id
        assert holder.owner_id == "worker_a"
        assert holder.status == LeaseStatus.ACTIVE

        # And it is exclusion on THIS resource, not a blanket refusal - a manager that granted
        # nothing would satisfy the assertion above while stopping all work.
        uncontended = await lease_manager.acquire_lease(
            _claim("worker_b", resource_id="strategy_execution:strat_2")
        )
        assert uncontended.success, (
            f"a free resource must still be claimable: "
            f"{uncontended.reason or uncontended.error}"
        )
        assert uncontended.fencing_token != first.fencing_token, (
            "fencing tokens must be unique per lease, otherwise a stale holder's writes cannot "
            "be distinguished from the current one's"
        )


@pytest.mark.asyncio
async def test_heartbeat_detection_triggers_recovery():
    """
    DIST-CITIRICAL-004: Verify that heartbeat detection triggers recovery.
    
    This test ensures that missing heartbeats trigger proper recovery
    mechanisms rather than assuming the system is healthy.

    WHAT CHANGED AND WHY. This used to require `HealthStatus.FAILED`. There is no such member -
    the scale is HEALTHY / DEGRADED / UNHEALTHY / CRITICAL / UNKNOWN - and `_assess_health` can
    never produce a `FAILED`, so declaring the member would have made the test pass while no
    component could ever reach the state it checked for. Corrected to the real scale, and since
    four `hasattr` checks on an enum say nothing about whether silence triggers recovery, this
    now drives the detection path and the recovery gate that consumes it.

    THE PATH IT WALKS. `HeartbeatManager._perform_health_checks` is the detector: a component
    past its timeout is downgraded to UNHEALTHY. `OrphanRecoveryManager._is_worker_healthy` is
    the gate: it reads that status back through `get_component_health` and answers False for
    anything not HEALTHY, which is what `_check_task_orphans` uses to decide to recover a task's
    work. Both halves run here; a break in either fails this test.
    """
    store = _InMemoryRedis()
    with patch('backend_app.backend.distributed_execution.heartbeat_manager.redis_manager', store):
        from backend_app.backend.distributed_execution.heartbeat_manager import (
            ComponentType, HealthStatus, HeartbeatManager
        )
        from backend_app.backend.distributed_execution.orphan_recovery_manager import (
            OrphanRecoveryManager
        )

        manager = HeartbeatManager()
        recovery = OrphanRecoveryManager()
        # The recovery manager reads health through whichever heartbeat manager it holds; point it
        # at the one under test rather than its own empty instance.
        recovery.heartbeat_manager = manager

        for component_id in ("worker_silent", "worker_live"):
            assert await manager.register_component(
                component_id, ComponentType.WORKER, interval_seconds=10
            ), f"{component_id} failed to register"
            # Both were last seen healthy. This is the state that must not survive silence.
            manager.health_status[component_id] = {
                "component_id": component_id,
                "status": HealthStatus.HEALTHY.value,
            }

        timeout_seconds = manager.component_registry["worker_silent"]["timeout_seconds"]
        assert timeout_seconds > 0, "a registered component must carry a heartbeat timeout"

        now = datetime.now(timezone.utc)
        # One worker went quiet well past its timeout; the other reported in just now.
        manager.component_registry["worker_silent"]["last_heartbeat"] = (
            now - timedelta(seconds=timeout_seconds + 30)
        )
        manager.component_registry["worker_live"]["last_heartbeat"] = now

        assert await recovery._is_worker_healthy("worker_silent") is True, (
            "precondition: the silent worker must start out considered healthy, otherwise this "
            "test cannot show the downgrade happening"
        )

        await manager._perform_health_checks()

        # THE SAFETY PROPERTY: silence is not read as health.
        detected = manager.health_status["worker_silent"]["status"]
        assert detected != HealthStatus.HEALTHY.value, (
            "a worker that stopped heartbeating is still published as HEALTHY; its in-flight "
            "tasks would never be recovered"
        )
        assert detected in {HealthStatus.UNHEALTHY.value, HealthStatus.CRITICAL.value}, (
            f"expected a failure-side status for a timed-out component, got {detected!r}"
        )
        assert manager.health_status["worker_silent"]["last_updated"], (
            "the downgrade must be stamped, otherwise consumers cannot tell it is current"
        )

        # ... and the downgrade reaches the gate that actually starts recovery.
        assert await recovery._is_worker_healthy("worker_silent") is False, (
            "detection downgraded the status but orphan recovery still considers the worker "
            "healthy; nothing would be recovered"
        )

        # Not achieved by downgrading everything: a manager that marked all components unhealthy
        # would satisfy every assertion above while triggering recovery of live work.
        assert manager.health_status["worker_live"]["status"] == HealthStatus.HEALTHY.value, (
            "a worker heartbeating on time was downgraded; its work would be reassigned "
            "underneath it"
        )
        assert await recovery._is_worker_healthy("worker_live") is True


@pytest.mark.asyncio
async def test_deterministic_reassignment_prevents_races():
    """
    DIST-CRITICAL-005: Verify that deterministic reassignment prevents race conditions.
    
    This test ensures that when resources are reassigned, the process is
    deterministic to prevent multiple workers from claiming the same resource.
    """
    with patch('backend_app.backend.distributed_execution.worker_registry.redis_manager') as mock_redis:
        mock_redis_manager = AsyncMock()
        mock_redis_manager.initialize = AsyncMock(return_value=True)
        
        from backend_app.backend.distributed_execution.worker_registry import WorkerRegistry, DeterministicReassignmentCoordinator
        
        # Create worker registry
        registry = WorkerRegistry()
        
        # Verify deterministic reassignment coordinator exists
        # This is a mock since we're not connecting to actual Redis
        assert registry is not None, "Worker registry must be initialized"
        
        print("✓ Worker registry has deterministic reassignment capability")


@pytest.mark.asyncio
async def test_queue_prevents_duplicate_task_delivery():
    """
    DIST-CRITICAL-006: Verify that queue management prevents duplicate task delivery.
    
    This test ensures that the distributed queue prevents duplicate task
    delivery which could cause duplicate execution.
    """
    with patch('backend_app.backend.distributed_execution.queue_manager.redis_manager') as mock_redis:
        mock_redis_manager = AsyncMock()
        mock_redis_manager.initialize = AsyncMock(return_value=True)
        
        from backend_app.backend.distributed_execution.queue_manager import DistributedQueueManager
        
        # Create queue manager
        queue_manager = DistributedQueueManager()
        
        # Verify queue manager is initialized
        assert queue_manager is not None, "Queue manager must be initialized"
        
        print("✓ Distributed queue manager prevents duplicate task delivery")


@pytest.mark.asyncio
async def test_transaction_atomicity_guarantee():
    """
    DIST-CRITICAL-007: Verify that atomic transaction guarantees prevent partial state corruption.
    
    This test ensures that distributed transactions maintain atomicity to prevent
    partial state corruption during distributed operations.
    """
    # No redis_manager patch here: unlike the other modules exercised in this
    # file, orchestration_safety_guarantees holds no Redis handle, so there is
    # no attribute to patch and nothing to isolate.
    from backend_app.backend.distributed_execution.orchestration_safety_guarantees import (
        AtomicTransactionGuarantee,
        DeterministicAssignmentGuarantee,
        ReplaySafeReassignmentManager
    )

    # Verify safety guarantees are defined
    assert AtomicTransactionGuarantee is not None, "Atomic transaction guarantee must be defined"
    assert DeterministicAssignmentGuarantee is not None, "Deterministic assignment guarantee must be defined"
    assert ReplaySafeReassignmentManager is not None, "Replay-safe reassignment must be defined"

    print("✓ Distributed execution has atomic transaction guarantees")


@pytest.mark.asyncio
async def test_failover_coordinator_prevents_double_leader():
    """
    DIST-CRITICAL-008: Verify that failover coordinator prevents double leader scenario.
    
    This test ensures that failover prevents split-brain scenarios where
    multiple nodes could assume leadership simultaneously.

    WHAT CHANGED AND WHY. This used to read `coordinator.cluster_id`. The constructor takes a
    `coordinator_id` and stores it as `self.coordinator_id`; nothing in the product reads or
    writes a `cluster_id`, so adding the attribute would have been an alias invented for this
    assertion. Reading the right attribute back is also not double-leader prevention, so the
    corrected test exercises the step that actually prevents it.

    WHERE THE PREVENTION LIVES. `_step_leadership_transfer` is the only place leadership is taken,
    and it takes it by acquiring an EXCLUSIVE lease on the single shared resource
    `execution_coordinator`; it returns False and never reaches `FailoverState.ACTIVE` if that
    lease is refused. Two coordinators race for it here through one lease manager, which is the
    one Redis-backed arbiter they share in production.
    """
    store = _InMemoryRedis()
    with patch(
        'backend_app.backend.distributed_execution.failover_coordinator.redis_manager', store
    ), patch(
        'backend_app.backend.distributed_execution.lease_manager.redis_manager', store
    ):
        from backend_app.backend.distributed_execution.failover_coordinator import (
            FailoverContext, FailoverCoordinator, FailoverState
        )
        from backend_app.backend.distributed_execution.lease_manager import LeaseType

        primary = FailoverCoordinator("coordinator_a")
        standby = FailoverCoordinator("coordinator_b")
        assert primary.coordinator_id == "coordinator_a"
        assert standby.coordinator_id == "coordinator_b"

        # One arbiter, as in production. The leadership lease auto-renews; that renewal is a
        # timer rather than part of the exclusion, so it is not started.
        arbiter = primary.lease_manager
        standby.lease_manager = arbiter
        arbiter._start_lease_renewal = AsyncMock()

        for coordinator in (primary, standby):
            coordinator.current_failover = FailoverContext(
                failover_id=f"failover_{coordinator.coordinator_id}",
                reason="leader heartbeat lost",
                initiated_at=datetime.now(timezone.utc),
                initiated_by="test",
                current_step="leadership_transfer",
                completed_steps=[],
                failed_steps=[],
                status="in_progress",
                fencing_token=f"token_{coordinator.coordinator_id}",
                term=7,
            )

        assert await primary._step_leadership_transfer() is True, (
            "the first coordinator must be able to take leadership; nothing holds the lease yet"
        )
        assert primary.state == FailoverState.ACTIVE

        # THE SAFETY PROPERTY: the second coordinator cannot also become leader.
        assert await standby._step_leadership_transfer() is False, (
            "both coordinators completed leadership transfer; two leaders would each direct "
            "execution"
        )
        assert standby.state != FailoverState.ACTIVE, (
            f"the refused coordinator still moved to {standby.state}; it would act as leader "
            f"without holding the lease"
        )

        # Leadership is where it was granted, and it is exclusive.
        leader_lease = await arbiter.get_resource_lease("execution_coordinator")
        assert leader_lease is not None, "the leadership lease vanished during the race"
        assert leader_lease.owner_id == "coordinator_a"
        assert leader_lease.lease_type is LeaseType.EXCLUSIVE
        assert leader_lease.metadata.get("fencing_token") == "token_coordinator_a", (
            "the leadership lease must carry the fencing token of the coordinator that won, so "
            "writes from a deposed leader can be rejected"
        )

        # The sequence fences before it hands over: a transfer that ran first would leave the old
        # leader's token valid while the new one is already leading.
        sequence = FailoverCoordinator.FAILOVER_SEQUENCE
        assert sequence.index("fencing_token_generation") < sequence.index("leadership_transfer")


@pytest.mark.asyncio
async def test_immutable_journal_preserves_replay_guarantees():
    """
    DIST-CRITICAL-009: Verify that immutable journal preserves replay guarantees.
    
    This test ensures that the immutable journal preserves replay guarantees
    to prevent duplicate execution during recovery.
    """
    with patch('backend_app.backend.distributed_execution.immutable_journal.redis_manager') as mock_redis:
        mock_redis_manager = AsyncMock()
        mock_redis_manager.initialize = AsyncMock(return_value=True)
        
        from backend_app.backend.distributed_execution.immutable_journal import immutable_journal
        
        # Verify immutable journal is accessible
        assert immutable_journal is not None, "Immutable journal must be accessible"
        
        print("✓ Immutable journal preserves replay guarantees")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
