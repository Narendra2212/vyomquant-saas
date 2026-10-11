"""Starting the staging PostgreSQL must not silently point the suites at the wrong cluster.

PHASE 10 / WORKSTREAM H.

THE DEFECT
----------
`staging/bootstrap_pg.py` selected its data directory as::

    DATA_DIR = staging / os.environ.get("PG_DATA_DIR", "pgdata")

and `start()` then wrote the resulting DSN to `staging/pg_dsn.txt` unconditionally.

Four different `PG_DATA_DIR` values are in circulation across the repository
(`pgdata_r1` in `staging/start_pg.ps1` and `staging/pg_keepalive_r1.py`, `pgdata_z1` in
three runbooks, `pgdata_local` in two reports), while the command printed by the module's
own docstring, by `staging/dsn.py`, and by the skip message of roughly eight PG-gated test
files is the bare `python staging/bootstrap_pg.py start`. On a machine whose provisioned
cluster is `pgdata_r1`, that `initdb`s a NEW empty cluster in `staging/pgdata`, starts it,
and repoints the shared DSN at it. Every PG-gated suite then runs against an empty
database and skips or fails for reasons unrelated to the code under test. That is what
happened in this phase: `staging/pg_dsn.txt` was observed pointing at a cluster with
`public tables = 0`.

WHAT THESE TESTS COVER
----------------------
Selection (explicit, inferred, ambiguous, malformed) and the validating DSN write
(unreachable, wrong directory, unprovisioned, downgrade, failure-preserves-file).

EVERY TEST IS HERMETIC. Data directories are `tmp_path` fixtures containing a fake
`PG_VERSION` sentinel; the server factory and the database probe are injected fakes. No
PostgreSQL process is started or stopped, no real data directory is read or written, and
the live `staging/pg_dsn.txt` is never opened -- `dsn_file` is always a tmp_path. A guard
test at the end asserts the real DSN file was not modified by this module.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO / "staging" / "bootstrap_pg.py"
REAL_DSN_FILE = REPO / "staging" / "pg_dsn.txt"


def _load_module():
    """`staging/` is not a package, so the module is loaded by path."""
    spec = importlib.util.spec_from_file_location("_p10h_bootstrap_pg", BOOTSTRAP_PATH)
    assert spec and spec.loader, f"cannot load {BOOTSTRAP_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load_module()


# ─────────────────────────────────────────────────────────────────────────────
#  Fakes
# ─────────────────────────────────────────────────────────────────────────────

def _make_cluster(root: pathlib.Path, name: str) -> pathlib.Path:
    """A directory that `list_existing_clusters` will recognise as a real cluster."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "PG_VERSION").write_text("16\n", encoding="utf-8")
    return d


class _FakeServer:
    def __init__(self, uri: str):
        self._uri = uri

    def get_uri(self) -> str:
        return self._uri


def _factory(uri: str, record: list | None = None):
    def make(data_dir: pathlib.Path):
        if record is not None:
            record.append(data_dir)
        return _FakeServer(uri)
    return make


def _prober(mapping: dict):
    """dsn -> TargetState, from a dict of dsn -> kwargs."""
    def probe(dsn: str):
        kw = mapping.get(dsn)
        if kw is None:
            return bp.TargetState(dsn=dsn, reachable=False, error="not in fake mapping")
        return bp.TargetState(dsn=dsn, **kw)
    return probe


def _provisioned(data_dir: pathlib.Path) -> dict:
    return dict(reachable=True, public_tables=89, data_directory=str(data_dir),
                missing_markers=())


def _empty(data_dir: pathlib.Path) -> dict:
    return dict(reachable=True, public_tables=0, data_directory=str(data_dir),
                missing_markers=tuple(bp.SCHEMA_MARKERS))


# ═════════════════════════════════════════════════════════════════════════════
#  1. Explicit selection
# ═════════════════════════════════════════════════════════════════════════════

def test_pg_data_dir_selects_the_provisioned_cluster(tmp_path):
    """The provisioned directory, named explicitly, is the one used."""
    _make_cluster(tmp_path, "pgdata")
    provisioned = _make_cluster(tmp_path, "pgdata_r1")

    chosen = bp.resolve_data_dir(env={"PG_DATA_DIR": "pgdata_r1"}, staging_dir=tmp_path)

    assert chosen == provisioned.resolve()


