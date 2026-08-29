# -*- coding: utf-8 -*-
"""
tests/test_block_registry.py

Unit and property tests for the backend-authoritative block registry,
`backend_app/backend/strategy_dag/registry.py`.

Spec: strategy-builder task 1.7 (`design.md` -> Block type registry
(backend-authoritative)). Requirements 4.1, 4.2, 4.7, 4.8, 4.9, 4.10, 5.1, 5.9, 5.10, 6.9.

The two structural guards this file exists to hold in place:

* **Requirement 4.9 / SB-03.** Every one of the seven `BlockCategory` values holds at
  least one descriptor, and emptying a source fails assembly *naming the category*. An
  empty FEATURE_ENGINEERING palette is a startup failure, never a blank panel.
* **Requirements 4.7 / 4.8 / SB-04.** Every advertised `runtime_ref` resolves to a real
  callable, and a descriptor that cannot be run fails assembly naming the offender. So
  `wma`, `hma`, `catboost` and `autoencoder` are advertised exactly when the platform can
  actually run them - never advertised-but-broken, never runnable-but-hidden.

Nothing is faked: the descriptor sources are the real `INDICATOR_SPECS`, `FEATURE_SPECS`,
`MODEL_SPECS`, `DATA_SPECS`, `MATH_SPECS`, `LOGIC_SPECS` and the real ACTION factory over
`exchange_executor.OrderType`. Where a test needs a broken or emptied source it derives it
from the real one with `dataclasses.replace`, so the guard is exercised against production
data rather than a stand-in.
"""

import dataclasses
import importlib.util
import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend import feature_engineering as features
from backend_app.backend import indicators_backend as indicators
from backend_app.backend import ml_models as models
from backend_app.backend.strategy_dag import block_specs, registry
from backend_app.backend.strategy_dag.block_specs import (
    ExecutionSemantics,
    ParamType,
)
from backend_app.backend.strategy_dag.registry import (
    CATEGORY_ORDER,
    COMPATIBILITY_MATRIX,
    BlockDescriptor,
    BlockRegistry,
    DescriptorSources,
    DuplicateBlockError,
    EmptyCategoryError,
    UnrunnableBlockError,
    build_registry,
    compatibility_matrix,
    compatible,
    default_sources,
    port_type_vocabulary,
)
from backend_app.backend.strategy_dag.schema import BlockCategory, Port, PortType

# The two model blocks defect SB-04 named as runnable-but-unselectable.
SB04_MODEL_BLOCKS = ("catboost", "autoencoder")

# The two indicators defect SB-04 named as runnable-but-unselectable.
SB04_INDICATOR_BLOCKS = ("wma", "hma")


# ---------------------------------------------------------------------------
# Fixtures - assembly is ~150 ms, so the real sources and one registry are shared
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sources() -> DescriptorSources:
    """The real descriptor sources, read from the modules that own them."""
    return default_sources()


@pytest.fixture(scope="module")
def reg(sources) -> BlockRegistry:
    return build_registry(sources)


def library_importable(spec) -> bool:
    """Whether every library a `ModelSpec` needs is present, measured not assumed."""
    for module_name in spec.required_modules:
        try:
            if importlib.util.find_spec(module_name) is None:
                return False
        except Exception:  # noqa: BLE001 - a broken install is an absent install
            return False
    return True


# ---------------------------------------------------------------------------
# 1. Assembly succeeds and covers all seven categories - Requirements 4.1, 4.2
# ---------------------------------------------------------------------------


