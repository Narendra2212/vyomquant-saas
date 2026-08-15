"""
core/pricing_service.py — Country-Aware Pricing Service

Determines the appropriate currency and pricing for a user based on:
1. User preference (saved in profile)
2. Billing preference (saved in profile)
3. GeoIP detection from IP address
4. Browser locale (from Accept-Language header)
5. USD fallback (default)
"""

import inspect
import logging
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger("PricingService")


class Currency:
    """Supported currencies."""
    USD = "USD"
    INR = "INR"


class PricingService:
    """Country-aware pricing service."""
    
    # INR countries (primary market)
    INR_COUNTRIES = {"IN", "NP", "BD", "LK", "PK", "BT", "MV"}
    
    # Currency symbols
    SYMBOLS = {
        Currency.USD: "$",
        Currency.INR: "₹",
    }
    
    @staticmethod
    def detect_from_geoip(ip_address: str) -> Optional[str]:
        """
        Detect country from IP address using GeoIP.
        Returns ISO country code or None if detection fails.
        """
        try:
            # TODO: Integrate with GeoIP service (e.g., MaxMind, ipstack)
            # For now, return None (will fall back to USD)
            logger.debug(f"GeoIP detection not implemented for IP: {ip_address}")
            return None
        except Exception as e:
            logger.error(f"GeoIP detection failed: {e}")
            return None
    
    @staticmethod
    def detect_from_locale(accept_language: str) -> Optional[str]:
        """
        Detect currency from browser Accept-Language header.
        """
        if not accept_language:
            return None
        
        try:
            # Parse Accept-Language header
            # Format: "en-US,en;q=0.9,hi;q=0.8"
            languages = [lang.split(";")[0].strip() for lang in accept_language.split(",")]
            
            for lang in languages:
                # Check for Indian locale
                if lang.lower().startswith("hi") or "IN" in lang.upper():
                    return Currency.INR
                # Check for US locale
                if lang.lower().startswith("en") and "US" in lang.upper():
                    return Currency.USD
            
            return None
        except Exception as e:
            logger.error(f"Locale detection failed: {e}")
            return None
    
    @staticmethod
    def country_to_currency(country_code: str) -> str:
        """
        Convert country code to currency.
        """
        if country_code.upper() in PricingService.INR_COUNTRIES:
            return Currency.INR
        return Currency.USD
    
    @staticmethod
    async def determine_currency(
        user_id: str,
        supabase: Any,
        request: Optional[Request] = None,
    ) -> str:
        """
        Determine appropriate currency for user.
        Priority:
        1. User preference (saved in profile)
        2. Billing preference (saved in profile)
        3. GeoIP detection
        4. Browser locale
        5. USD fallback
        """
        # Priority 1: User preference from profile
        if supabase:
            try:
                res = (
                    supabase.table("profiles")
                    .select("preferred_currency")
                    .eq("id", user_id)
                    .execute()
                )
                resp = await res if inspect.isawaitable(res) else res
                
                if resp and hasattr(resp, "data") and resp.data:
                    preferred_currency = resp.data[0].get("preferred_currency")
                    if preferred_currency in [Currency.USD, Currency.INR]:
                        logger.debug(f"Using user preferred currency: {preferred_currency}")
                        return preferred_currency
            except Exception as e:
                logger.warning(f"Failed to fetch user currency preference: {e}")
        
            # Priority 2: Billing preference from profile
            try:
                res = (
                    supabase.table("profiles")
                    .select("billing_currency")
                    .eq("id", user_id)
                    .execute()
                )
                resp = await res if inspect.isawaitable(res) else res
                
                if resp and hasattr(resp, "data") and resp.data:
                    billing_currency = resp.data[0].get("billing_currency")
                    if billing_currency in [Currency.USD, Currency.INR]:
                        logger.debug(f"Using billing currency: {billing_currency}")
                        return billing_currency
            except Exception as e:
                logger.warning(f"Failed to fetch billing currency: {e}")
        
        # Priority 3: GeoIP detection
        if request:
            client_ip = request.client.host if request.client else None
            if client_ip:
                country_code = PricingService.detect_from_geoip(client_ip)
                if country_code:
                    currency = PricingService.country_to_currency(country_code)
                    logger.debug(f"Detected currency from GeoIP: {currency}")
                    return currency
        
        # Priority 4: Browser locale
        if request:
            accept_language = request.headers.get("Accept-Language")
            if accept_language:
                currency = PricingService.detect_from_locale(accept_language)
                if currency:
                    logger.debug(f"Detected currency from locale: {currency}")
                    return currency
        
        # Priority 5: USD fallback
        logger.debug("Using USD as fallback currency")
        return Currency.USD
    
    @staticmethod
    async def set_user_currency_preference(
        user_id: str,
        currency: str,
        supabase: Any,
    ) -> bool:
        """
        Save user's currency preference.
        """
        if currency not in [Currency.USD, Currency.INR]:
            logger.error(f"Invalid currency: {currency}")
            return False
        
        if not supabase:
            return False

        try:
            res = (
                supabase.table("profiles")
                .update({"preferred_currency": currency})
                .eq("id", user_id)
                .execute()
            )
            if inspect.isawaitable(res):
                await res
            
            logger.info(f"Set currency preference for user {user_id}: {currency}")
            return True
        except Exception as e:
            logger.error(f"Failed to set currency preference: {e}")
            return False
    
    @staticmethod
    def format_price(amount_cents: int, currency: str) -> str:
        """
        Format price in cents/paise to display string.
        """
        if currency == Currency.INR:
            # Convert paise to rupees
            amount = amount_cents / 100
            return f"₹{amount:.2f}"
        else:
            # Convert cents to dollars
            amount = amount_cents / 100
            return f"${amount:.2f}"
    
    @staticmethod
    def get_currency_symbol(currency: str) -> str:
        """
        Get currency symbol.
        """
        return PricingService.SYMBOLS.get(currency, Currency.USD)