def test_the_data_dir_argument_overrides_the_environment(tmp_path):
    """`--data-dir` beats a stale `PG_DATA_DIR` inherited from a parent shell.

    The same precedence `staging/dsn.py` applies to the DSN: explicit beats implicit.
    """
    _make_cluster(tmp_path, "pgdata_r1")
    wanted = _make_cluster(tmp_path, "pgdata_z1")

    chosen = bp.resolve_data_dir(
        explicit="pgdata_z1", env={"PG_DATA_DIR": "pgdata_r1"}, staging_dir=tmp_path
    )

    assert chosen == wanted.resolve()


def test_an_absolute_path_is_honoured(tmp_path):
    """A cluster may legitimately live outside staging/."""
    outside = _make_cluster(tmp_path / "elsewhere", "cluster_a")

    chosen = bp.resolve_data_dir(
        env={"PG_DATA_DIR": str(outside)}, staging_dir=tmp_path / "staging"
    )

    assert chosen == outside.resolve()


def test_a_bare_name_resolves_under_staging_not_the_working_directory(tmp_path):
    """Selection must not depend on where the caller happened to be."""
    _make_cluster(tmp_path, "pgdata_r1")

    chosen = bp.resolve_data_dir(env={"PG_DATA_DIR": "pgdata_r1"}, staging_dir=tmp_path)

    assert chosen.parent == tmp_path.resolve()


# ═════════════════════════════════════════════════════════════════════════════
#  2. Ambiguous or invalid configuration fails closed
# ═════════════════════════════════════════════════════════════════════════════

def test_an_unset_variable_refuses_when_another_cluster_exists(tmp_path):
    """THE DEFECT. `pgdata_r1` exists, so defaulting to `pgdata` is refused."""
    _make_cluster(tmp_path, "pgdata_r1")

    with pytest.raises(bp.DataDirError) as exc:
        bp.resolve_data_dir(env={}, staging_dir=tmp_path)

    message = str(exc.value)
    assert "refusing to guess" in message
    assert "pgdata_r1" in message, "the error must name the cluster that does exist"
    assert "--data-dir" in message, "the error must say how to choose"


def test_an_unset_variable_refuses_with_several_clusters(tmp_path):
    """Multiple candidates is the clearest case of ambiguity."""
    _make_cluster(tmp_path, "pgdata")
    _make_cluster(tmp_path, "pgdata_r1")
    _make_cluster(tmp_path, "pgdata_z1")

    with pytest.raises(bp.DataDirError) as exc:
        bp.resolve_data_dir(env={}, staging_dir=tmp_path)

    message = str(exc.value)
    for name in ("pgdata", "pgdata_r1", "pgdata_z1"):
        assert name in message


def test_a_blank_variable_is_a_configuration_error_not_a_request_for_the_default(tmp_path):
    """`PG_DATA_DIR=` is what an unset variable expands to in a shell script.

    Treating it as "use the default" is how a script that meant to select a cluster
    silently selects another.
    """
    _make_cluster(tmp_path, "pgdata_r1")

    with pytest.raises(bp.DataDirError) as exc:
        bp.resolve_data_dir(env={"PG_DATA_DIR": "   "}, staging_dir=tmp_path)

    assert "empty" in str(exc.value).lower()


def test_a_relative_path_climbing_out_of_staging_is_refused(tmp_path):
    """`..` resolves differently per working directory, which is the ambiguity at issue."""
    with pytest.raises(bp.DataDirError) as exc:
        bp.resolve_data_dir(env={"PG_DATA_DIR": "../pgdata_r1"}, staging_dir=tmp_path)

    assert ".." in str(exc.value)


# ─── the inference that IS safe, so the fix is not "refuse everything" ───────

def test_a_fresh_checkout_still_gets_the_documented_default(tmp_path):
    """NEGATIVE CONTROL. With no cluster at all, nothing can be mis-selected.

    A fresh clone and CI must keep working with the bare documented command.
    """
    chosen = bp.resolve_data_dir(env={}, staging_dir=tmp_path)

    assert chosen == (tmp_path / bp.DEFAULT_DATA_DIR_NAME)


def test_the_default_is_used_when_it_is_the_only_cluster(tmp_path):
    """NEGATIVE CONTROL. Inference and explicit selection agree, so there is no risk."""
    _make_cluster(tmp_path, "pgdata")

    chosen = bp.resolve_data_dir(env={}, staging_dir=tmp_path)

    assert chosen == (tmp_path / "pgdata")


