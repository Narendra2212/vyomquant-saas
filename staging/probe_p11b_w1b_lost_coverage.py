"""Count EXACTLY what regression coverage the W1B collection guard suspends.

PHASE 11B / WORKSTREAM 1B — read-only.

The guard turns a collection ABORT into a module-level SKIP. A skip is not a pass, so the
report has to state precisely how much evidence stopped existing. pytest cannot tell us
(it never imports the module), so this counts node IDs by AST: one per test function, times
the cartesian product of its `@pytest.mark.parametrize` argument lists.

It also classifies each test by WHICH property it was the evidence for, so the report can
say what is no longer checked rather than only how many checks are gone.

STRICTLY READ-ONLY: parses one file, prints.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "tests" / "test_p10i_probe_pg_find_uses_shared_dsn_contract.py"

#: Which guarantee each test was the evidence for. Keyed by test function name so a
#: renamed or deleted test shows up as UNCLASSIFIED rather than being silently dropped.
PROPERTY = {
    "test_a_valid_provisioned_candidate_is_recorded":
        "happy path: a valid provisioned candidate IS recorded",
    "test_the_pid_file_port_is_discovered_without_starting_anything":
        "discovery reads postmaster.pid without starting a server",
    "test_a_candidate_serving_a_different_directory_is_not_recorded":
        "SAFETY: data_directory mismatch is refused (the check the old guard lacked)",
    "test_a_sixty_table_cluster_without_markers_is_not_provisioned":
        "SAFETY: schema markers required, not a bare >50 table count",
    "test_an_empty_candidate_cannot_replace_a_provisioned_dsn":
        "SAFETY: downgrade refusal via the shared helper",
    "test_all_candidates_unreachable_records_nothing":
        "SAFETY: all-unreachable records nothing",
    "test_the_dsn_is_preserved_byte_for_byte_on_every_refusal":
        "SAFETY: DSN preserved byte-for-byte on every refusal (sha256)",
    "test_pg_allow_dsn_downgrade_permits_the_rehearsal_case":
        "PG_ALLOW_DSN_DOWNGRADE override keeps its contract",
    "test_a_validated_endpoint_replaces_an_unreachable_one":
        "a live endpoint refreshes a dead one (the tool stays useful)",
    "test_the_probe_no_longer_writes_the_dsn_itself":
        "UNIFICATION: probe has no direct DSN write call site (anti-revert)",
    "test_every_remaining_dsn_writer_routes_through_the_shared_helper":
        "UNIFICATION: both remaining writers delegate to write_dsn_if_safe",
    "test_the_shared_helper_is_the_only_direct_writer":
        "UNIFICATION: exactly one direct DSN write call site in the tree",
    "test_no_other_module_writes_the_dsn_file_directly":
        "UNIFICATION: neither remaining writer touches the file itself (AST)",
    "test_candidate_discovery_is_ordered_and_deduplicated":
        "discovery ordering and de-duplication",
    "test_a_missing_pid_file_does_not_break_discovery":
        "a stopped cluster still yields candidates",
    "test_a_malformed_pid_file_is_ignored":
        "a malformed pid file is ignored",
    "test_the_cli_parses_the_documented_invocations":
        "CLI parses the invocations the runbook documents",
    "test_a_refused_write_maps_to_cli_exit_code_3":
        "bootstrap CLI maps a refused write to exit 3 (runbook 5b claim)",
    "test_the_probe_maps_a_refused_write_to_exit_code_3":
        "probe maps a refused write to the SAME exit 3",
    "test_dry_run_never_writes":
        "--dry-run never writes",
    "test_both_modules_load_by_path_without_sys_modules_registration":
        "by-path load without sys.modules registration stays working",
    "test_the_probe_resolves_its_bootstrap_sibling_at_runtime":
        "the probe resolves its bootstrap sibling at runtime",
    "test_this_suite_did_not_touch_the_real_dsn_file":
        "GUARD: the suite did not disturb the live staging/pg_dsn.txt",
}


def _param_count(fn: ast.FunctionDef) -> int:
    """Node IDs this function expands to: product of its parametrize argument lists."""
    total = 1
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        name = dec.func.attr if isinstance(dec.func, ast.Attribute) else getattr(
            dec.func, "id", "")
        if name != "parametrize" or len(dec.args) < 2:
            continue
        values = dec.args[1]
        if isinstance(values, (ast.List, ast.Tuple)):
            total *= max(1, len(values.elts))
    return total


def main() -> int:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    rows: list[tuple[str, int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            rows.append((node.name, _param_count(node),
                         PROPERTY.get(node.name, "UNCLASSIFIED -- review")))

    functions = len(rows)
    node_ids = sum(n for _, n, _ in rows)

    print(f"file                          : {TARGET.relative_to(REPO)}")
    print(f"test FUNCTIONS suspended      : {functions}")
    print(f"test NODE IDS suspended       : {node_ids}")
    print(f"unclassified                  : "
          f"{sum(1 for _, _, p in rows if p.startswith('UNCLASSIFIED'))}")
    print()
    print(f"{'node ids':>8}  test")
    print("-" * 78)
    for name, count, prop in rows:
        print(f"{count:>8}  {name}")
        print(f"          {prop}")
    print("-" * 78)

    safety = [(n, c) for n, c, p in rows if p.startswith("SAFETY")]
    unification = [(n, c) for n, c, p in rows if p.startswith("UNIFICATION")]
    print(f"of which DSN-SAFETY properties      : "
          f"{len(safety)} functions / {sum(c for _, c in safety)} node ids")
    print(f"of which WRITER-UNIFICATION properties: "
          f"{len(unification)} functions / {sum(c for _, c in unification)} node ids")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
