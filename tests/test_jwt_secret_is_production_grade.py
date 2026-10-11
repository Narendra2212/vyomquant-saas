"""A deployed environment must refuse to boot on an unfit JWT secret.

WHAT THIS GUARDS  (BUG-012)
---------------------------
``Settings.validate_required`` checked ``JWT_SECRET``'s strength with two
``logger.warning`` calls and then started the application anyway:

    if len(self.JWT_SECRET) < 32:
        logger.warning(f"SECURITY: JWT_SECRET is only {len(...)} characters. ...")
    if any(pattern in self.JWT_SECRET.lower() for pattern in weak_patterns):
        logger.warning("SECURITY: JWT_SECRET contains a weak pattern. ...")

The secret this deployment actually had was **24 bytes** — measured during the
pre-launch audit, and below RFC 7518 §3.2's 256-bit minimum for HS256. PyJWT itself
emits ``InsecureKeyLengthWarning`` for it. It shipped on a log line nobody reads at boot.

Why a hard failure is the right severity rather than a louder warning: ``JWT_SECRET`` is
the HMAC key, so its length is the floor on the cost of forging a token — and the audit
found a path where a token signed with it was accepted as proof of identity for an
arbitrary ``sub`` and an arbitrary ``app_metadata.role``. That path is closed now
(``auth_middleware._test_token_fallback_allowed``), which is why this is P1 and not P0,
but the key remains in HMAC use. A deployment that cannot authenticate safely should not
accept traffic.

**No test here asserts on a secret's value, and the implementation must not put one in
its error message.** ``test_the_error_names_no_part_of_the_secret`` enforces that: a
check that handles a secret must not be the thing that leaks it.
"""

import importlib

import pytest


@pytest.fixture
def settings_for(monkeypatch):
    """A factory returning a freshly-constructed Settings with a given environment.

    ``Settings`` reads ``os.getenv`` in its class body, so the module must be reloaded
    after the environment changes or the old values persist.
    """

    def _make(secret, *, env="production", dev_mode="false"):
        if secret is None:
            monkeypatch.delenv("JWT_SECRET", raising=False)
        else:
            monkeypatch.setenv("JWT_SECRET", secret)
        monkeypatch.setenv("ENV", env)
        monkeypatch.setenv("DEV_MODE", dev_mode)

        module = importlib.import_module("backend_app.core.config")
        importlib.reload(module)
        return module.Settings()

    return _make


#: 48 url-safe random bytes, generated for this test file only. Not a real secret and
#: never used to sign anything outside this process.
STRONG = "Zx7Qv2Lm9Kd4Rt6Yw1Bn8Hs3Jc5Pf0Ag_Ue-Vi2Ol4Nq6Mz8Xy"


# ═══════════════════════════════════════════════════════════════════════════
# 1. The length requirement
# ═══════════════════════════════════════════════════════════════════════════

def test_a_24_byte_secret_is_rejected_in_production(settings_for):
    """THE DEFECT, at the exact length this deployment had."""
    s = settings_for("a" * 24)

    problems = s.jwt_secret_problems()
    assert problems, "a 24-byte secret was accepted"
    assert any("24 bytes" in p for p in problems)
    assert any("RFC 7518" in p for p in problems)

    with pytest.raises(RuntimeError, match="not fit for a deployed environment"):
        s.assert_jwt_secret_is_production_grade()


@pytest.mark.parametrize("n", [1, 8, 16, 24, 31])
def test_every_secret_below_32_bytes_is_rejected(settings_for, n):
    s = settings_for("Qx7" * 40)       # placeholder, replaced below
    s.JWT_SECRET = "".join(chr(0x41 + (i % 26)) for i in range(n))
    assert s.jwt_secret_problems(), f"a {n}-byte secret was accepted"


def test_exactly_32_bytes_is_accepted(settings_for):
    """The boundary belongs to the operator: 32 is the stated minimum, not 33."""
    s = settings_for(STRONG)
    s.JWT_SECRET = "Ag7Kd2Lm9Qv4Rt6Yw1Bn8Hs3Jc5Pf0X"  # 31
    assert s.jwt_secret_problems(), "31 bytes should be refused"

    s.JWT_SECRET = "Ag7Kd2Lm9Qv4Rt6Yw1Bn8Hs3Jc5Pf0Xz"  # 32
    assert s.jwt_secret_problems() == [], (
        f"a 32-byte secret was refused: {s.jwt_secret_problems()}"
    )


def test_length_is_measured_in_bytes_not_characters(settings_for):
    """RFC 7518's requirement is on key material. A 20-character string of 2-byte
    characters is 40 bytes of key; a character count would call it too short, and a
    byte count is never more lenient than a character count."""
    s = settings_for(STRONG)
    s.JWT_SECRET = "é" * 20          # 20 characters, 40 bytes
    assert len(s.JWT_SECRET) == 20
    assert len(s.JWT_SECRET.encode("utf-8")) == 40

    problems = [p for p in s.jwt_secret_problems() if "bytes" in p]
    assert problems == [], "length was measured in characters rather than bytes"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Placeholder detection
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "secret",
    [
        "dev-secret-change-in-production-padding-to-length",
        "test-secret-value-long-enough-to-pass-the-length-check",
        "changeme-changeme-changeme-changeme-changeme",
        "my-default-application-signing-key-value-here",
        "correct-horse-battery-password-staple-long-enough",
        "placeholder-value-replace-before-deploying-this",
        "insecure-but-long-enough-to-satisfy-the-byte-check",
    ],
)
def test_a_long_placeholder_is_still_rejected(settings_for, secret):
    """Length alone is not strength. These all exceed 32 bytes and are all unfit."""
    s = settings_for(secret)
    problems = s.jwt_secret_problems()
    assert any("placeholder pattern" in p for p in problems), (
        f"a placeholder secret passed the check; problems were {problems}"
    )


