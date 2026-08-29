"""
core/pricing_service.py — Production-Grade Country-Aware Pricing & Currency Localization

Determines authoritative pricing and currency for SaaS users:
1. Manual override (query param / UI selection)
2. User profile preference (Supabase profiles.preferred_currency)
3. Server-side trusted IP geolocation (CountryDetector)
4. USD fallback (default)

Integrates:
- SubscriptionEngine (canonical base plan pricing)
- CountryDetector (trusted IP extraction & country mapping)
- FXService (exchange rates, minor units, precision, gateway routing)
"""

import asyncio
import inspect
import logging
from typing import Any, Dict, List, Optional
from fastapi import Request

from backend_app.core.country_detection import CountryDetector, COUNTRY_TO_CURRENCY
from backend_app.core.fx_service import (
    FXService,
    BASELINE_FX_RATES,
    STRIPE_SUPPORTED_CHECKOUT_CURRENCIES,
    RAZORPAY_SUPPORTED_CHECKOUT_CURRENCIES,
)
from backend_app.core.subscription_engine import Plan, SubscriptionEngine

logger = logging.getLogger("PricingService")


class Currency:
    """Supported currencies."""
    USD = "USD"
    INR = "INR"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"
    SGD = "SGD"
    CHF = "CHF"
    NZD = "NZD"
    BRL = "BRL"
    MXN = "MXN"
    ZAR = "ZAR"
    KRW = "KRW"
    HKD = "HKD"
    SEK = "SEK"
    NOK = "NOK"
    DKK = "DKK"
    PLN = "PLN"
    CZK = "CZK"
    TRY = "TRY"
    AED = "AED"