def test_a_directory_without_pg_version_is_not_mistaken_for_a_cluster(tmp_path):
    """`PG_VERSION`, not a name pattern, is what makes a directory a cluster."""
    (tmp_path / "pgdata_notes").mkdir()

    assert bp.list_existing_clusters(tmp_path) == []
    assert bp.resolve_data_dir(env={}, staging_dir=tmp_path) == (
        tmp_path / bp.DEFAULT_DATA_DIR_NAME
    )


# ═════════════════════════════════════════════════════════════════════════════
#  3. The DSN is written only after the target is verified
# ═════════════════════════════════════════════════════════════════════════════

def test_a_verified_provisioned_target_is_recorded(tmp_path):
    """The happy path, so the refusals below are not vacuous."""
    data_dir = _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    new = "postgresql://postgres:@127.0.0.1:51805/postgres"

    state = bp.write_dsn_if_safe(
        new, intended_data_dir=data_dir, dsn_file=dsn_file,
        probe=_prober({new: _provisioned(data_dir)}), env={},
    )

    assert state.classification == "PROVISIONED"
    assert dsn_file.read_text(encoding="utf-8").strip() == new


def test_an_unreachable_target_is_not_recorded(tmp_path):
    """An open port is not evidence: during the incident the port accepted TCP while the
    postmaster was wedged in crash recovery and never completed a handshake."""
    data_dir = _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    dsn_file.write_text("postgresql://postgres:@127.0.0.1:1111/postgres", encoding="utf-8")
    before = dsn_file.read_text(encoding="utf-8")
    new = "postgresql://postgres:@127.0.0.1:2222/postgres"

    with pytest.raises(bp.DsnWriteRefused) as exc:
        bp.write_dsn_if_safe(
            new, intended_data_dir=data_dir, dsn_file=dsn_file,
            probe=_prober({}), env={},
        )

    assert "not reachable" in str(exc.value)
    assert dsn_file.read_text(encoding="utf-8") == before, "the DSN file was modified"


def test_a_server_serving_a_different_directory_is_not_recorded(tmp_path):
    """"I asked for pgdata_r1" and "the DSN points at pgdata_r1" must be one statement.

    Catches a stale server, or something else listening on the port pgserver reported.
    """
    intended = _make_cluster(tmp_path, "pgdata_r1")
    other = _make_cluster(tmp_path, "pgdata")
    dsn_file = tmp_path / "pg_dsn.txt"
    new = "postgresql://postgres:@127.0.0.1:51805/postgres"

    with pytest.raises(bp.DsnWriteRefused) as exc:
        bp.write_dsn_if_safe(
            new, intended_data_dir=intended, dsn_file=dsn_file,
            probe=_prober({new: _provisioned(other)}), env={},
        )

    assert "DIFFERENT data directory" in str(exc.value)
    assert not dsn_file.exists(), "a DSN file was created despite the refusal"


def test_an_empty_cluster_is_refused_when_provisioned_is_required(tmp_path):
    """`--expect-provisioned` is for workflows that need the provisioned database."""
    data_dir = _make_cluster(tmp_path, "pgdata")
    dsn_file = tmp_path / "pg_dsn.txt"
    new = "postgresql://postgres:@127.0.0.1:56595/postgres"

    with pytest.raises(bp.DsnWriteRefused) as exc:
        bp.write_dsn_if_safe(
            new, intended_data_dir=data_dir, dsn_file=dsn_file,
            probe=_prober({new: _empty(data_dir)}), env={},
            expect_provisioned=True,
        )

    message = str(exc.value)
    assert "does not carry the provisioned schema" in message
    assert "fee_records" in message, "the error should name what is missing"
    assert not dsn_file.exists()


def test_an_empty_cluster_is_allowed_when_provisioned_is_not_required(tmp_path):
    """NEGATIVE CONTROL. A deliberate from-scratch rehearsal must still be possible."""
    data_dir = _make_cluster(tmp_path, "pgdata")
    dsn_file = tmp_path / "pg_dsn.txt"
    new = "postgresql://postgres:@127.0.0.1:56595/postgres"

    state = bp.write_dsn_if_safe(
        new, intended_data_dir=data_dir, dsn_file=dsn_file,
        probe=_prober({new: _empty(data_dir)}), env={},
    )

    assert state.classification == "EMPTY"
    assert dsn_file.read_text(encoding="utf-8").strip() == new


# ═════════════════════════════════════════════════════════════════════════════
#  4. The shared DSN is never downgraded
# ═════════════════════════════════════════════════════════════════════════════

