"""
core/country_detection.py — Production-Grade Server-Side Client IP & Country Detection

Features:
1. Trusted Client IP Extraction behind AWS ALB, ECS, Nginx, Cloudflare, Akamai:
   - Evaluates CF-Connecting-IP, True-Client-IP, X-Real-IP, X-Forwarded-For (leftmost public IP).
   - Strips private/loopback/bogon addresses.
   - Ignores client-controllable untrusted headers (X-Country, X-Country-Code, X-Client-Geo).
2. Multi-tier Country Resolution:
   Tier 1: Edge CDN trusted headers (CloudFront-Viewer-Country, CF-IPCountry)
   Tier 2: Redis / Memory cached IP -> Country lookup
   Tier 3: Local high-speed CIDR subnet lookup (zero external latency)
   Tier 4: Live asynchronous GeoIP provider lookup with strict timeout
   Tier 5: Deterministic "US" / "USD" fallback for private/unmapped IPs
3. Complete ISO-3166-1 alpha-2 country coverage (all 240+ global countries and territories).
4. Canonical Country -> Official Billing Currency mapping.
"""

import asyncio
import ipaddress
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from fastapi import Request

logger = logging.getLogger("CountryDetection")

# Private and loopback network ranges (RFC 1918, RFC 3927, RFC 4291)
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Canonical ISO 3166-1 alpha-2 Country Code -> Official Currency Code Mapping
# Complete coverage of all officially assigned ISO-2 countries and territories
COUNTRY_TO_CURRENCY: Dict[str, str] = {
    # A
    "AD": "EUR", "AE": "AED", "AF": "AFN", "AG": "XCD", "AI": "XCD",
    "AL": "ALL", "AM": "AMD", "AO": "AOA", "AQ": "USD", "AR": "ARS",
    "AS": "USD", "AT": "EUR", "AU": "AUD", "AW": "AWG", "AX": "EUR",
    "AZ": "AZN",
    # B
    "BA": "BAM", "BB": "BBD", "BD": "BDT", "BE": "EUR", "BF": "XOF",
    "BG": "BGN", "BH": "BHD", "BI": "BIF", "BJ": "XOF", "BL": "EUR",
    "BM": "BMD", "BN": "BND", "BO": "BOB", "BQ": "USD", "BR": "BRL",
    "BS": "BSD", "BT": "INR", "BV": "NOK", "BW": "BWP", "BY": "BYN",
    "BZ": "BZD",
    # C
    "CA": "CAD", "CC": "AUD", "CD": "CDF", "CF": "XAF", "CG": "XAF",
    "CH": "CHF", "CI": "XOF", "CK": "NZD", "CL": "CLP", "CM": "XAF",
    "CN": "CNY", "CO": "COP", "CR": "CRC", "CU": "CUP", "CV": "CVE",
    "CW": "ANG", "CX": "AUD", "CY": "EUR", "CZ": "CZK",
    # D
    "DE": "EUR", "DJ": "DJF", "DK": "DKK", "DM": "XCD", "DO": "DOP",
    "DZ": "DZD",
    # E
    "EC": "USD", "EE": "EUR", "EG": "EGP", "EH": "MAD", "ER": "ERN",
    "ES": "EUR", "ET": "ETB",
    # F
    "FI": "EUR", "FJ": "FJD", "FK": "FKP", "FM": "USD", "FO": "DKK",
    "FR": "EUR",
    # G
    "GA": "XAF", "GB": "GBP", "GD": "XCD", "GE": "GEL", "GF": "EUR",
    "GG": "GBP", "GH": "GHS", "GI": "GIP", "GL": "DKK", "GM": "GMD",
    "GN": "GNF", "GP": "EUR", "GQ": "XAF", "GR": "EUR", "GS": "GBP",
    "GT": "GTQ", "GU": "USD", "GW": "XOF", "GY": "GYD",
    # H
    "HK": "HKD", "HM": "AUD", "HN": "HNL", "HR": "EUR", "HT": "HTG",
    "HU": "HUF",
    # I
    "ID": "IDR", "IE": "EUR", "IL": "ILS", "IM": "GBP", "IN": "INR",
    "IO": "USD", "IQ": "IQD", "IR": "IRR", "IS": "ISK", "IT": "EUR",
    # J
    "JE": "GBP", "JM": "JMD", "JO": "JOD", "JP": "JPY",
    # K
    "KE": "KES", "KG": "KGS", "KH": "KHR", "KI": "AUD", "KM": "KMF",
    "KN": "XCD", "KP": "KPW", "KR": "KRW", "KW": "KWD", "KY": "KYD",
    "KZ": "KZT",
    # L
    "LA": "LAK", "LB": "LBP", "LC": "XCD", "LI": "CHF", "LK": "LKR",
    "LR": "LRD", "LS": "LSL", "LT": "EUR", "LU": "EUR", "LV": "EUR",
    "LY": "LYD",
    # M
    "MA": "MAD", "MC": "EUR", "MD": "MDL", "ME": "EUR", "MF": "EUR",
    "MG": "MGA", "MH": "USD", "MK": "MKD", "ML": "XOF", "MM": "MMK",
    "MN": "MNT", "MO": "MOP", "MP": "USD", "MQ": "EUR", "MR": "MRU",
    "MS": "XCD", "MT": "EUR", "MU": "MUR", "MV": "MVR", "MW": "MWK",
    "MX": "MXN", "MY": "MYR", "MZ": "MZN",
    # N
    "NA": "NAD", "NC": "XPF", "NE": "XOF", "NF": "AUD", "NG": "NGN",
    "NI": "NIO", "NL": "EUR", "NO": "NOK", "NP": "NPR", "NR": "AUD",
    "NU": "NZD", "NZ": "NZD",
    # O
    "OM": "OMR",
    # P
    "PA": "USD", "PE": "PEN", "PF": "XPF", "PG": "PGK", "PH": "PHP",
    "PK": "PKR", "PL": "PLN", "PM": "EUR", "PN": "NZD", "PR": "USD",
    "PS": "ILS", "PT": "EUR", "PW": "USD", "PY": "PYG",
    # Q
    "QA": "QAR",
    # R
    "RE": "EUR", "RO": "RON", "RS": "RSD", "RU": "RUB", "RW": "RWF",
    # S
    "SA": "SAR", "SB": "SBD", "SC": "SCR", "SD": "SDG", "SE": "SEK",
    "SG": "SGD", "SH": "SHP", "SI": "EUR", "SJ": "NOK", "SK": "EUR",
    "SL": "SLE", "SM": "EUR", "SN": "XOF", "SO": "SOS", "SR": "SRD",
    "SS": "SSP", "ST": "STN", "SV": "USD", "SX": "ANG", "SY": "SYP",
    "SZ": "SZL",
    # T
    "TC": "USD", "TD": "XAF", "TF": "EUR", "TG": "XOF", "TH": "THB",
    "TJ": "TJS", "TK": "NZD", "TL": "USD", "TM": "TMT", "TN": "TND",
    "TO": "TOP", "TR": "TRY", "TT": "TTD", "TV": "AUD", "TW": "TWD",
    "TZ": "TZS",
    # U
    "UA": "UAH", "UG": "UGX", "UM": "USD", "US": "USD", "UY": "UYU",
    "UZ": "UZS",
    # V
    "VA": "EUR", "VC": "XCD", "VE": "VES", "VG": "USD", "VI": "USD",
    "VN": "VND", "VU": "VUV",
    # W
    "WF": "XPF", "WS": "WST",
    # X
    "XK": "EUR",
    # Y
    "YE": "YER", "YT": "EUR",
    # Z
    "ZA": "ZAR", "ZM": "ZMW", "ZW": "ZWG",
}

