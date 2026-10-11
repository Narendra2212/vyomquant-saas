"""ENV-03 — a non-empty string is not a credential.

WHAT WAS WRONG
--------------
Every credential gate in this codebase was spelled ``if not value:``. That test is
satisfied by any non-empty string, and the values committed in ``backend_app/.env`` are
non-empty strings::

    SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY     # 30 chars, truthy
    SUPABASE_JWT_SECRET=YOUR_SUPABASE_JWT_SECRET                 # 24 chars, truthy
    MASTER_ENCRYPTION_KEYS=YOUR_FERNET_ENCRYPTION_KEY            # 26 chars, truthy
    JWT_SECRET=YOUR_JWT_SECRET_AT_LEAST_64_CHARS                 # 33 chars, truthy

So ``Settings.validate_required()``, ``validate_config()`` and the vault's own
``if not keys_string:`` all passed with the template still in place. The clearest case is
``.env.example``'s ``STRIPE_SECRET_KEY=sk_live_your-stripe-secret-key``:
``billing._validate_keys`` accepts a Stripe key only when it starts with ``sk_live_`` —
written precisely so a test-mode key cannot grant a paid entitlement — and the placeholder
starts with ``sk_live_``. A prefix is not a credential either.

AND WHICH FILE WAS EVEN BEING READ
----------------------------------
``core/config.py`` called a bare ``load_dotenv()``. With no argument that calls
``find_dotenv()``, which walks up from the CALLING MODULE's directory — ``backend_app/`` —
so it resolves ``backend_app/.env`` and never reaches the repository-root ``.env``. Both
files exist here. During this audit a required variable was added to the ROOT ``.env`` to
close a finding, recorded as fixed, and was inert.

WHAT IS ASSERTED HERE
---------------------
The four verdicts the brief requires — missing, empty, placeholder, configured — then that
a deployed environment refuses the first three and a development environment does not, then
that the check is actually wired into startup, then that the files committed in this
repository are recognised as placeholders by it.

NO TEST HERE CONTAINS A REAL CREDENTIAL. The "configured" values below are strings invented
for this file; they are shaped like the real thing so that the accepted case is a real test
of the vocabulary's false-positive rate, and they authenticate to nothing.
"""

import ast
import importlib
import pathlib

import pytest

from backend_app.core import config as config_module
from backend_app.core.secret_validation import (PLACEHOLDER_EXACT,
                                                PLACEHOLDER_SUBSTRINGS,
                                                SecretVerdict,
                                                classify_secret,
                                                describe_verdict,
                                                secret_is_usable)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════════════════════
#  A CONFIGURED DEPLOYMENT, INVENTED FOR THIS FILE
# ══════════════════════════════════════════════════════════════════════════════════════════
# Shaped like what each provider really issues, so "real configured -> accepted" measures
# the vocabulary's false-positive rate instead of trivially passing on a short random
# string. Not secrets: they are committed here on purpose and sign nothing.
CONFIGURED = {
    "SUPABASE_URL": "https://kqmbvtzrldnjfwhsepxg.supabase.co",
    "SUPABASE_ANON_KEY": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtxbWJ2dHpybGRuamZ3aHNlcHhnIiwicm9sZSI6ImFub24ifQ."
        "7Qd2Lm9Kv4Rt6Yw1Bn8Hs3Jc5Pf0AgUeVi2Ol4Nq6"
    ),
    "SUPABASE_SERVICE_ROLE_KEY": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtxbWJ2dHpybGRuamZ3aHNlcHhnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSJ9."
        "Mz8Xy1Jc5Pf0AgUeVi2Ol4Nq6Zx7Qv2Lm9Kd4Rt6Y"
    ),
    "SUPABASE_JWT_SECRET": "Ht4Wq9Zb2Nm7Kf1Rv6Jc3Ls8Yd0Pg5Ax2Ue7Vi4Ol9Nq",
    "JWT_SECRET": "Zx7Qv2Lm9Kd4Rt6Yw1Bn8Hs3Jc5Pf0Ag_Ue-Vi2Ol4Nq6Mz8Xy",
    "MASTER_ENCRYPTION_KEYS": "bVQ3eHJ6TGs5ZG1OcDJ3WTRzQjhmSjFjVjZhUTBnWD0=",
    "DATABASE_URL": (
        "postgresql://postgres.kqmbvtzrldnjfwhsepxg:Rt6Yw1Bn8Hs3Jc5Pf0Ag"
        "@aws-0-ap-south-1.pooler.supabase.com:6543/postgres?sslmode=require"
    ),
    "CREDENTIAL_VAULT_SALT": "9f2c7b41ae60d3581cb9e4f7a28d05163b7ec84902fd1a6e",
    "STRIPE_SECRET_KEY": "sk_live_51Qv2Lm9Kd4Rt6Yw1Bn8Hs3Jc5Pf0AgUeVi2Ol4Nq6Mz8X",
    "STRIPE_WEBHOOK_SECRET": "whsec_7Qd2Lm9Kv4Rt6Yw1Bn8Hs3Jc5Pf0AgUeVi",
    "RAZORPAY_KEY_ID": "rzp_live_Kd4Rt6Yw1Bn8Hs",
    "RAZORPAY_KEY_SECRET": "Lm9Kd4Rt6Yw1Bn8Hs3Jc5Pf0Ag",
    "RAZORPAY_WEBHOOK_SECRET": "Vi2Ol4Nq6Mz8Xy7Qv2Lm9Kd4Rt",
}

