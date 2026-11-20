"""Per-test JUnit tally for the A15 focused run, classified individually."""
import xml.etree.ElementTree as ET

root = ET.parse("staging/p11b15_focused.xml").getroot()
suite = root if root.tag == "testsuite" else root.find("testsuite")
a = suite.attrib

out = []
out.append("suite: tests=%s failures=%s errors=%s skipped=%s time=%s"
           % (a.get("tests"), a.get("failures"), a.get("errors"),
              a.get("skipped"), a.get("time")))

rows = []
for tc in suite.findall("testcase"):
    kind, msg = "PASSED", ""
    for ch in tc:
        if ch.tag in ("failure", "error"):
            kind = ch.tag.upper()
            msg = (ch.get("message") or "").strip().replace("\n", " ")[:120]
        elif ch.tag == "skipped":
            kind = "SKIPPED"
            msg = (ch.get("message") or "").strip().replace("\n", " ")[:120]
    rows.append((tc.get("classname", ""), tc.get("name"), kind, msg))

from collections import Counter
out.append("")
out.append("tally: %s" % dict(Counter(k for _, _, k, _ in rows)))

out.append("")
out.append("NON-PASSED, individually classified:")
for cls, name, kind, msg in rows:
    if kind == "PASSED":
        continue
    out.append("  %-8s %s::%s" % (kind, cls.split(".")[-1], name))
    if msg:
        out.append("           reason: %s" % msg)

out.append("")
out.append("REQUIRED REAL-REDIS CASES -- execution status on this machine:")
REQUIRED = [
    ("webhook", "test_the_credential_gate_refuses_test_keys"),
    ("webhook", "test_a_redelivered_webhook_is_ignored"),
    ("webhook", "test_the_lock_is_recorded_in_redis_under_the_documented_key"),
    ("webhook", "test_a_different_event_id_is_not_deduplicated"),
    ("webhook", "test_an_in_flight_delivery_blocks_a_concurrent_one"),
    ("webhook", "test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind"),
    ("slot", "test_the_slot_lock_is_mutually_exclusive_against_real_redis"),
    ("slot", "test_the_guard_admits_exactly_the_limit_against_real_redis"),
    ("metered", "test_concurrent_reservations_are_atomic_against_real_redis"),
    ("metered", "test_production_reserve_quota_admits_exactly_the_limit_against_real_redis"),
    ("metered", "test_production_zero_allowance_writes_no_meter_against_real_redis"),
]
by_name = {name: kind for _, name, kind, _ in rows}
for grp, name in REQUIRED:
    out.append("  %-8s %-74s %s" % (grp, name, by_name.get(name, "NOT COLLECTED")))

out.append("")
out.append("  All 11 are SKIPPED via the legitimate opt-out (AERORA_TEST_REDIS_URL unset).")
out.append("  A SKIP IS NOT A PASS. Zero real-Redis assertions executed on this machine.")

text = "\n".join(out)
open("staging/p11b15_junit.txt", "w", encoding="utf-8").write(text)
print(text)
