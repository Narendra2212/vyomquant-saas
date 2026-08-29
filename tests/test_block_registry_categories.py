# -*- coding: utf-8 -*-
"""
tests/test_block_registry_categories.py - the SB-03 guard test for category coverage.

Spec: strategy-builder task 1.17. Requirements 4.2, 4.3, 4.9.

**Property 9: Every `BlockCategory` holds at least one descriptor.**
(`design.md` -> Correctness properties, 9: `forall c in BlockCategory: |blocks(c)| >= 1`.)

What SB-03 actually was
-----------------------
The FEATURE_ENGINEERING palette section was permanently empty. The frontend rendered the
section header and a blank panel underneath it, and no author could ever place a feature
block, because the palette was a hand-maintained list that had drifted from the runtime's
real capability. Task 1.7 made the registry backend-authoritative and made an empty
category an `EmptyCategoryError` at assembly. This file is the test that keeps that guard
honest.

Relationship to `tests/test_block_registry.py`
----------------------------------------------
That file (task 1.7) already asserts the *positive* half - the real registry covers all
seven categories, FEATURE_ENGINEERING holds >= 15 - and that emptying one source raises
`EmptyCategoryError` mentioning that category. This file is the dedicated SB-03 regression
guard and adds the parts that keep the guard from going vacuous:

* The pre-fix state is *reconstructed as a real registry object* - assembled from the real
  descriptor sources with `features=()`, through the registry's own adapters, bypassing
  only `build_registry`'s guard - and the Property 9 check is run against it and observed
  to FAIL. The same check function is run against the real registry and observed to pass.
  So the guard is demonstrated to discriminate, not asserted as a constant.
* The served response is checked, not just `in_category`: SB-03 was a blank *panel*, so
  the guard covers the payload the palette actually renders from.
* Property 9 is exercised generatively over every combination of emptied sources, and the
  raised error is required to name *exactly* the categories that went empty - no more, no
  fewer - so the error text an operator reads at a failed startup stays accurate.
* Requirement 4.3's threshold of 15 is shown to be strictly stronger than Requirement
  4.2's "at least one": a 14-feature registry assembles fine and still fails 4.3.

Nothing is faked. The sources are the real `INDICATOR_SPECS`, `FEATURE_SPECS`,
`MODEL_SPECS`, `DATA_SPECS`/`MATH_SPECS`/`LOGIC_SPECS` and the real ACTION factory over
`exchange_executor.OrderType`. Broken variants are derived from that real data with
`dataclasses.replace`.
"""

import dataclasses
from typing import Dict, FrozenSet, List, Sequence, Set

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend import feature_engineering as features
from backend_app.backend.strategy_dag import registry
from backend_app.backend.strategy_dag.registry import (
    CATEGORY_ORDER,
    BlockDescriptor,
    BlockRegistry,
    DescriptorSources,
    EmptyCategoryError,
    build_registry,
    default_sources,
)
from backend_app.backend.strategy_dag.schema import BlockCategory

#: The category SB-03 left empty.
SB03_CATEGORY = BlockCategory.FEATURE_ENGINEERING

#: Requirement 4.3's floor for that category.
SB03_MINIMUM_DESCRIPTORS = 15

#: The five fields of `DescriptorSources`, read from the dataclass rather than listed, so a
#: sixth descriptor source cannot be added without this guard covering it.
SOURCE_FIELDS = tuple(f.name for f in dataclasses.fields(DescriptorSources))


# ---------------------------------------------------------------------------
# Property 9, as one check function used against both the real and the pre-fix state
# ---------------------------------------------------------------------------


def empty_categories(reg: BlockRegistry) -> List[BlockCategory]:
    """The categories violating Property 9 in ``reg``, in palette order."""
    return [category for category, _ in CATEGORY_ORDER if not reg.in_category(category)]


def check_property_9(reg: BlockRegistry) -> None:
    """Assert Property 9 over ``reg``: every ``BlockCategory`` holds >= 1 descriptor.

    Deliberately written as a function rather than inlined, so the very same assertion can
    be pointed at the pre-fix state and observed to fail. A guard that has never been seen
    to fail is not a guard.
    """
    violations = empty_categories(reg)
    assert not violations, (
        "Property 9 violated: "
        + ", ".join(category.value for category in violations)
        + " hold zero descriptors, so those palette sections render empty (SB-03)"
    )