#: Exactly the strings the brief names, plus the ones this repository's own templates ship.
NAMED_PLACEHOLDERS = (
    "YOUR_SUPABASE_SERVICE_ROLE_KEY",
    "dummy_key",
    "CHANGE_ME",
)


@pytest.fixture
def deployment(monkeypatch):
    """A factory returning a ``Settings`` for a given environment and credential set.

    ``credential_verdicts`` reads ``os.environ`` live — deliberately, see its docstring —
    so no module reload is needed and the verdicts describe the process as configured right
    now. ``Settings()`` is constructed fresh anyway so nothing leaks between cases.

    Every governed variable is set to its CONFIGURED value first, then ``overrides`` is
    applied; an override of ``None`` deletes the variable. That way each test states only
    the one thing it is about, and "one variable is wrong" never silently becomes "thirteen
    variables are wrong".
    """

    def _make(*, env="production", dev_mode="false", **overrides):
        if env is None:
            monkeypatch.delenv("ENV", raising=False)
        else:
            monkeypatch.setenv("ENV", env)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("DEV_MODE", dev_mode)

        for name, value in CONFIGURED.items():
            monkeypatch.setenv(name, value)
        for name, value in overrides.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        return config_module.Settings()

    return _make


# ══════════════════════════════════════════════════════════════════════════════════════════
#  1. THE FOUR VERDICTS
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_a_missing_value_is_missing():
    assert classify_secret(None) is SecretVerdict.MISSING
    assert not secret_is_usable(None)


@pytest.mark.parametrize("value", ["", " ", "\t", "\n", "   \t\n  "])
def test_an_empty_value_is_empty_and_not_merely_missing(value):
    """EMPTY is kept distinct from MISSING on purpose.

    They are different operator mistakes: a missing variable was forgotten, an empty one
    was *set to nothing*, which in a deployed environment almost always means a
    secret-manager reference that resolved to nothing — a broken deployment wearing the
    costume of an unconfigured one. Collapsing them into "falsy" hides the second.
    """
    assert classify_secret(value) is SecretVerdict.EMPTY
    assert not secret_is_usable(value)


@pytest.mark.parametrize("value", NAMED_PLACEHOLDERS)
def test_the_named_placeholders_are_placeholders(value):
    """THE DEFECT, at the exact strings it was reported with.

    ``bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")`` is ``True``. That is the whole finding.
    """
    assert bool(value) is True, "these are all truthy; that is the point"
    assert classify_secret(value) is SecretVerdict.PLACEHOLDER
    assert not secret_is_usable(value)