class TestAssembly:
    def test_build_registry_succeeds(self, reg):
        assert isinstance(reg, BlockRegistry)
        assert len(reg) > 0
        assert len(reg.blocks()) == len(reg)

    def test_every_category_holds_at_least_one_descriptor(self, reg):
        """Requirement 4.2 - and the observable half of the SB-03 guard."""
        for category in BlockCategory:
            assert reg.in_category(category), (
                f"{category.value} holds zero descriptors; its palette section would "
                "render empty (SB-03)"
            )

    def test_counts_match_the_owning_descriptor_sources(self, reg):
        """Consolidation, not authorship: each count is the owner's count, not a literal."""
        counts = reg.counts_by_category()
        assert counts["DATA"] == len(block_specs.DATA_SPECS)
        assert counts["MATH"] == len(block_specs.MATH_SPECS)
        assert counts["LOGIC"] == len(block_specs.LOGIC_SPECS)
        assert counts["INDICATOR"] == len(indicators.INDICATOR_SPECS)
        assert counts["FEATURE_ENGINEERING"] == len(features.FEATURE_SPECS)
        assert counts["ACTION"] == len(block_specs.action_specs())
        assert counts["ML_DL"] == len(models.available_model_specs())

    def test_at_least_fifteen_feature_blocks(self, reg):
        """Requirement 4.3 - the category SB-03 left empty."""
        assert len(reg.in_category(BlockCategory.FEATURE_ENGINEERING)) >= 15

    def test_every_implemented_indicator_is_advertised(self, reg):
        """Requirement 4.4 - the palette is the indicator library, not a subset of it."""
        advertised = {d.block_id for d in reg.in_category(BlockCategory.INDICATOR)}
        assert advertised == {spec.block_id for spec in indicators.INDICATOR_SPECS}

    def test_action_blocks_come_from_the_real_order_types(self, reg):
        """Requirement 4.10 - generated from what the execution layer supports."""
        advertised = {d.block_id for d in reg.in_category(BlockCategory.ACTION)}
        assert advertised == {spec.block_id for spec in block_specs.action_specs()}
        for descriptor in reg.in_category(BlockCategory.ACTION):
            assert descriptor.execution_semantics is ExecutionSemantics.TERMINAL
            assert descriptor.outputs == ()
            assert descriptor.allowed_successor_categories == frozenset()
            assert descriptor.metadata["order_type"]

    def test_blocks_are_served_in_palette_order(self, reg):
        """Requirement 4.1 - one response, ordered by the published category order."""
        order = {category: index for index, (category, _) in enumerate(CATEGORY_ORDER)}
        positions = [order[d.category] for d in reg.blocks()]
        assert positions == sorted(positions)

    def test_duplicate_block_ids_fail_assembly_naming_the_collision(self, sources):
        duplicate = dataclasses.replace(sources.indicators[0], block_id="add")  # MATH id
        with pytest.raises(DuplicateBlockError) as excinfo:
            build_registry(
                dataclasses.replace(
                    sources, indicators=(duplicate,) + sources.indicators[1:]
                )
            )
        assert "add" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. The SB-03 guard: an empty category fails startup naming the category
#    Requirement 4.9
# ---------------------------------------------------------------------------


class TestEmptyCategoryGuard:
    @pytest.mark.parametrize(
        "field_name,category",
        [
            ("features", BlockCategory.FEATURE_ENGINEERING),
            ("indicators", BlockCategory.INDICATOR),
            ("models", BlockCategory.ML_DL),
            ("actions", BlockCategory.ACTION),
        ],
    )
    def test_emptying_a_source_fails_and_names_the_category(
        self, sources, field_name, category
    ):
        emptied = dataclasses.replace(sources, **{field_name: ()})

        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(emptied)

        assert category in excinfo.value.categories
        assert category.value in str(excinfo.value)

    def test_emptying_the_static_family_names_all_three_empty_categories(self, sources):
        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(dataclasses.replace(sources, static_blocks=()))

        named = set(excinfo.value.categories)
        assert {BlockCategory.DATA, BlockCategory.MATH, BlockCategory.LOGIC} <= named
        for category in (BlockCategory.DATA, BlockCategory.MATH, BlockCategory.LOGIC):
            assert category.value in str(excinfo.value)

    def test_the_guard_is_an_exception_not_a_warning(self, sources, recwarn):
        """An empty palette section must stop startup, not degrade quietly."""
        with pytest.raises(EmptyCategoryError):
            build_registry(dataclasses.replace(sources, features=()))
        # No registry object came back that a caller could serve anyway.
        assert not [w for w in recwarn if "categor" in str(w.message).lower()]


