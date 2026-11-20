"""Did the PostgreSQL-lane suites this phase wrote actually RUN, or did they skip?

A pass count is only evidence if the tests executed. This reads both authoritative JUnit
files and reports, per file of interest, how many of its testcases passed / failed / skipped
in each lane - so "16 passed" is distinguishable from "16 skipped".
"""

import collections
import pathlib
import xml.etree.ElementTree as ET

STAGING = pathlib.Path(__file__).resolve().parent

LANES = {
    "CI": STAGING / "fullreg_FULLREG_P7B.xml",
    "PG": STAGING / "fullreg_FULLREG_P7BPG.xml",
}

OF_INTEREST = (
    "test_paper02_unfunded_order_state_postgres",
    "test_lib01_clone_strategy_postgres",
    "test_paper01_botrunner_fills_reach_the_ledger_postgres",
    "test_lib04_a_broken_client_is_503_not_500",
    "test_env03_placeholder_secrets_are_invalid",
    "test_env04_production_router_requires_explicit_enablement",
    "test_api05_documentation_exposure_matrix",
    "test_exchange_safety_fix",
    "test_validation_sweep",
    "test_orm_models_match_provisioned_schema",
    "test_cross_tenant_read_sweep_postgres",
)


def tally(xml: pathlib.Path):
    root = ET.parse(xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    out = collections.defaultdict(collections.Counter)
    for suite in suites:
        for case in suite.iter("testcase"):
            classname = case.get("classname") or ""
            module = classname.split(".")[-1] if classname else ""
            # Nested test classes: the module is the second-to-last component.
            parts = classname.split(".")
            for candidate in reversed(parts):
                if candidate.startswith("test_"):
                    module = candidate
                    break
            if module not in OF_INTEREST:
                continue
            children = {child.tag for child in case}
            if "failure" in children:
                out[module]["failed"] += 1
            elif "error" in children:
                out[module]["error"] += 1
            elif "skipped" in children:
                node = case.find("skipped")
                if (node.get("type") or "").strip() == "pytest.xfail":
                    out[module]["xfailed"] += 1
                else:
                    out[module]["skipped"] += 1
            else:
                out[module]["passed"] += 1
    return out


tallies = {name: tally(path) for name, path in LANES.items() if path.exists()}

header = f"{'module':58}" + "".join(f"{name:>26}" for name in tallies)
print(header)
print("-" * len(header))
for module in OF_INTEREST:
    row = f"{module:58}"
    for name, data in tallies.items():
        counts = data.get(module, collections.Counter())
        row += (
            f"{'P' + str(counts['passed']) + ' F' + str(counts['failed']) + ' S' + str(counts['skipped']):>26}"
        )
    print(row)