@pytest.mark.parametrize(
    "value",
    [
        # backend_app/.env — the file that actually wins
        "YOUR_JWT_SECRET_AT_LEAST_64_CHARS",
        "YOUR_FERNET_ENCRYPTION_KEY",
        "https://YOUR_PROJECT_REF.supabase.co",
        "YOUR_SUPABASE_ANON_KEY",
        "YOUR_SUPABASE_JWT_SECRET",
        "postgresql://postgres:YOUR_DB_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:6543/postgres",
        # .env.example — the lower-kebab convention
        "https://your-project-ref.supabase.co",
        "your-supabase-service-role-key-here",
        "your-jwt-secret-at-least-64-chars-long",
        "your-fernet-key-here,old-key-if-rotating",
        "your-unique-per-deployment-salt-here",
        # a prefix is not a credential
        "sk_live_your-stripe-secret-key",
        "whsec_your-stripe-webhook-secret",
        "rzp_live_your-key-id",
        # other conventions
        "CHANGE_ME",
        "changeme",
        "change-this-value",
        "replace_me_before_deploying",
        "<your-service-role-key>",
        "placeholder",
        "TODO",
        "dummy_key",
        "xxxxxxxxxxxx",
        "insert_key_here",
        "notasecret",
        "0000000000000000",
    ],
)
def test_every_placeholder_convention_in_this_repository_is_recognised(value):
    assert classify_secret(value) is SecretVerdict.PLACEHOLDER, (
        "a template placeholder was accepted as a credential"
    )


@pytest.mark.parametrize("name", sorted(CONFIGURED))
def test_a_realistically_shaped_credential_is_accepted(name):
    """"Real configured -> accepted", and the false-positive check for the vocabulary.

    These are provider-shaped strings — a Supabase project URL, two Supabase JWTs, a Fernet
    key, a pooler DSN, a ``sk_live_`` Stripe key, a ``whsec_`` webhook secret. If the
    placeholder vocabulary were too greedy this is where it would show, which is why the
    accepted case uses realistic shapes rather than a short random token.
    """
    assert classify_secret(CONFIGURED[name]) is SecretVerdict.USABLE, (
        f"{name}'s realistic value was misclassified; the placeholder vocabulary is too broad"
    )


def test_usable_is_the_weak_claim_it_says_it_is():
    """This module recognises fakes; it cannot verify a credential against its provider.

    Stated as a test so nobody later reads ``secret_is_usable`` as "authenticated". A
    random string of the right shape is USABLE and authenticates to nothing.
    """
    assert secret_is_usable("Qv2Lm9Kd4Rt6Yw1Bn8Hs3Jc5Pf0Ag")


def test_the_two_vocabularies_do_not_overlap_in_a_way_that_hides_a_word():
    """``PLACEHOLDER_EXACT`` exists for words that occur inside REAL credentials.

    ``key``, ``secret``, ``token``, ``test`` are placeholders only as a whole value. If one
    of them were also a substring pattern it would condemn every credential containing the
    letters, so the split has to hold.
    """
    for word in ("key", "secret", "token", "test", "value", "string"):
        assert word in PLACEHOLDER_EXACT
        assert not any(word == pattern for pattern in PLACEHOLDER_SUBSTRINGS)

    # And the exact list really is exact-only.
    assert classify_secret("servicekey_Qv2Lm9Kd4Rt6Yw1Bn8Hs") is SecretVerdict.USABLE
    assert classify_secret("key") is SecretVerdict.PLACEHOLDER


# ══════════════════════════════════════════════════════════════════════════════════════════
#  2. REQUIRED CREDENTIALS: missing, empty and placeholder are all unfit
# ══════════════════════════════════════════════════════════════════════════════════════════

REQUIRED = config_module.Settings.REQUIRED_DEPLOYED_CREDENTIALS
OPTIONAL = config_module.Settings.PLACEHOLDER_REJECTED_CREDENTIALS


def test_a_fully_configured_production_deployment_is_accepted(deployment):
    s = deployment(env="production")
    assert s.credential_problems() == []
    s.assert_credentials_are_production_grade()   # must not raise


@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize(
    "bad,expected",
    [
        (None, SecretVerdict.MISSING),
        ("", SecretVerdict.EMPTY),
        ("YOUR_SUPABASE_SERVICE_ROLE_KEY", SecretVerdict.PLACEHOLDER),
        ("CHANGE_ME", SecretVerdict.PLACEHOLDER),
        ("dummy_key", SecretVerdict.PLACEHOLDER),
    ],
)
def test_every_required_credential_refuses_every_unfit_value(deployment, name, bad, expected):
    """The matrix the brief asks for: each required variable × each unfit verdict.

    Everything else stays configured, so the refusal names exactly one variable and the
    test cannot pass for the wrong reason.
    """
    s = deployment(env="production", **{name: bad})

    assert s.credential_verdicts()[name] is expected
    problems = s.credential_problems()
    assert len(problems) == 1, problems
    assert name in problems[0]

    with pytest.raises(RuntimeError, match="credentials are not configured"):
        s.assert_credentials_are_production_grade()


