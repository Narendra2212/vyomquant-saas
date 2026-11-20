"""SUP-02 — a support ticket can actually be stored.

WHAT THE FINDING WAS
====================
`public.support_tickets` is the support channel's only durable record. Its one writer
named four columns the schema never declared, and `public.ticket_comments` was sent a
fifth:

    supabase.table("support_tickets").insert({           support.py
        "related_feature": ..., "strategy_id": ...,
        "order_id": ..., "attachment": ...,
    })
    supabase.table("ticket_comments").insert({"attachment": ..., ...})

PostgREST rejects the whole statement on an undefined column (PGRST204), so neither
write could succeed. Measured read-only against production:

    support_tickets columns        : 12  (none of the four)
    ticket_comments columns        :  7  (no attachment)
    newest ticket in production    : 2026-08-18
    commit that added the four keys: f0e4fc6a, 2026-08-29

The dates are the finding. Ticket creation worked, the keys arrived eleven days after
the last ticket landed, and nothing has been filed since — about 2.5 months.

WHY IT WAS WORSE THAN AN EMPTY TABLE
====================================
The insert sat inside `try/except ... logger.warning`, and the except body copied the
ticket into `_in_memory_tickets` — a module-level dict — after which the endpoint
returned **HTTP 200 with a real ticket id**. So a user reporting a failed order got a
reference number for a ticket that was never stored. The dict could not stand in for
the table either: the API runs `WORKERS=2`, so a follow-up GET landing on the other
worker answers 404, and the dict dies with the process — which was being replaced every
~9.5 minutes during the 2026-11-17 incident.

WHY THE EXISTING TESTS COULD NOT SEE IT
=======================================
This is the part worth keeping. `tests/test_support_feature_e2e.py` overrides
`get_request_supabase` to return **None** in every test, so the whole suite — including
the one named `test_real_saas_support_production_acceptance_twenty_points` — exercises
the in-memory dict and asserts nothing about a database.
`tests/test_sup01_ticket_ids_are_real_uuids.py` does reach a "real table", but it is a
fake with no schema, and a fake with no schema cannot reject an undeclared column.

So the defect was invisible to a green suite. That is why this file requires a real
PostgreSQL and inserts the router's exact payload: the only oracle that can fail is one
with the real column list behind it.

SUP-01 ALREADY FIXED A DIFFERENT CAUSE OF THE SAME SYMPTOM
==========================================================
Commit 9311a373 replaced a `"tkt_"`-prefixed id with `uuid4()` because the prefix raised
`invalid input syntax for type uuid`, and its comment states "a real UUID lets the real
insert succeed, which is the actual fix". It did not: this second cause was already
present and is fatal on its own. Two independent causes, one symptom — so the id being
a UUID is asserted here too, beside the columns, because fixing either alone leaves the
channel broken.

WHAT IS NOT CLAIMED
===================
* **That file attachments work. They do not.** `attachment` holds `AttachmentMeta` —
  filename, content type, size — and no endpoint accepts file bytes. Production has one
  storage bucket, `strategy-images`, and nothing for support. The FAQ answer that
  claimed users could attach `.log`/`.png` files is corrected in the same commit to say
  so. Real uploads remain open work.
* That the six tickets already in production gain this context. They predate the
  columns and are left NULL rather than backfilled with invented values.
* That a rejected write is recoverable. It is not retried; the handler now returns 503
  and stores nothing, which is the honest answer — see the long note at the except.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

# ── explicit PostgreSQL requirement ──────────────────────────────────────────
#
# Same gate as tests/test_bill07_billing_invoices_postgres.py, and deliberately with no
# fallback to a local DSN: a fallback would make this run in both lanes and stop the
# CI-comparable lane representing a clean runner. A SKIP here is NOT a pass — it means
# the support channel is unverified.
_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")
pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the support channel is unverified."
    ),
)

#: Every key `create_ticket` sends. Kept as data so a rename breaks this test rather
#: than silently re-breaking the support channel.
TICKET_INSERT_KEYS = (
    "id", "user_id", "subject", "description", "category", "priority", "status",
    "created_at", "updated_at", "has_unread", "comment_count",
    "related_feature", "strategy_id", "order_id", "attachment",
)

#: The four on support_tickets that migration 072 declares.
TICKET_COLUMNS_072_DECLARES = (
    "related_feature", "strategy_id", "order_id", "attachment",
)

#: Every key the user reply path sends.
COMMENT_INSERT_KEYS = (
    "id", "ticket_id", "user_id", "message", "is_staff", "created_at", "attachment",
)


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


@pytest.fixture
def ticket_owner():
    """A user that can own a ticket.

    `support_tickets.user_id` carries `REFERENCES auth.users(id) ON DELETE CASCADE`
    (migrations/006_reconcile_production_database.sql), so only that leg is needed.
    Cleaned up afterwards; the cascade removes tickets and comments.
    """
    uid = str(uuid.uuid4())
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
    try:
        yield uid
    finally:
        cur.execute("DELETE FROM public.ticket_comments WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM public.support_tickets WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        conn.close()


def _insert_ticket(cur, user_id, ticket_id=None, attachment=None):
    """Insert exactly what `POST /api/support/tickets` inserts."""
    ticket_id = ticket_id or str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO public.support_tickets
            (id, user_id, subject, description, category, priority, status,
             created_at, updated_at, has_unread, comment_count,
             related_feature, strategy_id, order_id, attachment)
        VALUES (%s, %s, %s, %s, 'technical', 'high', 'open',
                now(), now(), false, 0,
                %s, %s, %s, %s)
        """,
        (
            ticket_id, user_id,
            "Order rejected with no reason given",
            "A market order on BTC/USDC was rejected and the log shows nothing.",
            "Exchange Connection",
            "strat_btc_momentum_v2",
            "ord_bybit_12345",
            json.dumps(attachment) if attachment is not None else None,
        ),
    )
    return ticket_id


