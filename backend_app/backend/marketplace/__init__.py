"""Marketplace pure-logic package.

Spec: marketplace-subscriptions-paper-trading. ``design.md`` -> "Architecture" states the
layering rule this package exists to hold: the marketplace's decision logic lives in modules
that import no FastAPI and perform no I/O, so each one is importable and property-testable
standalone.

Modules
-------
money                  Minor_Units integer arithmetic and ISO 4217 currency exponents
                       (Requirements 8.12, 8.13, 9.2, 10.1, 10.2, 10.3)

IMPORTANT: no runtime imports here. The same convention ``backend_app/backend/__init__.py``
already follows - importing a submodule must not pull the rest of the package, a database
handle or an event loop behind it. Import directly from the submodule:

  from backend_app.backend.marketplace.money import split_ninety_ten
"""

__all__ = []