# ---------------------------------------------------------------------------
# 3. The runtime_ref guard - Requirements 4.7, 4.8
# ---------------------------------------------------------------------------


class TestRuntimeRefGuard:
    def test_every_advertised_runtime_ref_resolves_to_a_callable(self, reg):
        for descriptor in reg.blocks():
            for ref in descriptor.runtime_refs():
                assert callable(registry._resolve_runtime(descriptor.source_module, ref)), (
                    f"{descriptor.block_id} advertises runtime_ref {ref!r} that is not "
                    "callable"
                )

    def test_assert_runnable_passes_for_every_advertised_block(self, reg):
        for descriptor in reg.blocks():
            descriptor.assert_runnable()

    @pytest.mark.parametrize(
        "field_name,index",
        [("indicators", 0), ("features", 0), ("models", 0), ("static_blocks", 0)],
    )
    def test_an_unresolvable_runtime_ref_fails_startup_naming_the_offender(
        self, sources, field_name, index
    ):
        family = getattr(sources, field_name)
        victim = family[index]
        broken = dataclasses.replace(victim, runtime_ref="nowhere.does_not_exist")
        mutated = family[:index] + (broken,) + family[index + 1 :]

        with pytest.raises(UnrunnableBlockError) as excinfo:
            build_registry(dataclasses.replace(sources, **{field_name: mutated}))

        assert excinfo.value.block_id == victim.block_id
        assert victim.block_id in str(excinfo.value)
        assert "nowhere.does_not_exist" in str(excinfo.value)

    def test_a_broken_streaming_runtime_ref_also_fails_startup(self, sources):
        feed = next(
            spec for spec in sources.static_blocks if spec.streaming_runtime_ref
        )
        broken = dataclasses.replace(
            feed, streaming_runtime_ref="DataEngine.stream_nothing_at_all"
        )
        mutated = tuple(broken if s is feed else s for s in sources.static_blocks)

        with pytest.raises(UnrunnableBlockError) as excinfo:
            build_registry(dataclasses.replace(sources, static_blocks=mutated))

        assert excinfo.value.block_id == feed.block_id
        assert "stream_nothing_at_all" in str(excinfo.value)

    def test_the_guard_is_an_exception_not_a_warning(self, sources):
        broken = dataclasses.replace(
            sources.indicators[0], runtime_ref="indicators_backend.not_a_function"
        )
        with pytest.raises(UnrunnableBlockError):
            build_registry(
                dataclasses.replace(sources, indicators=(broken,) + sources.indicators[1:])
            )


# ---------------------------------------------------------------------------
# 4. SB-04: runnable means selectable - Requirements 4.4, 4.5, 4.6
# ---------------------------------------------------------------------------