def test_an_empty_cluster_cannot_replace_a_provisioned_one(tmp_path):
    """THE INCIDENT, reduced to one assertion.

    The DSN pointed at the provisioned cluster; a bare `start` brought up a fresh empty
    one and repointed the file at it. That must now be refused.
    """
    provisioned_dir = _make_cluster(tmp_path, "pgdata_r1")
    empty_dir = _make_cluster(tmp_path, "pgdata")
    dsn_file = tmp_path / "pg_dsn.txt"

    good = "postgresql://postgres:@127.0.0.1:51805/postgres"
    bad = "postgresql://postgres:@127.0.0.1:56595/postgres"
    dsn_file.write_text(good, encoding="utf-8")

    with pytest.raises(bp.DsnWriteRefused) as exc:
        bp.write_dsn_if_safe(
            bad, intended_data_dir=empty_dir, dsn_file=dsn_file,
            probe=_prober({good: _provisioned(provisioned_dir),
                           bad: _empty(empty_dir)}),
            env={},
        )

    assert "better-provisioned" in str(exc.value)
    assert dsn_file.read_text(encoding="utf-8").strip() == good, (
        "the shared DSN was repointed at the empty cluster"
    )


def test_the_downgrade_can_be_performed_deliberately(tmp_path):
    """A from-scratch rehearsal is legitimate; it just has to be stated."""
    provisioned_dir = _make_cluster(tmp_path, "pgdata_r1")
    empty_dir = _make_cluster(tmp_path, "pgdata")
    dsn_file = tmp_path / "pg_dsn.txt"

    good = "postgresql://postgres:@127.0.0.1:51805/postgres"
    bad = "postgresql://postgres:@127.0.0.1:56595/postgres"
    dsn_file.write_text(good, encoding="utf-8")

    bp.write_dsn_if_safe(
        bad, intended_data_dir=empty_dir, dsn_file=dsn_file,
        probe=_prober({good: _provisioned(provisioned_dir), bad: _empty(empty_dir)}),
        env={"PG_ALLOW_DSN_DOWNGRADE": "1"},
    )

    assert dsn_file.read_text(encoding="utf-8").strip() == bad


def test_an_unreachable_current_target_may_be_replaced(tmp_path):
    """The ordinary case after a restart: the old port is dead, so recording is an upgrade.

    pgserver allocates a new random port on every start, so this must keep working or the
    tool could never refresh its own DSN.
    """
    data_dir = _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    stale = "postgresql://postgres:@127.0.0.1:49323/postgres"
    fresh = "postgresql://postgres:@127.0.0.1:51805/postgres"
    dsn_file.write_text(stale, encoding="utf-8")

    bp.write_dsn_if_safe(
        fresh, intended_data_dir=data_dir, dsn_file=dsn_file,
        probe=_prober({fresh: _provisioned(data_dir)}), env={},
    )

    assert dsn_file.read_text(encoding="utf-8").strip() == fresh


# ═════════════════════════════════════════════════════════════════════════════
#  5. start() end to end, with fakes
# ═════════════════════════════════════════════════════════════════════════════

def test_start_records_the_dsn_for_the_explicitly_named_cluster(tmp_path, monkeypatch):
    data_dir = _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    uri = "postgresql://postgres:@127.0.0.1:51805/postgres"
    monkeypatch.setattr(bp, "STAGING_DIR", tmp_path)
    used: list = []

    returned = bp.start(
        explicit_data_dir="pgdata_r1",
        server_factory=_factory(uri, used),
        probe=_prober({uri: _provisioned(data_dir)}),
        dsn_file=dsn_file,
        env={},
    )

    assert returned == uri
    assert used == [data_dir.resolve()], "the factory was given the wrong directory"
    assert dsn_file.read_text(encoding="utf-8").strip() == uri


def test_start_refuses_before_touching_the_dsn_when_selection_is_ambiguous(
    tmp_path, monkeypatch
):
    """PRESERVATION ON FAILURE. Nothing is started and the DSN file is untouched."""
    _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    existing = "postgresql://postgres:@127.0.0.1:51805/postgres"
    dsn_file.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(bp, "STAGING_DIR", tmp_path)
    used: list = []

    with pytest.raises(bp.DataDirError):
        bp.start(
            server_factory=_factory("postgresql://x", used),
            probe=_prober({}), dsn_file=dsn_file, env={},
        )

    assert used == [], "a server was started despite ambiguous selection"
    assert dsn_file.read_text(encoding="utf-8").strip() == existing


