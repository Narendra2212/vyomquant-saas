"""OPS-01: the committed production Terraform declares alerting, and what it references exists.

Static (regex) checks only - `terraform validate`/plan against AWS is an operator step.
"""
import glob
import os
import re

TF = os.path.join(os.path.dirname(__file__), "..", "terraform")


def _active_tf():
    return {os.path.basename(p): open(p).read() for p in glob.glob(os.path.join(TF, "*.tf"))}


def _declared(files, kind):
    return {m for t in files.values() for m in re.findall(r'resource\s+"%s"\s+"([^"]+)"' % kind, t)}


def test_topic_and_core_alarms_exist():
    files = _active_tf()
    alarms = _declared(files, "aws_cloudwatch_metric_alarm")
    assert {"api_target_5xx", "api_unhealthy_hosts", "api_running_tasks", "critical_logs",
            "webhook_failures", "kill_switch_events", "audit_write_failures"} <= alarms
    assert "alerts" in _declared(files, "aws_sns_topic")


def test_every_alarm_notifies_the_topic_on_alarm_and_recovery():
    t = _active_tf()["alarms.tf"]
    blocks = re.split(r'(?=resource "aws_cloudwatch_metric_alarm")', t)[1:]
    assert blocks
    for b in blocks:
        assert "alarm_actions" in b and "aws_sns_topic.alerts.arn" in b, b[:120]
        assert "ok_actions" in b, b[:120]


def test_every_referenced_resource_is_declared_in_an_active_file():
    files = _active_tf()
    t = files["alarms.tf"]
    all_text = "\n".join(files.values())
    for kind, name in set(re.findall(r'\b(aws_[a-z_]+)\.([a-z_0-9]+)\.', t)):
        assert re.search(r'resource\s+"%s"\s+"%s"' % (kind, name), all_text), f"{kind}.{name} is not declared"
    assert re.search(r'variable\s+"alert_email"', files["variables.tf"])
    assert "alerts_topic_arn" in files["outputs.tf"]


def test_log_metric_filters_have_matching_alarms():
    t = _active_tf()["alarms.tf"]
    for metric in re.findall(r'name\s+=\s+"(\w+Count)"', t):
        assert re.search(r'metric_name\s+=\s+"%s"' % metric, t), metric


def test_sns_topic_is_encrypted():
    t = _active_tf()["alarms.tf"]
    assert "kms_master_key_id" in t
