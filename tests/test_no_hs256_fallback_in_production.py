"""
tests/test_no_hs256_fallback_in_production.py

Production accepts ES256 only. The HS256 path is test-only and quadruple-gated, and the
audit finding that treated it as a live fallback was wrong.

WHAT THE AUDIT CLAIMED, TWICE, AND WHY BOTH WERE WRONG
P1-12 was filed as an HS256 shared-secret path accepted alongside JWKS, then downgraded to
P2 on the grounds that ``ENV=production`` is set everywhere. It was later described as
"five live pentest scripts would break" and then, after a correction, as "production
websocket auth across five modules -- removing it is a real change". All three
characterisations were wrong. Reading ``backend_app/core/auth_middleware.py``:

``decode_token_local`` validates against a SERVER-DETERMINED TRUST ROOT::

    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token, signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        options={"verify_exp": True},
    )

``algorithms=["ES256"]`` is fixed by the server, so the token's own unverified ``alg``
header can never select a key or choose an algorithm. The function's docstring cites
CWE-347 algorithm confusion as the thing this shape exists to prevent, and it is correct.

HS256 is reached only when ALL FOUR of these hold:

1. the ES256/JWKS decode above already raised;
2. ``ENV`` is one of ``testing``, ``test``, ``development``, ``dev``, ``local``;
3. the token's ``iss`` is exactly ``"algo22-test"``;
4. the token verifies against the configured secret -- ``options={"verify_signature":
   True}``, not a bypass.

With ``ENV=production`` -- which all four ECS task definitions set -- condition 2 is false,
the branch is unreachable, and the function raises ``InvalidTokenError("Invalid token
signature or algorithm")``. So there is no HS256 acceptance in production to remove, and
the five websocket modules that call ``_decode_hs256_token`` reach this same function:
despite the helper's name, what they get in production is ES256-only verification.

THE ONE UNVERIFIED DECODE, AND WHY IT IS FINE
Inside the env-gated branch::

    unverified_payload = jwt.decode(token, options={"verify_signature": False},
                                    algorithms=["ES256", "HS256"])
    if unverified_payload.get("iss") == "algo22-test":

That reads ``iss`` to decide whether this is a test token, and nothing from it is
trusted -- the token is then re-decoded with ``verify_signature: True`` before any claim is
used. Reading an untrusted claim to route, then verifying before trusting, is the correct
pattern. It is noted here so a reader who greps for ``verify_signature: False`` does not
conclude otherwise.

WHAT THIS FILE IS FOR
Not a fix -- there is nothing to fix. It pins the four gates so the test-only path cannot
widen into production by someone adding an environment to the allow-list, relaxing the
issuer check, or dropping ``algorithms=["ES256"]`` from the primary decode.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core import auth_middleware as am

SOURCE = inspect.getsource(am.decode_token_local)

# ───────────────────────────────────────────────────────────────────────────────
# PRE-LAUNCH AUDIT UPDATE (2026-10-05) — THE GATE MOVED, AND GOT STRONGER
#
# This file's own analysis above is correct on its central point: with
# ``ENV=production`` the HS256 branch is unreachable, and the earlier finding that
# called it a live production fallback was wrong about THE ECS DEPLOYMENT. I verified
# that independently — all the task definitions do set ``ENV=production``.
#
# But the reasoning had one load-bearing gap, and it is the gap that mattered. The gate
# used to be:
#
#     current_env = (os.environ.get("ENV") or getattr(settings, "ENV", "") or "").lower()
#     if current_env in ("testing", "test", "development", "dev", "local"):
#
# ``core/config.py`` declares ``ENV: str = os.getenv("ENV", "development")``. So the
# second operand of that ``or`` carries a DEFAULT of ``"development"`` — a member of the
# allow-list. Condition 2 was false in production only because every deployment
# remembered to set the variable. One new environment, one missed task revision, one
# local run against a production database, and the HS256 path was open with no second
# control behind it. Reproduced during the audit with ``ENV`` and ``ENVIRONMENT`` unset:
# a token signed with ``JWT_SECRET`` carrying ``iss="algo22-test"`` was ACCEPTED with an
# arbitrary ``sub``, ``app_metadata.role="operator"`` and ``aal="aal2"``.
#
# The gate is now ``auth_middleware._test_token_fallback_allowed()``, which reads only
# the real ``ENV``/``ENVIRONMENT`` variables (never ``settings.ENV``), admits only
# ``testing`` and ``test``, and returns ``False`` when unset.
#
# WHAT CHANGED IN THIS FILE: the shape assertions below followed the gate into the
# helper. Not one of them was relaxed — the allow-list check is now stricter, because
# the list it inspects no longer contains ``development``/``dev``/``local``. And
# ``TestTheEnvironmentGateFailsClosed`` is new: it asserts the property this file's
# analysis previously had to ASSUME, namely that an unconfigured environment does not
# open the path.
# ───────────────────────────────────────────────────────────────────────────────

#: The gate itself, which is where the environment allow-list now lives.
GATE_SOURCE = inspect.getsource(am._test_token_fallback_allowed)


def _gate_ast() -> ast.AST:
    """The gate function's node, docstring stripped.

    Docstrings are excluded because this file's assertions are about CODE, and the
    explanations beside each control necessarily name the thing being controlled. Three
    of the four failures that prompted this update were a comment matching a string
    search, not a weakened control.
    """
    import textwrap

    tree = ast.parse(textwrap.dedent(GATE_SOURCE))
    func = tree.body[0]
    if ast.get_docstring(func):
        func.body = func.body[1:]
    return func


def _code_only(source: str) -> str:
    """``source`` with its leading docstring removed, for ordering assertions."""
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    func = tree.body[0]
    if not ast.get_docstring(func):
        return source
    first_statement = func.body[1]
    lines = textwrap.dedent(source).splitlines()
    return "\n".join(lines[first_statement.lineno - 1:])

#: Verbatim from the function. Restated so a change to either is deliberate.
EXPECTED_TEST_ENVS = {"testing", "test", "development", "dev", "local"}

#: The issuer that selects the test path.
TEST_ISSUER = "algo22-test"


def _function_ast():
    import textwrap

    return ast.parse(textwrap.dedent(SOURCE)).body[0]


# ---------------------------------------------------------------------------
# 1. The primary decode is server-determined and ES256-only
# ---------------------------------------------------------------------------


class TestThePrimaryDecodeIsEs256Only:
    def test_the_algorithm_list_is_fixed_to_es256(self):
        """The CWE-347 defence: the server picks the algorithm, not the token.

        Found as the actual keyword argument of the first ``jwt.decode`` call, so a
        second decode elsewhere in the function cannot satisfy it.
        """
        decodes = []
        for node in ast.walk(_function_ast()):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "decode":
                continue
            algorithms = None
            verify_signature = True
            for keyword in node.keywords:
                if keyword.arg == "algorithms" and isinstance(keyword.value, ast.List):
                    algorithms = [
                        element.value
                        for element in keyword.value.elts
                        if isinstance(element, ast.Constant)
                    ]
                if keyword.arg == "options" and isinstance(keyword.value, ast.Dict):
                    for key, value in zip(keyword.value.keys, keyword.value.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "verify_signature"
                            and isinstance(value, ast.Constant)
                        ):
                            verify_signature = value.value
            decodes.append((node.lineno, algorithms, verify_signature))

        verifying = [d for d in decodes if d[2]]
        assert verifying, "no signature-verifying decode found at all"

        primary = verifying[0]
        assert primary[1] == ["ES256"], (
            f"the first verifying decode now accepts {primary[1]}; accepting HS256 "
            f"against a server-selected key is the algorithm-confusion shape this "
            f"function was built to prevent"
        )

    def test_no_verifying_decode_accepts_hs256_without_an_environment_gate(self):
        """An HS256 decode may exist, but only inside the test-environment branch."""
        hs256_lines = [
            node.lineno
            for node in ast.walk(_function_ast())
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "decode"
            and any(
                keyword.arg == "algorithms"
                and isinstance(keyword.value, ast.List)
                and any(
                    isinstance(element, ast.Constant) and element.value == "HS256"
                    for element in keyword.value.elts
                )
                for keyword in node.keywords
            )
        ]
        if not hs256_lines:
            return  # nothing to gate

        # The gate used to be inline (`current_env = ...`) and is now the dedicated
        # `_test_token_fallback_allowed()`. What must hold is that the HS256 branch is
        # reached only through a gate, not that the gate is written in any one place —
        # so this asserts the CALL, which is the thing that cannot be removed without
        # removing the gate.
        gated = any(
            getattr(node.func, "id", None) == "_test_token_fallback_allowed"
            for node in ast.walk(_function_ast())
            if isinstance(node, ast.Call)
        )
        assert gated, (
            "an HS256 decode exists but decode_token_local no longer calls the "
            "environment gate, so the test-only path is not gated"
        )


# ---------------------------------------------------------------------------
# 2. The four gates on the HS256 path
# ---------------------------------------------------------------------------


class TestTheHs256PathIsQuadrupleGated:
    def test_it_is_behind_an_environment_allow_list(self):
        """Asserted on the gate's membership test rather than on a variable name.

        The previous form looked for the literal text ``"current_env in ("`` inside
        ``decode_token_local``. That pinned a spelling, not a control: the gate now
        lives in ``_test_token_fallback_allowed`` and the branch calls it.
        """
        has_membership_test = any(
            isinstance(op, (ast.In, ast.NotIn))
            for node in ast.walk(_gate_ast())
            if isinstance(node, ast.Compare)
            for op in node.ops
        )
        assert has_membership_test, (
            "the HS256 path is no longer gated on an environment allow-list"
        )
        assert any(
            getattr(node.func, "id", None) == "_test_token_fallback_allowed"
            for node in ast.walk(_function_ast())
            if isinstance(node, ast.Call)
        ), "decode_token_local no longer consults the gate"

    def test_the_allow_list_excludes_production(self):
        """The whole basis for saying there is nothing to remove.

        STRICTER THAN BEFORE. The allow-list was
        ``{"testing","test","development","dev","local"}`` and is now
        ``{"testing","test"}``. ``development`` had to go: ``settings.ENV`` DEFAULTS to
        it, so an unset ``ENV`` resolved into the allow-list and the gate opened in a
        deployment nobody had configured. See the note at the top of this file.
        """
        literals = {
            element.value
            for node in ast.walk(ast.parse(inspect.getsource(am)))
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_TEST_TOKEN_ENVS" for t in node.targets
            )
            for element in ast.walk(node.value)
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }

        assert literals, "the _TEST_TOKEN_ENVS allow-list could not be found"
        assert literals <= {"testing", "test"}, (
            f"the test-environment allow-list widened; found {sorted(literals)}"
        )
        # `development`, `dev` and `local` are forbidden alongside the deployed names,
        # and `development` is the important one: it is the DEFAULT value of
        # `settings.ENV`, so admitting it opens the HS256 path on any deployment that
        # never set ENV at all.
        for forbidden in ("production", "prod", "staging", "live",
                          "development", "dev", "local"):
            assert forbidden not in literals, (
                f"{forbidden!r} was added to the environment allow-list, which would "
                f"open the HS256 path in a deployed or unconfigured environment"
            )

    def test_it_is_gated_on_the_test_issuer(self):
        assert TEST_ISSUER in SOURCE, (
            "the HS256 path no longer requires iss == 'algo22-test', so any token "
            "reaching the fallback could use it"
        )

    def test_the_hs256_decode_verifies_the_signature(self):
        """A test-only path still may not accept an unsigned token."""
        assert '"verify_signature": True' in SOURCE, (
            "the HS256 decode no longer asserts verify_signature: True"
        )

    def test_it_fails_closed_without_a_secret(self):
        assert "Test mode requires a configured secret" in SOURCE, (
            "the HS256 path no longer refuses when no secret is configured"
        )

    def test_the_unverified_decode_is_only_used_to_read_the_issuer(self):
        """Noted so a reader grepping for the bypass does not misread it.

        The unverified decode exists to inspect ``iss`` and route; the token is
        re-decoded with verification before any claim is trusted.
        """
        # Ordering is checked against the CODE, with the docstring removed. The
        # docstring now names `iss="algo22-test"` while explaining where the gate lives,
        # which placed the issuer string ahead of the unverified decode and broke this
        # assertion on prose rather than on behaviour.
        code = _code_only(SOURCE)

        assert '"verify_signature": False' in code
        bypass = code.find('"verify_signature": False')
        issuer_check = code.find(TEST_ISSUER)
        verified = code.find('"verify_signature": True')

        assert bypass < issuer_check < verified, (
            "the unverified decode, the issuer check and the verifying decode are no "
            "longer in that order, so the bypass may no longer be confined to routing"
        )


# ---------------------------------------------------------------------------
# 2b. The gate fails closed  (added by the pre-launch audit, 2026-10-05)
# ---------------------------------------------------------------------------


class TestTheEnvironmentGateFailsClosed:
    """The property this file's analysis previously had to assume.

    The argument at the top — "with ``ENV=production`` the branch is unreachable, so
    there is nothing to remove" — was sound about the ECS deployment and is still true.
    What it could not assert was the case where ``ENV`` is absent. That case used to
    resolve, via ``settings.ENV``'s ``"development"`` default, INTO the allow-list.

    So the old gate was safe only while every deployment remembered one environment
    variable, with nothing behind it. These tests make "nobody configured this" a
    refusal, which is what turns the file's conclusion from a configuration observation
    into a property of the code.
    """

    def test_an_unset_environment_does_not_open_the_path(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        assert am._test_token_fallback_allowed() is False, (
            "with ENV unset the HS256 path is open; settings.ENV defaults to "
            "'development', so an unconfigured deployment has no authentication"
        )

    def test_a_blank_environment_does_not_open_the_path(self, monkeypatch):
        """``ENV=""`` is a real state — an empty value in a task definition."""
        monkeypatch.setenv("ENV", "  ")
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        assert am._test_token_fallback_allowed() is False

    @pytest.mark.parametrize(
        "env", ["production", "prod", "staging", "live", "development", "dev", "local"]
    )
    def test_no_deployed_or_developer_environment_opens_the_path(self, monkeypatch, env):
        monkeypatch.setenv("ENV", env)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        assert am._test_token_fallback_allowed() is False

    @pytest.mark.parametrize("env", ["testing", "test"])
    def test_only_an_explicit_test_environment_opens_the_path(self, monkeypatch, env):
        """``tests/conftest.py`` sets ``ENV=testing``, so the suite keeps working."""
        monkeypatch.setenv("ENV", env)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        assert am._test_token_fallback_allowed() is True

    def test_the_gate_never_reads_the_settings_object(self):
        """The root cause, pinned. ``settings.ENV`` carries a ``"development"`` default
        that is indistinguishable from a deliberate setting."""
        names = {
            node.id
            for node in ast.walk(_gate_ast())
            if isinstance(node, ast.Name)
        }

        assert "settings" not in names, (
            "the gate reads settings.ENV again; its default is 'development', which is "
            "how an unconfigured production deployment ended up admitting self-signed "
            "tokens"
        )


# ---------------------------------------------------------------------------
# 3. Behaviour, not just shape
# ---------------------------------------------------------------------------


class TestProductionRefusesAnHs256Token:
    def _hs256_token(self, secret="a-test-secret", issuer=TEST_ISSUER):
        import jwt

        return jwt.encode(
            {
                "sub": "11111111-1111-4111-8111-111111111111",
                "iss": issuer,
                "aud": "authenticated",
                "exp": 9_999_999_999,
            },
            secret,
            algorithm="HS256",
        )

    def test_a_test_issued_hs256_token_is_refused_under_production(self, monkeypatch):
        """The measurement. ES256/JWKS will fail for an HS256 token, and the fallback
        must then be closed because the environment is production.
        """
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-test-secret")

        with pytest.raises(Exception) as caught:
            am.decode_token_local(self._hs256_token())

        assert "Invalid token" in str(caught.value) or "signature" in str(
            caught.value
        ).lower(), f"unexpected refusal: {caught.value!r}"

    def test_a_non_test_issuer_is_refused_even_in_a_test_environment(self, monkeypatch):
        """The issuer gate, exercised rather than read."""
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-test-secret")

        with pytest.raises(Exception):
            am.decode_token_local(self._hs256_token(issuer="someone-else"))

    def test_a_wrongly_signed_test_token_is_refused_in_a_test_environment(
        self, monkeypatch
    ):
        """Even on the test path the signature has to be right."""
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "the-real-secret")

        with pytest.raises(Exception):
            am.decode_token_local(self._hs256_token(secret="not-the-secret"))


# ---------------------------------------------------------------------------
# 4. The websocket helper reaches this same function
# ---------------------------------------------------------------------------


def test_the_websocket_helper_delegates_here_despite_its_name():
    """``_decode_hs256_token`` is a thin wrapper, which is why its name misleads.

    Five websocket modules call it. What they get in production is the ES256-only path
    above, not an HS256 one.
    """
    from backend_app.core import websocket_auth as wa

    source = inspect.getsource(wa._decode_hs256_token)

    assert "decode_token_local" in source, (
        "_decode_hs256_token no longer delegates to decode_token_local, so the "
        "websocket path may have acquired its own verification -- re-check this file"
    )