# Country Names for UI Display
COUNTRY_NAMES: Dict[str, str] = {
    "US": "United States", "IN": "India", "GB": "United Kingdom",
    "DE": "Germany", "FR": "France", "IT": "Italy", "ES": "Spain",
    "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "IE": "Ireland",
    "PT": "Portugal", "FI": "Finland", "GR": "Greece", "CH": "Switzerland",
    "JP": "Japan", "AU": "Australia", "CA": "Canada", "SG": "Singapore",
    "AE": "United Arab Emirates", "NZ": "New Zealand", "BR": "Brazil",
    "MX": "Mexico", "ZA": "South Africa", "KR": "South Korea", "HK": "Hong Kong",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "PL": "Poland",
    "CZ": "Czech Republic", "TR": "Turkey", "IL": "Israel", "TH": "Thailand",
    "ID": "Indonesia", "MY": "Malaysia", "PH": "Philippines", "VN": "Vietnam",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "EG": "Egypt",
    "NG": "Nigeria", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "TW": "Taiwan", "CN": "China", "RO": "Romania", "HU": "Hungary",
    "PE": "Peru", "PK": "Pakistan", "BD": "Bangladesh", "UA": "Ukraine",
    "KE": "Kenya", "MA": "Morocco", "GH": "Ghana", "CR": "Costa Rica",
    "PA": "Panama", "UY": "Uruguay", "IS": "Iceland", "LU": "Luxembourg",
}

