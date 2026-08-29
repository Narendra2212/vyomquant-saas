"""
tests/test_task_8_2_deployment_binding.py

The deployment binding and the deploy gate, held in place.

Spec: strategy-builder task 8.2. ``design.md`` -> "Exchange-agnostic deployment binding"
(``STRUCTURE DeploymentBinding`` and ``PROCEDURE deploy``) and -> API surface.
Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.9, plus 12.1/SB-06 and 21.7.

WHAT THESE TESTS HOLD IN PLACE
------------------------------
* **13.1 - one binding, one row.** ``POST .../versions/{v}/deploy`` writes the version,
  the exchange account, the risk config, the execution config and the mode as one
  ``strategy_deployments`` row. ``mode`` is on **every** deployment either writer creates,
  because ``chk_sd_live_needs_account`` constrains ``mode`` and not the legacy
  ``environment`` column - task 8.1's capitalised note, asserted here rather than trusted.

* **13.2 - ownership, before the one INSERT.** The version, the account and the risk config
  are each confirmed to be this user's, and each refusal is a **404, not a 403**: a 403
  about an account id confirms the id exists. All three are reads, so a refusal leaves no
  row behind - asserted by checking the fake database recorded no insert.

* **13.3 - not READY is a 409 naming the state and the outstanding prerequisite.** Every
  state in ``chk_lifecycle_state`` other than ``READY`` is exercised, and each refusal is
  required to carry a non-empty, state-specific sentence. A refusal that only said "not
  READY" would pass a weaker test and leave the author with nothing to do.

* **13.4 / 13.5 - the symbol and the timeframe come from real sources.** The symbol is
  checked against task 7.1's cached universe (``AssetRef.available_on``) and the timeframe
  against task 7.2's pipeline intersection *and* the venue's own CCXT vocabulary. There is
  no symbol list and no timeframe list in the module, and a test asserts that by reading
  its source.

* **13.6 - live requires an account**, exhaustively over the 2x2 of mode and account, in
  the handler as a 422 naming the missing account rather than as a 23514 from a constraint
  that does not exist until 004e is applied.

* **13.7 - one gap rule, task 7.5's.** The refusal codes this module raises are required to
  be exactly the codes ``market_data_contract.resolve_gap_policy`` raises for the same
  input, so there is no second live-only rule here to drift from it.

* **13.9 / 21.7 - the binding is a reference.** No ``load_decrypted_keys`` call exists in
  the binding module or in either deploy handler, the request model declares no credential
  field, and the whole serialised response is searched for a key/secret/passphrase as a
  *substring* rather than one field being checked.

* **004e is unapplied.** A paper deployment degrades with a warning naming
  ``004e_deployment_bindings.sql`` and reports ``binding_stored: false``; a **live** one is
  refused (503) rather than written with its account reference dropped. Never a 500, and
  never a deployment reported as bound when the binding was not stored.

Nothing is mocked that is under test. The compiler, the plan, the gap-policy resolver, the
lifecycle vocabulary and the asset universe are all the real ones; the fake is the
PostgREST client, which is the one thing this environment has no instance of.
"""

import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import asset_universe as au
from backend_app.backend import deployment_binding as db
from backend_app.backend.market_data_contract import (
    MODE_LIVE,
    MODE_PAPER,
    MarketDataContractError,
    resolve_gap_policy,
)
from backend_app.backend.market_data_validation import GapHandlingStrategy
from backend_app.backend.strategy_builder import LIFECYCLE_READY, LIFECYCLE_STATES
from backend_app.backend.strategy_service import StrategyService

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "backend_app" / "migrations" / "004e_deployment_bindings.sql"
MODULE_PATH = REPO_ROOT / "backend_app" / "backend" / "deployment_binding.py"
SERVICE_PATH = REPO_ROOT / "backend_app" / "backend" / "strategy_service.py"
ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

USER_ID = "user_task_8_2"
OTHER_USER_ID = "user_someone_else"
STRATEGY_ID = "strategy_task_8_2"
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
RISK_ID = "33333333-3333-4333-8333-333333333333"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
MARKET_TYPE = "spot"
VENUE = "binance"


# ---------------------------------------------------------------------------
# Fixtures and doubles
# ---------------------------------------------------------------------------


def _executable_python(path: Path) -> str:
    """``path``'s source with every comment and string literal removed.

    Structural assertions have to be about *code*, not about prose: this file's own
    modules explain in their docstrings which call must not appear, and a naive substring
    search would be satisfied by that explanation. Tokenising is the only way to make
    "there is no call to X here" a fact rather than a coincidence of wording.
    """
    import io
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return "\n".join(kept)


