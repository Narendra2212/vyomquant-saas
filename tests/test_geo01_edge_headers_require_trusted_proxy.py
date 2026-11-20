"""GEO-01 -- checkout currency/country detection no longer trusts client-controllable
headers by default.

THE DEFECT
    core/country_detection.py's extract_client_ip and detect_country_from_headers
    honoured CF-Connecting-IP, True-Client-IP, X-Real-IP, X-Forwarded-For,
    CloudFront-Viewer-Country and CF-IPCountry UNCONDITIONALLY. Every one of those is a
    plain HTTP header: a caller that reaches this process directly, bypassing whatever
    CDN is supposed to set them (this deployment's own terraform does not describe a
    CloudFront/ALB topology at all -- INF-02), can set any of them to anything and get
    a cheaper region's price list.

THE FIX
    core/client_address.py's SEC-10 fix already answers "is there a real trusted proxy
    chain in front of this process" once, via TRUSTED_PROXY_HOPS (unset by default --
    fail-safe). That module now exports trusted_edge_is_configured(), and
    country_detection.py gates every one of the six headers above on it: unconfigured,
    none of them can change the detected IP or country, and detection falls through to
    the direct socket peer and the deterministic US/USD default.

WHAT THIS FILE PROVES
    1. trusted_edge_is_configured() tracks TRUSTED_PROXY_HOPS exactly the way
       trusted_client_address's own internal hop count already does (unset/invalid/<1 ->
       False).
    2. With TRUSTED_PROXY_HOPS unset, none of the six headers change
       extract_client_ip/detect_country_from_headers's answer.
    3. With TRUSTED_PROXY_HOPS set, the exact same headers are honoured -- a negative
       control proving this is a default, not a removal of the feature.
    4. The one case that actually matters commercially: an attacker who spoofs
       CloudFront-Viewer-Country directly against an unconfigured deployment gets the
       deterministic US/USD fallback, not the country they asked for.

tests/test_billing_localization_e2e.py's own TestIPGeolocationAndProxyHeaders and new
TestEdgeHeadersUntrustedByDefault classes cover the same gate end-to-end through
CountryDetector; this file is the narrower, direct unit coverage of
trusted_edge_is_configured() itself.
"""
from __future__ import annotations

import pytest
from fastapi import Request

from backend_app.core.client_address import trusted_edge_is_configured
from backend_app.core.country_detection import CountryDetector


def _mock_request(client_ip: str, headers: dict | None = None) -> Request:
    header_list = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "client": (client_ip, 12345),
        "headers": header_list,
        "path": "/api/billing/plans",
    })


@pytest.mark.parametrize("value,expected", [
    (None, False),
    ("", False),
    ("0", False),
    ("-1", False),
    ("abc", False),
    ("1", True),
    ("3", True),
])
def test_trusted_edge_is_configured_tracks_trusted_proxy_hops(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    assert trusted_edge_is_configured() is expected


class TestUnconfiguredDeploymentIgnoresEveryEdgeHeader:
    def test_x_forwarded_for(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"X-Forwarded-For": "103.21.124.1"})
        assert CountryDetector.extract_client_ip(req) == "10.0.0.1"

    def test_cf_connecting_ip(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"CF-Connecting-IP": "51.140.0.1"})
        assert CountryDetector.extract_client_ip(req) == "10.0.0.1"

    def test_true_client_ip(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"True-Client-IP": "13.112.0.1"})
        assert CountryDetector.extract_client_ip(req) == "10.0.0.1"

    def test_x_real_ip(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"X-Real-IP": "18.130.0.1"})
        assert CountryDetector.extract_client_ip(req) == "10.0.0.1"

    def test_cloudfront_viewer_country(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"CloudFront-Viewer-Country": "DE"})
        assert CountryDetector.detect_country_from_headers(req) is None

    def test_cf_ipcountry(self, monkeypatch):
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"CF-IPCountry": "FR"})
        assert CountryDetector.detect_country_from_headers(req) is None

    def test_the_named_attack_falls_back_to_the_deterministic_default_not_the_spoofed_country(self, monkeypatch):
        """An attacker hitting the origin directly with a spoofed edge header gets
        US/USD, not the cheaper region they asked for."""
        monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
        req = _mock_request("10.0.0.1", {"CloudFront-Viewer-Country": "IN"})
        client_ip = CountryDetector.extract_client_ip(req)
        country = CountryDetector.detect_country_from_ip(client_ip, req)
        assert country == "US"
        assert CountryDetector.get_country_currency(country) == "USD"


class TestConfiguredDeploymentStillHonoursEveryEdgeHeader:
    """Negative control: TRUSTED_PROXY_HOPS set restores the exact prior behaviour."""

    def test_x_forwarded_for(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
        req = _mock_request("10.0.0.1", {"X-Forwarded-For": "103.21.124.1"})
        assert CountryDetector.extract_client_ip(req) == "103.21.124.1"

    def test_cloudfront_viewer_country(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
        req = _mock_request("10.0.0.1", {"CloudFront-Viewer-Country": "DE"})
        assert CountryDetector.detect_country_from_headers(req) == "DE"