# Pre-compiled high-speed subnet lookup for major global regions
LOCAL_CIDR_COUNTRY_MAP: List[Tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]] = [
    # India Subnets
    (ipaddress.ip_network("103.21.124.0/22"), "IN"),
    (ipaddress.ip_network("103.22.200.0/22"), "IN"),
    (ipaddress.ip_network("103.24.188.0/22"), "IN"),
    (ipaddress.ip_network("103.27.8.0/22"), "IN"),
    (ipaddress.ip_network("103.48.0.0/16"), "IN"),
    (ipaddress.ip_network("103.50.0.0/16"), "IN"),
    (ipaddress.ip_network("103.100.0.0/16"), "IN"),
    (ipaddress.ip_network("103.208.0.0/16"), "IN"),
    (ipaddress.ip_network("117.192.0.0/11"), "IN"),
    (ipaddress.ip_network("122.160.0.0/12"), "IN"),
    (ipaddress.ip_network("125.16.0.0/12"), "IN"),
    (ipaddress.ip_network("182.64.0.0/11"), "IN"),
    (ipaddress.ip_network("49.204.0.0/14"), "IN"),
    (ipaddress.ip_network("14.139.0.0/16"), "IN"),
    (ipaddress.ip_network("13.232.0.0/14"), "IN"),  # AWS ap-south-1 Mumbai
    (ipaddress.ip_network("3.108.0.0/14"), "IN"),
    (ipaddress.ip_network("3.6.0.0/15"), "IN"),
    # UK Subnets
    (ipaddress.ip_network("51.140.0.0/14"), "GB"),
    (ipaddress.ip_network("82.165.0.0/16"), "GB"),
    (ipaddress.ip_network("185.86.0.0/16"), "GB"),
    (ipaddress.ip_network("35.176.0.0/14"), "GB"),  # AWS eu-west-2 London
    (ipaddress.ip_network("18.130.0.0/15"), "GB"),
    # Germany Subnets
    (ipaddress.ip_network("18.194.0.0/15"), "DE"),  # AWS eu-central-1 Frankfurt
    (ipaddress.ip_network("3.120.0.0/14"), "DE"),
    (ipaddress.ip_network("194.95.0.0/16"), "DE"),
    # France Subnets
    (ipaddress.ip_network("15.236.0.0/14"), "FR"),  # AWS eu-west-3 Paris
    (ipaddress.ip_network("193.54.0.0/15"), "FR"),
    # Japan Subnets
    (ipaddress.ip_network("13.112.0.0/14"), "JP"),  # AWS ap-northeast-1 Tokyo
    (ipaddress.ip_network("54.238.0.0/15"), "JP"),
    (ipaddress.ip_network("133.0.0.0/12"), "JP"),
    # Australia Subnets
    (ipaddress.ip_network("13.236.0.0/14"), "AU"),  # AWS ap-southeast-2 Sydney
    (ipaddress.ip_network("54.252.0.0/15"), "AU"),
    (ipaddress.ip_network("139.130.0.0/16"), "AU"),
    # Canada Subnets
    (ipaddress.ip_network("15.222.0.0/15"), "CA"),  # AWS ca-central-1 Montreal
    (ipaddress.ip_network("35.182.0.0/15"), "CA"),
    (ipaddress.ip_network("142.150.0.0/15"), "CA"),
    # Singapore Subnets
    (ipaddress.ip_network("13.212.0.0/14"), "SG"),  # AWS ap-southeast-1 Singapore
    (ipaddress.ip_network("18.136.0.0/14"), "SG"),
    (ipaddress.ip_network("118.189.0.0/16"), "SG"),
    # UAE Subnets
    (ipaddress.ip_network("15.184.0.0/14"), "AE"),  # AWS me-central-1 UAE
    (ipaddress.ip_network("86.96.0.0/13"), "AE"),
    # Switzerland Subnets
    (ipaddress.ip_network("16.62.0.0/15"), "CH"),   # AWS eu-central-2 Zurich
    (ipaddress.ip_network("193.134.0.0/16"), "CH"),
    # Brazil Subnets
    (ipaddress.ip_network("18.228.0.0/14"), "BR"),  # AWS sa-east-1 Sao Paulo
    (ipaddress.ip_network("54.232.0.0/15"), "BR"),
    (ipaddress.ip_network("200.144.0.0/16"), "BR"),
    # Mexico Subnets
    (ipaddress.ip_network("187.128.0.0/11"), "MX"),
    (ipaddress.ip_network("200.57.0.0/16"), "MX"),
    # South Africa Subnets
    (ipaddress.ip_network("13.244.0.0/14"), "ZA"),  # AWS af-south-1 Cape Town
    (ipaddress.ip_network("196.25.0.0/16"), "ZA"),
    # South Korea Subnets
    (ipaddress.ip_network("15.164.0.0/14"), "KR"),  # AWS ap-northeast-2 Seoul
    (ipaddress.ip_network("211.32.0.0/12"), "KR"),
    # Hong Kong Subnets
    (ipaddress.ip_network("18.162.0.0/15"), "HK"),  # AWS ap-east-1 Hong Kong
    (ipaddress.ip_network("202.64.0.0/14"), "HK"),
    # Sweden Subnets
    (ipaddress.ip_network("13.48.0.0/14"), "SE"),   # AWS eu-north-1 Stockholm
    (ipaddress.ip_network("194.71.0.0/16"), "SE"),
    # Turkey Subnets
    (ipaddress.ip_network("176.240.0.0/13"), "TR"),
    (ipaddress.ip_network("212.156.0.0/14"), "TR"),
    # Poland Subnets
    (ipaddress.ip_network("83.0.0.0/13"), "PL"),
    # US Subnets
    (ipaddress.ip_network("3.80.0.0/12"), "US"),
    (ipaddress.ip_network("52.0.0.0/11"), "US"),
    (ipaddress.ip_network("54.144.0.0/12"), "US"),
    (ipaddress.ip_network("34.192.0.0/12"), "US"),
    (ipaddress.ip_network("44.192.0.0/11"), "US"),
]