@pytest.mark.parametrize("name", OPTIONAL)
def test_an_optional_credential_may_be_absent(deployment, name):
    """These govern features that can be off. MISSING is a configuration choice."""
    s = deployment(env="production", **{name: None})
    assert s.credential_verdicts()[name] is SecretVerdict.MISSING
    assert s.credential_problems() == []
    s.assert_credentials_are_production_grade()   # must not raise


@pytest.mark.parametrize("name", OPTIONAL)
@pytest.mark.parametrize("bad", ["", "CHANGE_ME", "dummy_key", "your-key-here"])
def test_an_optional_credential_may_not_be_a_placeholder_or_empty(deployment, name, bad):
    """Absent is a choice; a template value left in place is not.

    EMPTY is refused here too: an empty value in a deployed environment is a
    secret-manager reference that resolved to nothing, not a decision to disable a feature.
    """
    s = deployment(env="production", **{name: bad})
    problems = s.credential_problems()
    assert len(problems) == 1, problems
    assert name in problems[0]
    with pytest.raises(RuntimeError):
        s.assert_credentials_are_production_grade()


def test_every_problem_is_reported_not_just_the_first(deployment):
    """An operator fixing configuration should not redeploy once per missing variable."""
    s = deployment(
        env="production",
        SUPABASE_SERVICE_ROLE_KEY="YOUR_SUPABASE_SERVICE_ROLE_KEY",
        MASTER_ENCRYPTION_KEYS=None,
        SUPABASE_JWT_SECRET="",
        STRIPE_SECRET_KEY="sk_live_your-stripe-secret-key",
    )
    problems = s.credential_problems()
    assert len(problems) == 4, problems
    for name in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "MASTER_ENCRYPTION_KEYS",
        "SUPABASE_JWT_SECRET",
        "STRIPE_SECRET_KEY",
    ):
        assert any(name in p for p in problems), name


def test_the_stripe_live_prefix_does_not_make_a_placeholder_a_credential(deployment):
    """``billing._validate_keys`` accepts only ``sk_live_``, and the template placeholder
    starts with ``sk_live_``. The prefix check and this check answer different questions,
    and only one of them can tell a key from a template."""
    assert "sk_live_your-stripe-secret-key".startswith("sk_live_")
    s = deployment(env="production", STRIPE_SECRET_KEY="sk_live_your-stripe-secret-key")
    assert s.credential_verdicts()["STRIPE_SECRET_KEY"] is SecretVerdict.PLACEHOLDER
    with pytest.raises(RuntimeError):
        s.assert_credentials_are_production_grade()


# ══════════════════════════════════════════════════════════════════════════════════════════
#  3. PRODUCTION FAILS CLOSED; DEVELOPMENT KEEPS ITS DOCUMENTED BEHAVIOUR
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("env", ["production", "prod", "staging", "", None])
def test_a_deployed_environment_fails_closed(deployment, env):
    """An UNSET or empty ``ENV`` counts as DEPLOYED.

    Same default as ``assert_jwt_secret_is_production_grade``, and for the same reason: the
    safe answer to "is somebody's money behind this?" is yes. This is deliberately the
    OPPOSITE default from the authentication gate, where defaulting to "development" was
    itself the defect (BUG-002 / RUNTIME-01).
    """
    s = deployment(env=env, SUPABASE_SERVICE_ROLE_KEY="YOUR_SUPABASE_SERVICE_ROLE_KEY")
    assert s._is_deployed_environment() is True
    with pytest.raises(RuntimeError):
        s.assert_credentials_are_production_grade()


@pytest.mark.parametrize("env", ["development", "dev", "local", "test", "testing"])
def test_a_development_environment_reports_but_does_not_refuse(deployment, env):
    """Documented local behaviour is retained unchanged.

    The placeholders committed in ``backend_app/.env`` are what a developer actually has,
    and the goal is to stop them reaching customers, not to stop local work. The state is
    still VISIBLE — ``credential_problems()`` still lists it, and ``print_config_status``
    prints it — it is simply not fatal. Reported, not enforced.
    """
    s = deployment(env=env, SUPABASE_SERVICE_ROLE_KEY="YOUR_SUPABASE_SERVICE_ROLE_KEY")

    assert s._is_deployed_environment() is False
    assert s.credential_problems(), "the problem must still be reported locally"
    s.assert_credentials_are_production_grade()   # must not raise


