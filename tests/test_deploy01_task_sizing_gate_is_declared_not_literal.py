"""DEPLOY-01 — the sizing gate that blocked every production deploy for a day.

WHAT HAPPENED
=============
`scripts/ecs_deploy_and_diagnose.py` refuses to register a task definition whose sizing is not
the expected one. The gate is right to exist: `register_new_task_definition` bases every new
revision on the **running** one, so a sizing change made outside the pipeline propagates into
every future deploy unless something refuses it.

On 2026-11-18 revision 351 was registered out of band with `memory=4096`. From then on every
deploy failed with:

    Task definition registration failed: Deployment safety validation failed:
        CRITICAL: Memory is not 2048, got 4096

Revisions 344-350 were all 2048. The gate was working; the expectation was stale.

WHY THE EXPECTATION MOVED RATHER THAN THE REVISION
==================================================
Two opposite reconciliations hung on one question -- was the task actually memory-starved? --
so it was measured before either was done. CloudWatch `AWS/ECS MemoryUtilization` for the
service, 36 hours, 431 datapoints at 5-minute resolution:

    steady state ON 4096        ~50.6 - 51.5 %   =>  ~2.07 - 2.11 GB resident
    PEAK while still ON 2048     91.7 %           =>  ~1.88 GB resident

The first line decides it: the working set is **above 2048 MB**, so at that limit it does not
fit. Which matches the task that started 08:47, registered 08:49, and was failed by the target
group at 08:50 -- a process under memory pressure answers a health check late or not at all.

Revision 351 differs from 350 in memory and nothing else: same image, same 36 `environment`
names, same 13 `secrets` names, same health check. A deliberate sizing decision, not a side
effect.

WHAT THIS FILE GUARDS
=====================
Not the number -- the *shape*. Three properties, each the reason the original gate was able to
rot:

  1. The expectation is a **named constant**, not a literal inside the comparison, so the next
     reader can find it and the next change has somewhere to be justified.
  2. The gate still **refuses** a mismatch. Making it pass by widening the comparison would
     destroy the only control standing between a console edit and every future deploy.
  3. The refusal message **names the expected and the actual value**, because the message is
     the entire diagnosis available to whoever reads a red pipeline.

WHAT IS NOT CLAIMED
===================
* **Not** that ~2.1 GB is the right working set for one API container. It is large, and
  raising a limit to accommodate growth nobody has explained is how an environment reaches
  8 GB. The same image peaked at ~1.88 GB on the old limit, so this is not new code pressing
  against a ceiling -- it is the same code measured against one it was already pressed
  against. The question is recorded in the session report and is not answered here.
* **Not** that the task definition is managed as code. It is not: the only declared
  `"memory": "2048"` in the repository is a rollback snapshot, and the deploy script mutates
  the running revision in place. That is the structural reason a console edit became the base
  of every deploy, and it is a separate change.
"""

from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "ecs_deploy_and_diagnose.py"
)


