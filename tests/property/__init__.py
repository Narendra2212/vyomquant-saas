"""Property-based tests for the marketplace, subscriptions and paper trading change.

One module per group of related correctness properties, as declared in
`.kiro/specs/marketplace-subscriptions-paper-trading/design.md` section
"Property-to-test mapping". Exactly one `test_p{n}_...` function per property
P-1...P-58; `test_property_coverage.py` in this package is the guard that keeps
that one-to-one relationship checkable rather than implied.
"""