def test_explicit_dev_mode_is_the_single_opt_out(deployment):
    s = deployment(env="production", dev_mode="true", MASTER_ENCRYPTION_KEYS=None)
    assert s._is_deployed_environment() is False
    s.assert_credentials_are_production_grade()   # must not raise


def test_the_suites_own_configuration_still_boots(deployment):
    """Guards against this fix breaking the test lane, which runs with the committed
    placeholders and ``ENV=testing``. If this fails, the fix is too broad."""
    s = deployment(
        env="testing",
        dev_mode="true",
        SUPABASE_URL="https://YOUR_PROJECT_REF.supabase.co",
        SUPABASE_ANON_KEY="YOUR_SUPABASE_ANON_KEY",
        SUPABASE_SERVICE_ROLE_KEY="YOUR_SUPABASE_SERVICE_ROLE_KEY",
        SUPABASE_JWT_SECRET="YOUR_SUPABASE_JWT_SECRET",
        JWT_SECRET="YOUR_JWT_SECRET_AT_LEAST_64_CHARS",
        MASTER_ENCRYPTION_KEYS="YOUR_FERNET_ENCRYPTION_KEY",
    )
    s.assert_credentials_are_production_grade()


# ══════════════════════════════════════════════════════════════════════════════════════════
#  4. THE CHECK MUST NOT LEAK WHAT IT IS CHECKING
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_the_refusal_names_no_part_of_any_value(deployment):
    """A control that handles secrets must not be the thing that discloses them.

    The raised message reaches container logs and the crash reporter, which are exactly the
    places a credential must not appear. Checked against a value that is *recognisably* a
    placeholder but also carries a distinctive fragment, because the dangerous version of
    this message is the one that names the pattern it matched.
    """
    secret = "CHANGE_ME_SUPERSECRETFRAGMENT_0123456789"
    s = deployment(env="production", SUPABASE_SERVICE_ROLE_KEY=secret)

    with pytest.raises(RuntimeError) as caught:
        s.assert_credentials_are_production_grade()

    message = str(caught.value)
    assert "SUPERSECRETFRAGMENT" not in message
    assert secret not in message
    for i in range(len(secret) - 8):
        assert secret[i:i + 9] not in message, (
            "the refusal contains a 9-character fragment of the configured value"
        )
    # It does name the VARIABLE, which is not a secret and is the only actionable part.
    assert "SUPABASE_SERVICE_ROLE_KEY" in message


def test_describe_verdict_cannot_leak_a_value_because_it_never_receives_one():
    """Structural, not textual: the function takes a name and a verdict, not a value."""
    import inspect

    params = list(inspect.signature(describe_verdict).parameters)
    assert params == ["name", "verdict"]

    text = describe_verdict("SUPABASE_SERVICE_ROLE_KEY", SecretVerdict.PLACEHOLDER)
    assert "SUPABASE_SERVICE_ROLE_KEY" in text
    assert "placeholder" in text.lower()


def test_the_refusal_tells_the_operator_how_to_fix_it(deployment):
    s = deployment(env="production", MASTER_ENCRYPTION_KEYS=None)
    with pytest.raises(RuntimeError) as caught:
        s.assert_credentials_are_production_grade()
    message = str(caught.value)
    assert "secret manager" in message
    assert "committed or logged" in message


