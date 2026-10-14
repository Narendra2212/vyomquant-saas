"""EXCH-02: inspect what a submitted exchange API key is allowed to do.

A trading key that can also WITHDRAW turns any compromise of this platform (or of the
customer's session) into direct theft of funds. A trading platform never needs withdrawal
rights, so keys that carry them are refused at storage time. IP allowlisting is reported so
the customer can be told to enable it.

Only venues whose permission endpoint is implemented are inspected. For every other venue,
or when the introspection call fails, the result is ``verified=False`` (UNKNOWN) - never
"safe". Set ``EXCHANGE_KEY_PERMISSIONS_STRICT=true`` to refuse unverifiable keys outright
(recommended for live-trading deployments).

Read-only: this performs one authenticated GET on the venue; it never places or cancels
anything and never logs the key material.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyPermissionReport:
    verified: bool                          # True only when the venue answered and we parsed it
    withdraw_enabled: Optional[bool]        # None = unknown
    ip_restricted: Optional[bool]           # None = unknown / not reported by venue
    venue: str = ""

    @property
    def refuse_reason(self) -> Optional[str]:
        if self.withdraw_enabled:
            return "WITHDRAW_ENABLED"
        if not self.verified and strict_mode():
            return "PERMISSIONS_UNVERIFIABLE"
        return None


def strict_mode() -> bool:
    return os.environ.get("EXCHANGE_KEY_PERMISSIONS_STRICT", "").strip().lower() in ("1", "true", "yes")


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def parse_binance(resp: dict) -> KeyPermissionReport:
    # GET /sapi/v1/account/apiRestrictions -> {"ipRestrict": bool, "enableWithdrawals": bool, ...}
    if not isinstance(resp, dict) or "enableWithdrawals" not in resp:
        return KeyPermissionReport(False, None, None, "binance")
    return KeyPermissionReport(True, _truthy(resp["enableWithdrawals"]),
                               _truthy(resp["ipRestrict"]) if "ipRestrict" in resp else None, "binance")


def parse_okx(resp: dict) -> KeyPermissionReport:
    # GET /api/v5/account/config -> data[0].perm : "read_only" | "trade" | "withdraw" (comma separated)
    try:
        row = (resp or {}).get("data", [])[0]
        perm = str(row["perm"]).lower()
    except Exception:  # noqa: BLE001
        return KeyPermissionReport(False, None, None, "okx")
    ip = row.get("ip")
    return KeyPermissionReport(True, "withdraw" in {p.strip() for p in perm.split(",")},
                               bool(ip) if ip is not None else None, "okx")


def parse_bybit(resp: dict) -> KeyPermissionReport:
    # GET /v5/user/query-api -> result.permissions.Wallet : ["AccountTransfer", ..., "Withdraw"]
    try:
        result = resp["result"]
        wallet = result["permissions"].get("Wallet") or []
    except Exception:  # noqa: BLE001
        return KeyPermissionReport(False, None, None, "bybit")
    ips = result.get("ips")
    restricted = None if ips is None else (bool(ips) and ips != ["*"])
    return KeyPermissionReport(True, any("withdraw" in str(w).lower() for w in wallet), restricted, "bybit")


async def inspect_key_permissions(exchange: Any, exchange_id: str) -> KeyPermissionReport:
    """Ask the venue what this (already connected) ccxt client's key may do. Never raises."""
    ex = (exchange_id or "").lower()
    try:
        if ex in ("binance", "binanceus", "binanceusdm", "binancecoinm"):
            return parse_binance(await exchange.sapiGetAccountApiRestrictions())
        if ex == "okx":
            return parse_okx(await exchange.privateGetAccountConfig())
        if ex == "bybit":
            return parse_bybit(await exchange.privateGetV5UserQueryApi())
    except Exception as e:  # noqa: BLE001
        logger.warning("EXCH-02: permission introspection failed for %s (%s)", ex, type(e).__name__)
        return KeyPermissionReport(False, None, None, ex)
    return KeyPermissionReport(False, None, None, ex)
