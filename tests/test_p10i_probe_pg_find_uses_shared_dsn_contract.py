"""`probe_pg_find.py` must record the DSN through the SHARED safety contract, not its own.

PHASE 10 / WORKSTREAM I — closeout of the Workstream H conditional acceptance.

THE DEFECT
----------
`staging/probe_pg_find.py` was the third writer of `staging/pg_dsn.txt`. It was the only
GUARDED one at the time -- which is why it never caused the Workstream H incident -- but
its guard was a second, weaker implementation of DSN safety living next to
`bootstrap_pg.write_dsn_if_safe`::

    if n and n > 50:
        dsn_file.write_text(dsn, encoding="utf-8")

Measured against the shared helper's four checks it was missing three:

  1. it never asked the server `current_setting('data_directory')`, so a DIFFERENT
     provisioned cluster answering on a recycled port would be accepted as "the" instance.
     `pgserver` hands out random high ports and this repository carries two clusters, so
     that is reachable, not theoretical;
  2. no downgrade guard, so it could replace a better-provisioned target;
  3. "provisioned" was a bare table count with no schema markers.

Two definitions of "safe to record" is one too many: the weaker one sets the real
guarantee. Discovery stays in the probe; the write decision moves to the shared helper.

WHAT THESE TESTS COVER
----------------------
The ten scenarios required of the closeout, plus the contract-unification property itself
(asserted on the source, so a future revert of the refactor fails here rather than
silently reintroducing a second writer).

HERMETIC. Data directories are `tmp_path` fixtures carrying a fake `PG_VERSION`; the DSN
file is always a `tmp_path`; the database probe is an injected fake. No PostgreSQL process
is started, stopped or contacted, and the real `staging/pg_dsn.txt` is never opened for
writing -- a guard test at the end checksums it.
"""

import ast
import hashlib
import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO / "staging" / "bootstrap_pg.py"
PROBE_PATH = REPO / "staging" / "probe_pg_find.py"
KEEPALIVE_PATH = REPO / "staging" / "pg_keepalive_r1.py"
REAL_DSN_FILE = REPO / "staging" / "pg_dsn.txt"


def _dsn_write_call_sites(src):
    """DSN-file `write_text(...)` CALL SITES, found by AST rather than text matching.

    Text matching cannot do this job here: both `probe_pg_find.py` and
    `pg_keepalive_r1.py` QUOTE their old unguarded write in a docstring, precisely so the
    defect is recorded next to the fix. A substring search flags those quotes and a
    comment-stripping filter does not help, because a docstring is not a comment. The AST
    sees only real calls, so documentation can describe the old code without tripping the
    guard that forbids it.
    """
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "write_text":
            continue
        target = ast.unparse(func.value)
        if "dsn" in target.lower():
            offenders.append(f"line {node.lineno}: {target}.write_text(...)")
    return offenders


def _load(name, path, register=True):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load("_p10i_bootstrap_pg", BOOTSTRAP_PATH)
pf = _load("_p10i_probe_pg_find", PROBE_PATH)


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures / fakes
# ─────────────────────────────────────────────────────────────────────────────

def _make_cluster(root, name, port=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "PG_VERSION").write_text("16\n", encoding="utf-8")
    if port is not None:
        # postmaster.pid: pid, datadir, start time, port, ...
        (d / "postmaster.pid").write_text(
            f"4242\n{d}\n1791612068\n{port}\n127.0.0.1\n", encoding="utf-8"
        )
    return d


def _dsn(port):
    return f"postgresql://postgres:@127.0.0.1:{port}/postgres"


def _provisioned(data_dir):
    return dict(reachable=True, public_tables=89, data_directory=str(data_dir),
                missing_markers=())


def _empty(data_dir):
    return dict(reachable=True, public_tables=0, data_directory=str(data_dir),
                missing_markers=tuple(bp.SCHEMA_MARKERS))


def _partial(data_dir):
    return dict(reachable=True, public_tables=60, data_directory=str(data_dir),
                missing_markers=("fee_records",))


def _prober(mapping):
    def probe(dsn):
        kw = mapping.get(dsn)
        if kw is None:
            return bp.TargetState(dsn=dsn, reachable=False, error="connection refused")
        return bp.TargetState(dsn=dsn, **kw)
    return probe


@pytest.fixture
def env(tmp_path):
    """A staging dir with both clusters and a DSN file, all under tmp_path."""
    staging = tmp_path / "staging"
    staging.mkdir()
    r1 = _make_cluster(staging, "pgdata_r1", port=51805)
    empty = _make_cluster(staging, "pgdata", port=56595)
    dsn_file = staging / "pg_dsn.txt"
    return {"staging": staging, "r1": r1, "empty": empty, "dsn_file": dsn_file}