# ══════════════════════════════════════════════════════════════════════════════════════════
#  5. IT IS ACTUALLY WIRED INTO STARTUP
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_startup_calls_the_assertion_before_binding():
    """A correct check that nothing calls is not a control.

    This repository has produced that exact shape twice already —
    ``safety_config.validate_safety_config_integrity`` (TRADE-06) and
    ``config.validate_production_config``, both uncalled — so the call site is asserted
    rather than assumed. Parsed out of ``main.lifespan`` with ``ast``: the call must exist,
    and it must precede the first service initialisation.
    """
    source = (REPO_ROOT / "backend_app" / "main.py").read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(source)

    lifespan = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "lifespan"
        ),
        None,
    )
    assert lifespan is not None, "main.lifespan not found"

    calls = [
        (n.lineno, n.func.attr)
        for n in ast.walk(lifespan)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    names = [name for _, name in calls]

    assert "assert_credentials_are_production_grade" in names, (
        "startup does not call assert_credentials_are_production_grade, so a deployment "
        "whose service-role key is the string YOUR_SUPABASE_SERVICE_ROLE_KEY still boots"
    )

    check_line = min(
        line for line, name in calls if name == "assert_credentials_are_production_grade"
    )
    recovery = [line for line, name in calls if name == "run_startup_recovery"]
    if recovery:
        assert check_line < min(recovery), (
            "the credential check runs after startup recovery; it must precede any "
            "service initialisation"
        )


def test_validate_production_config_is_still_uncalled_and_says_so():
    """The dead validator is kept as a record, and its docstring must keep saying it is dead.

    If somebody wires it up later this test fails and they update the docstring; if somebody
    deletes the docstring note while it is still uncalled, this test fails too. Either way
    the two cannot drift apart silently.
    """
    called_anywhere = False
    for path in REPO_ROOT.glob("backend_app/**/*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "config.py":
            continue
        if "validate_production_config(" in text:
            called_anywhere = True
            break

    doc = config_module.validate_production_config.__doc__ or ""
    if called_anywhere:
        pytest.fail(
            "validate_production_config now has a call site; update its docstring, which "
            "still says it has none"
        )
    assert "NO CALL SITES" in doc


# ══════════════════════════════════════════════════════════════════════════════════════════
#  6. WHICH .env FILE ACTUALLY WINS
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_the_dotenv_file_that_wins_is_the_one_under_backend_app():
    """Pinned because it is the opposite of what an operator expects.

    ``load_dotenv()`` with no argument walks up from the CALLING MODULE's directory, which
    for ``core/config.py`` is ``backend_app/``. So ``backend_app/.env`` wins and the
    repository-root ``.env`` is never read by this process. A fix applied to the root file
    is inert — which happened during this audit, to a finding that was recorded as closed.

    Not a behaviour change: ``load_dotenv(find_dotenv())`` resolves identically. What
    changed is that the path is now a named constant this test can read and
    ``print_config_status`` can print.
    """
    backend_env = REPO_ROOT / "backend_app" / ".env"
    if not backend_env.exists():
        pytest.skip("backend_app/.env is not present in this working tree")

    resolved = pathlib.Path(config_module.DOTENV_PATH)
    assert resolved.name == ".env"
    assert resolved.parent.name == "backend_app", (
        f"the dotenv file that wins is {resolved}, not backend_app/.env; if this changed "
        f"deliberately, every variable present in only one of the two files changed with it"
    )


def test_no_committed_dotenv_file_contains_a_value_that_would_be_accepted():
    """The "do not commit secrets" requirement, stated as a measurement.

    Every governed credential in ``backend_app/.env`` and ``.env.example`` must classify as
    a PLACEHOLDER. Two things follow, and both are wanted:

      * no real credential is committed in either file (a real one would be USABLE here);
      * the placeholder vocabulary actually covers this repository's own conventions, so a
        future template using a spelling it misses fails this test instead of shipping.
    """
    governed = set(REQUIRED) | set(OPTIONAL)
    checked = 0

    for relative in ("backend_app/.env", ".env.example"):
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            if name not in governed:
                continue
            checked += 1
            assert classify_secret(value.strip()) is SecretVerdict.PLACEHOLDER, (
                f"{relative} sets {name} to a value this check would ACCEPT. Either a real "
                f"credential is committed, or the placeholder vocabulary does not cover "
                f"the convention used here."
            )

    assert checked >= 6, (
        f"only {checked} governed variables were found in the committed templates; the "
        f"parser is probably not reading them"
    )


def test_reloading_config_does_not_change_the_resolved_dotenv_path():
    """``DOTENV_PATH`` is derived from this file's location, not from the cwd.

    ``find_dotenv(usecwd=True)`` would make the answer depend on where pytest was invoked
    from, which is how an environment check becomes order-dependent.
    """
    before = config_module.DOTENV_PATH
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.DOTENV_PATH == before
    finally:
        importlib.reload(config_module)