def _executable_sql(path: Path) -> str:
    """``path``'s SQL with ``--`` comments **and string literals** removed.

    The literals matter: 004e's own ``RAISE`` messages contain the phrase
    ``ADD COLUMN IF NOT EXISTS``, so a scan that kept them would find a sixth "column"
    named after the next word in an English sentence.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    text = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return re.sub(r"'(?:[^']|'')*'", "''", text)


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


def _plan(dag_hash="deadbeefcafe1234", symbol=SYMBOL, timeframe=TIMEFRAME,
          market_type=MARKET_TYPE, extra_data_node=False):
    """A minimal real ``CompiledPlan`` payload declaring one (or two) DATA nodes."""
    nodes = {
        "n_data": {
            "id": "n_data",
            "block_id": "data.market_data",
            "category": "data",
            "params": {
                "symbol": symbol,
                "timeframe": timeframe,
                "market_type": market_type,
            },
        }
    }
    data_nodes = ["n_data"]
    if extra_data_node:
        nodes["n_data2"] = {
            "id": "n_data2",
            "block_id": "data.market_data",
            "category": "data",
            "params": {"symbol": "ETH/USDT", "timeframe": timeframe,
                       "market_type": market_type},
        }
        data_nodes.append("n_data2")
    return {
        "dag_hash": dag_hash,
        "execution_order": data_nodes,
        "data_nodes": data_nodes,
        "node_index": nodes,
    }


def _version(**overrides):
    row = {
        "id": "44444444-4444-4444-8444-444444444444",
        "strategy_id": STRATEGY_ID,
        "version": "v3.0",
        "blueprint": {"nodes": [], "edges": []},
        "validation_state": "VALID",
        "dag_hash": "deadbeefcafe1234",
        "compiled_plan": _plan(),
        "lifecycle_state": LIFECYCLE_READY,
        "is_current": True,
    }
    row.update(overrides)
    return row


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """A chainable stand-in for the PostgREST builder that honours ``.eq`` filters.

    Filtering matters here: this file's subject includes tenant isolation, and a fake that
    ignored ``.eq("user_id", ...)`` would let an ownership test pass while the production
    filter was missing.
    """

    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._mode = "select"
        self._payload = None

    # builder ------------------------------------------------------------
    def select(self, columns="*", *a, **kw):
        self._mode = "select"
        missing = [
            c
            for c in (columns.split(",") if isinstance(columns, str) else [])
            if c.strip() and c.strip() != "*" and c.strip() in self._parent.absent_columns
        ]
        if missing:
            raise RuntimeError(
                f'column {self._table}.{missing[0]} does not exist (42703 undefined_column)'
            )
        return self

    def insert(self, payload, *a, **kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *a, **kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *a, **kw):
        return self

    # terminal -----------------------------------------------------------
    def execute(self):
        if self._table in self._parent.absent_tables:
            raise RuntimeError(f'relation "{self._table}" does not exist (42P01)')
        if self._mode == "insert":
            offending = sorted(set(self._payload or {}) & self._parent.absent_columns)
            if offending:
                raise RuntimeError(
                    f"Could not find the '{offending[0]}' column of "
                    f"'{self._table}' in the schema cache (PGRST204)"
                )
            self._parent.inserts.append((self._table, dict(self._payload)))
            row = dict(self._payload)
            self._parent.rows.setdefault(self._table, []).append(row)
            return _Result([row])
        if self._mode == "update":
            self._parent.updates.append((self._table, dict(self._payload), dict(self._filters)))
            return _Result([])
        rows = [
            row
            for row in self._parent.rows.get(self._table, [])
            if all(str(row.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return _Result(rows)


class _Supabase:
    def __init__(self, rows=None, absent_columns=(), absent_tables=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_columns = set(absent_columns)
        self.absent_tables = set(absent_tables)
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)

    # convenience
    def inserted(self, table):
        return [payload for name, payload in self.inserts if name == table]


def _default_rows(account_owner=USER_ID, with_risk=True, version=None):
    rows = {
        "strategies": [{"id": STRATEGY_ID, "user_id": USER_ID, "name": "s"}],
        "strategy_versions": [version or _version()],
        "strategy_deployments": [],
        "exchange_keys": [
            {"id": ACCOUNT_ID, "user_id": account_owner, "exchange_id": VENUE},
            {"id": OTHER_ACCOUNT_ID, "user_id": OTHER_USER_ID, "exchange_id": VENUE},
        ],
        "exchange_connections": [],
    }
    if with_risk:
        rows["risk_settings"] = [{"id": RISK_ID, "user_id": USER_ID, "max_drawdown_pct": 0.2}]
    else:
        rows["risk_settings"] = []
    return rows


def _universe(symbol=SYMBOL, market_type=MARKET_TYPE, venues=(VENUE,)):
    return au.AssetUniverse(
        assets=[
            au.AssetRef(
                symbol=symbol,
                base=symbol.split("/")[0],
                quote=symbol.split("/")[-1],
                market_type=market_type,
                active=True,
                price_precision=2,
                amount_precision=6,
                min_notional=10.0,
                min_amount=0.0001,
                available_on=tuple(venues),
                precision_source=venues[0] if venues else None,
            )
        ],
        generated_at=time.time(),
        exchanges=list(venues),
    )


@pytest.fixture(autouse=True)
def _clean_module_state():
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()
    yield
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()


@pytest.fixture
def seeded_universe():
    au._local_universe = _universe()  # noqa: SLF001 - reset_..._for_tests is the inverse
    return au._local_universe  # noqa: SLF001


def _service(sb):
    service = StrategyService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the one seam
    return service


def _no_quota_limits():
    """The quota/entitlement seams, granted. Not the subject of this file."""
    return (
        patch(
            "backend_app.core.subscription_dependencies.get_user_plan",
            AsyncMock(return_value="pro"),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
            AsyncMock(return_value=(True, 1, 10)),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.check_feature_entitlement",
            AsyncMock(return_value=True),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
            AsyncMock(return_value=None),
        ),
    )


async def _deploy(sb, *, payload=None, environment="paper", user=None, fleet_ok=True):
    """Run the full ``deploy_version`` path with the quota and fleet seams granted."""
    service = _service(sb)
    fleet = MagicMock()
    fleet.start_bot = AsyncMock(return_value=(fleet_ok, "started" if fleet_ok else "boom"))
    patches = _no_quota_limits()
    for p in patches:
        p.start()
    app_state_patch = patch("backend_app.core.state.app_state")
    mock_state = app_state_patch.start()
    mock_state.fleet = fleet
    try:
        result = await service.deploy_version(
            user=user or _user(),
            strategy_id=STRATEGY_ID,
            version="v3.0",
            environment=environment,
            binding_request=db.BindingRequest.from_payload(payload),
        )
    finally:
        app_state_patch.stop()
        for p in patches:
            p.stop()
    return result, fleet


# ---------------------------------------------------------------------------
# 1. The module agrees with the database and with the pipeline
# ---------------------------------------------------------------------------


class TestVocabularyAgreesWithTheSchema:
    def test_binding_modes_are_chk_sd_mode_verbatim(self):
        """``BINDING_MODES`` is parsed out of 004e, not restated here or there.

        A module that admitted a third mode would write rows the constraint rejects with
        23514; a module that admitted fewer would refuse legal deployments.
        """
        sql = MIGRATION_PATH.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"CHECK\s*\(\s*mode\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert match, "chk_sd_mode's predicate was not found in 004e"
        literals = tuple(re.findall(r"'([^']+)'", match.group(1)))
        assert set(db.BINDING_MODES) == set(literals)
        assert "backtest" not in db.BINDING_MODES

    def test_mode_constants_are_task_7_5s_own(self):
        assert db.MODE_PAPER is MODE_PAPER
        assert db.MODE_LIVE is MODE_LIVE

    def test_binding_columns_are_the_five_columns_004e_adds(self):
        added = set(
            re.findall(
                r"ADD COLUMN IF NOT EXISTS\s+(\w+)",
                _executable_sql(MIGRATION_PATH),
                re.IGNORECASE,
            )
        )
        assert added == set(db.BINDING_COLUMNS)

    def test_the_degradation_warning_names_the_migration_file(self, caplog):
        with caplog.at_level("WARNING"):
            db.warn_binding_columns_absent("probe said no")
        assert "004e_deployment_bindings.sql" in caplog.text
        assert db.DEPLOYMENT_BINDING_MIGRATION.endswith("004e_deployment_bindings.sql")
        assert (REPO_ROOT / db.DEPLOYMENT_BINDING_MIGRATION).is_file()

    def test_binding_states_are_requirement_13_10s_five(self):
        assert db.BINDING_STATES == ("DEPLOYING", "RUNNING", "PAUSED", "STOPPED", "FAILED")


class TestNoSecondSourceOfTruth:
    """SB-06 and Phase 7: no symbol list, no timeframe list, no venue literal."""

    def test_the_module_hardcodes_no_symbol_and_no_timeframe(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        # Strip docstrings so prose that *names* the defect does not trip the check.
        code = re.sub(r'"""(?:.|\n)*?"""', "", code)
        for banned in ('"BTC/USDT"', "'BTC/USDT'", '"1h"', "'1h'", '"5m"', "'5m'"):
            assert banned not in code, f"{banned} is a substituted default (SB-06)"

    def test_the_symbol_check_reads_the_asset_universe(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "asset_universe" in source
        assert "available_on" in source

    def test_the_timeframe_check_reads_the_pipeline_intersection(self):
        """The same set ``GET /registry/timeframes`` serves (task 7.2)."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "_pipeline_timeframes" in source
        labels = db.pipeline_timeframe_labels()
        from backend_app.routers.strategy_operations import _pipeline_timeframes

        served, _sources = _pipeline_timeframes()
        assert labels == [entry["id"] for entry in served]


