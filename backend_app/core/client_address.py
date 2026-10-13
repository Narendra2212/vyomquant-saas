"""Trusted client-address resolution for rate limiting (SEC-10).

Behind CloudFront and an ALB the socket peer is a shared edge/ALB address, so keying a rate
limit on it puts every user in one bucket. ``X-Forwarded-For`` is the only place the real
caller appears, but its LEFT side is attacker-authored. This reads it from the RIGHT: with
``TRUSTED_PROXY_HOPS=N`` appending proxies in front of the process, the entry N from the right
is the first address a trusted component vouched for.

Fail-safe defaults:
  * ``TRUSTED_PROXY_HOPS`` unset/invalid/<1 -> the header is NOT trusted; the socket peer is
    used (never spoofable, but shared behind a proxy - set the variable per deployment).
  * a chain shorter than the hop count -> the socket peer (never a caller-controlled slot).
Never raises: a key function that raises turns a 429 into a 500.
"""
from __future__ import annotations

import os
from typing import Any

from slowapi.util import get_remote_address


def _hops() -> int:
    raw = os.environ.get("TRUSTED_PROXY_HOPS")
    if raw is None or not str(raw).strip():
        return 0
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return value if value >= 1 else 0


def trusted_client_address(request: Any) -> str:
    try:
        peer = get_remote_address(request)
        hops = _hops()
        if hops:
            header = request.headers.get("x-forwarded-for")
            if header:
                entries = [p.strip() for p in header.split(",") if p.strip()]
                index = len(entries) - hops
                if entries and index >= 0:
                    return entries[index]
        return peer
    except Exception:  # noqa: BLE001 - a key function must not raise
        return "unknown"
