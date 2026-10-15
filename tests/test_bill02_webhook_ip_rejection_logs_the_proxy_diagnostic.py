"""BILL-02 -- a webhook IP rejection now logs what an operator needs to tell "the proxy
hop count is wrong" from "the provider's IP range changed".

THE DEFECT
    ``_client_ip_behind_proxies`` reads ``X-Forwarded-For`` from the right, trusting
    exactly ``TRUSTED_PROXY_HOPS`` appending proxies (default 1, i.e. an ALB alone). If
    this deployment's real chain has ONE MORE hop than that (a CDN in front of the load
    balancer the code was never told about), the resolved address is the NEARER proxy's
    own address, not the payment provider's - and that address is never in the
    allowlist, however current it is, so EVERY genuine webhook is rejected. The rejection
    log named only the resolved address and the allowlist size, and its own advice
    ("update the IP list") pointed an operator at the wrong fix for this specific cause.

    This repository's own Terraform does not establish whether a CDN actually sits in
    front of this deployment's ALB in production (and a separate finding records that
    this tree does not reliably describe what is actually deployed), so the correct
    ``TRUSTED_PROXY_HOPS`` value cannot be determined or set confidently from here. What
    CAN be fixed from here is the diagnostic: give an operator debugging a rejected
    genuine webhook the exact two facts (the raw header, the configured hop count) that
    let them tell the two causes apart themselves.

THE FIX
    The rejection's ERROR log now also carries the raw ``X-Forwarded-For`` header value
    and the configured ``TRUSTED_PROXY_HOPS``, with a sentence steering an operator to
    check the hop count rather than assume the allowlist is merely stale.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest
from starlette.requests import Request

from backend_app.routers import billing


def _request(forwarded_for: str | None, client_host: str = "203.0.113.9") -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/billing/webhook/stripe",
        "headers": headers,
        "query_string": b"",
        "root_path": "",
        "path_params": {},
        "client": (client_host, 12345),
    }
    return Request(scope)


class TestARejectionNamesTheProxyDiagnostic:
    def test_the_raw_forwarded_for_header_appears_in_the_rejection_log(self, caplog):
        # Simulates a deployment with one more hop than TRUSTED_PROXY_HOPS (default 1)
        # accounts for: the resolved address (second from the right, which the default
        # hop count of 1 reads as the rightmost) is a CDN edge address, never the
        # provider's own, so it will never be in the allowlist no matter how current.
        forwarded_for = "198.51.100.5, 192.0.2.44, 10.0.0.7"
        request = _request(forwarded_for)

        with patch.dict(os.environ, {"ENV": "production"}, clear=False):
            with caplog.at_level(logging.ERROR, logger="BillingRouter"):
                with pytest.raises(Exception):
                    billing._validate_webhook_ip(request, "stripe")

        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert forwarded_for in joined, (
            "the rejection log no longer carries the raw X-Forwarded-For header - an "
            "operator cannot tell a proxy-hop miscount from a stale allowlist without it"
        )

    def test_the_configured_hop_count_appears_in_the_rejection_log(self, caplog, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
        request = _request("198.51.100.5, 192.0.2.44, 10.0.0.7")

        with patch.dict(os.environ, {"ENV": "production"}, clear=False):
            with caplog.at_level(logging.ERROR, logger="BillingRouter"):
                with pytest.raises(Exception):
                    billing._validate_webhook_ip(request, "stripe")

        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "2" in joined and "trusted_proxy_hops" in joined

    def test_a_genuinely_allowed_ip_is_still_accepted_unchanged(self):
        """NEGATIVE CONTROL: the enriched logging only runs on the rejection path - an
        allowed request is not slowed down or altered.
        """
        allowed = billing.STRIPE_WEBHOOK_IPS[0]
        # Single-hop: the rightmost (and only) entry is what the ALB appended, which
        # with the default TRUSTED_PROXY_HOPS=1 is exactly what gets resolved.
        request = _request(allowed)

        with patch.dict(os.environ, {"ENV": "production"}, clear=False):
            billing._validate_webhook_ip(request, "stripe")  # must not raise