class TestCredentialsNeverEnterThisPath:
    """Requirement 13.9: resolved inside the execution process only. 21.7: never on the wire."""

    def test_the_binding_module_never_loads_decrypted_keys(self):
        assert "load_decrypted_keys" not in _executable_python(MODULE_PATH)

    def test_neither_deploy_handler_loads_decrypted_keys(self):
        # The whole of each file is required to be free of the call rather than a slice
        # of it, so a later edit cannot move the call a few lines and pass.
        assert "load_decrypted_keys" not in _executable_python(SERVICE_PATH)
        assert "load_decrypted_keys" not in _executable_python(ROUTER_PATH)

    def test_the_credential_vault_is_still_where_credentials_are_resolved(self):
        """13.9's other half: the execution process does resolve them, from the vault.

        ``master_executor`` is the live loop; the call it makes is the one the binding's
        ``exchange_account_id`` is a reference for. Asserted so "no credential in the
        deploy path" cannot be satisfied by there being no credential path at all.
        """
        executor = _executable_python(
            REPO_ROOT / "backend_app" / "backend" / "master_executor.py"
        )
        assert "load_decrypted_keys" in executor

    def test_the_request_model_declares_no_credential_field(self):
        from backend_app.routers.strategy_operations import DeploymentBindingRequest

        fields = set(DeploymentBindingRequest.model_fields)
        from backend_app.backend.strategy_dag.schema import FORBIDDEN_PARAM_FIELDS

        assert not fields & set(FORBIDDEN_PARAM_FIELDS)
        assert "exchange_account_id" in fields

    def test_execution_config_refuses_credential_material(self):
        for key in ("api_key", "apiKey", "secret", "passphrase", "password", "exchange_id"):
            with pytest.raises(db.DeployRejected) as exc:
                db.normalise_execution_config({key: "x"})
            assert exc.value.code == "EXECUTION_CONFIG_FORBIDDEN_FIELD"


# ---------------------------------------------------------------------------
# 2. Requirement 13.3 - the lifecycle gate
# ---------------------------------------------------------------------------


