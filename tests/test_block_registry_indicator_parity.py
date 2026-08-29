# -*- coding: utf-8 -*-
"""
tests/test_block_registry_indicator_parity.py

The SB-04 guard: **registry parity between what the backend can run and what the
palette advertises.**

Spec: strategy-builder task 1.18. Design -> Correctness properties #10, and
design -> Testing strategy -> Integration ("Registry parity").

Property 10: Every backend indicator and every importable model appears in the registry
-----------------------------------------------------------------------------------------
    forall i in indicators_backend.INDICATOR_SPECS:            i in registry
    forall m in ml_models.MODEL_SPECS where library importable: m in registry
    forall m in ml_models.MODEL_SPECS where library absent:     m not in registry

Stated as one biconditional over the whole capability surface:

    advertised(b)  <->  implemented(b) and runnable-in-this-image(b)

Left-to-right is Requirement 4.7/4.8 (never advertise what we cannot run) and is held in
place by `tests/test_block_registry.py::TestRuntimeRefGuard`. **This file owns
right-to-left**: Requirements 4.4, 4.5 and 4.6 - nothing the platform can actually run is
allowed to stay hidden, and nothing whose library is absent is allowed to be offered.

Why this file exists separately from `test_block_registry.py`
------------------------------------------------------------
SB-04 was `wma`, `hma`, `catboost` and `autoencoder` being fully implemented and runnable
in the backend while being unselectable in the UI, because the palette was a
hand-maintained list that had drifted from the real runtime capability. Task 1.7 removed
the hand-maintained list; `test_block_registry.py` asserts the four named blocks came
back. That is the *symptom* pinned down.

The defect, though, was not four missing ids - it was that drift between capability and
palette was *representable at all*. So this file asserts the parity relation itself, over
the whole indicator set and the whole model set, in both directions, against the engine
modules rather than against the source set the registry happened to be assembled from:

    parity is registry-vs-engine, not registry-vs-its-own-inputs

which is what makes the guard fail when a registry is built from a truncated palette - the
literal pre-fix state. `TestGuardFailsAgainstThePreFixState` demonstrates that, both from a
hand-built pre-fix source set and as a property over randomly drifted source sets.

Measurement, not assumption
---------------------------
Library availability is measured here with a *fresh* `importlib.util.find_spec` probe, run
independently of `ml_models`' own memoised `probe_module` cache and independently of the
`ModelSpec.backend_available` flag the registry consults. So a stale or wrong availability
flag is itself a parity violation this file catches, rather than a shared assumption both
sides agree on. There are no mocks and no hardcoded list of which libraries exist: in a
slim image where `catboost` or `tensorflow` is genuinely absent, the expected sets shrink
with the environment and every assertion below still holds.
"""

import dataclasses
import importlib.util
from typing import Dict, FrozenSet, List, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from backend_app.backend import indicators_backend as indicators
from backend_app.backend import ml_models as models
from backend_app.backend.strategy_dag.registry import (
    BlockRegistry,
    DescriptorSources,
    EmptyCategoryError,
    build_registry,
    default_sources,
)
from backend_app.backend.strategy_dag.schema import BlockCategory

#: The four blocks defect SB-04 named as runnable-but-unselectable. Two indicators, two
#: models - the two halves of the defect had the same cause and get the same guard.
SB04_INDICATOR_BLOCKS: Tuple[str, ...] = ("wma", "hma")
SB04_MODEL_BLOCKS: Tuple[str, ...] = ("catboost", "autoencoder")


# ---------------------------------------------------------------------------
# Independent capability measurement
# ---------------------------------------------------------------------------