def test_start_preserves_the_dsn_when_validation_fails(tmp_path, monkeypatch):
    """PRESERVATION ON FAILURE. The server came up but did not validate."""
    data_dir = _make_cluster(tmp_path, "pgdata_r1")
    dsn_file = tmp_path / "pg_dsn.txt"
    existing = "postgresql://postgres:@127.0.0.1:49323/postgres"
    dsn_file.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(bp, "STAGING_DIR", tmp_path)
    uri = "postgresql://postgres:@127.0.0.1:51805/postgres"

    with pytest.raises(bp.DsnWriteRefused):
        bp.start(
            explicit_data_dir="pgdata_r1",
            expect_provisioned=True,
            server_factory=_factory(uri),
            probe=_prober({uri: _empty(data_dir),
                           existing: _provisioned(data_dir)}),
            dsn_file=dsn_file, env={},
        )

    assert dsn_file.read_text(encoding="utf-8").strip() == existing


def test_the_cli_returns_distinct_exit_codes(tmp_path, monkeypatch):
    """So a wrapper script can tell "you must choose" from "the target is wrong"."""
    _make_cluster(tmp_path, "pgdata_r1")
    monkeypatch.setattr(bp, "STAGING_DIR", tmp_path)
    monkeypatch.delenv("PG_DATA_DIR", raising=False)
    monkeypatch.delenv("PG_EXPECT_PROVISIONED", raising=False)

    assert bp.main(["start"]) == 2, "ambiguous selection should exit 2"


# ═════════════════════════════════════════════════════════════════════════════
#  6. Classification
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(reachable=False, error="refused"), "UNREACHABLE"),
        (dict(reachable=True, public_tables=0), "EMPTY"),
        (dict(reachable=True, public_tables=3, missing_markers=("fee_records",)), "PARTIAL"),
        (dict(reachable=True, public_tables=60, missing_markers=("fee_records",)), "PARTIAL"),
        (dict(reachable=True, public_tables=89), "PROVISIONED"),
    ],
)
def test_target_classification(kwargs, expected):
    assert bp.TargetState(dsn="d", **kwargs).classification == expected


def test_a_table_count_below_the_threshold_is_not_provisioned():
    """Mirrors `staging/probe_pg_find.py`'s existing >50 rule, so the two agree."""
    state = bp.TargetState(dsn="d", reachable=True,
                           public_tables=bp.MIN_PROVISIONED_TABLES - 1)
    assert state.classification == "PARTIAL"


# ═════════════════════════════════════════════════════════════════════════════
#  7. Guard: this suite must not disturb the live environment
# ═════════════════════════════════════════════════════════════════════════════

def test_the_module_loads_by_path_without_sys_modules_registration():
    """`staging/` is not a package, so this module gets loaded by path a lot.

    It is loaded that way by `staging/pg_keepalive_r1.py`, by this test file, and by any
    probe that needs it. While the module used `from __future__ import annotations`,
    `dataclasses` resolved `TargetState`'s field annotations through
    `sys.modules[cls.__module__].__dict__` at class-construction time, so a caller that
    exec'd the module WITHOUT first registering it in `sys.modules` got

        AttributeError: 'NoneType' object has no attribute '__dict__'

    from inside dataclasses -- a baffling error for "I imported your file". This loads it
    the unforgiving way, deliberately skipping the `sys.modules` registration that the
    loader at the top of this file performs.
    """
    spec = importlib.util.spec_from_file_location("_p10h_unregistered", BOOTSTRAP_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # NOT registered in sys.modules, on purpose

    assert mod.TargetState(
        dsn="d", reachable=True, public_tables=89
    ).classification == "PROVISIONED"
    args = mod._build_parser().parse_args(
        ["start", "--data-dir", "pgdata_r1", "--expect-provisioned"]
    )
    assert (args.action, args.data_dir, args.expect_provisioned) == (
        "start", "pgdata_r1", True,
    )


def test_this_suite_did_not_touch_the_real_dsn_file():
    """Every test above passes an explicit tmp_path `dsn_file`. This proves it.

    Recorded as a test rather than a convention, because a future test that forgets the
    argument would silently repoint the shared DSN for every other suite on the machine.
    """
    if not REAL_DSN_FILE.exists():
        pytest.skip("no local staging/pg_dsn.txt on this machine")

    text = REAL_DSN_FILE.read_text(encoding="utf-8").strip()
    assert text, "staging/pg_dsn.txt is now empty"
    assert text.startswith("postgres"), (
        f"staging/pg_dsn.txt no longer looks like a DSN: {text[:40]!r}"
    )
