"""
backend_app/core/exchange_connection_schema.py — Authoritative Dynamic Exchange Connection Schema & Definition Registry.

Provides dynamic, per-exchange credential & configuration field requirements:
- Binance: api_key, secret_key, sandbox (bool), market_type (spot, futures)
- OKX: api_key, secret_key, password (passphrase), sandbox (bool), market_type (spot, futures, swap)
- Bybit: api_key, secret_key, sandbox (bool), market_type (spot, linear, inverse)
- Coinbase: api_key, secret_key, sandbox (bool), market_type (spot)
- Kraken: api_key, secret_key, market_type (spot, futures)
- KuCoin: api_key, secret_key, password (passphrase), sandbox (bool), market_type (spot, futures)
- Gate.io: api_key, secret_key, sandbox (bool), market_type (spot, futures)
- Bitfinex: api_key, secret_key, market_type (spot)
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("ExchangeConnectionSchema")


class FieldType(str, Enum):
    TEXT = "text"
    PASSWORD = "password"
    SELECT = "select"
    BOOLEAN = "boolean"
    NUMBER = "number"


@dataclass
class ExchangeCredentialField:
    field_id: str
    label: str
    type: FieldType
    required: bool = True
    secret: bool = False
    placeholder: str = ""
    description: str = ""
    default: Any = None
    options: List[Dict[str, str]] = field(default_factory=list)
    validation_regex: Optional[str] = None


@dataclass
class ExchangeConnectionDefinition:
    exchange_id: str
    display_name: str
    ccxt_id: str
    certification_level: int
    is_certified: bool
    fields: List[ExchangeCredentialField]
    supported_market_types: List[str]
    sandbox_supported: bool
    description: str = ""
    docs_url: str = ""
    known_limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "display_name": self.display_name,
            "ccxt_id": self.ccxt_id,
            "certification_level": self.certification_level,
            "is_certified": self.is_certified,
            "supported_market_types": self.supported_market_types,
            "sandbox_supported": self.sandbox_supported,
            "description": self.description,
            "docs_url": self.docs_url,
            "known_limitations": self.known_limitations,
            "fields": [
                {
                    "name": f.field_id,
                    "field_id": f.field_id,
                    "label": f.label,
                    "type": f.type.value,
                    "required": f.required,
                    "secret": f.secret,
                    "placeholder": f.placeholder,
                    "description": f.description,
                    "default": f.default,
                    "options": f.options,
                }
                for f in self.fields
            ]
        }


class ExchangeConnectionSchemaRegistry:
    """
    Authoritative registry of dynamic connection definitions per exchange.
    """
    def __init__(self):
        self._definitions: Dict[str, ExchangeConnectionDefinition] = {}
        self._register_default_schemas()

    def _register_default_schemas(self):
        # 1. Binance
        self._definitions["binance"] = ExchangeConnectionDefinition(
            exchange_id="binance",
            display_name="Binance",
            ccxt_id="binance",
            certification_level=5,
            is_certified=True,
            supported_market_types=["spot", "futures"],
            sandbox_supported=True,
            description="Global leader in crypto spot and futures volume.",
            docs_url="https://binance-docs.github.io/apidocs/",
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Binance API Key",
                    description="Standard HMAC API key with Spot/Futures permissions."
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="API Secret",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Binance API Secret",
                    description="Secret key corresponding to your Binance API Key."
                ),
                ExchangeCredentialField(
                    field_id="market_type",
                    label="Default Market Type",
                    type=FieldType.SELECT,
                    required=False,
                    default="spot",
                    options=[
                        {"label": "Spot Trading", "value": "spot"},
                        {"label": "USDT-M Futures", "value": "futures"}
                    ],
                    description="Market partition for this connection."
                ),
                ExchangeCredentialField(
                    field_id="sandbox",
                    label="Testnet / Sandbox Mode",
                    type=FieldType.BOOLEAN,
                    required=False,
                    default=False,
                    description="Connect to Binance Testnet instead of Production."
                )
            ]
        )

        # 2. OKX (Requires Password / Passphrase)
        self._definitions["okx"] = ExchangeConnectionDefinition(
            exchange_id="okx",
            display_name="OKX",
            ccxt_id="okx",
            certification_level=5,
            is_certified=True,
            supported_market_types=["spot", "futures", "swap"],
            sandbox_supported=True,
            description="Leading derivatives and spot cryptocurrency exchange.",
            docs_url="https://www.okx.com/docs-v5/en/",
            known_limitations=["Requires API Passphrase"],
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter OKX API Key",
                    description="OKX v5 API Key."
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="API Secret",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter OKX API Secret",
                    description="OKX v5 API Secret."
                ),
                ExchangeCredentialField(
                    field_id="password",
                    label="Passphrase",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter OKX API Passphrase",
                    description="Passphrase set when creating the OKX API Key."
                ),
                ExchangeCredentialField(
                    field_id="market_type",
                    label="Market Type",
                    type=FieldType.SELECT,
                    required=False,
                    default="spot",
                    options=[
                        {"label": "Spot", "value": "spot"},
                        {"label": "Futures", "value": "futures"},
                        {"label": "Perpetual Swap", "value": "swap"}
                    ]
                ),
                ExchangeCredentialField(
                    field_id="sandbox",
                    label="Demo / Sandbox Mode",
                    type=FieldType.BOOLEAN,
                    required=False,
                    default=False,
                    description="Connect to OKX Demo Trading environment."
                )
            ]
        )

        # 3. Bybit
        self._definitions["bybit"] = ExchangeConnectionDefinition(
            exchange_id="bybit",
            display_name="Bybit",
            ccxt_id="bybit",
            certification_level=5,
            is_certified=True,
            supported_market_types=["spot", "linear", "inverse"],
            sandbox_supported=True,
            description="High-performance crypto derivatives and spot exchange.",
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Bybit API Key"
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="API Secret",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Bybit API Secret"
                ),
                ExchangeCredentialField(
                    field_id="market_type",
                    label="Market Type",
                    type=FieldType.SELECT,
                    required=False,
                    default="spot",
                    options=[
                        {"label": "Spot", "value": "spot"},
                        {"label": "USDT Linear Perpetual", "value": "linear"},
                        {"label": "Inverse Perpetual", "value": "inverse"}
                    ]
                ),
                ExchangeCredentialField(
                    field_id="sandbox",
                    label="Testnet Mode",
                    type=FieldType.BOOLEAN,
                    required=False,
                    default=False,
                    description="Connect to Bybit Testnet."
                )
            ]
        )

        # 4. Coinbase
        self._definitions["coinbase"] = ExchangeConnectionDefinition(
            exchange_id="coinbase",
            display_name="Coinbase Advanced",
            ccxt_id="coinbase",
            certification_level=5,
            is_certified=True,
            supported_market_types=["spot"],
            sandbox_supported=True,
            description="Regulated US spot exchange.",
            known_limitations=["Spot trading only"],
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Coinbase API Key (or CDP API key name)"
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="API Secret",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Coinbase API Secret (or CDP private key)"
                ),
                ExchangeCredentialField(
                    field_id="sandbox",
                    label="Sandbox Mode",
                    type=FieldType.BOOLEAN,
                    required=False,
                    default=False
                )
            ]
        )

        # 5. Kraken
        self._definitions["kraken"] = ExchangeConnectionDefinition(
            exchange_id="kraken",
            display_name="Kraken",
            ccxt_id="kraken",
            certification_level=5,
            is_certified=True,
            supported_market_types=["spot", "futures"],
            sandbox_supported=False,
            description="Security-first spot and futures crypto exchange.",
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Kraken API Key"
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="Private Key (Secret)",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter Kraken Private Key"
                ),
                ExchangeCredentialField(
                    field_id="market_type",
                    label="Market Type",
                    type=FieldType.SELECT,
                    required=False,
                    default="spot",
                    options=[
                        {"label": "Spot", "value": "spot"},
                        {"label": "Futures", "value": "futures"}
                    ]
                )
            ]
        )

        # 6. KuCoin (Level 4, Requires Passphrase)
        self._definitions["kucoin"] = ExchangeConnectionDefinition(
            exchange_id="kucoin",
            display_name="KuCoin",
            ccxt_id="kucoin",
            certification_level=4,
            is_certified=False,
            supported_market_types=["spot", "futures"],
            sandbox_supported=True,
            description="Global crypto exchange with wide altcoin selection.",
            known_limitations=["Level 4: Undergoing final live latency certification"],
            fields=[
                ExchangeCredentialField(
                    field_id="api_key",
                    label="API Key",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter KuCoin API Key"
                ),
                ExchangeCredentialField(
                    field_id="secret_key",
                    label="API Secret",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter KuCoin API Secret"
                ),
                ExchangeCredentialField(
                    field_id="password",
                    label="Passphrase",
                    type=FieldType.PASSWORD,
                    required=True,
                    secret=True,
                    placeholder="Enter KuCoin API Passphrase"
                ),
                ExchangeCredentialField(
                    field_id="sandbox",
                    label="Sandbox Mode",
                    type=FieldType.BOOLEAN,
                    required=False,
                    default=False
                )
            ]
        )

    def get_schema(self, exchange_id: str) -> Optional[ExchangeConnectionDefinition]:
        """Fetch connection schema definition for an exchange."""
        return self._definitions.get(exchange_id.lower())

    def list_schemas(self) -> List[ExchangeConnectionDefinition]:
        """List all defined connection schemas."""
        return list(self._definitions.values())


# Singleton instance
_schema_registry = ExchangeConnectionSchemaRegistry()

def get_exchange_connection_schema_registry() -> ExchangeConnectionSchemaRegistry:
    return _schema_registry