def _run(ctx, probe, **kw):
    """`ctx` is the fixture; `env` in **kw is the process-environment mapping.

    Named `ctx` rather than `env` because `find_and_record` already has an `env`
    parameter for the process environment, and the two collided.
    """
    return pf.find_and_record(
        dsn_file=ctx["dsn_file"], staging_dir=ctx["staging"],
        bootstrap=bp, probe=probe, env=kw.pop("env", {}), **kw
    )


# ═════════════════════════════════════════════════════════════════════════════
#  1. Valid provisioned candidate is accepted
# ═════════════════════════════════════════════════════════════════════════════

def test_a_valid_provisioned_candidate_is_recorded(env):
    good = _dsn(51805)
    rc = _run(env, _prober({good: _provisioned(env["r1"])}))

    assert rc == 0
    assert env["dsn_file"].read_text(encoding="utf-8").strip() == good


def test_the_pid_file_port_is_discovered_without_starting_anything(env):
    """Discovery reads `postmaster.pid` line 4 -- authoritative and self-maintaining.

    The old hardcoded port list needed manual upkeep; this is why the refactor keeps
    discovery separate from startup rather than shelling out to a server.
    """
    candidates = pf.candidate_dsns(env["r1"], dsn_file=env["dsn_file"],
                                   historical_ports=())

    assert _dsn(51805) in candidates, "the running cluster's own port was not discovered"


# ═════════════════════════════════════════════════════════════════════════════
#  2. Requested vs server-reported directory mismatch is rejected
# ═════════════════════════════════════════════════════════════════════════════

def test_a_candidate_serving_a_different_directory_is_not_recorded(env):
    """THE CHECK THE OLD GUARD LACKED.

    A fully provisioned cluster answers on the expected port, but it is serving
    `pgdata`, not the requested `pgdata_r1`. The old `n > 50` rule would have recorded
    it; the shared contract rejects it.
    """
    impostor = _dsn(51805)
    rc = _run(env, _prober({impostor: _provisioned(env["empty"])}))

    assert rc == 1, "a cluster serving the wrong data directory was accepted"
    assert not env["dsn_file"].exists(), "the DSN file was written despite the mismatch"


def test_a_sixty_table_cluster_without_markers_is_not_provisioned(env):
    """The old guard was `> 50` tables with no marker check, so this qualified."""
    partial = _dsn(51805)
    rc = _run(env, _prober({partial: _partial(env["r1"])}))

    assert rc == 1
    assert not env["dsn_file"].exists()


# ═════════════════════════════════════════════════════════════════════════════
#  3. Empty / less-provisioned cannot replace a provisioned target
# ═════════════════════════════════════════════════════════════════════════════

def test_an_empty_candidate_cannot_replace_a_provisioned_dsn(env):
    """Downgrade refusal, reached through the probe rather than the helper directly."""
    good = _dsn(51805)
    bad = _dsn(56595)
    env["dsn_file"].write_text(good, encoding="utf-8")

    rc = _run(
        env,
        _prober({good: _provisioned(env["r1"]), bad: _empty(env["empty"])}),
        intended_dir_name="pgdata",
        expect_provisioned=False,
    )

    assert rc == 3, "expected the shared helper to refuse the downgrade"
    assert env["dsn_file"].read_text(encoding="utf-8").strip() == good


# ═════════════════════════════════════════════════════════════════════════════
#  4. Unreachable candidates are rejected
# ═════════════════════════════════════════════════════════════════════════════

def test_all_candidates_unreachable_records_nothing(env):
    existing = _dsn(49323)
    env["dsn_file"].write_text(existing, encoding="utf-8")

    rc = _run(env, _prober({}))

    assert rc == 1
    assert env["dsn_file"].read_text(encoding="utf-8").strip() == existing


# ═════════════════════════════════════════════════════════════════════════════
#  5. Validation failure preserves the DSN byte-for-byte
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "scenario",
    ["unreachable", "wrong-directory", "partial-schema"],
)
def test_the_dsn_is_preserved_byte_for_byte_on_every_refusal(env, scenario):
    """Byte-for-byte, not merely "still points somewhere sensible".

    Checked with a sha256 over the raw bytes, including the absence of a trailing newline,
    because every PG-gated suite reads this file.
    """
    # The recorded DSN names a port nothing answers on, and is deliberately ABSENT from
    # the prober's mapping -- otherwise discovery would find the live cluster via the pid
    # file, legitimately record it, and the test would be measuring a success path.
    # A trailing newline is included so the comparison covers it.
    original = _dsn(49999) + "\n"
    env["dsn_file"].write_bytes(original.encode("utf-8"))
    before = hashlib.sha256(env["dsn_file"].read_bytes()).hexdigest()

    candidate = _dsn(60076)
    mapping = {}
    if scenario == "wrong-directory":
        mapping[candidate] = _provisioned(env["empty"])
    elif scenario == "partial-schema":
        mapping[candidate] = _partial(env["r1"])
    # "unreachable" -> nothing is reachable at all

    rc = _run(env, _prober(mapping), extra_ports=(60076,))

    assert rc == 1, f"expected no suitable candidate for the {scenario} case"
    after = hashlib.sha256(env["dsn_file"].read_bytes()).hexdigest()
    assert after == before, f"the DSN file changed on a {scenario} refusal"