class TestSB04ParityBetweenRunnableAndAdvertised:
    @pytest.mark.parametrize("block_id", SB04_INDICATOR_BLOCKS)
    def test_wma_and_hma_are_advertised(self, reg, block_id):
        descriptor = reg.get(block_id)
        assert descriptor is not None, f"{block_id} is runnable but unselectable (SB-04)"
        assert descriptor.category is BlockCategory.INDICATOR
        assert callable(descriptor.resolve_runtime())

    @pytest.mark.parametrize("block_id", SB04_MODEL_BLOCKS)
    def test_catboost_and_autoencoder_are_advertised_iff_importable(self, reg, block_id):
        spec = models.MODEL_SPECS[block_id]
        expected = library_importable(spec)
        assert (reg.get(block_id) is not None) is expected, (
            f"{block_id}: library importable={expected} but advertised="
            f"{reg.get(block_id) is not None}"
        )

    def test_every_available_model_is_advertised(self, reg):
        """Requirement 4.5 - nothing runnable stays hidden."""
        advertised = {d.block_id for d in reg.in_category(BlockCategory.ML_DL)}
        assert advertised == {spec.block_id for spec in models.available_model_specs()}

    def test_an_unavailable_model_is_omitted_not_advertised(self, sources):
        """Requirement 4.6 - omitted, not offered and then failing at train time."""
        victim = sources.models[0]
        gated = tuple(
            dataclasses.replace(spec, backend_available=False) if spec is victim else spec
            for spec in sources.models
        )

        reg = build_registry(dataclasses.replace(sources, models=gated))

        assert reg.get(victim.block_id) is None
        assert victim.block_id not in reg.block_ids()
        assert victim.block_id not in {
            block["block_id"] for block in reg.to_dict()["blocks"]
        }

    def test_all_models_unavailable_is_an_empty_category_failure(self, sources):
        """A model-free image must fail loudly, not serve an empty ML section."""
        gated = tuple(
            dataclasses.replace(spec, backend_available=False) for spec in sources.models
        )
        with pytest.raises(EmptyCategoryError) as excinfo:
            build_registry(dataclasses.replace(sources, models=gated))
        assert BlockCategory.ML_DL in excinfo.value.categories


# ---------------------------------------------------------------------------
# 5. Lookup surface - the seam schema._discover_default_resolver probes
# ---------------------------------------------------------------------------


class TestLookup:
    def test_get_returns_none_for_an_unknown_block_id(self, reg):
        assert reg.get("no_such_block_at_all") is None
        assert "no_such_block_at_all" not in reg

    @pytest.mark.parametrize("bogus", [None, 42, object(), ["rsi"]])
    def test_get_returns_none_for_a_non_string_id(self, reg, bogus):
        assert reg.get(bogus) is None

    def test_module_level_get_returns_none_for_an_unknown_block_id(self):
        """The structural contract `schema._discover_default_resolver` relies on."""
        assert registry.get("no_such_block_at_all") is None
        assert registry.get("rsi") is not None

    def test_the_migration_seam_discovers_this_module(self):
        from backend_app.backend.strategy_dag import schema

        schema.reset_default_resolver()
        try:
            lookup = schema._discover_default_resolver()
            assert lookup is not None
            assert lookup("rsi") is not None
            assert lookup("no_such_block_at_all") is None
        finally:
            schema.reset_default_resolver()

    def test_getitem_raises_keyerror_for_an_unknown_block_id(self, reg):
        with pytest.raises(KeyError):
            reg["no_such_block_at_all"]

    def test_lookup_is_case_insensitive_for_legacy_operator_tokens(self, reg):
        assert reg.get("AND") is reg.get("and")

    def test_port_lookup_helpers(self, reg):
        assert reg.input_port("rsi", "series").type is PortType.PRICE_SERIES
        assert reg.output_port("rsi", "value").type is PortType.SCALAR_SERIES
        assert reg.input_port("rsi", "no_such_port") is None
        assert reg.output_port("rsi", "no_such_port") is None
        assert reg.input_port("no_such_block", "series") is None
        assert registry.input_port("rsi", "series") is not None
        assert registry.output_port("rsi", "value") is not None

    def test_multi_output_indicators_publish_one_port_per_output(self, reg):
        """Requirement 5.10."""
        macd = reg["macd"]
        assert [port.name for port in macd.outputs] == [
            port.name for port in indicators.INDICATOR_SPECS_BY_ID["macd"].outputs
        ]
        assert len(macd.outputs) > 1


# ---------------------------------------------------------------------------
# 6. registry_version - deterministic, and sensitive to any descriptor change
# ---------------------------------------------------------------------------