class PricingService:
    """Country-aware pricing and currency localization engine."""

    @staticmethod
    def get_currency_symbol(currency: str) -> str:
        """Get currency symbol."""
        return FXService.get_currency_symbol(currency)

    @staticmethod
    def country_to_currency(country_code: str) -> str:
        """Convert ISO-2 country code to currency."""
        return CountryDetector.get_country_currency(country_code)

    @staticmethod
    async def determine_pricing_context(
        user_id: Optional[str] = None,
        supabase: Any = None,
        request: Optional[Request] = None,
        currency_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve complete localized pricing context.
        
        Priority:
        1. Explicit currency override (if valid)
        2. Saved user profile preference (profiles.preferred_currency)
        3. Trusted client IP country detection
        4. USD default fallback
        """
        detected_country = "US"
        currency_source = "fallback"
        resolved_currency = "USD"

        # Step 1: Detect country from trusted server-side client IP
        if request:
            client_ip = CountryDetector.extract_client_ip(request)
            detected_country = CountryDetector.detect_country_from_ip(client_ip, request)
            detected_currency = CountryDetector.get_country_currency(detected_country)
            resolved_currency = detected_currency
            currency_source = "ip"
            logger.debug(f"Detected IP country: {detected_country} -> {detected_currency}")

        # Step 2: Check saved user preference if user is authenticated
        if user_id and supabase:
            try:
                query_res = (
                    supabase.table("profiles")
                    .select("preferred_currency, billing_currency")
                    .eq("id", user_id)
                    .limit(1)
                    .execute()
                )
                resp = await query_res if inspect.isawaitable(query_res) else query_res
                if resp and hasattr(resp, "data") and resp.data:
                    row = resp.data[0]
                    pref = row.get("preferred_currency")
                    if pref and pref.upper() in BASELINE_FX_RATES:
                        resolved_currency = pref.upper()
                        currency_source = "user_preference"
            except Exception as e:
                logger.debug(f"Could not read user currency preference: {e}")

        # Step 3: Explicit currency override takes highest precedence if provided
        if currency_override:
            clean_override = currency_override.strip().upper()
            if clean_override in BASELINE_FX_RATES:
                resolved_currency = clean_override
                currency_source = "manual_override"

        # Step 4: Resolve FX rate & gateway routing
        fx_result = await FXService.get_fx_rate(resolved_currency)
        provider, checkout_curr = FXService.resolve_checkout_provider_and_currency(resolved_currency)
        is_direct = (checkout_curr == resolved_currency)

        return {
            "country": detected_country,
            "country_name": CountryDetector.get_country_name(detected_country),
            "currency": resolved_currency,
            "currency_symbol": FXService.get_currency_symbol(resolved_currency),
            "base_currency": FXService.BASE_CURRENCY,
            "fx_rate": fx_result.rate,
            "fx_rate_timestamp": fx_result.timestamp,
            "currency_source": currency_source,
            "checkout_currency": checkout_curr,
            "checkout_currency_symbol": FXService.get_currency_symbol(checkout_curr),
            "checkout_provider": provider,
            "is_direct_checkout": is_direct,
            "decimals": FXService.get_currency_decimals(resolved_currency),
            "is_fallback": fx_result.is_fallback,
        }

    @staticmethod
    async def determine_currency(
        user_id: str,
        supabase: Any,
        request: Optional[Request] = None,
    ) -> str:
        """Backward-compatible helper returning simple currency string."""
        context = await PricingService.determine_pricing_context(user_id, supabase, request)
        return context["currency"]

    @staticmethod
    async def get_localized_plans(
        pricing_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate complete localized plan payload from canonical SubscriptionEngine base prices.
        """
        currency = pricing_context["currency"]
        plans = SubscriptionEngine.get_all_plans()
        frontend_plans = []

        for plan in plans:
            # Base USD price in whole dollars (pricing["USD"] is in cents: 0, 500, 1000, 2500)
            base_usd = plan.pricing.get("USD", 0) / 100.0

            localized_calc = await FXService.localize_price(base_usd, currency)

            frontend_plans.append({
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "features": plan.features,
                "quotas": plan.quotas,
                "base_price": base_usd,
                "base_currency": "USD",
                "localized_price": localized_calc.localized_price,
                "currency": currency,
                "currency_symbol": localized_calc.currency_symbol,
                "decimals": localized_calc.decimals,
                "minor_units": localized_calc.minor_units,
                "checkout_price": localized_calc.checkout_amount_minor / (10 ** FXService.get_currency_decimals(localized_calc.checkout_currency)) if FXService.get_currency_decimals(localized_calc.checkout_currency) > 0 else localized_calc.checkout_amount_minor,
                "checkout_currency": localized_calc.checkout_currency,
                "checkout_currency_symbol": FXService.get_currency_symbol(localized_calc.checkout_currency),
                "checkout_provider": localized_calc.checkout_provider,
                "is_direct_checkout": localized_calc.is_direct_checkout,
                "recommended": (plan.id == "pro"),
            })

        return {
            "country": pricing_context["country"],
            "country_name": pricing_context["country_name"],
            "currency": pricing_context["currency"],
            "currency_symbol": pricing_context["currency_symbol"],
            "base_currency": pricing_context["base_currency"],
            "fx_rate": pricing_context["fx_rate"],
            "fx_rate_timestamp": pricing_context["fx_rate_timestamp"],
            "currency_source": pricing_context["currency_source"],
            "checkout_currency": pricing_context["checkout_currency"],
            "checkout_currency_symbol": pricing_context["checkout_currency_symbol"],
            "checkout_provider": pricing_context["checkout_provider"],
            "is_direct_checkout": pricing_context["is_direct_checkout"],
            "supported_currencies": FXService.get_supported_display_currencies(),
            "plans": frontend_plans,
        }

    @staticmethod
    async def set_user_currency_preference(
        user_id: str,
        currency: str,
        supabase: Any,
    ) -> bool:
        """
        Persist user's manual currency preference in Supabase profiles.
        """
        clean_curr = currency.strip().upper()
        if clean_curr not in BASELINE_FX_RATES:
            logger.error(f"Invalid or unsupported currency preference: {currency}")
            return False

        if not supabase:
            return False

        try:
            res = (
                supabase.table("profiles")
                .update({"preferred_currency": clean_curr})
                .eq("id", user_id)
                .execute()
            )
            if inspect.isawaitable(res):
                await res

            # Invalidate cached currency
            try:
                from backend_app.core.cache.redis_manager import redis_manager
                await redis_manager.delete(f"billing:currency:{user_id}")
            except Exception:
                pass

            logger.info(f"Updated currency preference for user {user_id}: {clean_curr}")
            return True
        except Exception as e:
            logger.error(f"Failed to save currency preference for user {user_id}: {e}")
            return False
