"""Load-testing harnesses for the observability stack.

Two standalone scripts: ``metrics_pressure_test.py`` (Prometheus exporter under
sustained cardinality) and ``websocket_stress_test.py`` (fan-out under concurrent
subscribers). Neither is imported by the application; both are run directly.

THIS FILE EXISTS BECAUSE THE PACKAGE HAS TO BE IMPORTABLE TO BE AUDITED.
``scripts/import_audit.py`` walks ``backend_app`` and fails the Code Quality job on any
directory holding ``.py`` files without an ``__init__.py`` -- the directory was reported
as the job's single "Missing __init__.py" entry, which is why `01 PR Check` was red. The
reason the check cares is not style: without this file the two modules are not part of any
package, so ``import_audit`` cannot reach them and neither can a test that wants to assert
anything about them. They were unauditable, not merely unconventional.

Deliberately exports nothing. These are entry points, not a library, and re-exporting them
here would make importing the package start a load test.
"""