class TestRegistryVersion:
    def test_two_builds_of_the_same_sources_agree(self, sources):
        assert build_registry(sources).registry_version == (
            build_registry(sources).registry_version
        )

    def test_version_is_a_prefixed_hex_digest(self, reg):
        version = reg.registry_version
        assert version.startswith("r_")
        assert len(version) == 10
        int(version[2:], 16)

    def test_version_changes_when_a_descriptor_changes(self, sources):
        before = build_registry(sources).registry_version
        bumped = dataclasses.replace(sources.indicators[0], version="9.9.9")
        after = build_registry(
            dataclasses.replace(sources, indicators=(bumped,) + sources.indicators[1:])
        ).registry_version
        assert after != before

    def test_version_changes_when_a_param_range_changes(self, sources):
        """Requirement 5.9: a changed indicator range is a changed registry."""
        victim = sources.indicators[0]
        param = victim.params[0]
        widened = dataclasses.replace(
            victim,
            params=(dataclasses.replace(param, max=(param.max or 10) + 1),)
            + victim.params[1:],
        )
        before = build_registry(sources).registry_version
        after = build_registry(
            dataclasses.replace(sources, indicators=(widened,) + sources.indicators[1:])
        ).registry_version
        assert after != before

    def test_version_changes_when_a_block_disappears(self, sources):
        before = build_registry(sources).registry_version
        after = build_registry(
            dataclasses.replace(sources, indicators=sources.indicators[1:])
        ).registry_version
        assert after != before

    def test_module_level_registry_version_matches_the_memoised_registry(self):
        assert registry.registry_version() == registry.get_registry().registry_version

    @settings(
        max_examples=15,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(seed=st.integers(min_value=0, max_value=10_000))
    def test_version_is_independent_of_source_ordering(self, sources, seed):
        """Deterministic across processes means independent of mapping/list order."""
        rng = random.Random(seed)

        def shuffled(items):
            out = list(items)
            rng.shuffle(out)
            return tuple(out)

        permuted = dataclasses.replace(
            sources,
            indicators=shuffled(sources.indicators),
            features=shuffled(sources.features),
            models=shuffled(sources.models),
            static_blocks=shuffled(sources.static_blocks),
            actions=shuffled(sources.actions),
        )
        assert build_registry(permuted).registry_version == (
            build_registry(sources).registry_version
        )


# ---------------------------------------------------------------------------
# 7. Published vocabulary and compatibility matrix - Requirement 6.9
# ---------------------------------------------------------------------------


class TestPublishedVocabulary:
    def test_port_types_cover_every_canonical_port_type(self, reg):
        assert reg.port_types == [port_type.value for port_type in PortType]
        assert port_type_vocabulary() == reg.port_types

    def test_compatibility_matrix_is_total_over_port_type(self, reg):
        matrix = reg.compatibility_matrix
        assert set(matrix) == {port_type.value for port_type in PortType}
        for source, targets in matrix.items():
            assert set(targets) <= {port_type.value for port_type in PortType}, source

    def test_compatibility_matrix_matches_the_design_table(self):
        assert compatibility_matrix() == {
            "OHLCV_FRAME": ["OHLCV_FRAME", "PRICE_SERIES"],
            "PRICE_SERIES": ["PRICE_SERIES", "SCALAR_SERIES"],
            "SCALAR_SERIES": ["SCALAR_SERIES"],
            "BOOLEAN_SERIES": ["BOOLEAN_SERIES", "SIGNAL"],
            "FEATURE_MATRIX": ["FEATURE_MATRIX"],
            "PREDICTION": ["PREDICTION", "SCALAR_SERIES"],
            "SIGNAL": ["SIGNAL", "TRADE_INTENT"],
            "TRADE_INTENT": [],
            "SCALAR": ["SCALAR", "SCALAR_SERIES"],
        }

    def test_categories_are_published_with_display_names_and_order(self, reg):
        published = reg.categories()
        assert [entry["id"] for entry in published] == [
            category.value for category, _ in CATEGORY_ORDER
        ]
        assert [entry["order"] for entry in published] == list(
            range(1, len(CATEGORY_ORDER) + 1)
        )
        for entry in published:
            assert entry["display_name"]

    def test_registry_response_shape(self, reg):
        payload = reg.to_dict()
        assert set(payload) >= {
            "registry_version",
            "port_types",
            "categories",
            "blocks",
            "compatibility_matrix",
        }
        assert payload["registry_version"] == reg.registry_version
        assert len(payload["blocks"]) == len(reg)

    @given(
        source=st.sampled_from(list(PortType)), target=st.sampled_from(list(PortType))
    )
    def test_compatible_agrees_with_the_published_matrix_for_every_pair(
        self, source, target
    ):
        """One rule, two enforcement points: the served matrix *is* the backend rule."""
        served = compatibility_matrix()[source.value]
        assert compatible(source, target) is (target.value in served)
        assert compatible(source.value, target.value) is (target.value in served)

    def test_compatible_rejects_a_value_outside_the_vocabulary(self):
        assert compatible("NOT_A_PORT_TYPE", "SCALAR_SERIES") is False
        assert compatible("SCALAR_SERIES", "NOT_A_PORT_TYPE") is False

    def test_terminal_intent_is_accepted_by_no_block_input(self, reg):
        """TRADE_INTENT leaves the graph; no block may consume it."""
        assert compatibility_matrix()["TRADE_INTENT"] == []
        for descriptor in reg.blocks():
            for port in descriptor.inputs:
                assert port.type is not PortType.TRADE_INTENT, descriptor.block_id


# ---------------------------------------------------------------------------
# 8. One ParamSpec, published once, read by both the form and the validator
#    Requirements 5.1, 5.9
# ---------------------------------------------------------------------------


class TestParameterContract:
    def test_registry_reuses_the_block_specs_param_spec(self):
        """There is exactly one parameter contract, not a competing second one."""
        assert registry.ParamSpec is block_specs.ParamSpec

    def test_every_published_param_is_the_canonical_param_spec(self, reg):
        for descriptor in reg.blocks():
            for param in descriptor.params:
                assert isinstance(param, block_specs.ParamSpec), descriptor.block_id
                assert isinstance(param.type, ParamType), descriptor.block_id

    def test_every_published_port_is_the_canonical_port(self, reg):
        for descriptor in reg.blocks():
            for port in descriptor.inputs + descriptor.outputs:
                assert isinstance(port, Port), descriptor.block_id
                assert isinstance(port.type, PortType), descriptor.block_id

    def test_every_param_carries_what_a_form_needs_to_render(self, reg):
        """Requirement 5.2 - label, help and an option set where the control needs one.

        Help text is asserted for every family except ML_DL: 19 of the 48
        `ml_models.MODEL_SPECS` hyperparameters carry `help=""` today. That is a gap in
        the descriptor source (task 1.5), and the registry deliberately does not paper
        over it by inventing help text - it publishes exactly what the owner declares.
        """
        for descriptor in reg.blocks():
            for param in descriptor.params:
                assert param.label, f"{descriptor.block_id}.{param.key}"
                if param.type in (ParamType.SELECT, ParamType.MULTISELECT):
                    assert param.options or param.options_source, (
                        f"{descriptor.block_id}.{param.key}"
                    )
                if descriptor.category is not BlockCategory.ML_DL:
                    assert param.help, f"{descriptor.block_id}.{param.key}"

    def test_help_text_is_published_verbatim_from_the_owning_descriptor(self, reg):
        """The registry never authors or substitutes parameter help."""
        for spec in indicators.INDICATOR_SPECS:
            descriptor = reg[spec.block_id]
            for param in spec.params:
                assert descriptor.param(param.key).help == param.help
        for spec in models.available_model_specs():
            descriptor = reg[spec.block_id]
            for param in spec.hyperparameters:
                assert descriptor.param(param.key).help == (param.help or "")

    def test_indicator_ranges_and_warmup_survive_adaptation(self, reg):
        """Requirement 5.9 - published ranges and warmup are the indicator's own."""
        for spec in indicators.INDICATOR_SPECS:
            descriptor = reg[spec.block_id]
            for param in spec.params:
                published = descriptor.param(param.key)
                assert published is not None, f"{spec.block_id}.{param.key}"
                assert (published.min, published.max, published.step) == (
                    param.min,
                    param.max,
                    param.step,
                )
            assert descriptor.warmup() == spec.warmup(spec.defaults())

    def test_defaults_keep_behaviour_changing_params_empty(self, reg):
        """Requirement 5.4 - no silent symbol, timeframe or position size."""
        feed = reg["ohlcv_feed"]
        assert feed.defaults()["symbol"] is None
        assert feed.defaults()["timeframe"] is None
        buy = reg["action_buy_market"]
        assert buy.defaults()["quantity"] is None
        assert buy.defaults()["quantity_type"] is None

    def test_descriptors_serialize_without_callables(self, reg):
        for descriptor in reg.blocks():
            payload = descriptor.to_dict()
            assert payload["block_id"] == descriptor.block_id
            assert payload["category"] == descriptor.category.value
            assert "warmup_fn" not in payload
            assert "validate" not in payload
            assert payload["has_cross_field_validation"] is (
                descriptor.validate is not None
            )


# ---------------------------------------------------------------------------
# 9. The per-block validate(params) hook - cross-field rules ranges cannot express
# ---------------------------------------------------------------------------


class TestCrossFieldValidationHook:
    def test_macd_rejects_fast_greater_than_or_equal_to_slow(self, reg):
        macd = reg["macd"]
        assert macd.validate is not None

        for fast, slow in ((26, 26), (30, 10), (13, 12)):
            issues = macd.cross_field_issues({"fast": fast, "slow": slow})
            assert issues, f"fast={fast} slow={slow} was accepted"
            assert all(issue["block_id"] == "macd" for issue in issues)
            assert all(issue["code"] for issue in issues)
            assert all(issue["message"] for issue in issues)

    def test_macd_accepts_fast_below_slow(self, reg):
        macd = reg["macd"]
        assert macd.cross_field_issues({"fast": 12, "slow": 26}) == []
        assert macd.cross_field_issues({"fast": 5, "slow": 200}) == []

    def test_macd_defaults_satisfy_their_own_cross_field_rule(self, reg):
        assert reg["macd"].cross_field_issues() == []

    def test_hook_issues_are_structured_for_the_validator(self, reg):
        issue = reg["macd"].cross_field_issues({"fast": 30, "slow": 10})[0]
        assert set(issue) >= {
            "code",
            "severity",
            "field",
            "message",
            "expected",
            "actual",
            "fix_hint",
            "block_id",
        }
        assert issue["severity"] == registry.SEVERITY_ERROR

    def test_string_returning_hooks_are_normalised_too(self, reg):
        """`block_specs` and `feature_engineering` hooks return plain strings."""
        clamp = reg["clamp"]
        assert clamp.validate is not None
        issues = clamp.cross_field_issues({"lower": 10, "upper": 1})
        assert issues and issues[0]["block_id"] == "clamp"
        assert issues[0]["code"] == registry.CODE_CROSS_FIELD
        assert clamp.cross_field_issues({"lower": 1, "upper": 10}) == []

    def test_feature_cross_field_hook_is_wired_from_its_owner(self, reg):
        rolling_std = reg["feat_rolling_std"]
        assert rolling_std.validate is not None
        assert rolling_std.cross_field_issues({"window": 1, "ddof": 1})
        assert rolling_std.cross_field_issues({"window": 20, "ddof": 1}) == []

    def test_a_block_without_a_hook_reports_no_cross_field_issues(self, reg):
        sma = reg["sma"]
        assert sma.validate is None
        assert sma.cross_field_issues({"window": 20}) == []

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        fast=st.integers(min_value=1, max_value=200),
        slow=st.integers(min_value=1, max_value=200),
    )
    def test_macd_hook_is_exactly_the_fast_below_slow_rule(self, reg, fast, slow):
        """The hook accepts a parameterisation iff `fast < slow`, for every pair."""
        issues = reg["macd"].cross_field_issues(
            {"fast": fast, "slow": slow, "signal": 9}
        )
        assert bool(issues) is (fast >= slow)


