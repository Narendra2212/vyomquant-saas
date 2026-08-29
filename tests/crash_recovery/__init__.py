"""The crash-recovery regression suite Requirement 19.4 mandates.

One test module per named failure mode (tasks 12.1 - 12.5), all five asserting the same
three things against a different interruption, all five built on the ONE harness in
``tests/crash_recovery/harness.py``. See that module's header for the seams.
"""
