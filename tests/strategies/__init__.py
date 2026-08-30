"""Shared Hypothesis generators for the marketplace / paper-trading property suite.

Feature: marketplace-subscriptions-paper-trading (design.md § Testing Strategy).

Every generator used by more than one property test lives here, so a generator is
defined once:

- ``marketplace_generators`` — money, calendar instants, backtest evidence,
  listing rows, Protected_Logic documents, tenant pairs.
- ``paper_generators`` — market event streams, order intents, fill sequences,
  equity series (task 2.2).
"""
