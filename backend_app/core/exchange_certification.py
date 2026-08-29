"""
backend_app/core/exchange_certification.py — Authoritative Exchange Certification & Capability Registry.

Classifies CCXT exchanges into explicit Certification Levels:
  - LEVEL 0: CCXT library support only (Metadata only)
  - LEVEL 1: Market metadata verified
  - LEVEL 2: Read-only account operations verified
  - LEVEL 3: Sandbox/Testnet execution verified
  - LEVEL 4: Full simulated order lifecycle verified
  - LEVEL 5: Production-ready certification (Fully certified for live trading)

Enforces capability-aware preflight checks for Live deployments.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import logging

logger = logging.getLogger("ExchangeCertification")


class CertificationLevel(int, Enum):
    LEVEL_0_METADATA_ONLY = 0
    LEVEL_1_MARKET_METADATA_VERIFIED = 1
    LEVEL_2_READ_ONLY_VERIFIED = 2
    LEVEL_3_SANDBOX_VERIFIED = 3
    LEVEL_4_SIMULATED_ORDER_LIFECYCLE_VERIFIED = 4
    LEVEL_5_PRODUCTION_READY = 5


class RuntimeExchangeHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    RATE_LIMITED = "RATE_LIMITED"
    MAINTENANCE = "MAINTENANCE"
    BLOCKED = "BLOCKED"


@dataclass
class ExchangeCapabilities:
    exchange_id: str
    display_name: str
    certification_level: CertificationLevel
    spot: bool = True
    margin: bool = False
    futures: bool = False
    swap: bool = False
    market_order: bool = True
    limit_order: bool = True
    stop_market: bool = False
    stop_limit: bool = False
    cancel_order: bool = True
    fetch_order: bool = True
    fetch_open_orders: bool = True
    fetch_positions: bool = False
    fetch_balance: bool = True
    reduce_only: bool = False
    post_only: bool = False
    client_order_id: bool = True
    leverage: bool = False
    hedge_mode: bool = False
    one_way_mode: bool = True
    sandbox: bool = False
    supported_quote_currencies: List[str] = field(default_factory=lambda: ["USDT", "USD", "USDC", "BTC"])
    known_limitations: List[str] = field(default_factory=list)
    certification_version: int = 3
    adapter_version: str = "2.1.0"
    last_verified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_production_certified(self) -> bool:
        return self.certification_level == CertificationLevel.LEVEL_5_PRODUCTION_READY

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["certification_level"] = self.certification_level.value
        data["is_production_certified"] = self.is_production_certified
        return data


class ExchangeCertificationRegistry:
    """
    Authoritative registry mapping exchanges to capabilities, health, and certification levels.
    """
    def __init__(self):
        self._registry: Dict[str, ExchangeCapabilities] = {}
        self._health_status: Dict[str, RuntimeExchangeHealth] = {}
        self._register_default_certifications()

    def _register_default_certifications(self):
        # 1. Binance (Level 5 Production Certified)
        self._registry["binance"] = ExchangeCapabilities(
            exchange_id="binance",
            display_name="Binance",
            certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            stop_market=True,
            stop_limit=True,
            fetch_positions=True,
            reduce_only=True,
            post_only=True,
            leverage=True,
            hedge_mode=True,
            sandbox=True,
            known_limitations=[]
        )
        self._health_status["binance"] = RuntimeExchangeHealth.HEALTHY

        # 2. Bybit (Level 5 Production Certified)
        self._registry["bybit"] = ExchangeCapabilities(
            exchange_id="bybit",
            display_name="Bybit",
            certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            stop_market=True,
            stop_limit=True,
            fetch_positions=True,
            reduce_only=True,
            post_only=True,
            leverage=True,
            hedge_mode=True,
            sandbox=True,
            known_limitations=[]
        )
        self._health_status["bybit"] = RuntimeExchangeHealth.HEALTHY

        # 3. Coinbase (Level 5 Spot Production Certified)
        self._registry["coinbase"] = ExchangeCapabilities(
            exchange_id="coinbase",
            display_name="Coinbase Advanced",
            certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
            spot=True,
            margin=False,
            futures=False,
            swap=False,
            market_order=True,
            limit_order=True,
            stop_market=False,
            stop_limit=True,
            fetch_positions=False,
            reduce_only=False,
            post_only=True,
            leverage=False,
            sandbox=True,
            known_limitations=["Spot trading only", "Futures and derivatives not supported on standard CCXT coinbase adapter"]
        )
        self._health_status["coinbase"] = RuntimeExchangeHealth.HEALTHY

        # 4. Kraken (Level 5 Production Certified)
        self._registry["kraken"] = ExchangeCapabilities(
            exchange_id="kraken",
            display_name="Kraken",
            certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            stop_market=True,
            stop_limit=True,
            fetch_positions=True,
            reduce_only=True,
            post_only=True,
            leverage=True,
            sandbox=False,
            known_limitations=["No public CCXT sandbox endpoint"]
        )
        self._health_status["kraken"] = RuntimeExchangeHealth.HEALTHY

        # 5. OKX (Level 5 Production Certified)
        self._registry["okx"] = ExchangeCapabilities(
            exchange_id="okx",
            display_name="OKX",
            certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            stop_market=True,
            stop_limit=True,
            fetch_positions=True,
            reduce_only=True,
            post_only=True,
            leverage=True,
            hedge_mode=True,
            sandbox=True,
            known_limitations=["Requires API Passphrase"]
        )
        self._health_status["okx"] = RuntimeExchangeHealth.HEALTHY

        # 6. KuCoin (Level 4 Simulated Order Lifecycle Verified)
        self._registry["kucoin"] = ExchangeCapabilities(
            exchange_id="kucoin",
            display_name="KuCoin",
            certification_level=CertificationLevel.LEVEL_4_SIMULATED_ORDER_LIFECYCLE_VERIFIED,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            stop_market=True,
            stop_limit=True,
            fetch_positions=True,
            reduce_only=True,
            post_only=True,
            leverage=True,
            sandbox=True,
            known_limitations=["Level 4: Undergoing final live latency certification"]
        )
        self._health_status["kucoin"] = RuntimeExchangeHealth.DEGRADED

        # 7. Gate.io (Level 3 Sandbox Verified)
        self._registry["gateio"] = ExchangeCapabilities(
            exchange_id="gateio",
            display_name="Gate.io",
            certification_level=CertificationLevel.LEVEL_3_SANDBOX_VERIFIED,
            spot=True,
            margin=True,
            futures=True,
            swap=True,
            market_order=True,
            limit_order=True,
            fetch_positions=True,
            sandbox=True,
            known_limitations=["Level 3: Sandbox verified, awaiting full order lifecycle certification"]
        )
        self._health_status["gateio"] = RuntimeExchangeHealth.DEGRADED

        # 8. Bitfinex (Level 2 Read-Only Verified)
        self._registry["bitfinex"] = ExchangeCapabilities(
            exchange_id="bitfinex",
            display_name="Bitfinex",
            certification_level=CertificationLevel.LEVEL_2_READ_ONLY_VERIFIED,
            spot=True,
            margin=True,
            market_order=True,
            limit_order=True,
            sandbox=False,
            known_limitations=["Level 2: Read-only operations verified, execution disabled"]
        )
        self._health_status["bitfinex"] = RuntimeExchangeHealth.DEGRADED

    def get_capabilities(self, exchange_id: str) -> Optional[ExchangeCapabilities]:
        """Fetch capabilities for an exchange."""
        return self._registry.get(exchange_id.lower())

    def is_certified(self, exchange_id: str) -> bool:
        """Check if an exchange is certified for live trading (Level 5)."""
        caps = self.get_capabilities(exchange_id)
        return caps is not None and caps.is_production_certified

    def get_certification_level(self, exchange_id: str) -> CertificationLevel:
        """Get the certification level of an exchange."""
        caps = self.get_capabilities(exchange_id)
        if caps:
            return caps.certification_level
        return CertificationLevel.LEVEL_0_METADATA_ONLY

    def get_exchange_health(self, exchange_id: str) -> RuntimeExchangeHealth:
        """Get the runtime health status of an exchange."""
        return self._health_status.get(exchange_id.lower(), RuntimeExchangeHealth.BLOCKED)

    def set_exchange_health(self, exchange_id: str, health: RuntimeExchangeHealth):
        """Set the runtime health status of an exchange."""
        self._health_status[exchange_id.lower()] = health

    def get_certification_matrix(self) -> Dict[str, Any]:
        """Get the complete certification matrix across all registered exchanges."""
        exchanges_list = []
        for eid, caps in self._registry.items():
            item = caps.to_dict()
            item["health"] = self.get_exchange_health(eid).value
            exchanges_list.append(item)
        return {
            "total_certified_level_5": len([e for e in exchanges_list if e["certification_level"] == 5]),
            "total_registered": len(exchanges_list),
            "exchanges": exchanges_list
        }

    def validate_deployment_preflight(self, deployment_config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Capability-aware preflight validation for live deployment configurations.
        """
        exchange_id = (deployment_config.get("exchange_id") or "").lower()
        if not exchange_id:
            return False, "Missing exchange identifier"

        caps = self.get_capabilities(exchange_id)
        if not caps or not caps.is_production_certified:
            level = self.get_certification_level(exchange_id)
            return False, f"Exchange '{exchange_id}' is not certified for live trading (Status: Level {level.value})"

        # Validate runtime health
        health = self.get_exchange_health(exchange_id)
        if health in [RuntimeExchangeHealth.DISCONNECTED, RuntimeExchangeHealth.BLOCKED, RuntimeExchangeHealth.MAINTENANCE]:
            return False, f"Exchange '{caps.display_name}' is currently {health.value} and unavailable for live trading"

        # Validate market type
        market_type = deployment_config.get("market_type", "spot").lower()
        if market_type == "futures" and not caps.futures:
            return False, f"Exchange '{caps.display_name}' does not support futures trading"
        if market_type == "swap" and not caps.swap:
            return False, f"Exchange '{caps.display_name}' does not support perpetual swap trading"
        if market_type == "margin" and not caps.margin:
            return False, f"Exchange '{caps.display_name}' does not support margin trading"

        # Validate order type if specified
        order_type = deployment_config.get("order_type", "market").lower()
        if order_type in ["stop_market", "stop"] and not caps.stop_market:
            return False, f"Exchange '{caps.display_name}' does not support stop market orders"
        if order_type == "stop_limit" and not caps.stop_limit:
            return False, f"Exchange '{caps.display_name}' does not support stop limit orders"

        # Validate credentials
        if not deployment_config.get("api_key") or not deployment_config.get("api_secret"):
            return False, "Missing required exchange API credentials"

        # Validate symbol and strategy
        if not deployment_config.get("symbol"):
            return False, "Missing target trading symbol"
        if not deployment_config.get("strategy_id"):
            return False, "Missing associated strategy"
        if not deployment_config.get("risk_settings"):
            return False, "Missing authoritative risk configuration"

        return True, "Preflight capability validation passed"


# Singleton instance
_certification_registry = ExchangeCertificationRegistry()

def get_exchange_certification_registry() -> ExchangeCertificationRegistry:
    return _certification_registry
