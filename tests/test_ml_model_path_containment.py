"""
tests/test_ml_model_path_containment.py

``strategies.ml_model_path`` is client-writable free text, and the live bot hands it to
``joblib.load``. Deserialising a pickle runs its ``__reduce__``, so this is arbitrary code
execution in the process that holds the decrypted exchange API keys.

P1-06.

THE WRITE SIDE -- TWO ROUTES, NEITHER VALIDATING
``POST /api/strategies`` takes ``body: Dict[str, Any]`` (no Pydantic model, so no field
validation anywhere) and stores the value verbatim::

    "ml_model_path": body.get("ml_model_path"),

``PUT /api/strategies/{id}`` is the wider hole and was not in the original finding::

    update_payload = dict(body)
    ...
    sb.table("strategies").update(update_payload).eq("id", strategy_id).eq("user_id", user["id"])

There is no column allowlist. The handler's only field-specific guard is on ``status``
(``DIRECT_STATUS_UPDATE_FORBIDDEN``), so ``{"ml_model_path": "/etc/passwd"}`` is written
as sent.

The contrast inside the same ``create_strategy`` body is the telling part: ``symbol`` and
``timeframe`` are deliberately re-derived from the compiled graph, and the *server's*
canonical graph is stored rather than the client's payload, with the comment "a client
that sent an ``exchange`` or an ``api_key`` inside a node's params would have it persisted
verbatim - the canonical parse drops both". ``ml_model_path`` received none of that
treatment.

The only server-derived writer, ``_persist_trained_model_path``, is dead code: its sole
callers are in ``tests/test_ml_strategy_id_linking.py``. The governed training path writes
to ``model_versions.artifact_uri`` instead -- checksummed, append-only, never returned to a
client -- and **nothing bridges the two**. So in practice every value this column holds
came from a request body.

THE LOAD SIDE -- NO VALIDATION OF ANY KIND
``BotRunner.__init__`` reads the column off the blueprint and ``_recover_live_state``
loads it::

    self.ml_model_path = strategy_blueprint.get("ml_model_path")
    self.ml_block = create_ml_block(self.ml_model_path)
    ...
    if self.ml_block and self.ml_model_path:
        self.ml_block.load_model_to_memory(self.ml_model_path)

``load_model_to_memory`` checks ``os.path.exists`` and then deserialises. There is no
``realpath``, no base-directory containment, no symlink check, no extension check and no
allowlist. ``create_ml_block`` is the only inspection of the name and it is substring
dispatch on the basename with a permissive default ("Unknown model type ... Defaulting to
XGBoost").

``SafeModelLoader`` is not the mitigation it appears to be. It *computes* a SHA-256 and
caches it, but compares it only when an ``expected_checksum`` is supplied -- and
``load_model_to_memory`` passes only ``validate_integrity=True``, so the comparison never
happens. ``validate_integrity`` then runs **after** ``joblib.load``, i.e. after the
pickle's code has already run. And ``ml_models`` opens with::

    try:
        import joblib
    except ImportError:
        import pickle as joblib

so in a joblib-less environment the sink is literally ``pickle.load``.

ALL THREE OVERLOADS ARE REACHABLE. ``TreeStrategyBlock`` takes a full path straight to
``joblib.load``. The sequence and autoencoder blocks take a *base name* and
``os.path.join(self.models_dir, f"{name}.keras")`` -- which does not confine anything,
because an absolute second argument discards the first and ``..`` segments traverse out.

THE DEPLOY GATES CHECK TRUTHINESS ONLY
``strategy_service.deploy_strategy`` and the router's ``_validate_ml_models_present`` both
test ``not ml_model_path`` and never look at the value; the latter carries its own
``# TODO: Add additional validation to check if model file actually exists``.
``tests/test_ml_deployment_guards.py`` asserts that ``"/path/to/model.pkl"`` -- a path that
does not exist and was never validated -- passes.

WHAT IS AND IS NOT EXPLOITABLE TODAY. There is no file-upload endpoint anywhere in
``backend_app/``, so an attacker cannot plant a pickle through the API and then name it.
What remains is pointing the column at a file that already exists and is readable by the
worker: anything on the volume or a shared mount, and in particular another tenant's
artifact under the model-artifact root. The containment gate below closes the first; the
cross-tenant case is narrower but not closed by containment alone and is recorded as such
at the bottom of this file rather than left implied.

WHAT CONTAINMENT IS WORTH, GIVEN THE FAILURE IS CAUGHT. ``fleet_manager._boot_and_run``
catches the exception, drops the bot and returns, so a failed load does not trade. That
bounds the *availability* impact, not the code-execution one: a pickle that loads
successfully never reaches the exception handler, because it has already run.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import ml_models as mm


# Paths no strategy should ever be able to name.
HOSTILE_PATHS = [
    "/etc/passwd",
    "../../etc/passwd",
    "../../../../etc/shadow",
    "/tmp/evil.pkl",
    "user_strategies/../../../tmp/evil.pkl",
    "C:\\Windows\\System32\\config\\SAM",
    "/proc/self/environ",
]


# ---------------------------------------------------------------------------
# 1. The module declares a containment contract at all
# ---------------------------------------------------------------------------


class TestTheContainmentContractExists:
    def test_the_allowed_roots_are_declared(self):
        """A reader must be able to answer "where may a model live?" from one place."""
        roots = mm.model_artifact_roots()
        assert roots, "no model artifact root is declared"
        assert all(os.path.isabs(str(r)) for r in roots), (
            f"roots must be absolute so containment is decidable: {roots}"
        )

    def test_the_configured_artifact_root_is_honoured(self, monkeypatch, tmp_path):
        """The same env var ``model_versioning`` already uses for the governed store."""
        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(tmp_path))

        roots = [os.path.realpath(str(r)) for r in mm.model_artifact_roots()]

        assert os.path.realpath(str(tmp_path)) in roots

    def test_the_allowed_extensions_are_declared(self):
        assert mm.MODEL_ARTIFACT_EXTENSIONS, "no artifact extension allowlist declared"
        assert ".pkl" in mm.MODEL_ARTIFACT_EXTENSIONS
        assert ".joblib" in mm.MODEL_ARTIFACT_EXTENSIONS

    def test_the_refusal_has_its_own_exception_type(self):
        """So a caller can tell "you may not name that" from "it is not there"."""
        assert issubclass(mm.UnsafeModelPath, Exception)


# ---------------------------------------------------------------------------
# 2. The resolver refuses what it must
# ---------------------------------------------------------------------------


class TestTheResolverRefusesPathsOutsideTheRoot:
    @pytest.mark.parametrize("candidate", HOSTILE_PATHS)
    def test_a_path_outside_every_root_is_refused(self, candidate):
        with pytest.raises(mm.UnsafeModelPath):
            mm.resolve_model_artifact_path(candidate, must_exist=False)

    def test_a_symlink_pointing_out_of_the_root_is_refused(self, monkeypatch, tmp_path):
        """Containment is decided on the REAL path, not the one supplied.

        A check that normalised ``..`` but not symlinks would be bypassable by any
        process that can create a link inside the root.
        """
        root = tmp_path / "artifacts"
        root.mkdir()
        outside = tmp_path / "outside.pkl"
        outside.write_bytes(b"not-a-model")
        link = root / "innocent.pkl"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("this platform/user cannot create symlinks")
        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(root))

        with pytest.raises(mm.UnsafeModelPath):
            mm.resolve_model_artifact_path(str(link), must_exist=False)

    def test_an_unexpected_extension_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(tmp_path))

        with pytest.raises(mm.UnsafeModelPath):
            mm.resolve_model_artifact_path(
                str(tmp_path / "model.so"), must_exist=False
            )

    def test_an_empty_or_non_string_path_is_refused(self):
        for candidate in ("", "   ", None, 12, [], {}):
            with pytest.raises(mm.UnsafeModelPath):
                mm.resolve_model_artifact_path(candidate, must_exist=False)

    def test_a_path_inside_the_root_is_accepted(self, monkeypatch, tmp_path):
        """The gate must not refuse everything; a legitimate artifact still loads."""
        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(tmp_path))
        good = tmp_path / "u1_strat_xgb_v1.pkl"
        good.write_bytes(b"x")

        resolved = mm.resolve_model_artifact_path(str(good), must_exist=True)

        assert os.path.realpath(resolved) == os.path.realpath(str(good))

    def test_a_nested_path_inside_the_root_is_accepted(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(tmp_path))
        nested = tmp_path / "tenant" / "deep"
        nested.mkdir(parents=True)
        good = nested / "m_lgb_.joblib"
        good.write_bytes(b"x")

        assert mm.resolve_model_artifact_path(str(good), must_exist=True)

    def test_extra_roots_are_honoured_for_server_chosen_directories(self, tmp_path):
        """``models_dir`` is a Python default, never client input.

        ``create_ml_block(..., models_dir="test_models/")`` and the blocks' own
        ``user_strategies/`` default are server-chosen, so a block may name its own
        directory as a root. A request body cannot.
        """
        good = tmp_path / "m_rf_.pkl"
        good.write_bytes(b"x")

        assert mm.resolve_model_artifact_path(
            str(good), extra_roots=(str(tmp_path),), must_exist=True
        )


# ---------------------------------------------------------------------------
# 3. The loader refuses before it deserialises
# ---------------------------------------------------------------------------


class TestTheLoaderRefusesBeforeDeserialising:
    def test_the_tree_block_refuses_a_path_outside_the_root(self, tmp_path):
        """The decisive test: refusal must happen BEFORE joblib touches the file.

        A pickle that deserialises has already executed, so a check that runs after the
        load -- which is what ``SafeModelLoader``'s ``validate_integrity`` does -- cannot
        be the control.
        """
        payload = tmp_path / "evil.pkl"
        payload.write_bytes(b"\x80\x04N.")  # a valid pickle of None
        block = mm.XGBoostStrategyBlock(models_dir=str(tmp_path / "models"))

        with pytest.raises(mm.UnsafeModelPath):
            block.load_model_to_memory(str(payload))

        assert block.active_model is None, "a refused path must leave nothing loaded"

    def test_the_tree_block_never_calls_joblib_for_a_refused_path(
        self, tmp_path, monkeypatch
    ):
        """Proven by observation, not by reading the order of statements."""
        calls = []
        monkeypatch.setattr(
            mm, "joblib", type("J", (), {"load": staticmethod(lambda p: calls.append(p))})
        )
        payload = tmp_path / "evil.pkl"
        payload.write_bytes(b"\x80\x04N.")
        block = mm.XGBoostStrategyBlock(models_dir=str(tmp_path / "models"))

        with pytest.raises(mm.UnsafeModelPath):
            block.load_model_to_memory(str(payload))

        assert calls == [], f"the deserialiser was reached anyway: {calls}"

    @pytest.mark.parametrize("candidate", HOSTILE_PATHS)
    def test_the_tree_block_refuses_every_hostile_path(self, candidate, tmp_path):
        block = mm.XGBoostStrategyBlock(models_dir=str(tmp_path / "models"))

        with pytest.raises((mm.UnsafeModelPath, FileNotFoundError)):
            block.load_model_to_memory(candidate)

    def test_a_traversing_base_name_is_refused_by_the_sequence_block(self, tmp_path):
        """``os.path.join(models_dir, name)`` confines nothing.

        An absolute ``name`` discards ``models_dir`` entirely, and ``..`` segments
        traverse out of it, so the sequence and autoencoder blocks need the same gate as
        the tree block even though they take a base name rather than a path.
        """
        block = mm.LSTMStrategyBlock(models_dir=str(tmp_path))

        with pytest.raises((mm.UnsafeModelPath, FileNotFoundError)):
            block.load_model_to_memory("../../../../etc/passwd")

    def test_an_absolute_base_name_is_refused_by_the_sequence_block(self, tmp_path):
        block = mm.LSTMStrategyBlock(models_dir=str(tmp_path))

        with pytest.raises((mm.UnsafeModelPath, FileNotFoundError)):
            block.load_model_to_memory("/etc/passwd")


# ---------------------------------------------------------------------------
# 4. The write side refuses the value rather than storing it
# ---------------------------------------------------------------------------


class TestTheRoutesRefuseAnUnsafeModelPath:
    def test_the_router_declares_a_write_side_validator(self):
        from backend_app.routers import strategies as sr

        assert hasattr(sr, "_assert_model_path_storable"), (
            "routers/strategies.py has no write-side check for ml_model_path, so "
            "`/etc/passwd` is stored and only refused later at load time -- by which "
            "point the deploy has already reported success"
        )

    @pytest.mark.parametrize("candidate", HOSTILE_PATHS)
    def test_the_write_side_validator_refuses_hostile_paths(self, candidate):
        from fastapi import HTTPException

        from backend_app.routers import strategies as sr

        with pytest.raises(HTTPException) as caught:
            sr._assert_model_path_storable(candidate)
        assert caught.value.status_code == 422

    def test_the_write_side_validator_allows_absence(self):
        """Most strategies have no model, and that is not an error."""
        from backend_app.routers import strategies as sr

        sr._assert_model_path_storable(None)

    def test_the_write_side_validator_does_not_require_the_file_to_exist_yet(
        self, monkeypatch, tmp_path
    ):
        """A save may precede training. Containment is decidable without the file."""
        from backend_app.routers import strategies as sr

        monkeypatch.setenv("STRATEGY_MODEL_ARTIFACT_DIR", str(tmp_path))

        sr._assert_model_path_storable(str(tmp_path / "not_yet_trained_xgb_.pkl"))

    def test_both_write_routes_call_it(self):
        """Create AND update. The update route is the one with no column allowlist."""
        import inspect

        from backend_app.routers import strategies as sr

        for handler in (sr.create_strategy, sr.update_strategy):
            source = inspect.getsource(handler)
            assert "_assert_model_path_storable" in source, (
                f"{handler.__name__} does not validate ml_model_path before writing it"
            )


# ---------------------------------------------------------------------------
# 5. The gap containment does NOT close
# ---------------------------------------------------------------------------


class TestTheLibraryCloneDoesNotCarryTheAuthorsModelPath:
    """The cross-tenant half, now closed.

    ``library.py``'s clone path used to copy ``source["ml_model_path"]`` onto the cloning
    user's own strategy row, so user B's bot process deserialised the bytes of user A's
    model artifact. Containment cannot fix that: the path is inside the model artifact
    root, which is exactly where every other tenant's model also lives.

    It also bypassed the artifact exchange this repository already has.
    ``model_versions`` rows carry ``user_id``, ``artifact_uri`` is omitted from every
    public projection, and downloads go through a signed, short-TTL,
    ownership-checked token. A copied filesystem path sidesteps all three.

    THE TRADE, STATED. The cloner gets the graph, the logic trees, the indicators and the
    risk configuration. They do not get the author's trained weights, which are per-user
    by the registry's own schema. So a cloned ML strategy arrives needing training, and
    the clone response says so rather than letting it surface later as an
    ``ML_MODEL_MISSING`` refusal nobody can explain.
    """

    def _clone_payload_columns(self) -> set:
        """The column names in the ``clone_payload`` dict literal.

        Parsed rather than sliced out of the source. Two reasons: the comment above the
        literal names ``ml_model_path`` deliberately, so a text search would match the
        explanation instead of the behaviour; and the literal's own
        ``f"[Clone] {lib_entry['name']}"`` value contains a closing brace, so splitting
        on ``}`` truncates the payload after its second key.
        """
        import ast
        import inspect
        import textwrap

        from backend_app.routers import library as lib

        tree = ast.parse(textwrap.dedent(inspect.getsource(lib.clone_strategy)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "clone_payload"
                for target in node.targets
            ):
                continue
            assert isinstance(node.value, ast.Dict), (
                "clone_payload is no longer a dict literal; re-check this test"
            )
            return {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
        raise AssertionError("clone_payload assignment not found in clone_strategy")

    def test_the_clone_payload_does_not_carry_ml_model_path(self):
        columns = self._clone_payload_columns()

        assert "ml_model_path" not in columns, (
            f"the library clone payload carries ml_model_path again, which puts the "
            f"author's artifact path onto the cloning user's row. Columns: "
            f"{sorted(columns)}"
        )

    def test_it_still_carries_the_things_a_clone_is_for(self):
        """A clone that dropped the strategy would satisfy the test above and be useless."""
        columns = self._clone_payload_columns()

        for column in ("buy_logic", "sell_logic", "risk", "indicators"):
            assert column in columns, (
                f"the clone no longer copies {column}; dropping the model path should "
                f"not have cost the strategy itself. Columns: {sorted(columns)}"
            )

    def test_the_response_tells_the_cloner_training_is_needed(self):
        """So the consequence arrives with the clone, not at the next deploy attempt.

        Parsed rather than sliced out of the source, for the same reason
        ``_clone_payload_columns`` above is: the comment immediately before the real
        assignment names ``requires_training`` too (explaining why it exists), so a bare
        substring search would keep passing even if the key were dropped from the
        returned dict, as long as the comment survived.
        """
        import ast
        import inspect
        import textwrap

        from backend_app.routers import library as lib

        tree = ast.parse(textwrap.dedent(inspect.getsource(lib.clone_strategy)))
        returned_keys: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                returned_keys |= {
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
        assert "requires_training" in returned_keys, (
            "the clone response does not state that a cloned model strategy needs "
            f"training before it can deploy. Returned keys seen: {sorted(returned_keys)}"
        )

    def test_the_source_side_gate_is_untouched(self):
        """A listing whose strategy declares a model it lacks is still refused outright.

        That gate reads ``source.get("ml_model_path")`` -- the author's row -- so it is
        unaffected by not copying the value, and it must stay that way.
        """
        import inspect

        from backend_app.routers import library as lib

        source = inspect.getsource(lib)
        assert 'not source.get("ml_model_path")' in source, (
            "the clone gate no longer refuses a source with model nodes and no model"
        )

    def test_the_ml_node_type_vocabularies_still_disagree(self):
        """A second recorded gap, in the gates rather than the loader.

        The router detects model nodes with
        ``_LEGACY_ML_NODE_TYPES = {"ml", "dl", "ml_dl", "ml_model"}`` while
        ``strategy_service.deploy_strategy``, ``library.clone_strategy`` and
        ``tee/main.py`` each test ``n.get("type", "").lower() in ["ml", "dl"]``. A node
        typed ``ml_dl`` or ``ml_model`` therefore satisfies the router's "this strategy
        needs a model" check but is invisible to the other three, so three of the four
        gates pass it with no model at all.

        Not fixed here: unifying the vocabulary touches four call sites across three
        modules and changes which strategies are deployable, which is its own change with
        its own regression surface.
        """
        from backend_app.routers import strategies as sr

        assert sr._LEGACY_ML_NODE_TYPES == frozenset(
            {"ml", "dl", "ml_dl", "ml_model"}
        ), "the router's model-node vocabulary moved; re-check the other three gates"