def library_present(module_name: str) -> bool:
    """Whether `module_name` resolves in this image, probed fresh.

    Deliberately *not* `ml_models.probe_module`: that result is memoised per process and is
    the same measurement the registry already trusted. An independent probe means the test
    can disagree with the flag the registry read, which is the only way a stale
    availability flag shows up as a failure instead of as a shared blind spot.

    Never raises - a broken install is an absent install, exactly as `ml_models` treats it.
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:  # noqa: BLE001 - ImportError, ValueError, third-party __init__
        return False


def model_runnable_here(spec) -> bool:
    """Whether every library a `ModelSpec` needs is present in this image."""
    return all(library_present(module_name) for module_name in spec.required_modules)


def indicator_runnable_here(spec) -> bool:
    """Whether an `IndicatorSpec`'s implementation resolves to a callable.

    Measured through `indicators_backend`'s own resolver - the module that owns the
    implementations - so this is the left-hand side of the parity implication
    ("implemented and runnable") established from the spec, independently of whether the
    registry chose to publish a descriptor for it.
    """
    for ref in filter(None, (spec.runtime_ref,)):
        try:
            if not callable(indicators.resolve_indicator_runtime_ref(ref)):
                return False
        except Exception:  # noqa: BLE001 - unresolvable is not runnable
            return False
    return True


def runnable_indicator_ids() -> FrozenSet[str]:
    """Every indicator the platform can actually run. The palette's lower bound."""
    return frozenset(
        spec.block_id for spec in indicators.INDICATOR_SPECS if indicator_runnable_here(spec)
    )


def importable_model_ids() -> FrozenSet[str]:
    """Every model block whose libraries are present here. Requirement 4.5's subject."""
    return frozenset(
        spec.block_id for spec in models.MODEL_SPECS.values() if model_runnable_here(spec)
    )


def absent_library_model_ids() -> FrozenSet[str]:
    """Every model block whose libraries are missing here. Requirement 4.6's subject."""
    return frozenset(
        spec.block_id
        for spec in models.MODEL_SPECS.values()
        if not model_runnable_here(spec)
    )


# ---------------------------------------------------------------------------
# Property 10 as a computable relation
# ---------------------------------------------------------------------------


def parity_violations(reg: BlockRegistry) -> Dict[str, List[str]]:
    """Property 10 evaluated against `reg`, as data rather than as an assertion.

    Returning the four violation classes instead of asserting lets the same relation be
    used twice: to assert emptiness against the real registry, and to assert *non*-emptiness
    against a deliberately drifted one. A guard that cannot be shown to fail is not a guard.

    The expected sets are read from the engine modules, never from the `DescriptorSources`
    the registry was assembled from. That is the whole point: a registry assembled from a
    truncated palette must be judged against the engine's real capability, which is how the
    pre-fix state gets caught.

    Postconditions
        Empty lists in every class exactly when advertised == implemented-and-runnable for
        both the INDICATOR and the ML_DL category.
    """
    advertised_indicators = {d.block_id for d in reg.in_category(BlockCategory.INDICATOR)}
    advertised_models = {d.block_id for d in reg.in_category(BlockCategory.ML_DL)}

    runnable_indicators = runnable_indicator_ids()
    importable_models = importable_model_ids()
    absent_models = absent_library_model_ids()

    return {
        # Requirement 4.4 - runnable but unselectable. The original SB-04.
        "indicators_hidden": sorted(runnable_indicators - advertised_indicators),
        # An indicator advertised that the engine does not implement at all.
        "indicators_invented": sorted(advertised_indicators - runnable_indicators),
        # Requirement 4.5 - an importable model library left out of the palette.
        "models_hidden": sorted(importable_models - advertised_models),
        # Requirement 4.6 - offered, then failing at train time because the library is gone.
        "models_advertised_without_library": sorted(advertised_models & absent_models),
    }


def assert_parity(reg: BlockRegistry) -> None:
    """Property 10 holds for `reg`, reporting every offending block id when it does not."""
    violations = {key: ids for key, ids in parity_violations(reg).items() if ids}
    assert not violations, (
        "Registry parity broken (SB-04). Implemented-and-runnable must imply advertised, "
        f"and library-absent must imply omitted: {violations}"
    )


def drift(
    sources: DescriptorSources,
    drop_indicators: FrozenSet[str] = frozenset(),
    drop_models: FrozenSet[str] = frozenset(),
) -> DescriptorSources:
    """The pre-fix state, reconstructed: a palette narrower than the real capability.

    Derived from the real sources with `dataclasses.replace`, so the drifted registry is
    assembled from production descriptors with entries withheld - the same shape as a
    hand-maintained list that fell behind the engine, and not a stand-in for one.
    """
    return dataclasses.replace(
        sources,
        indicators=tuple(
            spec for spec in sources.indicators if spec.block_id not in drop_indicators
        ),
        models=tuple(spec for spec in sources.models if spec.block_id not in drop_models),
    )


