"""SCRATCH probe - delete after use.

Drives the two failing Requirement 21.4 cells twice:
  (a) exactly as the matrix harness drives them today (un-entitled u1)
  (b) with the two plan gates the harness does NOT override also neutralised,
      i.e. an ENTITLED caller who is under their strategy capacity.

If (b) reproduces the originally pinned 409/404 and 404/404 pairs, the gap is
MASKED, not closed.
"""
from contextlib import contextmanager

from fastapi import Depends

import tests.property.test_tenant_isolation_matrix as M
from backend_app.main import app

CELL_KEYS = (
    "POST /api/library/{library_id}/checkout",
    "POST /api/library/{library_id}/clone",
)

cells = [
    c
    for c in M.MATRIX
    if c.row.key in CELL_KEYS and c.column == M.LISTING_PRIVATE
]
print("cells found:", [(c.row.key, c.column, c.oracle) for c in cells])

original_matrix_app = M._matrix_app

import os

EXTRA_GATES = tuple(
    n for n in os.environ.get("PROBE_GATES", "").split(",") if n
)
print("EXTRA_GATES =", EXTRA_GATES)


@contextmanager
def entitled_matrix_app(store, caller):
    with original_matrix_app(store, caller) as client:
        for name in EXTRA_GATES:
            gate = getattr(M.plan_gates, name)
            app.dependency_overrides[gate] = lambda: True
        yield client


def drive(label):
    for cell in cells:
        foreign = M._attempt(cell, M.U2_IDS, caller=M.U1, identity=M.U1)
        absent = M._attempt(cell, M.ABSENT_IDS, caller=M.U1, identity=M.U1)
        print()
        print(f"[{label}] {cell.row.key} x {cell.column}")
        print("   foreign =", repr(M._gap_signature(foreign)))
        print("   absent  =", repr(M._gap_signature(absent)))
        pinned = M.REQUIREMENT_21_4_GAPS[(cell.row.key, cell.column)]
        print("   pinned  =", repr(pinned.foreign), "/", repr(pinned.absent))
        print(
            "   identical?",
            M._gap_signature(foreign) == M._gap_signature(absent),
            "| matches pin?",
            (M._gap_signature(foreign), M._gap_signature(absent))
            == (pinned.foreign, pinned.absent),
        )


print("=" * 72)
print("(a) HARNESS AS IT IS TODAY - u1 un-entitled")
print("=" * 72)
drive("today")

print()
print("=" * 72)
print("(b) ENTITLED CALLER - the two missing plan gates also neutralised")
print("=" * 72)
M._matrix_app = entitled_matrix_app
try:
    drive("entitled")
finally:
    M._matrix_app = original_matrix_app