def check_requirement_4_3(reg: BlockRegistry) -> None:
    """Assert Requirement 4.3: FEATURE_ENGINEERING holds at least 15 descriptors."""
    held = len(reg.in_category(SB03_CATEGORY))
    assert held >= SB03_MINIMUM_DESCRIPTORS, (
        f"{SB03_CATEGORY.value} holds {held} descriptors, below the "
        f"{SB03_MINIMUM_DESCRIPTORS} Requirement 4.3 demands"
    )


def served_category_counts(reg: BlockRegistry) -> Dict[str, int]:
    """How many blocks each advertised palette section holds *in the served payload*.

    Counted from ``to_dict()`` rather than from the internal index, because SB-03 was a
    blank panel in the client: the section was advertised and the block list for it was
    empty. This is the shape the palette renders from.
    """
    payload = reg.to_dict()
    counts = {section["id"]: 0 for section in payload["categories"]}
    for block in payload["blocks"]:
        counts[block["category"]] = counts.get(block["category"], 0) + 1
    return counts


def assemble_bypassing_the_category_guard(sources: DescriptorSources) -> BlockRegistry:
    """Assemble a registry from ``sources`` with the empty-category guard removed.

    This is how the pre-fix state is reconstructed. It runs the registry's own family
    adapters and its own adjacency derivation over real descriptor sources, and skips only
    the `EmptyCategoryError` check - so what comes back is the palette the platform served
    before task 1.7, as a real `BlockRegistry` that a real response can be rendered from.

    Nothing here re-implements a descriptor: an emptied source stays empty, and every
    descriptor is produced by the same adapter production uses.
    """
    descriptors: List[BlockDescriptor] = []
    descriptors += [registry.descriptor_from_block_spec(s) for s in sources.static_blocks]
    descriptors += [registry.descriptor_from_indicator_spec(s) for s in sources.indicators]
    descriptors += [registry.descriptor_from_feature_spec(s) for s in sources.features]
    descriptors += [
        registry.descriptor_from_model_spec(s)
        for s in sources.models
        if getattr(s, "backend_available", False)
    ]
    descriptors += [registry.descriptor_from_block_spec(s) for s in sources.actions]
    return BlockRegistry(registry._derive_adjacency(descriptors))


# ---------------------------------------------------------------------------
# Fixtures - the real sources, and the categories each source field feeds
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sources() -> DescriptorSources:
    """The real descriptor sources, read from the modules that own the implementations."""
    return default_sources()


@pytest.fixture(scope="module")
def reg(sources) -> BlockRegistry:
    return build_registry(sources)


@pytest.fixture(scope="module")
def pre_fix_sources(sources) -> DescriptorSources:
    """The real sources with the feature family removed - SB-03, reconstructed."""
    return dataclasses.replace(sources, features=())


@pytest.fixture(scope="module")
def pre_fix_registry(pre_fix_sources) -> BlockRegistry:
    """The pre-fix palette, as a registry object the guard would have let through."""
    return assemble_bypassing_the_category_guard(pre_fix_sources)


@pytest.fixture(scope="module")
def contributors(sources) -> Dict[BlockCategory, FrozenSet[str]]:
    """Category -> the ``DescriptorSources`` fields that publish into it.

    Derived from the real sources, not tabulated here: this test must not carry its own
    copy of "which family fills which section", which is the drift SB-03 was made of.
    """
    mapping: Dict[BlockCategory, Set[str]] = {category: set() for category in BlockCategory}
    for field_name in SOURCE_FIELDS:
        for category in categories_published_by(field_name, getattr(sources, field_name)):
            mapping[category].add(field_name)
    return {category: frozenset(fields) for category, fields in mapping.items()}


