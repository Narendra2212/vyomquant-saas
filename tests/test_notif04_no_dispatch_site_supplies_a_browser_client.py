"""NOTIF-04: no notification dispatch site may supply a request-scoped client.

WHAT WAS BROKEN
---------------
`notification_dispatcher._resolve_persistence_client` honours a caller-supplied client
("a supplied client wins"), which NOTIF-01 built so that no call site *had* to change.
Three sites in `routers/support.py` took that option, and none of them could ever write:

    :592   user_id="support_staff"    written through the ticket submitter's client
    :1074  user_id=target_user_id     written through the support staff's client
    :904   user_id=<ticket owner>     written through the caller's client   <- NOTIF-04

`public.notifications` carries an owner-scoped policy, so for a row addressed to anyone
but the caller `auth.uid() <> user_id` and PostgREST refuses the insert. NOTIF-03 fixed
the first two by dropping the argument. It missed the third, because the change was
scoped by the two sites the dispatcher's docstring happened to name, and the docstring
was describing the defect rather than enumerating it.

Since then `999_privilege_hardening.sql` has revoked `authenticated INSERT` on
`public.notifications` -- measured in production on 2026-11-18, `authenticated` holds
`DELETE,SELECT,UPDATE`. So the surviving site now fails on the PRIVILEGE (42501) as well
as the policy, and it fails for an owner-performed status change too, not only a
staff-performed one.

Every one of these sites is wrapped in `except Exception: logger.debug(...)`, so a
customer whose ticket was resolved, closed or reopened was simply never told.

WHY A TEST AND NOT JUST THE FIX
-------------------------------
This is the second time this defect has been fixed site-by-site. A per-site fix cannot
stop a fourth site appearing, because the dispatcher still accepts the argument -- and it
must keep accepting it, since that is the seam a service-role caller legitimately uses.
So the invariant is asserted over EVERY call site by parsing the source, which is the
only form that holds for code not yet written.

The complementary half is `test_mkt10_backtest_writes_are_service_role.py`: move the
writer, then take the privilege away. This file is the "did every writer actually move"
check for notifications.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend_app"

#: Parameter names of `dispatch_user_notification` / `create_notification` that carry a
#: Supabase client.
CLIENT_KWARGS = {"supabase", "supabase_client", "sb"}

#: The dispatch entry points whose call sites this rule governs.
DISPATCH_FUNCS = {"dispatch_user_notification", "create_notification"}

#: Identifiers that denote a SERVICE-ROLE client and are therefore permitted. A
#: service-role caller supplying its own client is the seam working as designed -- what is
#: forbidden is handing in a client built from the caller's JWT.
SERVICE_CLIENT_NAMES = {
    "svc", "service_sb", "service_client", "sb_service", "_background_sb",
    "get_service_supabase_async", "get_service_supabase",
}


#: The dispatcher module itself is excluded, and only it. `dispatch_user_notification`
#: forwards the client that `_resolve_persistence_client` just resolved into
#: `create_notification` (`supabase=client`), which is the resolution itself and not a
#: producer choosing a client. Excluding the file is narrower than excluding the pattern:
#: any OTHER module forwarding a variable called `client` is still caught.
RESOLVER_MODULE = BACKEND / "core" / "notification_dispatcher.py"


def _python_files():
    for path in BACKEND.rglob("*.py"):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        if path == RESOLVER_MODULE:
            continue
        yield path


def _describe(node: ast.AST) -> str:
    """A readable name for whatever expression was passed as the client."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _describe(node.func) + "()"
    if isinstance(node, ast.Attribute):
        return _describe(node.value) + "." + node.attr
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Await):
        return "await " + _describe(node.value)
    return type(node).__name__


def _dispatch_sites_supplying_a_client():
    """Every dispatch call site that passes a client, with what it passes."""
    found = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's problem
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(
                node.func, "id", None
            )
            if name not in DISPATCH_FUNCS:
                continue
            for kw in node.keywords:
                if kw.arg in CLIENT_KWARGS:
                    found.append((
                        path.relative_to(REPO).as_posix(),
                        node.lineno,
                        kw.arg,
                        _describe(kw.value),
                    ))
    return found


class TestNoBrowserRoleClientReachesANotificationWrite:
    def test_no_dispatch_site_passes_a_request_scoped_client(self):
        offenders = [
            site for site in _dispatch_sites_supplying_a_client()
            # `None` is explicit "no client", which is the same as omitting it.
            if site[3] != "None"
            and site[3].replace("()", "").replace("await ", "") not in SERVICE_CLIENT_NAMES
        ]
        assert not offenders, (
            "a notification dispatch site supplies a client that is not a service-role "
            "client:\n"
            + "\n".join(f"  {f}:{line}  {arg}={what}" for f, line, arg, what in offenders)
            + "\n\n`public.notifications` carries an owner-scoped policy AND "
              "`authenticated` no longer holds INSERT (revoked by "
              "999_privilege_hardening.sql), so such a write is refused twice over -- and "
              "every dispatch site is wrapped in `except Exception: logger.debug`, so the "
              "refusal is invisible. Pass NO client and let "
              "`_resolve_persistence_client` resolve the service-role client."
        )

    def test_the_support_status_change_site_specifically(self):
        """The site NOTIF-03 missed. Named explicitly so a regression here is unambiguous."""
        source = (BACKEND / "routers" / "support.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        status_dispatches = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(
                node.func, "id", None
            )
            if name != "dispatch_user_notification":
                continue
            for kw in node.keywords:
                if kw.arg == "event_type":
                    rendered = ast.dump(kw.value)
                    if "support_ticket_" in rendered or "ticket_status" in rendered:
                        status_dispatches.append(node)
        assert status_dispatches, (
            "the ticket status-change notification dispatch is gone from support.py -- if "
            "it moved, point this test at its new home rather than deleting it"
        )
        for node in status_dispatches:
            supplied = [kw.arg for kw in node.keywords if kw.arg in CLIENT_KWARGS]
            assert not supplied, (
                f"support.py:{node.lineno} passes {supplied} to the ticket status-change "
                "notification. That row is addressed to the ticket OWNER while the status "
                "change is performed by support staff, so the owner policy refuses it; and "
                "`authenticated` has no INSERT on notifications any more, so it is refused "
                "on privilege regardless of who calls it."
            )


class TestTheDispatcherStillAcceptsAServiceClient:
    """The fix must not close the seam, only stop the wrong client going through it."""

    def test_resolve_persistence_client_still_prefers_a_supplied_client(self):
        import inspect

        from backend_app.core import notification_dispatcher as nd

        source = inspect.getsource(nd._resolve_persistence_client)
        assert "if supplied is not None" in source, (
            "`_resolve_persistence_client` no longer honours a supplied client. "
            "library.py passes a service-role client deliberately at five sites; "
            "removing the seam would break them."
        )

    def test_the_dispatcher_falls_back_to_the_service_role_client(self):
        import inspect

        from backend_app.core import notification_dispatcher as nd

        source = inspect.getsource(nd._resolve_persistence_client)
        assert "get_service_supabase_async" in source, (
            "the dispatcher must resolve the service-role client when no client is "
            "supplied -- that is what makes passing none the correct choice at every "
            "support site"
        )
