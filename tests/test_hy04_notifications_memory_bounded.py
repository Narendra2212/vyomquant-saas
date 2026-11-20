"""HY-04 -- the in-memory notification fallback store is now bounded, with an O(1)
idempotency index instead of an O(n) full-dict scan.

THE DEFECT
    backend_app/routers/notifications.py kept `_in_memory_notifications` as a plain,
    unbounded module-level dict: one entry retained forever for every notification
    ever created in this process's lifetime, across every tenant, whether or not it
    was ALSO durably persisted to Supabase (`list_notifications` always tries
    Supabase first and only falls back to this dict when Supabase has nothing -- so a
    persisted entry's in-memory copy is pure, unbounded-growth dead weight). The
    idempotency check also scanned every single entry on every single notification
    created, so it got slower, not just memory-hungrier, as the dict grew.

THE FIX
    A hard cap (`_MAX_IN_MEMORY_NOTIFICATIONS`, env-overridable via
    MAX_IN_MEMORY_NOTIFICATIONS, default 5000) that evicts the OLDEST entries
    (insertion order) once exceeded, plus an O(1) idempotency index
    (`_idempotency_index`, keyed by `(user_id, idempotency_key)`) that replaces the
    full-dict scan and is kept in sync on insert, eviction, and delete.

WHAT THIS FILE PROVES
    1. Inserting more than the cap evicts the OLDEST entries first, keeping the
       store at the cap.
    2. A duplicate idempotency key for the SAME user returns the existing id without
       growing the store.
    3. The SAME idempotency key for a DIFFERENT user does not collide -- both get
       their own notification.
    4. `delete_notification` cleans up the idempotency index -- a deleted
       notification's idempotency key can be reused to create a genuinely new one,
       not silently resurrect the deleted id.
    5. `delete_all_notifications` cleans up the idempotency index for every
       notification it removes, not just the in-memory dict.
    6. Eviction of an entry that was registered under an idempotency key also
       removes that key from the index (no dangling index entry pointing at a
       notification that no longer exists).
"""
from __future__ import annotations

import pytest

from backend_app.routers.notifications import (
    NotificationCreate,
    create_notification,
    delete_notification,
    delete_all_notifications,
    _in_memory_notifications,
    _idempotency_index,
    _notifications_lock,
)


@pytest.fixture(autouse=True)
def clean_notifications_state():
    with _notifications_lock:
        _in_memory_notifications.clear()
        _idempotency_index.clear()
    yield
    with _notifications_lock:
        _in_memory_notifications.clear()
        _idempotency_index.clear()


@pytest.fixture
def small_cap(monkeypatch):
    """Shrink the cap to a size this test can exceed in a handful of inserts,
    rather than actually inserting 5000+ rows to prove the same property."""
    import backend_app.routers.notifications as notif_mod

    monkeypatch.setattr(notif_mod, "_MAX_IN_MEMORY_NOTIFICATIONS", 3)
    yield


def _make(user_id: str, label: str, idempotency_key: str | None = None) -> NotificationCreate:
    metadata = {"idempotency_key": idempotency_key} if idempotency_key else None
    return NotificationCreate(
        user_id=user_id,
        type="test",
        category="system",
        title=label,
        message=label,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_exceeding_the_cap_evicts_the_oldest_entries_first(small_cap):
    ids = []
    for i in range(5):
        nid = await create_notification(_make("usr_cap", f"n{i}"), None, None)
        ids.append(nid)

    with _notifications_lock:
        assert len(_in_memory_notifications) == 3
        remaining = set(_in_memory_notifications.keys())

    # The 3 MOST RECENT survive; the 2 oldest were evicted.
    assert remaining == set(ids[-3:])
    assert ids[0] not in remaining
    assert ids[1] not in remaining


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_for_same_user_returns_existing_id_without_growing_store():
    n1 = _make("usr_idem", "first", idempotency_key="order-fill-42")
    n2 = _make("usr_idem", "second", idempotency_key="order-fill-42")

    id1 = await create_notification(n1, None, None)
    id2 = await create_notification(n2, None, None)

    assert id1 == id2
    with _notifications_lock:
        assert len(_in_memory_notifications) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_for_different_users_does_not_collide():
    n_alpha = _make("usr_alpha", "alpha-fill", idempotency_key="shared-key")
    n_beta = _make("usr_beta", "beta-fill", idempotency_key="shared-key")

    id_alpha = await create_notification(n_alpha, None, None)
    id_beta = await create_notification(n_beta, None, None)

    assert id_alpha != id_beta
    with _notifications_lock:
        assert len(_in_memory_notifications) == 2


@pytest.mark.asyncio
async def test_delete_notification_cleans_the_idempotency_index():
    n1 = _make("usr_del", "original", idempotency_key="retry-key-1")
    id1 = await create_notification(n1, None, None)

    await delete_notification(notification_id=id1, user={"id": "usr_del"}, supabase=None)

    with _notifications_lock:
        assert "retry-key-1" not in [k[1] for k in _idempotency_index]

    # The same idempotency key now creates a genuinely NEW notification, not a
    # silent resurrection of the deleted one.
    n2 = _make("usr_del", "replacement", idempotency_key="retry-key-1")
    id2 = await create_notification(n2, None, None)
    assert id2 != id1
    with _notifications_lock:
        assert id2 in _in_memory_notifications


@pytest.mark.asyncio
async def test_delete_all_notifications_cleans_the_idempotency_index_for_every_entry():
    await create_notification(_make("usr_bulk", "a", idempotency_key="k-a"), None, None)
    await create_notification(_make("usr_bulk", "b", idempotency_key="k-b"), None, None)
    await create_notification(_make("usr_other", "c", idempotency_key="k-c"), None, None)

    await delete_all_notifications(user={"id": "usr_bulk"}, supabase=None)

    with _notifications_lock:
        remaining_users = {k[0] for k in _idempotency_index}
        assert "usr_bulk" not in remaining_users
        assert "usr_other" in remaining_users
        assert len(_in_memory_notifications) == 1


@pytest.mark.asyncio
async def test_eviction_removes_the_evicted_entrys_idempotency_index_entry(small_cap):
    id1 = await create_notification(_make("usr_evict", "oldest", idempotency_key="evict-me"), None, None)
    await create_notification(_make("usr_evict", "n2"), None, None)
    await create_notification(_make("usr_evict", "n3"), None, None)
    # Exceeds the cap of 3 -> evicts id1, the oldest.
    await create_notification(_make("usr_evict", "n4"), None, None)

    with _notifications_lock:
        assert id1 not in _in_memory_notifications
        assert ("usr_evict", "evict-me") not in _idempotency_index

    # The evicted key is free to be reused.
    new_id = await create_notification(
        _make("usr_evict", "reused", idempotency_key="evict-me"), None, None
    )
    with _notifications_lock:
        assert _idempotency_index[("usr_evict", "evict-me")] == new_id