def _load_module():
    """Import the deploy script as a module without executing its CLI entry point.

    Loaded by path rather than by package name because `scripts/` is not a package, and the
    module is the thing under test -- re-stating its constants here would make every assertion
    below a tautology.
    """
    spec = importlib.util.spec_from_file_location("_ecs_deploy_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def deploy():
    return _load_module()


def _valid_task_def(deploy) -> dict:
    """The minimum task definition `validate_critical_config` accepts.

    Built from the module's own required sets so the fixture cannot drift out of acceptance for
    a reason unrelated to sizing -- which would make the sizing assertions below pass for the
    wrong reason.
    """
    secrets = [
        {"name": name, "valueFrom": f"arn:aws:secretsmanager:::secret:vyomquant/production/{name.lower()}"}
        for name in (
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_ANON_KEY",
            "SUPABASE_JWT_SECRET",
            "DATABASE_URL",
            "MASTER_ENCRYPTION_KEYS",
            "CREDENTIAL_VAULT_SALT",
            "JWT_SECRET",
        )
    ]
    secrets.append(
        {
            "name": "REDIS_URL",
            "valueFrom": "arn:aws:secretsmanager:::secret:vyomquant/production/redis_url",
        }
    )
    return {
        "family": "vyomquant-api",
        "cpu": deploy.EXPECTED_TASK_CPU,
        "memory": deploy.EXPECTED_TASK_MEMORY,
        "containerDefinitions": [
            {
                "name": "vyomquant-api",
                "image": "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:abc",
                "portMappings": [{"containerPort": 8000}],
                "environment": [],
                "secrets": secrets,
            }
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. The expectation is declared, not buried in the comparison
# ══════════════════════════════════════════════════════════════════════════


def test_the_sizing_expectation_is_a_named_constant(deploy) -> None:
    """A literal inside an `if` has nowhere for a justification to live.

    The original form was `if task_def.get("memory") != "2048"`, so the only record of why
    2048 was expected was the absence of any record. The constant is what the comment above it
    is attached to.
    """
    assert deploy.EXPECTED_TASK_CPU == "1024"
    assert deploy.EXPECTED_TASK_MEMORY == "4096"
    # Strings, because that is what the ECS API returns and a silent int/str mismatch would
    # make the comparison always fail.
    assert isinstance(deploy.EXPECTED_TASK_MEMORY, str)
    assert isinstance(deploy.EXPECTED_TASK_CPU, str)


def test_the_comparison_reads_the_constant_rather_than_a_literal() -> None:
    """Asserted against the source, because this is a property of the code's shape.

    If someone re-inlines the number, the constant above keeps passing while the gate stops
    consulting it.
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    body = source.split("# Validate CPU and memory", 1)[1].split("return True", 1)[0]
    assert "EXPECTED_TASK_MEMORY" in body
    assert "EXPECTED_TASK_CPU" in body
    assert '!= "2048"' not in body, "the stale literal is back in the comparison"
    assert '!= "4096"' not in body, (
        "the new value is inlined; it belongs in EXPECTED_TASK_MEMORY where its "
        "justification is"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. The gate still refuses — widening it would remove the only control
# ══════════════════════════════════════════════════════════════════════════


def test_the_declared_sizing_is_accepted(deploy) -> None:
    ok, message = deploy.validate_critical_config(_valid_task_def(deploy))
    assert ok is True, message


@pytest.mark.parametrize("memory", ["2048", "8192", "1024", "", None])
def test_any_other_memory_is_refused(deploy, memory) -> None:
    """Including 2048 — the value that used to be the expectation.

    The point of the gate is that it refuses whatever the running revision happens to say, in
    both directions. A one-way check would let a console edit downward through.
    """
    task_def = copy.deepcopy(_valid_task_def(deploy))
    task_def["memory"] = memory
    ok, message = deploy.validate_critical_config(task_def)
    assert ok is False
    assert "Memory is not" in message


@pytest.mark.parametrize("cpu", ["512", "2048", None])
def test_any_other_cpu_is_refused(deploy, cpu) -> None:
    task_def = copy.deepcopy(_valid_task_def(deploy))
    task_def["cpu"] = cpu
    ok, message = deploy.validate_critical_config(task_def)
    assert ok is False
    assert "CPU is not" in message


# ══════════════════════════════════════════════════════════════════════════
# 3. The message is the whole diagnosis a red pipeline offers
# ══════════════════════════════════════════════════════════════════════════


def test_the_refusal_names_both_the_expected_and_the_actual_value(deploy) -> None:
    """"Memory is wrong" sends the reader to the console to guess.

    The original message got this right and it is why the incident was diagnosable at all: it
    said `Memory is not 2048, got 4096`, which names both halves. Keeping that property is
    the point of this case.
    """
    task_def = copy.deepcopy(_valid_task_def(deploy))
    task_def["memory"] = "2048"
    _ok, message = deploy.validate_critical_config(task_def)
    assert deploy.EXPECTED_TASK_MEMORY in message, "the expected value is not in the message"
    assert "2048" in message, "the actual value is not in the message"


def test_the_refusal_explains_why_the_gate_exists(deploy) -> None:
    """The next person to hit this should not have to find out what I had to find out.

    The gate refuses because the deploy bases each revision on the RUNNING one. Without that
    sentence, "CRITICAL: Memory is not 4096" reads like a pipeline bug rather than a report of
    an out-of-band change, and the tempting fix is to delete the check.
    """
    task_def = copy.deepcopy(_valid_task_def(deploy))
    task_def["memory"] = "2048"
    _ok, message = deploy.validate_critical_config(task_def)
    assert "RUNNING" in message
    assert "out-of-band" in message
    assert "do not widen" in message.lower()