# ---------------------------------------------------------------------------
# Fixtures - assembly is ~60 ms; the real sources and one registry are shared
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sources() -> DescriptorSources:
    """The real descriptor sources, read from the modules that own the implementations."""
    return default_sources()


@pytest.fixture(scope="module")
def reg(sources) -> BlockRegistry:
    return build_registry(sources)


@pytest.fixture(scope="module")
def served(reg) -> Dict[str, object]:
    """The wire payload the palette actually renders from."""
    return reg.to_dict()


# ---------------------------------------------------------------------------
# 1. Property 10 over the whole capability surface - Requirements 4.4, 4.5, 4.6
# ---------------------------------------------------------------------------


class TestProperty10RegistryParity:
    """**Validates: Requirements 4.4, 4.5, 4.6**"""

    def test_parity_holds_for_the_real_registry(self, reg):
        """Property 10, both directions, over every indicator and every model.

        The single assertion this whole file is built around.
        """
        assert_parity(reg)

    def test_every_runnable_indicator_is_advertised(self, reg):
        """Requirement 4.4, established from the specs rather than from the descriptors.

        Runnability is measured through `indicators_backend.resolve_indicator_runtime_ref`,
        so the left-hand side of the implication ("implemented and runnable") is observed
        without consulting the registry at all.
        """
        runnable = runnable_indicator_ids()
        assert runnable, "no indicator resolved to a callable - the probe itself is broken"
        assert len(runnable) == len(indicators.INDICATOR_SPECS), (
            "some INDICATOR_SPECS entry does not resolve: "
            f"{sorted({s.block_id for s in indicators.INDICATOR_SPECS} - runnable)}"
        )
        hidden = sorted(runnable - set(reg.block_ids()))
        assert not hidden, f"runnable but unselectable indicators (SB-04): {hidden}"

    def test_every_importable_model_is_advertised(self, reg):
        """Requirement 4.5, measured with a fresh probe over all eight model blocks."""
        advertised = {d.block_id for d in reg.in_category(BlockCategory.ML_DL)}
        assert importable_model_ids() <= advertised, (
            "importable model libraries left out of the palette: "
            f"{sorted(importable_model_ids() - advertised)}"
        )

    def test_no_model_is_advertised_without_its_library(self, reg):
        """Requirement 4.6 - omitted, rather than offered and then failing at train time."""
        advertised = {d.block_id for d in reg.in_category(BlockCategory.ML_DL)}
        offered_anyway = sorted(advertised & absent_library_model_ids())
        assert not offered_anyway, (
            f"advertised despite an absent library: {offered_anyway}"
        )

    @pytest.mark.parametrize(
        "block_id", sorted(spec.block_id for spec in models.MODEL_SPECS.values())
    )
    def test_each_model_is_advertised_iff_its_library_is_importable(self, reg, block_id):
        """The biconditional, per model block, so the failure names the offender.

        Stated as *iff* on purpose: in a slim image without `catboost` or `tensorflow` the
        correct behaviour is omission, and this assertion tracks the environment instead of
        demanding a library be present.
        """
        spec = models.MODEL_SPECS[block_id]
        expected = model_runnable_here(spec)
        actual = reg.get(block_id) is not None
        assert actual is expected, (
            f"{block_id}: libraries {list(spec.required_modules)} importable={expected} "
            f"but advertised={actual}"
        )

    def test_the_availability_flag_the_registry_reads_matches_an_independent_probe(self):
        """A stale `backend_available` would make every other parity check vacuous.

        The registry gates ML_DL on `ModelSpec.backend_available`. If that flag were wrong,
        registry and flag would agree with each other and disagree with reality, so it is
        re-measured here against a fresh `find_spec` probe rather than trusted.
        """
        disagreements = {
            spec.block_id: (spec.backend_available, model_runnable_here(spec))
            for spec in models.MODEL_SPECS.values()
            if bool(spec.backend_available) is not model_runnable_here(spec)
        }
        assert not disagreements, (
            "ModelSpec.backend_available disagrees with a fresh import probe "
            f"(block_id -> (flag, probed)): {disagreements}"
        )