def categories_published_by(field_name: str, specs: Sequence[object]) -> Set[BlockCategory]:
    """Which categories the specs in one ``DescriptorSources`` field land in.

    ``IndicatorSpec`` and ``ModelSpec`` carry no ``category`` attribute - the registry's
    adapters assign INDICATOR and ML_DL - so those two are read from the adapters' contract
    rather than from the spec. An unavailable model publishes nothing, matching
    ``build_registry``.
    """
    if field_name == "indicators":
        return {BlockCategory.INDICATOR} if specs else set()
    if field_name == "models":
        available = [s for s in specs if getattr(s, "backend_available", False)]
        return {BlockCategory.ML_DL} if available else set()
    return {
        BlockCategory(getattr(spec.category, "value", spec.category)) for spec in specs
    }


# ---------------------------------------------------------------------------
# 1. Property 9 holds for the registry the platform actually serves
# ---------------------------------------------------------------------------


class TestProperty9OnTheRealRegistry:
    """**Property 9: Every `BlockCategory` holds at least one descriptor.**

    **Validates: Requirements 4.2, 4.3, 4.9**
    """

    def test_every_category_holds_at_least_one_descriptor(self, reg):
        """Requirement 4.2 - the positive half of Property 9."""
        check_property_9(reg)

    def test_every_advertised_palette_section_is_non_empty_in_the_served_payload(
        self, reg
    ):
        """Requirement 4.2 - no section is advertised with an empty block list.

        SB-03 was a rendered header over a blank panel, so the guard is applied to the
        response the palette is built from, not only to the internal index.
        """
        counts = served_category_counts(reg)
        assert set(counts) == {category.value for category, _ in CATEGORY_ORDER}
        blank = sorted(name for name, held in counts.items() if held == 0)
        assert not blank, f"palette sections served with zero blocks: {blank}"
        assert counts == reg.counts_by_category()

    def test_feature_engineering_holds_at_least_fifteen_descriptors(self, reg):
        """Requirement 4.3 - the category SB-03 left permanently empty."""
        check_requirement_4_3(reg)
        assert len(reg.in_category(SB03_CATEGORY)) == len(features.FEATURE_SPECS)
        assert served_category_counts(reg)[SB03_CATEGORY.value] >= (
            SB03_MINIMUM_DESCRIPTORS
        )

    def test_every_feature_descriptor_is_individually_placeable(self, reg):
        """The defect was not "few blocks", it was "no block an author can place".

        Each advertised feature block must resolve to a runnable callable and declare the
        ports the canvas needs to connect it, or the section is decorative.
        """
        for descriptor in reg.in_category(SB03_CATEGORY):
            assert callable(descriptor.resolve_runtime()), descriptor.block_id
            assert descriptor.inputs, descriptor.block_id
            assert descriptor.outputs, descriptor.block_id


# ---------------------------------------------------------------------------
# 2. The guard, demonstrated to fail against the pre-fix state
# ---------------------------------------------------------------------------


