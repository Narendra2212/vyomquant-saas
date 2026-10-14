"""API-04 -- the global exception handler logs through `logging`, not `print`.

THE DEFECT
    ``main.py``'s ``global_exception_handler`` wrote the exception type, message and
    full traceback to stdout via three bare ``print()``/``traceback.print_exc()``
    calls -- entirely bypassing this application's own logging configuration (no
    level, no handler routing, no structured format for whatever ingests this
    process's logs) and landing in the SAME raw, unredacted stream any other stdout
    write would, rather than the one path every other error in this codebase reports
    an unhandled failure through.

THE FIX
    Routed through the module's own ``logger.exception(...)`` instead, which logs at
    ERROR and attaches the current exception's traceback. The response body is
    unchanged -- it already reported only the generic "Internal server error" and
    never echoed the exception to the caller; this fix is about where the detail is
    recorded operationally, not about what the client sees.

WHAT THIS FILE PROVES
    * the handler calls ``logger.exception`` (or an equivalent `logging` call) and
      contains no bare ``print`` call;
    * driving the handler with a real exception actually reaches the logger at ERROR
      level with the exception's own type name and message somewhere in the record,
      and nothing is written to stdout;
    * the response body is untouched: still a generic 500 with no exception detail.
"""
from __future__ import annotations

import ast
import inspect
import logging
import textwrap

import pytest
from starlette.requests import Request

from backend_app.main import global_exception_handler


def test_the_handler_source_contains_no_bare_print():
    source = textwrap.dedent(inspect.getsource(global_exception_handler))
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]
    assert not calls, (
        "global_exception_handler still writes to stdout directly via print() instead of "
        "going through the application's logging configuration"
    )
    attr_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "print_exc"
    ]
    assert not attr_calls, "global_exception_handler still calls traceback.print_exc()"


def test_the_handler_logs_through_the_logging_module(caplog: pytest.LogCaptureFixture):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/boom",
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "path_params": {},
    }
    request = Request(scope)

    class _Boom(ValueError):
        pass

    with caplog.at_level(logging.ERROR, logger="Algo22"):
        import asyncio

        asyncio.run(
            global_exception_handler(request, _Boom("a distinctive failure message"))
        )

    records = [r for r in caplog.records if r.name == "Algo22"]
    assert records, "no ERROR-level record was emitted on the application's own logger"
    joined = "\n".join(r.getMessage() for r in records)
    assert "_Boom" in joined or "Boom" in joined
    assert "a distinctive failure message" in joined
    # logger.exception attaches the traceback via exc_info, not by formatting it into
    # the message string itself -- confirm the record actually carries exception info.
    assert any(r.exc_info is not None for r in records)


def test_the_response_body_still_hides_the_exception_detail(capsys: pytest.CaptureFixture):
    """NEGATIVE CONTROL: nothing about the fix changed what the CLIENT sees, and
    nothing is written to stdout any more either.

    Calls the handler directly (the same seam ``test_the_handler_logs_through_the_
    logging_module`` uses) rather than through the full app/middleware stack, so this
    is a test of the handler's own body-construction, not of an unrelated
    middleware/exception-propagation interaction.
    """
    import asyncio

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/__api04_test_boom__",
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "path_params": {},
    }
    request = Request(scope)
    exc = RuntimeError("a secret-shaped detail that must never reach the client")

    response = asyncio.run(global_exception_handler(request, exc))

    assert response.status_code == 500
    body_bytes = bytes(response.body)
    assert b"a secret-shaped detail" not in body_bytes
    assert b"Internal server error" in body_bytes

    captured = capsys.readouterr()
    assert "a secret-shaped detail" not in captured.out
    assert "GLOBAL EXCEPTION HANDLER" not in captured.out
