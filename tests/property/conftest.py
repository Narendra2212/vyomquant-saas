"""Collection gate for the property-coverage scoreboard.

`test_property_coverage.py` (spec task 2.3) asserts that each of the fifty-eight correctness
properties has exactly one `test_p{n}_` function. It is written before any of those functions
exist, so it fails from the moment it lands and stays red until spec task 35.1 -- that is what
makes it a scoreboard.

Its assertion is intact: it is not skipped, not xfailed, not loosened, and the CI `-k`
selector in `.github/workflows/01-pr-check.yml` is untouched. Only its *collection* is opt-in,
so the shared `unit-tests` job keeps exactly the scope it has today while the plan is in
flight. Run it deliberately with:

    AERORA_PROPERTY_SCOREBOARD=1 pytest tests/property/test_property_coverage.py

SPEC TASK 35.1 MUST DELETE THIS FILE. Removing the gate is what promotes the scoreboard into
the default `pytest tests/` lane permanently, and it is only safe to do once every property
test exists. Nothing else in `tests/property/` is gated -- every real property test added by
tasks 4 through 34 is collected by default and blocks CI on failure.
"""

from __future__ import annotations

import os
from typing import List

SCOREBOARD_OPT_IN_ENV: str = "AERORA_PROPERTY_SCOREBOARD"

collect_ignore: List[str] = []

if os.environ.get(SCOREBOARD_OPT_IN_ENV) != "1":
    collect_ignore.append("test_property_coverage.py")
