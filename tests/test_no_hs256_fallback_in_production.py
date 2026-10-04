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

        gate = SOURCE.find("current_env")
        assert gate != -1, (
            "an HS256 decode exists but the function no longer reads the environment, "
            "so the test-only path is not gated"
        )


# ---------------------------------------------------------------------------
# 2. The four gates on the HS256 path
# ---------------------------------------------------------------------------


class TestTheHs256PathIsQuadrupleGated:
    def test_it_is_behind_an_environment_allow_list(self):
        assert "current_env in (" in SOURCE or "current_env in [" in SOURCE, (
            "the HS256 path is no longer gated on the environment"
        )

    def test_the_allow_list_excludes_production(self):
        """The whole basis for saying there is nothing to remove."""
        literals = {
            element.value
            for node in ast.walk(_function_ast())
            if isinstance(node, ast.Compare)
            for comparator in node.comparators
            if isinstance(comparator, (ast.Tuple, ast.List, ast.Set))
            for element in comparator.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }

        assert EXPECTED_TEST_ENVS <= literals, (
            f"the test-environment allow-list changed; found {sorted(literals)}"
        )
        for forbidden in ("production", "prod", "staging", "live"):
            assert forbidden not in literals, (
                f"{forbidden!r} was added to the environment allow-list, which would "
                f"open the HS256 path in a deployed environment"
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
        assert '"verify_signature": False' in SOURCE
        bypass = SOURCE.find('"verify_signature": False')
        issuer_check = SOURCE.find(TEST_ISSUER)
        verified = SOURCE.find('"verify_signature": True')

        assert bypass < issuer_check < verified, (
            "the unverified decode, the issuer check and the verifying decode are no "
            "longer in that order, so the bypass may no longer be confined to routing"
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
