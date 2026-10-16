"""AUD-01: SignalService._local_signals' read paths used to fail OPEN, not closed, for
an unidentifiable caller.

Found while investigating AUD-01's own remaining_action ("the process-wide, identity-
unscoped in-memory fallback... was not fixed"). Both SignalService.get_signal and
list_signals route to this in-process dict when Supabase is unreachable -- and both are
still called directly from the four signal-trace READ endpoints AUD-01 deliberately left
in place (only the four WRITE endpoints were deleted).

get_signal's fallback read:
    if not user_id or str(local.get("user_id", "")) == user_id or user_id == "anonymous":
        return local
An empty/unresolved user_id made `not user_id` true, matching the condition
unconditionally -- ANY signal, owned by ANY tenant, was returned to a caller this
service could not identify. It also compared the CALLER's resolved id against the
literal string "anonymous", not the signal's own stored owner -- inconsistent with
list_signals' own fallback a few lines below, which (correctly) treats "anonymous" as a
property of the row.

list_signals' bulk fallback read:
    if user_id and owner != user_id and owner != "anonymous":
        continue
The leading `user_id and` made the whole condition false whenever user_id was empty, so
`continue` never ran: every signal in this process's memory, across every tenant, was
returned.

Both are fixed to fail closed: no identifiable caller means no results, not all of them;
"anonymous" is read from the signal's own stored owner field in both paths now, not from
the caller's resolved identity.
"""

from __future__ import annotations

import asyncio

from backend_app.backend.signal_service import SignalService


def run(coro):
    return asyncio.run(coro)


def _service_with_local_signals(signals: dict) -> SignalService:
    service = SignalService()
    service._local_signals.update(signals)
    return service


OWNER_A = "11111111-1111-1111-1111-111111111111"
OWNER_B = "22222222-2222-2222-2222-222222222222"


class TestGetSignalFailsClosed:
    def test_an_unidentifiable_caller_gets_nothing_not_everything(self):
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A},
        })

        # user.get("id", "") resolves to "" for a dict with no "id" key at all.
        result = run(service.get_signal({"access_token": None}, "sig-a"))

        assert result is None, (
            "an unidentifiable caller (empty user_id) must never receive a signal it "
            "cannot be shown to own"
        )

    def test_a_different_owners_signal_is_still_refused(self):
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A},
        })

        result = run(service.get_signal({"id": OWNER_B}, "sig-a"))

        assert result is None

    def test_the_real_owner_still_gets_their_own_signal(self):
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A},
        })

        result = run(service.get_signal({"id": OWNER_A}, "sig-a"))

        assert result is not None
        assert result["id"] == "sig-a"

    def test_anonymous_is_a_property_of_the_signal_not_of_the_caller(self):
        """The old code checked `user_id == "anonymous"` -- the CALLER's resolved id.
        An ownerless (system-generated) signal is "anonymous" by what it stores, not by
        who happens to be asking."""
        service = _service_with_local_signals({
            "sig-system": {"id": "sig-system", "user_id": "anonymous"},
        })

        result = run(service.get_signal({"id": OWNER_A}, "sig-system"))

        assert result is not None, (
            "a real, identified caller must still be able to read an ownerless "
            "(system-generated) signal"
        )

    def test_an_unidentifiable_caller_cannot_use_the_literal_string_anonymous_as_an_id(self):
        """The old code's `user_id == "anonymous"` meant a caller resolving to the
        literal string "anonymous" saw every signal, owned by anyone. Confirm that is
        gone: only an actually-empty user_id is refused outright, and a caller whose id
        happens to be the string "anonymous" is treated like any other non-matching id."""
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A},
        })

        result = run(service.get_signal({"id": "anonymous"}, "sig-a"))

        assert result is None, (
            "a caller resolving to the literal string 'anonymous' must not see another "
            "tenant's signal"
        )


class TestListSignalsFailsClosed:
    def test_an_unidentifiable_caller_lists_nothing_not_everyone(self):
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A, "generated_at": "2026-01-01T00:00:00Z"},
            "sig-b": {"id": "sig-b", "user_id": OWNER_B, "generated_at": "2026-01-02T00:00:00Z"},
        })

        results = run(service.list_signals({"access_token": None}))

        assert results == [], (
            "an unidentifiable caller (empty user_id) must never be handed every "
            "tenant's signals"
        )

    def test_a_real_caller_sees_only_their_own_and_anonymous_signals(self):
        service = _service_with_local_signals({
            "sig-a": {"id": "sig-a", "user_id": OWNER_A, "generated_at": "2026-01-01T00:00:00Z"},
            "sig-b": {"id": "sig-b", "user_id": OWNER_B, "generated_at": "2026-01-02T00:00:00Z"},
            "sig-system": {"id": "sig-system", "user_id": "anonymous", "generated_at": "2026-01-03T00:00:00Z"},
        })

        results = run(service.list_signals({"id": OWNER_A}))

        ids = {r["id"] for r in results}
        assert ids == {"sig-a", "sig-system"}, (
            f"expected only OWNER_A's own signal plus the ownerless one, got {ids}"
        )