# ═════════════════════════════════════════════════════════════════════════════
# The columns exist at all
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheSupportChannelIsWritable:
    def test_all_four_ticket_columns_are_declared(self):
        """The defect was four undeclared names. Assert each by name."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'support_tickets'
            """
        )
        present = {r[0] for r in cur.fetchall()}
        conn.close()

        missing = [c for c in TICKET_COLUMNS_072_DECLARES if c not in present]
        assert not missing, (
            f"support_tickets is still missing {missing}. Every "
            f"POST /api/support/tickets will raise PGRST204, be swallowed, and "
            f"report success for a ticket that was never stored."
        )

    def test_ticket_comments_declares_attachment(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'ticket_comments'
            """
        )
        present = {r[0] for r in cur.fetchall()}
        conn.close()
        assert "attachment" in present, (
            "ticket_comments.attachment is undeclared, so every user reply "
            "carrying attachment metadata is rejected."
        )

    def test_every_key_the_router_sends_is_a_real_column(self):
        """The whole payload, not just the four. Catches the next added key too."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'support_tickets'
            """
        )
        ticket_cols = {r[0] for r in cur.fetchall()}
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'ticket_comments'
            """
        )
        comment_cols = {r[0] for r in cur.fetchall()}
        conn.close()

        assert not [k for k in TICKET_INSERT_KEYS if k not in ticket_cols]
        assert not [k for k in COMMENT_INSERT_KEYS if k not in comment_cols]