class TestLifecycleGate:
    def test_ready_passes(self):
        db.assert_version_ready(_version(), STRATEGY_ID, "v3.0")

    @pytest.mark.parametrize(
        "state", sorted(s for s in LIFECYCLE_STATES if s != LIFECYCLE_READY)
    )
    def test_every_other_state_is_a_409_naming_the_state_and_the_prerequisite(self, state):
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_version_ready(_version(lifecycle_state=state), STRATEGY_ID, "v3.0")
        rejected = exc.value
        assert rejected.code == "VERSION_NOT_READY"
        assert rejected.http_status == 409
        # The state itself, in the message and in the structured detail.
        assert state in rejected.message
        assert rejected.details["lifecycle_state"] == state
        # And the outstanding prerequisite, state-specific and non-empty.
        prerequisite = rejected.details["outstanding_prerequisite"]
        assert prerequisite and len(prerequisite) > 20
        assert prerequisite in rejected.message
        assert "not a lifecycle state this platform recognises" not in prerequisite

    def test_every_lifecycle_state_has_its_own_sentence(self):
        """No state may fall through to the generic "unrecognised" wording."""
        for state in LIFECYCLE_STATES:
            if state == LIFECYCLE_READY:
                continue
            assert state in db._OUTSTANDING_PREREQUISITE  # noqa: SLF001

    def test_a_missing_lifecycle_state_is_refused(self):
        row = _version()
        row.pop("lifecycle_state")
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_version_ready(row, STRATEGY_ID, "v3.0")
        assert exc.value.details["lifecycle_state"] is None
        assert "records no lifecycle state" in exc.value.details["outstanding_prerequisite"]

    def test_a_state_outside_the_constraint_is_refused_and_says_so(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_version_ready(
                _version(lifecycle_state="ALMOST_READY"), STRATEGY_ID, "v3.0"
            )
        assert "chk_lifecycle_state" in exc.value.details["outstanding_prerequisite"]

    def test_lowercase_ready_is_accepted_and_lowercase_draft_is_not(self):
        db.assert_version_ready(_version(lifecycle_state="ready"), STRATEGY_ID, "v3.0")
        with pytest.raises(db.DeployRejected):
            db.assert_version_ready(_version(lifecycle_state="draft"), STRATEGY_ID, "v3.0")


# ---------------------------------------------------------------------------
# 3. Mode, and Requirement 13.6
# ---------------------------------------------------------------------------


class TestModeNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("paper", MODE_PAPER),
        ("live", MODE_LIVE),
        ("PAPER", MODE_PAPER),
        ("  Live ", MODE_LIVE),
    ])
    def test_recognised_modes(self, raw, expected):
        assert db.normalise_mode(raw) == expected

    def test_backtest_is_not_a_deployment_mode(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_mode("backtest")
        assert exc.value.code == "MODE_UNRECOGNISED"

    def test_an_explicit_unknown_mode_is_refused_not_defaulted(self):
        for raw in ("cloud", "local", "sandbox", "liv", "LIVE!"):
            with pytest.raises(db.DeployRejected):
                db.normalise_mode(raw)

    @pytest.mark.parametrize("environment", ["cloud", "local", "sandbox", "paper", ""])
    def test_a_legacy_environment_that_says_nothing_about_fills_resolves_to_paper(
        self, environment
    ):
        """`cloud`/`local` describe where a worker runs, not whether fills are real."""
        assert db.normalise_mode(None, environment=environment) == MODE_PAPER

    def test_a_legacy_live_environment_resolves_to_live(self):
        assert db.normalise_mode(None, environment="live") == MODE_LIVE

    def test_no_mode_and_no_environment_is_paper(self):
        assert db.normalise_mode(None) == MODE_PAPER


class TestLiveRequiresAnExchangeAccount:
    """Requirement 13.6, exhaustively over mode x account - the accepting cases too."""

    def test_live_without_an_account_is_refused_naming_the_missing_account(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_account_required_for_live(MODE_LIVE, None)
        assert exc.value.code == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
        assert exc.value.http_status == 422
        assert "exchange account" in exc.value.message.lower()

    def test_live_with_an_account_is_admitted(self):
        db.assert_account_required_for_live(MODE_LIVE, ACCOUNT_ID)

    def test_paper_is_admitted_with_or_without_an_account(self):
        db.assert_account_required_for_live(MODE_PAPER, None)
        db.assert_account_required_for_live(MODE_PAPER, ACCOUNT_ID)

    def test_the_refusal_matches_chk_sd_live_needs_account_exactly(self):
        """The one rejected combination, and only that one - a predicate that also refused
        paper-with-account would refuse legal deployments, which is worse than none."""
        rejected = []
        for mode in db.BINDING_MODES:
            for account in (None, ACCOUNT_ID):
                try:
                    db.assert_account_required_for_live(mode, account)
                except db.DeployRejected:
                    rejected.append((mode, account))
        assert rejected == [(MODE_LIVE, None)]


# ---------------------------------------------------------------------------
# 4. Requirement 13.7 - the gap policy is task 7.5's, not a second one
# ---------------------------------------------------------------------------


class TestGapPolicyIsDelegated:
    @pytest.mark.parametrize("mode", [MODE_PAPER, MODE_LIVE])
    @pytest.mark.parametrize(
        "requested",
        ["synthetic_fill", "linear_interpolate", "spline_interpolate", "forward_fill"],
    )
    def test_every_fabricating_request_is_refused_with_task_7_5s_own_code(
        self, mode, requested
    ):
        """The code raised here must be the code ``resolve_gap_policy`` raises, so this
        module cannot hold a looser rule than the one that owns the question."""
        member = GapHandlingStrategy(requested)
        with pytest.raises(MarketDataContractError) as reference:
            resolve_gap_policy(mode, member)
        with pytest.raises(db.DeployRejected) as actual:
            db.resolve_binding_gap_policy(mode, requested)
        assert actual.value.code == reference.value.code
        assert actual.value.http_status == 422

    def test_synthetic_fill_on_live_is_requirement_13_7s_named_case(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.resolve_binding_gap_policy(MODE_LIVE, "synthetic_fill")
        assert exc.value.code == "SYNTHETIC_FILL_FORBIDDEN_LIVE"

    def test_the_default_policy_fills_nothing(self):
        policy = db.resolve_binding_gap_policy(MODE_LIVE, None)
        assert policy.fills_gaps is False
        assert policy.effective is GapHandlingStrategy.SKIP_EXECUTION

    def test_an_unknown_gap_label_is_refused_not_ignored(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.resolve_binding_gap_policy(MODE_LIVE, "make_it_up")
        assert exc.value.code == "GAP_STRATEGY_UNRECOGNISED"

    def test_this_module_declares_no_second_set_of_gap_strategies(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "FABRICATING_STRATEGIES" not in source


# ---------------------------------------------------------------------------
# 5. execution_config
# ---------------------------------------------------------------------------


class TestExecutionConfig:
    def test_the_designs_fields_are_kept(self):
        payload = {
            "max_order_notional": 1000,
            "max_open_positions": 2,
            "slippage_tolerance_bps": 15,
            "order_timeout_seconds": 30,
            "retry_policy": {"attempts": 3},
        }
        assert db.normalise_execution_config(payload) == payload
        assert set(payload) == set(db.EXECUTION_CONFIG_FIELDS)

    def test_none_is_an_empty_document_not_a_refusal(self):
        assert db.normalise_execution_config(None) == {}

    def test_an_unknown_field_is_refused_rather_than_dropped(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({"max_notional": 5})
        assert exc.value.code == "EXECUTION_CONFIG_UNKNOWN_FIELD"
        assert "max_notional" in exc.value.details["unknown_fields"]

    def test_a_non_object_is_refused(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config([1, 2])
        assert exc.value.code == "EXECUTION_CONFIG_INVALID"


# ---------------------------------------------------------------------------
# 6. Requirement 13.2 - ownership, 404 not 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOwnership:
    async def test_an_owned_account_resolves_with_its_venue(self):
        sb = _Supabase(_default_rows())
        account = await db.load_owned_exchange_account(sb, USER_ID, ACCOUNT_ID)
        assert account["id"] == ACCOUNT_ID
        assert db.account_exchange_id(account) == VENUE
        assert account["_source_table"] == "exchange_keys"

    async def test_another_tenants_account_is_a_404_not_a_403(self):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await db.load_owned_exchange_account(sb, USER_ID, OTHER_ACCOUNT_ID)
        assert exc.value.code == "EXCHANGE_ACCOUNT_NOT_FOUND"
        assert exc.value.http_status == 404

    async def test_a_nonexistent_account_is_the_same_404(self):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await db.load_owned_exchange_account(sb, USER_ID, "no-such-id")
        assert exc.value.http_status == 404

    async def test_absent_account_tables_refuse_rather_than_500(self):
        sb = _Supabase(_default_rows(), absent_tables=db.EXCHANGE_ACCOUNT_TABLES)
        with pytest.raises(db.DeployRejected) as exc:
            await db.load_owned_exchange_account(sb, USER_ID, ACCOUNT_ID)
        assert exc.value.http_status == 404
        assert exc.value.details["tables_checked"] == []

    async def test_an_owned_risk_config_resolves(self):
        sb = _Supabase(_default_rows())
        row = await db.load_owned_risk_config(sb, USER_ID, RISK_ID)
        assert row["id"] == RISK_ID

    async def test_another_tenants_risk_config_is_a_404(self):
        rows = _default_rows()
        rows["risk_settings"] = [{"id": RISK_ID, "user_id": OTHER_USER_ID}]
        sb = _Supabase(rows)
        with pytest.raises(db.DeployRejected) as exc:
            await db.load_owned_risk_config(sb, USER_ID, RISK_ID)
        assert exc.value.code == "RISK_CONFIG_NOT_FOUND"
        assert exc.value.http_status == 404

    async def test_an_absent_risk_table_refuses_rather_than_500(self):
        sb = _Supabase(_default_rows(), absent_tables=("risk_settings",))
        with pytest.raises(db.DeployRejected) as exc:
            await db.load_owned_risk_config(sb, USER_ID, RISK_ID)
        assert exc.value.http_status == 404


# ---------------------------------------------------------------------------
# 7. Requirements 13.4 / 13.5 - market compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSymbolAvailability:
    async def test_a_symbol_the_venue_lists_is_admitted(self, seeded_universe):
        verdict = await db.assert_symbol_available(
            SYMBOL, MARKET_TYPE, exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
        )
        assert verdict["scope"] == "account"
        assert VENUE in verdict["available_on"]

    async def test_a_symbol_the_venue_does_not_list_names_the_symbol_and_the_account(self):
        au._local_universe = _universe(venues=("kraken",))  # noqa: SLF001
        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_symbol_available(
                SYMBOL, MARKET_TYPE, exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
            )
        rejected = exc.value
        assert rejected.code == "SYMBOL_NOT_AVAILABLE"
        assert SYMBOL in rejected.message
        assert ACCOUNT_ID in rejected.message
        assert VENUE in rejected.message
        assert rejected.details["symbol"] == SYMBOL
        assert rejected.details["exchange_account_id"] == ACCOUNT_ID
        assert rejected.details["listed_on"] == ["kraken"]

    async def test_the_market_type_is_part_of_the_question(self):
        au._local_universe = _universe(market_type="swap")  # noqa: SLF001
        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_symbol_available(
                SYMBOL, "spot", exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
            )
        assert exc.value.code == "SYMBOL_NOT_AVAILABLE"

    async def test_an_empty_universe_refuses_rather_than_assumes(self):
        with patch.object(au, "schedule_refresh", MagicMock(return_value=True)):
            with pytest.raises(db.DeployRejected) as exc:
                await db.assert_symbol_available(
                    SYMBOL, MARKET_TYPE, exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
                )
        assert exc.value.code == "MARKET_UNIVERSE_UNAVAILABLE"
        assert exc.value.http_status == 503

    async def test_with_no_account_the_answer_is_scoped_to_the_platform_and_says_so(
        self, seeded_universe
    ):
        verdict = await db.assert_symbol_available(
            SYMBOL, MARKET_TYPE, exchange_id=None, exchange_account_id=None
        )
        assert verdict["scope"] == "platform"

    async def test_a_symbol_no_venue_lists_is_refused_even_with_no_account(
        self, seeded_universe
    ):
        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_symbol_available(
                "NOPE/USDT", MARKET_TYPE, exchange_id=None, exchange_account_id=None
            )
        assert exc.value.code == "SYMBOL_NOT_AVAILABLE"


class TestTimeframeSupport:
    def test_a_pipeline_timeframe_is_admitted(self):
        source = db.assert_timeframe_supported(
            TIMEFRAME, exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
        )
        assert source in ("venue+pipeline", "pipeline")

    def test_a_timeframe_the_pipeline_cannot_process_names_it_and_the_account(self):
        outside = "1w"
        assert outside not in db.pipeline_timeframe_labels()
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_timeframe_supported(
                outside, exchange_id=VENUE, exchange_account_id=ACCOUNT_ID
            )
        rejected = exc.value
        assert rejected.code == "TIMEFRAME_NOT_SUPPORTED"
        assert outside in rejected.message
        assert ACCOUNT_ID in rejected.message
        assert rejected.details["source"] == "pipeline"

    def test_a_timeframe_the_venue_does_not_serve_is_refused_naming_the_venue(self):
        """``6h`` is in the pipeline intersection and is not one Kraken publishes."""
        venue_labels = db.venue_timeframes("kraken")
        if venue_labels is None or "6h" in venue_labels:
            pytest.skip("the installed ccxt build does not exhibit this venue gap")
        assert "6h" in db.pipeline_timeframe_labels()
        with pytest.raises(db.DeployRejected) as exc:
            db.assert_timeframe_supported(
                "6h", exchange_id="kraken", exchange_account_id=ACCOUNT_ID
            )
        assert exc.value.details["source"] == "venue"
        assert "kraken" in exc.value.message
        assert ACCOUNT_ID in exc.value.message

    def test_an_unreadable_venue_vocabulary_falls_back_and_states_the_source(self):
        with patch.object(db, "venue_timeframes", lambda _slug: None):
            assert (
                db.assert_timeframe_supported(
                    TIMEFRAME, exchange_id="unknown_venue", exchange_account_id=ACCOUNT_ID
                )
                == "pipeline"
            )

    def test_venue_timeframes_never_returns_an_empty_set(self):
        """An empty set would read as "supports nothing" and refuse every deploy."""
        assert db.venue_timeframes("definitely_not_an_exchange") is None
        assert db.venue_timeframes("") is None
        for slug in ("binance", "kraken"):
            labels = db.venue_timeframes(slug)
            assert labels is None or labels


# ---------------------------------------------------------------------------
# 8. The market comes from the plan (SB-06)
# ---------------------------------------------------------------------------


class TestMarketResolvedFromThePlan:
    def test_the_symbol_timeframe_and_market_type_come_from_the_data_node(self):
        market = db.resolve_binding_market(_version())
        assert market == {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "market_type": MARKET_TYPE,
        }

    def test_an_unreadable_plan_is_a_409_not_a_substituted_default(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.resolve_binding_market(_version(compiled_plan={"execution_order": []}))
        assert exc.value.code == "PLAN_UNREADABLE"
        assert exc.value.http_status == 409

    def test_a_plan_with_no_market_is_refused(self):
        plan = _plan()
        plan["node_index"]["n_data"]["params"] = {}
        with pytest.raises(db.DeployRejected) as exc:
            db.resolve_binding_market(_version(compiled_plan=plan))
        assert exc.value.code == "MARKET_UNRESOLVED"

    def test_two_markets_are_refused_rather_than_one_chosen(self):
        with pytest.raises(db.DeployRejected) as exc:
            db.resolve_binding_market(_version(compiled_plan=_plan(extra_data_node=True)))
        assert exc.value.code == "MARKET_AMBIGUOUS"
        assert set(exc.value.details["symbols"]) == {SYMBOL, "ETH/USDT"}

    def test_a_json_encoded_plan_is_read_too(self):
        import json

        market = db.resolve_binding_market(_version(compiled_plan=json.dumps(_plan())))
        assert market["symbol"] == SYMBOL


# ---------------------------------------------------------------------------
# 9. Requirement 13.1 end to end, through deploy_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBindingIsWrittenAsOneRow:
    async def test_the_row_carries_every_binding_field(self, seeded_universe):
        sb = _Supabase(_default_rows())
        result, fleet = await _deploy(
            sb,
            payload={
                "mode": "live",
                "exchange_account_id": ACCOUNT_ID,
                "risk_config_id": RISK_ID,
                "execution_config": {"max_open_positions": 2},
            },
            environment="live",
        )
        (row,) = sb.inserted("strategy_deployments")
        assert row["version_id"] == _version()["id"]
        assert row["exchange_account_id"] == ACCOUNT_ID
        assert row["risk_config_id"] == RISK_ID
        assert row["execution_config"] == {"max_open_positions": 2}
        assert row["mode"] == "live"
        assert row["dag_hash"] == _version()["dag_hash"]
        # ... and it is ONE row.
        assert len(sb.inserted("strategy_deployments")) == 1
        assert result["binding"]["binding_stored"] is True
        fleet.start_bot.assert_awaited_once()

    async def test_mode_is_written_on_a_paper_deployment_too(self, seeded_universe):
        """Task 8.1: chk_sd_live_needs_account guards `mode`, so `mode` must always be set."""
        sb = _Supabase(_default_rows())
        await _deploy(sb, payload={"mode": "paper"})
        (row,) = sb.inserted("strategy_deployments")
        assert row["mode"] == "paper"

    async def test_mode_is_written_even_with_no_binding_body_at_all(self, seeded_universe):
        sb = _Supabase(_default_rows())
        await _deploy(sb, payload=None)
        (row,) = sb.inserted("strategy_deployments")
        assert row["mode"] == "paper"

    async def test_the_legacy_environment_column_is_untouched(self, seeded_universe):
        sb = _Supabase(_default_rows())
        await _deploy(sb, payload={"mode": "paper"}, environment="cloud")
        (row,) = sb.inserted("strategy_deployments")
        assert row["environment"] == "cloud"
        assert row["mode"] == "paper"

    async def test_the_traded_symbol_comes_from_the_plan_not_a_default(
        self, seeded_universe
    ):
        sb = _Supabase(_default_rows())
        _result, fleet = await _deploy(sb, payload=None)
        (row,) = sb.inserted("strategy_deployments")
        assert row["exchange_symbol"] == SYMBOL
        assert fleet.start_bot.await_args.kwargs["symbol"] == SYMBOL

    async def test_the_response_carries_no_credential_substring(self, seeded_universe):
        sb = _Supabase(_default_rows())
        result, _fleet = await _deploy(
            sb,
            payload={"mode": "live", "exchange_account_id": ACCOUNT_ID},
            environment="live",
        )
        blob = repr(result).lower()
        for forbidden in ("api_key", "secret", "passphrase", "encrypted_"):
            assert forbidden not in blob


@pytest.mark.asyncio
class TestNothingIsWrittenWhenAGateRefuses:
    async def test_a_non_ready_version_creates_no_row_and_starts_nothing(
        self, seeded_universe
    ):
        sb = _Supabase(_default_rows(version=_version(lifecycle_state="TRAINING")))
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(sb, payload=None)
        assert exc.value.code == "VERSION_NOT_READY"
        assert sb.inserts == []

    async def test_live_without_an_account_creates_no_row(self, seeded_universe):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(sb, payload={"mode": "live"}, environment="live")
        assert exc.value.code == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
        assert sb.inserts == []

    async def test_another_tenants_account_creates_no_row(self, seeded_universe):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(
                sb,
                payload={"mode": "live", "exchange_account_id": OTHER_ACCOUNT_ID},
                environment="live",
            )
        assert exc.value.http_status == 404
        assert sb.inserts == []

    async def test_another_tenants_risk_config_creates_no_row(self, seeded_universe):
        rows = _default_rows()
        rows["risk_settings"] = [{"id": RISK_ID, "user_id": OTHER_USER_ID}]
        sb = _Supabase(rows)
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(sb, payload={"risk_config_id": RISK_ID})
        assert exc.value.code == "RISK_CONFIG_NOT_FOUND"
        assert sb.inserts == []

    async def test_another_tenants_version_is_not_found(self, seeded_universe):
        rows = _default_rows()
        rows["strategies"] = [{"id": STRATEGY_ID, "user_id": OTHER_USER_ID}]
        sb = _Supabase(rows)
        with pytest.raises(ValueError):
            await _deploy(sb, payload=None)
        assert sb.inserts == []

    async def test_a_synthetic_gap_request_creates_no_row(self, seeded_universe):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(
                sb,
                payload={
                    "mode": "live",
                    "exchange_account_id": ACCOUNT_ID,
                    "gap_strategy": "synthetic_fill",
                },
                environment="live",
            )
        assert exc.value.code == "SYNTHETIC_FILL_FORBIDDEN_LIVE"
        assert sb.inserts == []

    async def test_an_unlisted_symbol_creates_no_row(self):
        au._local_universe = _universe(venues=("kraken",))  # noqa: SLF001
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(
                sb,
                payload={"mode": "live", "exchange_account_id": ACCOUNT_ID},
                environment="live",
            )
        assert exc.value.code == "SYMBOL_NOT_AVAILABLE"
        assert sb.inserts == []


# ---------------------------------------------------------------------------
# 10. Migration 004e unapplied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUnappliedMigrationDegradesHonestly:
    async def test_a_paper_deployment_is_written_without_the_binding_columns(
        self, seeded_universe, caplog
    ):
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        with caplog.at_level("WARNING"):
            result, _fleet = await _deploy(sb, payload={"mode": "paper"})
        (row,) = sb.inserted("strategy_deployments")
        for column in db.BINDING_COLUMNS:
            assert column not in row
        assert result["binding"]["binding_stored"] is False
        assert result["binding"]["binding_migration"] == db.DEPLOYMENT_BINDING_MIGRATION
        assert any(
            w["code"] == "BINDING_NOT_STORED" for w in result["binding"]["warnings"]
        )
        assert "004e_deployment_bindings.sql" in caplog.text

    async def test_a_live_deployment_is_refused_rather_than_reported_as_bound(
        self, seeded_universe
    ):
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        with pytest.raises(db.DeployRejected) as exc:
            await _deploy(
                sb,
                payload={"mode": "live", "exchange_account_id": ACCOUNT_ID},
                environment="live",
            )
        assert exc.value.code == "BINDING_NOT_STORABLE"
        assert exc.value.http_status == 503
        assert db.DEPLOYMENT_BINDING_MIGRATION in str(exc.value)
        assert sb.inserts == []

    async def test_a_migration_applied_late_is_picked_up_without_a_restart(self):
        """The negative verdict expires, so an operator does not have to redeploy."""
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        assert await db.binding_columns_supported(sb) is False
        assert db.binding_column_support_state() is False
        applied = _Supabase(_default_rows())
        with patch.object(db, "BINDING_COLUMN_RECHECK_SECONDS", -1.0):
            assert await db.binding_columns_supported(applied) is True

    async def test_a_positive_verdict_that_turns_out_wrong_degrades_at_the_insert(
        self, seeded_universe, caplog
    ):
        """004e absent while the cache says present: the INSERT must degrade, not 500."""
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        db.reset_binding_column_support()
        with patch.object(db, "binding_columns_supported", AsyncMock(return_value=True)):
            with caplog.at_level("WARNING"):
                result, _fleet = await _deploy(sb, payload={"mode": "paper"})
        assert result["binding"]["binding_stored"] is False
        assert "004e_deployment_bindings.sql" in caplog.text
        (row,) = sb.inserted("strategy_deployments")
        assert "mode" not in row

    async def test_an_unrelated_write_failure_is_not_swallowed(self, seeded_universe):
        sb = _Supabase(_default_rows(), absent_tables=("strategy_deployments",))
        with pytest.raises(RuntimeError):
            await _deploy(sb, payload={"mode": "paper"})

    def test_a_missing_table_error_is_not_treated_as_a_missing_column(self):
        assert db.is_missing_binding_column_error(Exception("PGRST205 no such table")) is False
        assert db.is_missing_binding_column_error(Exception("42703 undefined_column")) is True
        assert db.is_missing_binding_column_error(Exception("connection reset")) is False
        assert (
            db.is_missing_binding_column_error(
                Exception("column strategy_deployments.mode does not exist")
            )
            is True
        )


# ---------------------------------------------------------------------------
# 11. The legacy strategy-level deploy: mode, and 13.6, and nothing else
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLegacyStrategyDeploy:
    def _service_for(self, sb):
        service = _service(sb)
        service.get_strategy = AsyncMock(
            return_value={
                "strategy": {"id": STRATEGY_ID, "symbol": SYMBOL, "pair": SYMBOL},
                "version": _version(),
            }
        )
        return service

    async def _run(self, sb, **kwargs):
        service = self._service_for(sb)
        fleet = MagicMock()
        fleet.start_bot = AsyncMock(return_value=(True, "started"))
        with patch("backend_app.core.state.app_state") as mock_state:
            mock_state.fleet = fleet
            return await service.deploy_strategy(
                user=_user(), strategy_id=STRATEGY_ID, **kwargs
            )

    async def test_mode_is_recorded_on_the_legacy_path_too(self):
        sb = _Supabase(_default_rows())
        result = await self._run(sb, environment="paper")
        (row,) = sb.inserted("strategy_deployments")
        assert row["mode"] == "paper"
        assert result["mode"] == "paper"
        assert result["binding_stored"] is True

    async def test_a_live_environment_becomes_live_mode_when_an_account_is_named(self):
        sb = _Supabase(_default_rows())
        await self._run(sb, environment="live", exchange_account_id=ACCOUNT_ID)
        (row,) = sb.inserted("strategy_deployments")
        assert row["mode"] == "live"
        assert row["exchange_account_id"] == ACCOUNT_ID

    async def test_a_live_environment_with_no_account_is_refused_naming_it(self):
        sb = _Supabase(_default_rows())
        with pytest.raises(db.DeployRejected) as exc:
            await self._run(sb, environment="live")
        assert exc.value.code == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
        assert sb.inserts == []

    async def test_an_unapplied_migration_keeps_a_paper_deploy_working(self):
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        result = await self._run(sb, environment="paper")
        (row,) = sb.inserted("strategy_deployments")
        assert "mode" not in row
        assert result["binding_stored"] is False
        assert result["binding_migration"] == db.DEPLOYMENT_BINDING_MIGRATION

    async def test_an_unapplied_migration_refuses_a_live_deploy(self):
        sb = _Supabase(_default_rows(), absent_columns=db.BINDING_COLUMNS)
        with pytest.raises(db.DeployRejected) as exc:
            await self._run(sb, environment="live", exchange_account_id=ACCOUNT_ID)
        assert exc.value.code == "BINDING_NOT_STORABLE"
        assert sb.inserts == []


# ---------------------------------------------------------------------------
# 12. The HTTP surface
# ---------------------------------------------------------------------------


def _client_and_router():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.routers import strategy_operations as ops

    app = FastAPI()
    app.include_router(ops.router, prefix="/api")
    app.state.limiter = ops.limiter
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=False), ops


class TestHttpMapping:
    def test_the_route_keeps_its_auth_dependency_and_its_limiter(self):
        from backend_app.routers import strategy_operations as ops

        source = ROUTER_PATH.read_text(encoding="utf-8")
        index = source.index('@router.post("/strategies/{strategy_id}/versions/{version}/deploy")')
        window = source[index : index + 900]
        assert "@limiter.limit(" in window
        assert "Depends(get_current_user)" in window
        assert hasattr(ops, "DeploymentBindingRequest")

    def test_a_non_ready_version_is_a_409_naming_the_prerequisite(self):
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_version = AsyncMock(
            side_effect=db.DeployRejected(
                "VERSION_NOT_READY",
                "Cannot deploy version v3.0 of strategy s1: its lifecycle state is "
                "TRAINING, not READY. Outstanding prerequisite: training is still running.",
                {
                    "lifecycle_state": "TRAINING",
                    "outstanding_prerequisite": "training is still running",
                },
                http_status=409,
            )
        )

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post("/api/strategies/s1/versions/v3.0/deploy", json={})
        assert resp.status_code == 409
        body = resp.json()["detail"]
        assert body["error"] == "VERSION_NOT_READY"
        assert body["lifecycle_state"] == "TRAINING"
        assert body["outstanding_prerequisite"]

    def test_another_tenants_account_is_a_404_over_http(self):
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_version = AsyncMock(
            side_effect=db.DeployRejected(
                "EXCHANGE_ACCOUNT_NOT_FOUND",
                "No exchange account belongs to this user",
                {"exchange_account_id": OTHER_ACCOUNT_ID},
                http_status=404,
            )
        )

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post(
                "/api/strategies/s1/versions/v3.0/deploy",
                json={"exchange_account_id": OTHER_ACCOUNT_ID, "mode": "live"},
            )
        assert resp.status_code == 404

    def test_an_unloaded_market_universe_is_a_503(self):
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_version = AsyncMock(
            side_effect=db.DeployRejected(
                "MARKET_UNIVERSE_UNAVAILABLE", "not loaded", {}, http_status=503
            )
        )

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post("/api/strategies/s1/versions/v3.0/deploy", json={})
        assert resp.status_code == 503

    def test_an_invented_body_field_is_refused(self):
        """`extra="forbid"`: a limit the client thinks it set must not be dropped."""
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_version = AsyncMock(return_value={})

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post(
                "/api/strategies/s1/versions/v3.0/deploy",
                json={"max_notional": 10},
            )
        assert resp.status_code == 422
        service.deploy_version.assert_not_awaited()

    def test_a_body_carrying_a_credential_is_refused_before_the_service_is_called(self):
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_version = AsyncMock(return_value={})

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post(
                "/api/strategies/s1/versions/v3.0/deploy",
                json={"mode": "live", "api_key": "AK", "secret": "SK"},
            )
        assert resp.status_code == 422
        service.deploy_version.assert_not_awaited()

    def test_the_legacy_strategy_deploy_maps_a_binding_refusal_too(self):
        client, ops = _client_and_router()
        service = MagicMock()
        service.deploy_strategy = AsyncMock(
            side_effect=db.DeployRejected(
                "LIVE_REQUIRES_EXCHANGE_ACCOUNT",
                "A live deployment must name the exchange account it trades through.",
                {"mode": "live"},
                http_status=422,
            )
        )

        async def _get():
            return service

        with patch.object(ops, "get_strategy_service", _get):
            resp = client.post("/api/strategies/s1/deploy", json={"environment": "live"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
