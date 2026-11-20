"""SEC-10: rate limits key on the address a trusted proxy vouched for, not the shared peer."""
from types import SimpleNamespace

import pytest

from backend_app.core.client_address import trusted_client_address
from backend_app.core import rate_limit


def _req(peer="10.0.0.9", xff=None):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_limiter_uses_the_trusted_key_function():
    assert rate_limit.limiter._key_func is trusted_client_address


def test_two_users_behind_cloudfront_and_alb_get_distinct_keys(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    a = trusted_client_address(_req(xff="203.0.113.5, 130.176.1.1"))   # client, cloudfront
    b = trusted_client_address(_req(xff="198.51.100.7, 130.176.1.1"))
    assert (a, b) == ("203.0.113.5", "198.51.100.7")


def test_a_spoofed_left_entry_cannot_choose_the_key(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    # attacker prepends a fake address; the trusted slot (2 from the right) is still theirs
    assert trusted_client_address(_req(xff="1.2.3.4, 203.0.113.5, 130.176.1.1")) == "203.0.113.5"


def test_unset_hops_ignores_the_header_entirely(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    assert trusted_client_address(_req(peer="10.0.0.9", xff="1.2.3.4")) == "10.0.0.9"


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
def test_invalid_hops_ignore_the_header(monkeypatch, bad):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", bad)
    assert trusted_client_address(_req(xff="1.2.3.4")) == "10.0.0.9"


def test_chain_shorter_than_hops_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    assert trusted_client_address(_req(xff="1.2.3.4")) == "10.0.0.9"


def test_never_raises_on_a_malformed_request():
    assert trusted_client_address(object()) == "unknown"