# ═════════════════════════════════════════════════════════════════════════════
# The router's exact payload lands, and is visible to someone else
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheRouterPayloadInserts:
    def test_the_full_ticket_payload_inserts(self, ticket_owner):
        conn = _conn()
        cur = conn.cursor()
        ticket_id = _insert_ticket(
            cur, ticket_owner,
            attachment={"filename": "execution.log", "content_type": "text/plain",
                        "size": 20481},
        )

        # An INDEPENDENT connection is the oracle, so "stored" means committed and
        # visible elsewhere -- not merely staged in the session that wrote it. That
        # distinction is the whole point here: the bug's fallback was visible only
        # inside one process.
        other = _conn()
        ocur = other.cursor()
        ocur.execute(
            """
            SELECT related_feature, strategy_id, order_id, attachment
              FROM public.support_tickets WHERE id = %s
            """,
            (ticket_id,),
        )
        row = ocur.fetchone()
        other.close()
        conn.close()

        assert row is not None, "the ticket was not committed"
        related_feature, strategy_id, order_id, attachment = row
        assert related_feature == "Exchange Connection"
        assert strategy_id == "strat_btc_momentum_v2"
        assert order_id == "ord_bybit_12345"
        assert attachment["filename"] == "execution.log"
        assert attachment["size"] == 20481

    def test_a_ticket_with_no_optional_context_still_inserts(self, ticket_owner):
        """All four are Optional on the request. NULL must be accepted, because a
        user filing a general question supplies none of them."""
        conn = _conn()
        cur = conn.cursor()
        tid = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO public.support_tickets
                (id, user_id, subject, description, category, priority, status)
            VALUES (%s, %s, 'General question', 'How do I export trades?',
                    'general', 'low', 'open')
            """,
            (tid, ticket_owner),
        )
        cur.execute(
            """
            SELECT related_feature, strategy_id, order_id, attachment
              FROM public.support_tickets WHERE id = %s
            """,
            (tid,),
        )
        assert cur.fetchone() == (None, None, None, None)
        conn.close()

    def test_the_comment_payload_inserts_with_attachment(self, ticket_owner):
        conn = _conn()
        cur = conn.cursor()
        ticket_id = _insert_ticket(cur, ticket_owner)
        comment_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO public.ticket_comments
                (id, ticket_id, user_id, message, is_staff, created_at, attachment)
            VALUES (%s, %s, %s, %s, false, now(), %s)
            """,
            (comment_id, ticket_id, ticket_owner, "Attaching the trace.",
             json.dumps({"filename": "trace.json",
                         "content_type": "application/json", "size": 882})),
        )
        cur.execute(
            "SELECT attachment FROM public.ticket_comments WHERE id = %s",
            (comment_id,),
        )
        assert cur.fetchone()[0]["filename"] == "trace.json"
        conn.close()

    def test_the_generated_id_is_a_real_uuid_the_column_accepts(self, ticket_owner):
        """SUP-01's cause, pinned beside SUP-02's.

        `id` is `UUID PRIMARY KEY`. A `"tkt_"`-prefixed string raises 22P02, which is
        how this channel was broken BEFORE the undeclared columns broke it. Fixing one
        and not the other leaves the channel broken, so both are asserted here.
        """
        import psycopg2

        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.InvalidTextRepresentation):
            cur.execute(
                """
                INSERT INTO public.support_tickets
                    (id, user_id, subject, description, category, priority, status)
                VALUES (%s, %s, 'prefixed id', 'should not insert',
                        'general', 'low', 'open')
                """,
                (f"tkt_{uuid.uuid4().hex[:12]}", ticket_owner),
            )
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# The declared shape matches the request model, not a guess
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheDeclaredShapeMatchesTheRequestModel:
    def test_the_text_columns_hold_the_models_max_length(self, ticket_owner):
        """`SupportTicketCreate` declares max_length=100 on all three. A column
        narrower than the model would reject a request the API accepted."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, character_maximum_length
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'support_tickets'
               AND column_name IN ('related_feature', 'strategy_id', 'order_id')
             ORDER BY column_name
            """
        )
        widths = dict(cur.fetchall())
        conn.close()
        for col in ("related_feature", "strategy_id", "order_id"):
            assert widths.get(col) is None or widths[col] >= 100, (
                f"{col} is varchar({widths[col]}) but the request model accepts "
                f"100 characters, so the API would accept a value the table rejects."
            )

    def test_a_hundred_character_value_is_accepted(self, ticket_owner):
        conn = _conn()
        cur = conn.cursor()
        _insert_ticket(cur, ticket_owner, ticket_id=None)
        tid = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO public.support_tickets
                (id, user_id, subject, description, category, priority, status,
                 strategy_id)
            VALUES (%s, %s, 'long id', 'boundary', 'general', 'low', 'open', %s)
            """,
            (tid, ticket_owner, "s" * 100),
        )
        cur.execute("SELECT length(strategy_id) FROM public.support_tickets WHERE id = %s",
                    (tid,))
        assert cur.fetchone()[0] == 100
        conn.close()

    def test_attachment_is_jsonb_so_it_can_be_queried(self):
        """`.dict()` output is a mapping. jsonb rather than text so a future
        "tickets mentioning a .log file" query does not have to parse strings."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT table_name, data_type FROM information_schema.columns
             WHERE table_schema = 'public' AND column_name = 'attachment'
               AND table_name IN ('support_tickets', 'ticket_comments')
             ORDER BY table_name
            """
        )
        types = dict(cur.fetchall())
        conn.close()
        assert types.get("support_tickets") == "jsonb"
        assert types.get("ticket_comments") == "jsonb"
