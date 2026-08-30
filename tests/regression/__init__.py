"""Requirement 25 regression baseline.

``capture_baseline.py`` records the behaviour of the live-order, credential-vault,
live-runtime, signal-generation, signal-trace, billing and authentication paths, of every
surface named in Requirement 25.4, of the risk-utilisation computation (Requirement 25.5)
and of the six existing ``/api/paper/*`` endpoints (Requirement 17.12), one JSON file per
path and per surface under ``baseline/``.

``test_baseline_unchanged.py`` re-runs each capture and asserts per-file equality.
"""