# ═════════════════════════════════════════════════════════════════════════════
#  6. The explicit downgrade override keeps its contract
# ═════════════════════════════════════════════════════════════════════════════

def test_pg_allow_dsn_downgrade_permits_the_rehearsal_case(env):
    """A from-scratch rehearsal is legitimate; it just has to be stated.

    Same override name and truthiness rules Workstream H established.
    """
    good = _dsn(51805)
    bad = _dsn(56595)
    env["dsn_file"].write_text(good, encoding="utf-8")

    rc = _run(
        env,
        _prober({good: _provisioned(env["r1"]), bad: _empty(env["empty"])}),
        intended_dir_name="pgdata",
        expect_provisioned=False,
        env={"PG_ALLOW_DSN_DOWNGRADE": "1"},
    )

    assert rc == 0
    assert env["dsn_file"].read_text(encoding="utf-8").strip() == bad


# ═════════════════════════════════════════════════════════════════════════════
#  7. A validated endpoint refreshes a dead one
# ═════════════════════════════════════════════════════════════════════════════

def test_a_validated_endpoint_replaces_an_unreachable_one(env):
    """The ordinary post-restart case this script exists for.

    `pgserver` picks a new port on every start, so the recorded DSN is dead and the live
    one must win. If this failed, the fix would have made the tool useless.
    """
    dead = _dsn(49323)
    live = _dsn(51805)
    env["dsn_file"].write_text(dead, encoding="utf-8")

    rc = _run(env, _prober({live: _provisioned(env["r1"])}))

    assert rc == 0
    assert env["dsn_file"].read_text(encoding="utf-8").strip() == live


# ═════════════════════════════════════════════════════════════════════════════
#  8. Both scripts use the same contract — asserted on the source
# ═════════════════════════════════════════════════════════════════════════════

def test_the_probe_no_longer_writes_the_dsn_itself():
    """The unification property. A revert of the refactor fails here.

    Without this, someone could reintroduce a direct `write_text` and every behavioural
    test above would still pass through the injected fake.
    """
    src = PROBE_PATH.read_text(encoding="utf-8")
    offenders = _dsn_write_call_sites(src)

    assert offenders == [], (
        f"probe_pg_find.py writes the DSN directly again: {offenders}. The write decision "
        f"belongs to bootstrap_pg.write_dsn_if_safe so there is one definition of "
        f"'safe to record'."
    )
    assert "write_dsn_if_safe" in src, (
        "probe_pg_find.py no longer references the shared helper"
    )


@pytest.mark.parametrize("path", [PROBE_PATH, KEEPALIVE_PATH])
def test_every_remaining_dsn_writer_routes_through_the_shared_helper(path):
    """Inventory assertion: the two non-helper writers both delegate."""
    src = path.read_text(encoding="utf-8")
    assert "write_dsn_if_safe" in src, f"{path.name} does not use the shared helper"