# ---------------------------------------------------------------------------
# 2. The four blocks SB-04 named - the task's explicit assertions
# ---------------------------------------------------------------------------


class TestTheFourSB04Blocks:
    """The named symptoms, kept as a regression floor beneath the general property.

    **Validates: Requirements 4.4, 4.5**
    """

    @pytest.mark.parametrize("block_id", SB04_INDICATOR_BLOCKS)
    def test_wma_and_hma_are_present_and_runnable(self, reg, block_id):
        spec = indicators.INDICATOR_SPECS_BY_ID[block_id]
        assert indicator_runnable_here(spec), f"{block_id} does not resolve to a callable"

        descriptor = reg.get(block_id)
        assert descriptor is not None, f"{block_id} is runnable but unselectable (SB-04)"
        assert descriptor.category is BlockCategory.INDICATOR
        assert callable(descriptor.resolve_runtime())

    @pytest.mark.parametrize("block_id", SB04_MODEL_BLOCKS)
    def test_catboost_and_autoencoder_are_present_iff_importable(self, reg, block_id):
        spec = models.MODEL_SPECS[block_id]
        expected = model_runnable_here(spec)
        descriptor = reg.get(block_id)
        assert (descriptor is not None) is expected, (
            f"{block_id}: importable={expected} but advertised={descriptor is not None}"
        )
        if descriptor is not None:
            assert descriptor.category is BlockCategory.ML_DL
            assert callable(descriptor.resolve_runtime())

    @pytest.mark.parametrize(
        "block_id", SB04_INDICATOR_BLOCKS + SB04_MODEL_BLOCKS
    )
    def test_the_four_blocks_are_selectable_in_the_served_payload(
        self, reg, served, block_id
    ):
        """The palette renders from the wire response, so parity must survive `to_dict()`.

        A descriptor present in-process but dropped during serialisation would be
        unselectable in exactly the way SB-04 was.
        """
        expected = reg.get(block_id) is not None
        block_ids = {block["block_id"] for block in served["blocks"]}
        assert (block_id in block_ids) is expected


# ---------------------------------------------------------------------------
# 3. Parity survives serialisation - the palette reads the payload, not the object
# ---------------------------------------------------------------------------


class TestServedPayloadParity:
    """**Validates: Requirements 4.4, 4.5, 4.6**"""

    def test_served_indicator_blocks_are_exactly_the_runnable_indicators(self, served):
        advertised = {
            block["block_id"]
            for block in served["blocks"]
            if block["category"] == BlockCategory.INDICATOR.value
        }
        assert advertised == runnable_indicator_ids()

    def test_served_model_blocks_are_exactly_the_importable_models(self, served):
        advertised = {
            block["block_id"]
            for block in served["blocks"]
            if block["category"] == BlockCategory.ML_DL.value
        }
        assert advertised == importable_model_ids()
        assert not advertised & absent_library_model_ids()


# ---------------------------------------------------------------------------
# 4. The legacy exported lists the design names, and the old endpoint still reads
# ---------------------------------------------------------------------------