# ---------------------------------------------------------------------------
# 10. Derived category adjacency - Requirement 6.3 support
# ---------------------------------------------------------------------------


class TestDerivedAdjacency:
    def test_declared_adjacency_is_never_overwritten(self, reg):
        for spec in features.FEATURE_SPECS:
            descriptor = reg[spec.block_id]
            assert descriptor.allowed_successor_categories == frozenset(
                BlockCategory(value) for value in spec.allowed_successor_categories
            )

    def test_terminal_blocks_keep_an_empty_successor_set(self, reg):
        for descriptor in reg.blocks():
            if descriptor.is_terminal:
                assert descriptor.allowed_successor_categories == frozenset()

    def test_source_blocks_keep_an_empty_predecessor_set(self, reg):
        for descriptor in reg.blocks():
            if not descriptor.inputs:
                assert descriptor.allowed_predecessor_categories == frozenset()

    def test_indicator_adjacency_is_derived_not_absent(self, reg):
        rsi = reg["rsi"]
        assert BlockCategory.DATA in rsi.allowed_predecessor_categories
        assert rsi.allowed_successor_categories
        assert BlockCategory.DATA not in rsi.allowed_successor_categories

    def test_no_block_lists_data_as_a_successor(self, reg):
        """A DATA block is a source: it has no inputs, so nothing may feed it (R5)."""
        for descriptor in reg.blocks():
            assert BlockCategory.DATA not in descriptor.allowed_successor_categories

    def test_derived_adjacency_agrees_with_the_compatibility_matrix(self, reg):
        """Every category a *derived* set names has at least one legally connectable port.

        Only INDICATOR and ML_DL sets are derived: `IndicatorSpec` and `ModelSpec` declare
        no adjacency, so the registry computes theirs from the port types. A declared set
        (`block_specs`, `feature_engineering`) is curated deliberately and may be broader
        than the port types strictly permit - `ohlcv_feed` lists ML_DL as a successor even
        though no model consumes an OHLCV_FRAME, and R4 rejects that edge on port type. A
        derived set has no such licence: if it names a category, a legal port pair exists.
        """
        derived = [
            d
            for d in reg.blocks()
            if d.category in (BlockCategory.INDICATOR, BlockCategory.ML_DL)
        ]
        assert derived
        for descriptor in derived:
            assert descriptor.allowed_successor_categories, descriptor.block_id
            for category in descriptor.allowed_successor_categories:
                assert any(
                    compatible(out_port.type, in_port.type)
                    for other in reg.in_category(category)
                    for out_port in descriptor.outputs
                    for in_port in other.inputs
                ), f"{descriptor.block_id} -> {category.value} has no legal port pair"


# ---------------------------------------------------------------------------
# 11. Import purity - Requirement 21.10 / the package's layering rule
# ---------------------------------------------------------------------------


def test_importing_the_registry_does_not_drag_in_fastapi_a_db_or_ccxt():
    """Assembly may read `exchange_executor`; importing the module must not."""
    import subprocess
    import sys

    code = (
        "import sys;"
        "import backend_app.backend.strategy_dag.registry;"
        "heavy=[m for m in ('fastapi','ccxt','supabase','sqlalchemy','asyncpg') "
        "if m in sys.modules];"
        "print(heavy)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_descriptor_is_frozen(reg):
    with pytest.raises(dataclasses.FrozenInstanceError):
        reg["rsi"].block_id = "tampered"


def test_descriptor_type(reg):
    assert all(isinstance(d, BlockDescriptor) for d in reg.blocks())
