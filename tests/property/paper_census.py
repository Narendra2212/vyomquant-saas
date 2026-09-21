"""
tests/property/paper_census.py - the non-vacuity census, written once.

Not a test module: the name deliberately does not match ``test_*.py``, so pytest does not collect
it and ``tests/property/test_property_coverage.py`` - which discovers by ``ast``-walking every
``test_*.py`` under ``tests/property/`` - does not read it looking for a ``test_p{n}_`` claim.

WHY THIS FILE EXISTS
--------------------
Four property modules now need the same two helpers:

* :class:`Recorder` - one property's census: a count per bucket, a floor per bucket, a Hypothesis
  ``event`` label per bucket. It was written for ``tests/property/test_paper_order_lifecycle.py``
  (P-17..P-20, P-24) and is needed verbatim by ``test_paper_idempotence.py`` (P-21, P-22) and
  ``test_paper_confluence.py`` (P-23).
* :func:`publish_hypothesis_statistics` - restores ``--hypothesis-show-statistics`` for a property
  driven by a **nested** ``check``. Three copies of it already existed, character for character,
  in ``test_paper_persistence_roundtrip.py`` (P-32), ``test_market_event_dedupe.py`` (P-54) and
  ``test_paper_order_lifecycle.py``.

Three copies of a census helper are three censuses, and the one that gets fixed is never the one
that is running. So both live here and every module imports them.

WHY THE PROPERTIES ARE DRIVEN BY A NESTED ``check``
--------------------------------------------------
A census can only be asserted **after** Hypothesis has finished generating, so the property body
has to be an inner function and the collected ``test_p{n}_...`` has to be a plain function that
runs it and then calls :meth:`Recorder.assert_not_vacuous`. That shape has one cost: the collected
function is not itself a Hypothesis test, so the bundled pytest plugin returns early on
``is_hypothesis_test(item.obj)`` and never installs its statistics collector -
``--hypothesis-show-statistics`` would print nothing at all for the module.
:func:`publish_hypothesis_statistics` installs the same collector by hand, which is what makes the
distribution - including the census's ``event`` labels - readable in CI rather than only assertable.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Set

from hypothesis import event
from hypothesis.statistics import collector as hypothesis_statistics_collector
from hypothesis.statistics import describe_statistics

__all__ = ["Recorder", "publish_hypothesis_statistics"]


class Recorder:
    """One property's census: a count per bucket, a floor per bucket, an ``event`` per bucket.

    ``mark`` refuses an unknown bucket rather than creating one, so a renamed bucket is a loud
    failure instead of a silently empty count that no floor guards.
    """

    __slots__ = ("name", "floors", "labels", "counts", "_seen")

    def __init__(
        self,
        name: str,
        floors: Mapping[str, int],
        labels: Mapping[str, str],
    ) -> None:
        unknown = sorted(set(labels) - set(floors))
        assert not unknown, f"{name}: labelled buckets with no floor: {unknown}"
        self.name = name
        self.floors: Dict[str, int] = dict(floors)
        self.labels: Dict[str, str] = dict(labels)
        self.counts: Dict[str, int] = {key: 0 for key in floors}
        self._seen: Set[str] = set()

    def start(self) -> None:
        """Begin one example. The per-example ``event`` set is cleared, the counts are not."""
        self._seen = set()

    def mark(self, key: str, times: int = 1) -> None:
        assert key in self.counts, f"{self.name}: {key!r} is not a census bucket"
        self.counts[key] += int(times)
        self._seen.add(key)

    def finish(self) -> None:
        """End one example, emitting one Hypothesis ``event`` per bucket it exercised."""
        for key in sorted(self._seen):
            label = self.labels.get(key)
            if label is not None:
                event(label)

    def assert_not_vacuous(self) -> None:
        shortfalls = {
            key: (self.counts[key], floor)
            for key, floor in self.floors.items()
            if self.counts[key] < floor
        }
        assert not shortfalls, (
            f"{self.name} would be vacuous: "
            + ", ".join(
                f"{key} occurred {seen} time(s), floor {floor}"
                for key, (seen, floor) in sorted(shortfalls.items())
            )
            + f" -- full census {self.counts}. Fix a shortfall by FORCING the case in the "
            "generator, never by lowering the floor."
        )


def publish_hypothesis_statistics(item: Any) -> Any:
    """Report a nested property's Hypothesis statistics under ``item`` (a pytest item).

    Used as a context manager around the nested ``check()`` call. See the module docstring for why
    the nesting is necessary and why the bundled plugin's collector has to be installed by hand.
    """

    def note(stats: Dict[str, Any]) -> None:
        stats["nodeid"] = item.nodeid
        item.hypothesis_statistics = describe_statistics(stats)

    return hypothesis_statistics_collector.with_value(note)