class TestLegacyExportedListsAreSubsetsOfTheRegistry:
    """Design -> Testing strategy -> Integration: `AVAILABLE_INDICATORS` subset of registry,
    `AVAILABLE_ML_MODELS` union `AVAILABLE_DL_MODELS` subset of registry.

    These three module-level names are what the pre-fix palette path
    (`routers/strategies.py::get_block_registry`) still reads, and they are the surface a
    hand-maintained list would reappear on. Asserting containment against them checks the
    *other* end of the drift: not just that the registry is complete, but that no
    already-published name has fallen out of it.

    **Validates: Requirements 4.4, 4.5**
    """

    def test_available_indicators_is_a_subset_of_the_registry(self, reg):
        block_ids = set(reg.block_ids())
        missing = sorted(set(indicators.AVAILABLE_INDICATORS) - block_ids)
        assert not missing, f"published indicators absent from the registry: {missing}"

    def test_available_models_are_a_subset_of_the_registry(self, reg):
        block_ids = set(reg.block_ids())
        published = set(models.AVAILABLE_ML_MODELS) | set(models.AVAILABLE_DL_MODELS)
        missing = sorted(published - block_ids)
        assert not missing, f"published models absent from the registry: {missing}"

    def test_the_legacy_lists_are_themselves_derived_not_hand_maintained(self):
        """The mechanism, not just the outcome: the lists must equal their descriptor sets.

        SB-04's cause was a list maintained beside the implementations. If these ever stop
        being derived, parity would have to be re-established by hand - so the derivation
        is asserted directly.
        """
        assert set(indicators.AVAILABLE_INDICATORS) == {
            spec.block_id for spec in indicators.INDICATOR_SPECS
        }
        assert set(models.AVAILABLE_ML_MODELS) | set(models.AVAILABLE_DL_MODELS) == {
            spec.block_id for spec in models.available_model_specs()
        }


# ---------------------------------------------------------------------------
# 5. The guard fails against the pre-fix state - task 1.18's explicit requirement
# ---------------------------------------------------------------------------


class TestGuardFailsAgainstThePreFixState:
    """A guard that cannot be made to fail proves nothing.

    The pre-fix state was a palette narrower than the engine. It is reconstructed here by
    assembling a registry from the real sources with entries withheld, then checking that
    :func:`parity_violations` reports exactly the withheld ids.

    **Validates: Requirements 4.4, 4.5, 4.6**
    """

    def test_the_original_sb04_palette_is_rejected(self, sources):
        """Withhold exactly the four blocks SB-04 named, and watch the guard fire."""
        importable = importable_model_ids()
        drop_models = frozenset(SB04_MODEL_BLOCKS) & importable
        pre_fix = drift(
            sources,
            drop_indicators=frozenset(SB04_INDICATOR_BLOCKS),
            drop_models=drop_models,
        )

        drifted = build_registry(pre_fix)
        violations = parity_violations(drifted)

        assert violations["indicators_hidden"] == sorted(SB04_INDICATOR_BLOCKS)
        assert violations["models_hidden"] == sorted(drop_models)
        assert not violations["indicators_invented"]
        assert not violations["models_advertised_without_library"]

        with pytest.raises(AssertionError) as excinfo:
            assert_parity(drifted)
        message = str(excinfo.value)
        for block_id in tuple(SB04_INDICATOR_BLOCKS) + tuple(drop_models):
            assert block_id in message, f"the failure does not name {block_id}"

    @pytest.mark.parametrize("block_id", SB04_INDICATOR_BLOCKS)
    def test_withholding_a_single_indicator_is_caught(self, sources, block_id):
        drifted = build_registry(drift(sources, drop_indicators=frozenset({block_id})))
        assert parity_violations(drifted)["indicators_hidden"] == [block_id]

    @pytest.mark.parametrize(
        "block_id", sorted(spec.block_id for spec in models.MODEL_SPECS.values())
    )
    def test_a_model_whose_library_is_unavailable_is_absent(self, sources, block_id):
        """Requirement 4.6, exercised per model block against the real spec set.

        `backend_available=False` is what `ml_models` sets when the import probe fails, so
        flipping it on one spec is a faithful stand-in for that library being uninstalled -
        and the assertion is absence from the object, the id list *and* the served payload,
        because the palette reads the last of those.
        """
        gated = tuple(
            dataclasses.replace(spec, backend_available=False)
            if spec.block_id == block_id
            else spec
            for spec in sources.models
        )
        reg = build_registry(dataclasses.replace(sources, models=gated))

        assert reg.get(block_id) is None
        assert block_id not in reg.block_ids()
        assert block_id not in {
            block["block_id"] for block in reg.to_dict()["blocks"]
        }
        assert block_id not in {
            d.block_id for d in reg.in_category(BlockCategory.ML_DL)
        }

    def test_a_palette_missing_every_model_fails_assembly_rather_than_serving_empty(
        self, sources
    ):
        """The degenerate drift: a model-free palette must stop startup, not render blank."""
        gated = tuple(
            dataclasses.replace(spec, backend_available=False) for spec in sources.models
        )
        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(dataclasses.replace(sources, models=gated))
        assert BlockCategory.ML_DL in excinfo.value.categories

    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=st.data())
    def test_any_drift_between_capability_and_palette_is_caught(self, sources, data):
        """Property 10's contrapositive, over randomly drifted palettes.

        Generator: withhold a random non-empty selection of runnable indicators and/or
        importable models from the real source set, always leaving each category non-empty
        so the drift is a *parity* failure rather than an empty-category failure (the
        latter is Property 9's job, guarded in `test_block_registry_categories.py`).

        The assertion is exact, not merely "something failed": the violation report must
        name precisely the withheld ids and nothing else. A guard that over-reports is as
        useless as one that under-reports.

        **Validates: Requirements 4.4, 4.5**
        """
        indicator_ids = sorted(runnable_indicator_ids())
        model_ids = sorted(importable_model_ids())

        # Leave at least one descriptor in each category; emptying one is a different guard.
        max_indicators = max(0, min(5, len(indicator_ids) - 1))
        max_models = max(0, min(3, len(model_ids) - 1))

        drop_indicators = frozenset(
            data.draw(
                st.lists(
                    st.sampled_from(indicator_ids),
                    min_size=0,
                    max_size=max_indicators,
                    unique=True,
                )
                if max_indicators
                else st.just([]),
                label="drop_indicators",
            )
        )
        drop_models = frozenset(
            data.draw(
                st.lists(
                    st.sampled_from(model_ids),
                    min_size=0,
                    max_size=max_models,
                    unique=True,
                )
                if max_models
                else st.just([]),
                label="drop_models",
            )
        )
        assume(drop_indicators or drop_models)

        drifted = build_registry(
            drift(sources, drop_indicators=drop_indicators, drop_models=drop_models)
        )
        violations = parity_violations(drifted)

        assert violations["indicators_hidden"] == sorted(drop_indicators)
        assert violations["models_hidden"] == sorted(drop_models)
        assert not violations["indicators_invented"]
        assert not violations["models_advertised_without_library"]

    @settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=st.data())
    def test_withholding_nothing_leaves_parity_intact(self, sources, data):
        """The other half of the contrapositive: the guard does not cry wolf.

        Reordering the descriptor sources - which a dict or glob iteration order change
        could do at any time - must not register as drift.

        **Validates: Requirements 4.4, 4.5**
        """
        shuffled_indicators = data.draw(
            st.permutations(list(sources.indicators)), label="indicators"
        )
        shuffled_models = data.draw(st.permutations(list(sources.models)), label="models")

        reordered = dataclasses.replace(
            sources,
            indicators=tuple(shuffled_indicators),
            models=tuple(shuffled_models),
        )
        assert_parity(build_registry(reordered))