class CountryDetector:
    """Production-grade trusted client IP extraction and multi-tier country resolution."""

    # In-memory IP -> Country cache
    _ip_cache: Dict[str, str] = {}

    @staticmethod
    def is_private_ip(ip_str: str) -> bool:
        """Check if an IP address is private, loopback, link-local, or bogon."""
        try:
            ip_obj = ipaddress.ip_address(ip_str.strip())
            for network in PRIVATE_NETWORKS:
                if ip_obj in network:
                    return True
            return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local
        except Exception:
            return True

    @classmethod
    def extract_client_ip(cls, request: Optional[Request]) -> str:
        """
        Extract trusted client IP address from request.
        
        Evaluates trusted proxy headers:
        1. CF-Connecting-IP (Cloudflare)
        2. True-Client-IP (Akamai / Cloudflare Enterprise)
        3. X-Real-IP (Nginx reverse proxy)
        4. X-Forwarded-For (ALB / ECS proxy chain: leftmost non-private IP)
        5. Direct socket connection IP (request.client.host)
        
        Strictly ignores spoofed headers (X-Country, X-Country-Code, X-Client-Geo).
        """
        if not request:
            return "127.0.0.1"

        # Check direct trusted CDN headers
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip and not cls.is_private_ip(cf_ip):
            return cf_ip.strip()

        true_client_ip = request.headers.get("True-Client-IP")
        if true_client_ip and not cls.is_private_ip(true_client_ip):
            return true_client_ip.strip()

        x_real_ip = request.headers.get("X-Real-IP")
        if x_real_ip and not cls.is_private_ip(x_real_ip):
            return x_real_ip.strip()

        # Parse X-Forwarded-For header
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
            for ip in ips:
                if not cls.is_private_ip(ip):
                    return ip
            if ips:
                return ips[0]

        # Fall back to socket host
        if request.client and request.client.host:
            return request.client.host.strip()

        return "127.0.0.1"

    @classmethod
    def detect_country_from_headers(cls, request: Optional[Request]) -> Optional[str]:
        """
        Extract trusted edge CDN country headers injected by reverse proxies/CDNs.
        - CloudFront-Viewer-Country (AWS CloudFront GeoIP)
        - CF-IPCountry (Cloudflare GeoIP)
        """
        if not request:
            return None

        # AWS CloudFront edge GeoIP header
        cf_viewer_country = request.headers.get("CloudFront-Viewer-Country")
        if cf_viewer_country:
            code = cf_viewer_country.strip().upper()
            if len(code) == 2 and code in COUNTRY_TO_CURRENCY:
                return code

        # Cloudflare edge GeoIP header
        cf_country = request.headers.get("CF-IPCountry")
        if cf_country:
            code = cf_country.strip().upper()
            if len(code) == 2 and code in COUNTRY_TO_CURRENCY and code != "XX":
                return code

        return None

    @classmethod
    def detect_country_from_ip(cls, ip_address: str, request: Optional[Request] = None) -> str:
        """
        Resolve country ISO code from IP address using multi-tier resolution strategy:
        
        1. Test/Dev environment override: GEOIP_MOCK_COUNTRY (if explicitly set for testing)
        2. Edge CDN trusted headers (CloudFront-Viewer-Country, CF-IPCountry)
        3. In-memory IP cache
        4. Local high-speed CIDR subnet lookup
        5. Deterministic fallback to "US"
        """
        if not ip_address:
            return "US"

        # 1. Support test mocking via environment
        mock_country = os.getenv("GEOIP_MOCK_COUNTRY")
        if mock_country and len(mock_country) == 2:
            return mock_country.upper()

        # 2. Check edge CDN trusted headers if request object is provided
        if request:
            header_country = cls.detect_country_from_headers(request)
            if header_country:
                return header_country

        # 3. If IP is private or loopback, default to US
        if cls.is_private_ip(ip_address):
            return "US"

        # 4. Check in-memory IP cache
        clean_ip = ip_address.strip()
        if clean_ip in cls._ip_cache:
            return cls._ip_cache[clean_ip]

        # 5. Check local subnet CIDR map
        try:
            ip_obj = ipaddress.ip_address(clean_ip)
            for network, country_code in LOCAL_CIDR_COUNTRY_MAP:
                if ip_obj in network:
                    cls._ip_cache[clean_ip] = country_code
                    return country_code
        except Exception as e:
            logger.debug(f"IP subnet match error for {clean_ip}: {e}")

        # 6. Default to US for unmapped public IPs
        return "US"

    @classmethod
    async def detect_country_async(cls, ip_address: str, request: Optional[Request] = None) -> str:
        """
        Asynchronous multi-tier country detector with Redis caching and optional live GeoIP lookup.
        """
        if not ip_address:
            return "US"

        # 1. Edge header check
        if request:
            header_country = cls.detect_country_from_headers(request)
            if header_country:
                return header_country

        # 2. Private IP safety
        if cls.is_private_ip(ip_address):
            return "US"

        clean_ip = ip_address.strip()

        # 3. Redis cache check
        cache_key = f"billing:geoip:{clean_ip}"
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            cached_country = await redis_manager.get(cache_key)
            if cached_country and len(cached_country) == 2:
                return cached_country
        except Exception:
            pass

        # 4. Local subnet lookup
        sync_result = cls.detect_country_from_ip(clean_ip, request)
        if sync_result != "US":
            # Cache positive subnet match
            try:
                from backend_app.core.cache.redis_manager import redis_manager
                await redis_manager.set(cache_key, sync_result, ex=86400)
            except Exception:
                pass
            return sync_result

        # 5. Optional live external GeoIP provider query if configured
        geoip_url = os.getenv("GEOIP_API_URL")
        if geoip_url:
            try:
                import httpx
                target_url = geoip_url.format(ip=clean_ip) if "{ip}" in geoip_url else f"{geoip_url.rstrip('/')}/{clean_ip}"
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get(target_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        country = data.get("country_code") or data.get("country") or data.get("countryCode")
                        if country and len(country) == 2 and country.upper() in COUNTRY_TO_CURRENCY:
                            resolved = country.upper()
                            cls._ip_cache[clean_ip] = resolved
                            try:
                                from backend_app.core.cache.redis_manager import redis_manager
                                await redis_manager.set(cache_key, resolved, ex=86400)
                            except Exception:
                                pass
                            return resolved
            except Exception as e:
                logger.debug(f"Live GeoIP lookup failed for {clean_ip}: {e}")

        return sync_result

    @classmethod
    def get_country_currency(cls, country_code: str) -> str:
        """Map ISO country code to official national billing currency."""
        if not country_code:
            return "USD"
        return COUNTRY_TO_CURRENCY.get(country_code.strip().upper(), "USD")

    @classmethod
    def get_country_name(cls, country_code: str) -> str:
        """Get human-readable country name."""
        if not country_code:
            return "United States"
        return COUNTRY_NAMES.get(country_code.strip().upper(), country_code.upper())
