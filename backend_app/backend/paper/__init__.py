"""Paper trading package.

The server-side paper trading domain: the order state machine, the accounting engine,
the deterministic simulator and the persistence layer that back ``/api/paper/*``.

Modules
-------
``paper_order_state.py``
    ``PaperOrderState`` (the six Paper_Order_State values), ``PAPER_ORDER_TRANSITIONS``
    (the nine permitted transitions of Requirement 16.2), ``TERMINAL``,
    ``can_transition`` and ``LEGACY_STATUS_FOR_STATE`` - the single mapping onto the
    retained ``PaperOrderStatus`` spellings.

Purity contract
---------------
The state-machine, accounting and validation modules in this package take values and
return values. No database handle, no HTTP client, no FastAPI import, no ``random``
draw. That is what makes them usable from the request path, the session worker and a
property test alike.

Following the convention of ``backend_app/backend/__init__.py``, no runtime imports are
performed here. Import directly from the submodules:

    from backend_app.backend.paper.paper_order_state import PaperOrderState
"""

__all__ = []