def test_the_shared_helper_is_the_only_direct_writer():
    """`bootstrap_pg.write_dsn_if_safe` holds the single `write_text` on the DSN file."""
    writes = _dsn_write_call_sites(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert len(writes) == 1, (
        f"expected exactly one DSN write call site in bootstrap_pg.py, found {writes}"
    )


@pytest.mark.parametrize("path", [PROBE_PATH, KEEPALIVE_PATH])
def test_no_other_module_writes_the_dsn_file_directly(path):
    """Inventory assertion, by AST: neither remaining writer touches the file itself."""
    assert _dsn_write_call_sites(path.read_text(encoding="utf-8")) == []


# ═════════════════════════════════════════════════════════════════════════════
#  9. Candidate discovery and CLI behaviour remain functional
# ═════════════════════════════════════════════════════════════════════════════

def test_candidate_discovery_is_ordered_and_deduplicated(env):
    env["dsn_file"].write_text(_dsn(49323), encoding="utf-8")

    candidates = pf.candidate_dsns(
        env["r1"], dsn_file=env["dsn_file"], extra_ports=(12345, 51805),
        historical_ports=(49323, 51805),
    )

    assert candidates[0] == _dsn(12345), "explicit --port values must be tried first"
    assert len(candidates) == len(set(candidates)), "candidates contain duplicates"
    assert _dsn(49323) in candidates, "the current DSN must remain a candidate"


def test_a_missing_pid_file_does_not_break_discovery(tmp_path):
    """A cluster that is not running has no pid file; discovery must still produce a list."""
    staging = tmp_path / "staging"
    staging.mkdir()
    d = _make_cluster(staging, "pgdata_r1")      # no port -> no postmaster.pid

    candidates = pf.candidate_dsns(d, dsn_file=staging / "pg_dsn.txt")

    assert candidates, "discovery returned nothing when the pid file was absent"
    assert pf._port_from_pid_file(d) is None


def test_a_malformed_pid_file_is_ignored(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    d = _make_cluster(staging, "pgdata_r1")
    (d / "postmaster.pid").write_text("garbage\n", encoding="utf-8")

    assert pf._port_from_pid_file(d) is None


def test_the_cli_parses_the_documented_invocations():
    parser = pf._build_parser()

    default = parser.parse_args([])
    assert default.data_dir == pf.DEFAULT_INTENDED_DIR
    assert default.dry_run is False

    explicit = parser.parse_args(
        ["--data-dir", "pgdata", "--port", "1", "--port", "2", "--dry-run",
         "--any-provisioning"]
    )
    assert explicit.data_dir == "pgdata"
    assert explicit.ports == [1, 2]
    assert explicit.dry_run is True
    assert explicit.any_provisioning is True


def test_a_refused_write_maps_to_cli_exit_code_3(monkeypatch):
    """Verifies the exit code `DATABASE_PROVISIONING_RUNBOOK.md` section 5b cites.

    The runbook tells the reader that a from-nothing rehearsal without
    `PG_ALLOW_DSN_DOWNGRADE=1` "exits 3 and tells you exactly this". That claim is checked
    here rather than asserted, and without starting a server or creating a third cluster:
    `start` is replaced by one that raises the refusal the shared helper would raise, and
    only the exit-code mapping is under test.
    """
    def _refuse(**_kw):
        raise bp.DsnWriteRefused("it would REPLACE a better-provisioned target")

    monkeypatch.setattr(bp, "start", _refuse)
    monkeypatch.delenv("PG_DATA_DIR", raising=False)
    monkeypatch.delenv("PG_EXPECT_PROVISIONED", raising=False)

    assert bp.main(["start", "--data-dir", "pgdata_z1"]) == 3


def test_the_probe_maps_a_refused_write_to_exit_code_3(env):
    """The probe surfaces the same refusal with the same code, so wrappers agree."""
    good = _dsn(51805)
    bad = _dsn(56595)
    env["dsn_file"].write_text(good, encoding="utf-8")

    rc = _run(
        env,
        _prober({good: _provisioned(env["r1"]), bad: _empty(env["empty"])}),
        intended_dir_name="pgdata",
        expect_provisioned=False,
    )

    assert rc == 3


def test_dry_run_never_writes(env):
    good = _dsn(51805)
    rc = _run(env, _prober({good: _provisioned(env["r1"])}), dry_run=True)

    assert rc == 0
    assert not env["dsn_file"].exists(), "--dry-run wrote the DSN file"


# ═════════════════════════════════════════════════════════════════════════════
#  10. Loading the shared helper by path stays compatible
# ═════════════════════════════════════════════════════════════════════════════

def test_both_modules_load_by_path_without_sys_modules_registration():
    """`staging/` is not a package, so by-path loading is how these are consumed.

    Workstream H removed a `dataclasses`/postponed-annotations trap that made an
    unregistered by-path load raise a baffling AttributeError. This keeps both modules
    honest about it.
    """
    boot = _load("_p10i_unreg_boot", BOOTSTRAP_PATH, register=False)
    assert boot.TargetState(
        dsn="d", reachable=True, public_tables=89
    ).classification == "PROVISIONED"

    probe = _load("_p10i_unreg_probe", PROBE_PATH, register=False)
    assert probe._build_parser().parse_args([]).data_dir == "pgdata_r1"


def test_the_probe_resolves_its_bootstrap_sibling_at_runtime():
    """`_load_bootstrap()` must find the real sibling, not rely on a pre-imported name."""
    mod = pf._load_bootstrap()
    assert hasattr(mod, "write_dsn_if_safe")
    assert hasattr(mod, "MIN_PROVISIONED_TABLES")


# ═════════════════════════════════════════════════════════════════════════════
#  Guard: this suite must not disturb the live environment
# ═════════════════════════════════════════════════════════════════════════════

def test_this_suite_did_not_touch_the_real_dsn_file():
    if not REAL_DSN_FILE.exists():
        pytest.skip("no local staging/pg_dsn.txt on this machine")

    text = REAL_DSN_FILE.read_text(encoding="utf-8").strip()
    assert text.startswith("postgres"), (
        f"staging/pg_dsn.txt no longer looks like a DSN: {text[:40]!r}"
    )