def test_a_low_entropy_secret_is_rejected(settings_for):
    """A 40-byte secret of three distinct characters is not a generated value."""
    s = settings_for("ababababababababababababababababababababab")
    problems = s.jwt_secret_problems()
    assert any("distinct characters" in p for p in problems), problems


def test_a_strong_secret_passes(settings_for):
    s = settings_for(STRONG)
    assert s.jwt_secret_problems() == []
    s.assert_jwt_secret_is_production_grade()   # must not raise


# ═══════════════════════════════════════════════════════════════════════════
# 3. Which environments enforce it
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("env", ["production", "staging", "prod", ""])
def test_a_deployed_environment_enforces_the_requirement(settings_for, env):
    """An UNSET env is treated as deployed. That is the opposite default from the
    authentication gate, and deliberately so: there, defaulting to "development" opened
    a bypass; here the safe answer to "is somebody's money behind this?" is yes."""
    s = settings_for("a" * 24, env=env)
    with pytest.raises(RuntimeError):
        s.assert_jwt_secret_is_production_grade()


@pytest.mark.parametrize("env", ["development", "dev", "local", "test", "testing"])
def test_a_developer_environment_does_not_enforce_it(settings_for, env):
    """The goal is to stop a weak secret reaching customers, not to make local work
    harder. ``tests/conftest.py`` sets a short secret and ``ENV=testing``."""
    s = settings_for("dev-secret-change-in-production", env=env)
    s.assert_jwt_secret_is_production_grade()   # must not raise


def test_explicit_dev_mode_does_not_enforce_it(settings_for):
    s = settings_for("short", env="production", dev_mode="true")
    s.assert_jwt_secret_is_production_grade()   # must not raise


def test_the_suite_s_own_configuration_still_boots(settings_for):
    """Guards against this fix breaking the test lane, which sets a weak secret on
    purpose. If this fails, the fix is too broad."""
    s = settings_for("dev-secret-change-in-production", env="testing", dev_mode="true")
    s.assert_jwt_secret_is_production_grade()


# ═══════════════════════════════════════════════════════════════════════════
# 4. The check must not leak what it is checking
# ═══════════════════════════════════════════════════════════════════════════

def test_the_error_names_no_part_of_the_secret(settings_for):
    """A control that handles a secret must not be the thing that discloses it.

    The raised message reaches container logs and crash reporters, which are exactly
    the places a secret must not appear.
    """
    secret = "dev-secret-SUPERSECRETFRAGMENT-abcdef"
    s = settings_for(secret)

    with pytest.raises(RuntimeError) as caught:
        s.assert_jwt_secret_is_production_grade()

    message = str(caught.value)
    assert "SUPERSECRETFRAGMENT" not in message
    assert secret not in message
    # Nor any run of the secret long enough to be useful.
    for i in range(len(secret) - 8):
        assert secret[i:i + 9] not in message, (
            f"the error message contains a 9-character fragment of the secret"
        )


def test_the_problem_list_names_no_part_of_the_secret(settings_for):
    secret = "changeme-ANOTHERFRAGMENT-0123456789abcdef"
    s = settings_for(secret)

    text = " ".join(s.jwt_secret_problems())
    assert "ANOTHERFRAGMENT" not in text
    assert secret not in text


def test_the_error_tells_the_operator_how_to_fix_it(settings_for):
    """A refusal that stops a deployment must say what to do about it."""
    s = settings_for("a" * 24)

    with pytest.raises(RuntimeError) as caught:
        s.assert_jwt_secret_is_production_grade()

    message = str(caught.value)
    assert "secrets.token_urlsafe" in message
    assert "secret manager" in message
    assert "never be committed or logged" in message


# ═══════════════════════════════════════════════════════════════════════════
# 5. It is actually wired into startup
# ═══════════════════════════════════════════════════════════════════════════

def test_startup_calls_the_assertion_before_binding(settings_for):
    """A correct check that nothing calls is not a control.

    Asserted by parsing ``main.lifespan``: the call must be present, and it must come
    before the service initialisation that follows it.
    """
    import ast
    import pathlib

    source = pathlib.Path("backend_app/main.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)

    lifespan = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lifespan":
            lifespan = node
            break
    assert lifespan is not None, "main.lifespan not found"

    calls = [
        (n.lineno, n.func.attr)
        for n in ast.walk(lifespan)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    names = [c[1] for c in calls]

    assert "assert_jwt_secret_is_production_grade" in names, (
        "startup does not call assert_jwt_secret_is_production_grade, so an unfit "
        "secret still boots"
    )

    jwt_line = min(l for l, a in calls if a == "assert_jwt_secret_is_production_grade")
    recovery_lines = [l for l, a in calls if a == "run_startup_recovery"]
    if recovery_lines:
        assert jwt_line < min(recovery_lines), (
            "the JWT secret check runs after startup recovery; it must precede any "
            "service initialisation"
        )
