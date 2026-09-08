"""
tests/test_task_26_3_paper_channel.py

The seventh owned channel family: ``paper.{session_id}``.

Spec: marketplace-subscriptions-paper-trading task 26.3. ``design.md`` -> "``paper/paper_events.py``
and the Paper_Channel". Requirements 19.1, 19.2, 19.4, 19.5, 21.4, 21.6.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **The family follows ``SIGNAL_FAMILY`` exactly, and diverges from it in nothing but its
   namespace, its resource and its vocabulary.** The task names that family as the template, so the
   shape rule, the malformed-name rule and the refusal frame are asserted **against the existing
   families** rather than restated as literals this file happens to believe.

2. **No new authorisation code.** Requirement 19.5 is answered by the ``paper_sessions`` lookup
   ``core/websocket_auth`` already performs for every other family - one ``_OwnerRelation`` map
   entry, not a resolver. The assertions here are that the same lookup reads the same relation, the
   same row and the same owner, and refuses through the same frames. If a resolver of its own had
   been written, this is where it would show up as a second read, a different table, or a refusal
   that does not match ``execution``'s.

3. **Requirements 19.4 and 21.4: a refusal is not an existence oracle.** Another user's paper
   session and one that does not exist must produce refusals that are identical field for field -
   not merely similar - and neither may carry any session data. This is the assertion the whole
   design of the shared ``_forbidden`` frame exists for, and it is the one this file would fail if
   somebody "improved" the message to name what was missing.

4. **The six existing families still route and still authorise.** A seventh entry in a
   longest-namespace-first sweep and in a namespace->relation map is exactly the kind of change that
   reroutes an existing name, so the six that existed before are re-asserted against the extended
   registry.

5. **``paper_events`` and ``ws_channels`` agree about the vocabulary and the channel name.** One
   definition, imported - which is what makes ``chk_paper_event_type``, the payload models and the
   family's ``events`` set the same sixteen values.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: ``ws_channels``, ``authorize_channel_subscription``, ``paper_events``.

Doubles, all reused from the training-channel files rather than re-invented, exactly as
``tests/test_task_14_1_signal_channel.py`` reuses them: the in-memory ``FakeSupabase`` (which does
NOT enforce RLS, so both the empty-result and the visible-foreign-row shapes are exercised) and the
token decoder.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import ws_channels as C  # noqa: E402
from backend_app.backend.paper import paper_events as events  # noqa: E402
from backend_app.core import websocket_auth as WA  # noqa: E402
from backend_app.core.websocket_auth import (  # noqa: E402
    authorize_channel_subscription,
)

from tests.test_training_realtime_channels import (  # noqa: E402 - shared doubles
    ExplodingSupabase,
    owner_user,
)
from tests.test_training_service_admission import (  # noqa: E402 - shared doubles
    OTHER_USER_ID,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
)

SESSION_ID = "ps-0001"
DEPLOYMENT_ID = "dep-0001"

#: The relation the Paper_Channel resolves its owner through, and the file that creates it. Named
#: once here and asserted against ``websocket_auth``'s own entry, so a change to either is a failure
#: rather than a silent divergence.
SESSIONS_TABLE = "paper_sessions"
SESSIONS_MIGRATION = "backend_app/migrations/009_paper_trading.sql"

PAPER_CHANNEL = f"paper.{SESSION_ID}"


def _seeded_store():
    """The shared in-memory store, plus a paper session owned by ``USER_ID``."""
    store = FakeSupabase.seeded()
    store.tables[SESSIONS_TABLE] = [
        {
            "id": SESSION_ID,
            "user_id": USER_ID,
            "session_state": "RUNNING",
            "environment": "PAPER",
        }
    ]
    store.tables["strategy_deployments"] = [
        {"id": DEPLOYMENT_ID, "user_id": USER_ID, "strategy_id": STRATEGY_ID}
    ]
    return store


@pytest.fixture
def store():
    return _seeded_store()


@pytest.fixture
def wired(monkeypatch, store):
    """Point the ownership lookup at the in-memory store."""
    from backend_app.core import dependencies as D

    async def _client(token):
        return store

    monkeypatch.setattr(D, "create_request_supabase_async", _client)
    return store


@pytest.fixture
def no_sessions_table(monkeypatch):
    """A store with no ``paper_sessions`` relation at all - 009 unapplied."""
    from backend_app.core import dependencies as D

    store = FakeSupabase.seeded()

    async def _client(token):
        return store

    monkeypatch.setattr(D, "create_request_supabase_async", _client)
    return store


# ═══════════════════════════════════════════════════════════════════════════
# 1. The family — Requirement 19.1, and `SIGNAL_FAMILY` as the template
# ═══════════════════════════════════════════════════════════════════════════


class TestThePaperFamily:
    def test_the_registry_now_holds_exactly_seven_owned_families(self):
        """The exhaustive set, which this file owns because it closed it.

        ``market.{symbol}.{timeframe}`` stays outside: a symbol belongs to nobody, so it cannot be
        authorised against an owner.
        """
        assert {family.namespace for family in C.OWNED_CHANNEL_FAMILIES} == {
            "training",
            "builder.validation",
            "strategy",
            "deployment",
            "execution",
            "signal",
            "paper",
        }
        assert C.claims_owned_namespace("market.BTC/USDT.1h") is False

    def test_the_family_matches_signal_in_everything_but_namespace_and_resource(self):
        """Requirement 19.1: "following the existing ``OwnedChannelFamily`` pattern".

        Asserted against ``SIGNAL_FAMILY`` itself rather than against literals, so a change to the
        template cannot leave this family behind.
        """
        assert isinstance(C.PAPER_FAMILY, C.OwnedChannelFamily)
        assert C.PAPER_FAMILY.namespace == "paper"
        assert C.PAPER_FAMILY.resource == "session_id"
        assert C.PAPER_FAMILY.prefix == "paper."
        assert type(C.PAPER_FAMILY.events) is type(C.SIGNAL_FAMILY.events)

    def test_the_vocabulary_is_requirement_19_2s_sixteen_types(self):
        assert C.PAPER_FAMILY.events == frozenset(events.PAPER_CHANNEL_EVENTS)
        assert len(C.PAPER_FAMILY.events) == 16
        assert C.PAPER_CHANNEL_EVENTS is events.PAPER_CHANNEL_EVENTS

    def test_the_channel_name_round_trips(self):
        assert C.PAPER_FAMILY.channel(SESSION_ID) == PAPER_CHANNEL
        assert C.PAPER_FAMILY.parse(PAPER_CHANNEL) == SESSION_ID
        assert C.owned_channel(C.PAPER_FAMILY, SESSION_ID) == PAPER_CHANNEL
        assert events.paper_channel(SESSION_ID) == PAPER_CHANNEL

        reference = C.parse_owned_channel(PAPER_CHANNEL)
        assert reference is not None
        assert reference.family is C.PAPER_FAMILY
        assert reference.resource_id == SESSION_ID
        assert reference.resource == "session_id"
        assert reference.channel == PAPER_CHANNEL
        assert C.is_owned_channel(PAPER_CHANNEL) is True

    @pytest.mark.parametrize(
        "suffix",
        [
            "",              # the bare namespace names no resource
            ".",             # empty id segment
            "..",
            ".a.b",          # a second segment smuggled in
            ".*",            # a wildcard
            ".%",
            ".a b",          # whitespace
            "./etc/passwd",
            ".a\\b",
            "." + "x" * 65,  # long enough to be a payload
        ],
    )
    def test_it_admits_no_name_the_other_six_families_refuse(self, suffix):
        """The shape rule is stated once, and the seventh family must not widen it."""
        channel = f"paper{suffix}"
        assert C.parse_owned_channel(channel) is None, channel
        assert C.is_owned_channel(channel) is False, channel
        assert C.claims_owned_namespace(channel) is True, channel

    def test_a_malformed_id_cannot_produce_a_channel_name(self):
        for bad in ["", "a.b", "a b", "*", "x" * 65, None]:
            with pytest.raises(ValueError):
                C.PAPER_FAMILY.channel(bad)

    def test_longer_namespaces_are_still_tried_first(self):
        """The sweep order is a rule, not a property of how the tuple was typed."""
        lengths = [len(f.namespace) for f in C.OWNED_CHANNEL_FAMILIES]
        assert lengths == sorted(lengths, reverse=True)

    def test_the_six_existing_families_still_route_their_own_names(self):
        """A seventh entry in a prefix sweep is how an existing name gets rerouted."""
        for channel, family in [
            (f"builder.validation.{STRATEGY_ID}", C.BUILDER_VALIDATION_FAMILY),
            (f"strategy.{STRATEGY_ID}", C.STRATEGY_FAMILY),
            (f"deployment.{DEPLOYMENT_ID}", C.DEPLOYMENT_FAMILY),
            (f"execution.{DEPLOYMENT_ID}", C.EXECUTION_FAMILY),
            (f"signal.{DEPLOYMENT_ID}", C.SIGNAL_FAMILY),
            ("training.job-1", C.TRAINING_FAMILY),
        ]:
            reference = C.parse_owned_channel(channel)
            assert reference is not None, channel
            assert reference.family is family, channel

    def test_the_namespace_map_and_the_event_union_both_include_it(self):
        assert C.OWNED_CHANNEL_FAMILIES_BY_NAMESPACE["paper"] is C.PAPER_FAMILY
        assert set(events.PAPER_CHANNEL_EVENTS) <= C.OWNED_CHANNEL_EVENTS

    def test_the_fixed_channel_vocabulary_is_untouched(self):
        """A parameterised family is never admitted to ``ChannelType``.

        ``ws_event_stream`` calls ``ChannelType(name)`` on the line after
        ``is_valid_channel(name)`` returns True, so a parameterised name that passed the predicate
        would hand it a name its very next line cannot construct.
        """
        assert C.is_valid_channel(PAPER_CHANNEL) is False
        assert C.VALID_CHANNELS == {ch.value for ch in C.ChannelType}
        assert C.claims_owned_namespace("bot_status") is False

    def test_no_family_accepts_another_familys_vocabulary(self):
        """A producer that reached for the wrong vocabulary is a bug, not a frame."""
        assert C.is_owned_channel_event(PAPER_CHANNEL, "paper_order_filled") is True
        assert C.is_owned_channel_event(PAPER_CHANNEL, "signal.generated") is False
        assert C.is_owned_channel_event(PAPER_CHANNEL, "execution.fill") is False
        assert C.is_owned_channel_event(PAPER_CHANNEL, "training.progress") is False
        assert (
            C.is_owned_channel_event(f"signal.{DEPLOYMENT_ID}", "paper_order_filled")
            is False
        )
        assert (
            C.is_owned_channel_event(f"execution.{DEPLOYMENT_ID}", "market_tick")
            is False
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. The owner relation — ONE map entry, and no new authorisation code
# ═══════════════════════════════════════════════════════════════════════════


class TestTheOwnerRelation:
    def test_the_family_maps_onto_paper_sessions_and_names_009(self):
        relation = WA._owner_relations()[C.PAPER_FAMILY.namespace]

        assert relation.table == SESSIONS_TABLE
        assert relation.migration == SESSIONS_MIGRATION
        assert relation.noun == "paper session"
        assert relation.owner_column == "user_id"

    def test_it_is_the_only_family_pointing_at_paper_sessions(self):
        relations = WA._owner_relations()
        pointing = [
            namespace
            for namespace, relation in relations.items()
            if relation.table == SESSIONS_TABLE
        ]
        assert pointing == [C.PAPER_FAMILY.namespace]

    def test_every_family_still_has_a_relation(self):
        """A family with no relation cannot be authorised, so it is refused - by construction."""
        relations = WA._owner_relations()
        for family in C.OWNED_CHANNEL_FAMILIES:
            assert family.namespace in relations, family.namespace

    def test_the_relation_is_the_same_dataclass_the_others_use(self):
        """One relation type means one lookup and one set of refusal branches."""
        relations = WA._owner_relations()
        assert isinstance(relations["paper"], WA._OwnerRelation)
        assert type(relations["paper"]) is type(relations["execution"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirements 19.4 / 19.5 / 21.4 / 21.6 — the SHARED lookup and refusals
# ═══════════════════════════════════════════════════════════════════════════


class TestSubscriptionAuthorization:
    @pytest.mark.asyncio
    async def test_the_owner_is_allowed(self, wired):
        decision = await authorize_channel_subscription(PAPER_CHANNEL, owner_user())

        assert decision.allowed is True, decision.reason
        assert decision.owner_id == USER_ID
        assert decision.code == ""

    @pytest.mark.asyncio
    async def test_it_reads_paper_sessions_and_nothing_else(self, wired):
        """The whole of task 26.3's authorisation: one map entry, one existing lookup.

        Requirement 19.5's "re-verify session ownership at subscription time rather than trusting
        an identifier supplied in the subscribe message" is this read. If a resolver of its own had
        been written, it would show up here as a second read or a different table.
        """
        wired.touched.clear()

        decision = await authorize_channel_subscription(PAPER_CHANNEL, owner_user())

        assert decision.allowed is True
        assert wired.touched == [SESSIONS_TABLE]

    @pytest.mark.asyncio
    async def test_another_users_session_is_refused_and_says_so(self, wired):
        """Requirement 21.4's first half: refused, and the refusal is reported."""
        decision = await authorize_channel_subscription(
            PAPER_CHANNEL, owner_user(OTHER_USER_ID)
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert decision.owner_id is None
        frame = decision.refusal_frame()
        assert frame["type"] == "subscription_refused"
        assert frame["channel"] == PAPER_CHANNEL
        assert frame["reason"], "a refusal with no reason is a silent refusal"

    @pytest.mark.asyncio
    async def test_a_nonexistent_session_and_a_foreign_one_are_indistinguishable(
        self, wired
    ):
        """Requirements 19.4 and 21.4, and the reason they are load-bearing.

        A refusal that distinguished the two would let a non-owner confirm another tenant's
        Paper_Session identifier by subscribing to it. Under the owner-scoped RLS policy these ARE
        the same empty result, and the answer must be too - field for field, not merely similar.
        """
        missing = await authorize_channel_subscription(
            "paper.does-not-exist", owner_user()
        )
        foreign = await authorize_channel_subscription(
            PAPER_CHANNEL, owner_user(OTHER_USER_ID)
        )

        assert missing.allowed is foreign.allowed is False
        assert missing.code == foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert missing.reason == foreign.reason
        assert missing.owner_id == foreign.owner_id is None

        # The frames differ only in the channel the subscriber itself named.
        missing_frame = missing.refusal_frame()
        foreign_frame = foreign.refusal_frame()
        assert set(missing_frame) == set(foreign_frame)
        assert {k: v for k, v in missing_frame.items() if k != "channel"} == {
            k: v for k, v in foreign_frame.items() if k != "channel"
        }

    @pytest.mark.asyncio
    async def test_the_refusal_shape_does_not_diverge_from_the_other_families(
        self, wired
    ):
        """One refusal frame for seven families, so no family is a softer target."""
        intruder = owner_user(OTHER_USER_ID)
        paper = await authorize_channel_subscription(PAPER_CHANNEL, intruder)
        execution = await authorize_channel_subscription(
            f"execution.{DEPLOYMENT_ID}", intruder
        )

        assert set(paper.refusal_frame()) == set(execution.refusal_frame())
        assert paper.code == execution.code
        # Different relation, therefore a different NOUN in the sentence - and nothing else.
        assert "paper session" in paper.reason
        assert paper.reason.replace("paper session", "deployment") == execution.reason

    @pytest.mark.asyncio
    async def test_a_refusal_carries_no_session_data(self, wired):
        decision = await authorize_channel_subscription(
            PAPER_CHANNEL, owner_user(OTHER_USER_ID)
        )
        frame = decision.refusal_frame()

        assert set(frame) == {"type", "channel", "code", "reason"}
        blob = repr(frame)
        for leak in (USER_ID, STRATEGY_ID, "RUNNING", "PAPER"):
            assert leak not in blob

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_refuses_and_names_009(
        self, no_sessions_table, caplog
    ):
        """A missing ownership table is where "allow" would be indefensible."""
        with caplog.at_level("WARNING"):
            decision = await authorize_channel_subscription(
                PAPER_CHANNEL, owner_user()
            )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED
        assert SESSIONS_MIGRATION in decision.reason
        assert SESSIONS_TABLE in caplog.text

    @pytest.mark.asyncio
    async def test_no_database_client_refuses(self, monkeypatch):
        from backend_app.core import dependencies as D

        async def _none(token):
            return None

        monkeypatch.setattr(D, "create_request_supabase_async", _none)

        decision = await authorize_channel_subscription(PAPER_CHANNEL, owner_user())

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unclassifiable_failure_refuses_rather_than_raising(self):
        decision = await authorize_channel_subscription(
            PAPER_CHANNEL, owner_user(), supabase=ExplodingSupabase()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unauthenticated_subscription_is_refused(self, wired):
        """Requirement 21.6: refused before any data access."""
        wired.touched.clear()

        for user in (None, {}, {"id": ""}):
            decision = await authorize_channel_subscription(PAPER_CHANNEL, user)
            assert decision.allowed is False
            assert decision.code == WA.CHANNEL_REFUSED_UNAUTHENTICATED

        assert wired.touched == [], "an unauthenticated attempt must not reach the database"

    @pytest.mark.asyncio
    async def test_a_session_row_without_an_owner_is_refused(self, monkeypatch):
        from backend_app.core import dependencies as D

        store = _seeded_store()
        store.tables[SESSIONS_TABLE] = [{"id": SESSION_ID, "user_id": None}]

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        decision = await authorize_channel_subscription(PAPER_CHANNEL, owner_user())

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_malformed_name_is_refused_before_any_lookup(self, wired):
        wired.touched.clear()

        for suffix in ("", ".", ".a.b", ".*"):
            channel = f"paper{suffix}"
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is False, channel
            assert decision.code == WA.CHANNEL_REFUSED_UNKNOWN, channel

        assert wired.touched == [], (
            "a name that cannot identify a resource must not reach the database"
        )

    @pytest.mark.asyncio
    async def test_a_deployment_id_used_as_a_session_id_is_refused(self, wired):
        """The family decides which relation is read, not the id.

        ``DEPLOYMENT_ID`` is a real, owned deployment. On ``paper.*`` it names no session.
        """
        decision = await authorize_channel_subscription(
            f"paper.{DEPLOYMENT_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN

    @pytest.mark.asyncio
    async def test_a_session_id_used_on_another_family_is_refused(self, wired):
        """And the converse: an owned paper session is not a deployment."""
        decision = await authorize_channel_subscription(
            f"signal.{SESSION_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN

    @pytest.mark.asyncio
    async def test_the_legacy_fixed_channels_keep_the_authorization_they_had(self, wired):
        """Task 26.3 adds one channel; it re-decides none of the existing ones."""
        wired.touched.clear()

        for channel in ["signal_trace", "orders", "pnl", "all", "market.BTC/USDT.1h"]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.code == ""

        assert wired.touched == []

    @pytest.mark.asyncio
    async def test_the_six_existing_families_still_authorise_as_they_did(self, wired):
        """The owner is still allowed on every channel that existed before."""
        for channel in [
            f"builder.validation.{STRATEGY_ID}",
            f"strategy.{STRATEGY_ID}",
            f"deployment.{DEPLOYMENT_ID}",
            f"execution.{DEPLOYMENT_ID}",
            f"signal.{DEPLOYMENT_ID}",
        ]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.owner_id == USER_ID, channel