# ---------------------------------------------------------------------------
# 6. The measurement itself is honest
# ---------------------------------------------------------------------------


class TestTheProbeIsReal:
    """If the availability probe were hardcoded, every assertion above would be theatre."""

    def test_the_probe_disagrees_with_a_name_that_cannot_exist(self):
        assert library_present("this_library_does_not_exist_anywhere_12345") is False

    def test_the_probe_agrees_with_a_module_that_certainly_exists(self):
        assert library_present("json") is True

    def test_the_probe_is_independent_of_the_ml_models_cache(self):
        """Same answer as `ml_models.probe_module`, reached without its memo table."""
        module_names: Sequence[str] = sorted(
            {name for spec in models.MODEL_SPECS.values() for name in spec.required_modules}
        )
        assert module_names, "no model block declares a required module"
        for module_name in module_names:
            assert library_present(module_name) is models.probe_module(module_name), (
                f"independent probe and ml_models.probe_module disagree on {module_name}"
            )

    def test_every_model_spec_declares_a_library_to_probe(self):
        """A spec with no `required_modules` could never be gated - it would be unfalsifiable."""
        ungated = [
            spec.block_id
            for spec in models.MODEL_SPECS.values()
            if not spec.required_modules
        ]
        assert not ungated, f"model blocks with nothing to probe: {ungated}"