class TestGuardFailsAgainstThePreFixState:
    """The pre-fix state is SB-03: real sources, but nothing in FEATURE_ENGINEERING."""

    def test_the_pre_fix_registry_is_the_sb03_defect(self, pre_fix_registry):
        """Sanity: the reconstruction really is a palette with a blank feature panel."""
        assert pre_fix_registry.in_category(SB03_CATEGORY) == ()
        assert pre_fix_registry.counts_by_category()[SB03_CATEGORY.value] == 0
        # The section is still advertised, so the client renders a header over nothing.
        served = served_category_counts(pre_fix_registry)
        assert SB03_CATEGORY.value in served
        assert served[SB03_CATEGORY.value] == 0
        # And only that section is broken - the rest of the palette worked, which is
        # exactly why the defect survived: six of seven sections looked fine.
        assert empty_categories(pre_fix_registry) == [SB03_CATEGORY]

    def test_property_9_check_fails_on_the_pre_fix_registry(self, pre_fix_registry, reg):
        """The guard discriminates: the same check passes on real, fails on pre-fix.

        If `check_property_9` were an assertion about a constant, this test could not
        distinguish the two registries.
        """
        check_property_9(reg)  # real state: passes

        with pytest.raises(AssertionError) as excinfo:
            check_property_9(pre_fix_registry)

        assert SB03_CATEGORY.value in str(excinfo.value)
        assert "SB-03" in str(excinfo.value)

    def test_requirement_4_3_check_fails_on_the_pre_fix_registry(
        self, pre_fix_registry, reg
    ):
        check_requirement_4_3(reg)  # real state: passes

        with pytest.raises(AssertionError) as excinfo:
            check_requirement_4_3(pre_fix_registry)

        assert "holds 0 descriptors" in str(excinfo.value)

    def test_the_served_payload_check_fails_on_the_pre_fix_registry(
        self, pre_fix_registry
    ):
        """The blank-panel check is load-bearing too, not incidental."""
        blank = [
            name
            for name, held in served_category_counts(pre_fix_registry).items()
            if held == 0
        ]
        assert blank == [SB03_CATEGORY.value]

    def test_assembling_the_pre_fix_state_fails_naming_feature_engineering(
        self, pre_fix_sources
    ):
        """Requirement 4.9 - with the guard in place, SB-03 cannot start up at all."""
        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(pre_fix_sources)

        assert excinfo.value.categories == (SB03_CATEGORY,)
        assert SB03_CATEGORY.value in str(excinfo.value)
        assert excinfo.value.code == "REGISTRY_CATEGORY_EMPTY"

    def test_a_fourteen_feature_registry_passes_4_2_but_still_fails_4_3(self, sources):
        """Requirement 4.3 is strictly stronger than 4.2, and both are enforced.

        A single surviving feature block satisfies the non-empty guard, so the count floor
        is what actually stands between the platform and a near-empty feature palette. The
        registry assembles - as it should, 4.9 is about zero - and the 4.3 check is the one
        that catches the regression.
        """
        thinned = dataclasses.replace(
            sources, features=sources.features[: SB03_MINIMUM_DESCRIPTORS - 1]
        )

        reg = build_registry(thinned)

        check_property_9(reg)
        assert len(reg.in_category(SB03_CATEGORY)) == SB03_MINIMUM_DESCRIPTORS - 1
        with pytest.raises(AssertionError):
            check_requirement_4_3(reg)


# ---------------------------------------------------------------------------
# 3. Property 9 over every combination of emptied sources
# ---------------------------------------------------------------------------


class TestProperty9UnderEveryEmptiedSourceCombination:
    """**Property 9: Every `BlockCategory` holds at least one descriptor.**

    Generator: every non-empty subset of the `DescriptorSources` fields, emptied against
    otherwise-real data. The guard must fire for exactly the categories that lost their
    last descriptor - naming fewer would hide a blank panel, naming more would send an
    operator after a category that is fine.

    **Validates: Requirements 4.2, 4.9**
    """

    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        emptied=st.lists(
            st.sampled_from(SOURCE_FIELDS),
            min_size=1,
            max_size=len(SOURCE_FIELDS),
            unique=True,
        )
    )
    def test_emptying_any_combination_of_sources_names_exactly_those_categories(
        self, sources, contributors, emptied
    ):
        emptied_fields = set(emptied)
        expected = {
            category
            for category, fields in contributors.items()
            if fields and fields <= emptied_fields
        }
        assert expected, f"emptying {sorted(emptied_fields)} should empty some category"

        broken = dataclasses.replace(sources, **{name: () for name in emptied_fields})

        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(broken)

        assert set(excinfo.value.categories) == expected, (
            f"emptying {sorted(emptied_fields)}: guard named "
            f"{sorted(c.value for c in excinfo.value.categories)}, expected "
            f"{sorted(c.value for c in expected)}"
        )
        message = str(excinfo.value)
        for category in expected:
            assert category.value in message

    def test_the_guard_names_categories_in_palette_order(self, sources):
        """An operator reads this message at a failed startup; keep it deterministic."""
        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(dataclasses.replace(sources, static_blocks=(), features=()))

        order = [category for category, _ in CATEGORY_ORDER]
        named = list(excinfo.value.categories)
        assert named == sorted(named, key=order.index)
        assert set(named) == {
            BlockCategory.DATA,
            BlockCategory.MATH,
            BlockCategory.LOGIC,
            SB03_CATEGORY,
        }

    def test_no_registry_object_survives_an_empty_category(self, sources):
        """The guard raises instead of returning something a caller could serve."""
        outcome = None
        try:
            outcome = build_registry(dataclasses.replace(sources, features=()))
        except EmptyCategoryError:
            pass
        assert outcome is None
