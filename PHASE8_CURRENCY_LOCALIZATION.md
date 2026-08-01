# Phase 8: Currency Localization
**Implement Country Detection and Regional Pricing**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Currency detection and regional pricing implementation

---

## Executive Summary

The current billing system has manual currency selection (INR hardcoded in Wizard, USD/INR toggle in Billing) with no automatic detection based on user location. This phase implements IP-based country detection, automatic currency selection, and regional pricing support.

---

## Current State

### Currency Handling Issues

1. **Wizard.jsx**
   - Hardcoded to INR currency
   - No USD option
   - No automatic detection

2. **Billing.jsx**
   - Manual USD/INR toggle
   - No automatic detection
   - No regional pricing

3. **Pricing.jsx**
   - USD only (no INR)
   - No currency toggle
   - No regional pricing

4. **Backend billing.py**
   - Supports USD and INR
   - No regional pricing
   - No currency detection

---

## Implementation Plan

### Phase 8.1: Country Detection Service

**Priority:** P0 (Critical)
**Effort:** 4 hours

**Tasks:**
1. Create `core/country_detection.py`
2. Implement IP-based country detection
3. Implement browser language detection
4. Add fallback to user profile preference
5. Add caching for country detection results
6. Add API endpoint for country detection

**File:** `backend_app/core/country_detection.py`

### Phase 8.2: Currency Mapping

**Priority:** P0 (Critical)
**Effort:** 2 hours

**Tasks:**
1. Create country-to-currency mapping
2. Create currency-to-region mapping
3. Add supported currencies list
4. Add currency symbol mapping
5. Add currency formatting utilities

**File:** `backend_app/core/currency_utils.py`

### Phase 8.3: Regional Pricing

**Priority:** P1 (High)
**Effort:** 4 hours

**Tasks:**
1. Update plan_prices table to support regional pricing
2. Add region column to plan_prices
3. Insert regional pricing data
4. Create regional pricing service
5. Update billing router to use regional pricing

**File:** `backend_app/core/regional_pricing.py`

### Phase 8.4: Backend API Endpoints

**Priority:** P1 (High)
**Effort:** 4 hours

**Tasks:**
1. Create `GET /api/billing/currency` endpoint
2. Create `GET /api/billing/pricing` endpoint
3. Update checkout endpoint to use detected currency
4. Update plan endpoint to return regional pricing

**File:** `routers/billing.py` (update)

### Phase 8.5: Frontend Integration

**Priority:** P1 (High)
**Effort:** 6 hours

**Tasks:**
1. Create currency detection API client
2. Update Billing.jsx to use detected currency
3. Update Wizard.jsx to use detected currency
4. Update Pricing.jsx to support multiple currencies
5. Add currency selector component
6. Add currency preference to user profile

**Files to Update:**
- `pages/Billing.jsx`
- `pages/Wizard.jsx`
- `components/landing/Pricing.jsx`
- `components/CurrencySelector.jsx` (new)

---

## Country Detection Implementation

### IP-Based Detection

**Service:** `core/country_detection.py`

```python
import logging
from typing import Optional
from fastapi import Request

logger = logging.getLogger("CountryDetection")

class CountryDetectionService:
    """Service for detecting user country from IP address."""
    
    # Country to currency mapping
    COUNTRY_TO_CURRENCY = {
        # India
        'IN': 'INR',
        # USA
        'US': 'USD',
        # Europe (EUR)
        'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR',
        'NL': 'EUR', 'BE': 'EUR', 'AT': 'EUR', 'IE': 'EUR',
        # UK (GBP)
        'GB': 'GBP',
        # Canada (CAD)
        'CA': 'CAD',
        # Australia (AUD)
        'AU': 'AUD',
        # Japan (JPY)
        'JP': 'JPY',
        # Singapore (SGD)
        'SG': 'SGD',
        # UAE (AED)
        'AE': 'AED',
        # Default to USD for others
    }
    
    # Supported currencies
    SUPPORTED_CURRENCIES = {'USD', 'INR'}
    
    async def detect_from_ip(self, ip_address: str) -> Optional[str]:
        """
        Detect country from IP address.
        
        Uses free IP geolocation API or local database.
        """
        try:
            # Option 1: Use local database (MaxMind GeoLite2)
            # import geoip2.database
            # reader = geoip2.database.Reader('GeoLite2-Country.mmdb')
            # response = reader.country(ip_address)
            # return response.country.iso_code
            
            # Option 2: Use free API (ip-api.com)
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://ip-api.com/json/{ip_address}")
                if response.status_code == 200:
                    data = response.json()
                    return data.get('countryCode')
            
            return None
        except Exception as e:
            logger.warning(f"Failed to detect country from IP {ip_address}: {e}")
            return None
    
    async def detect_from_request(self, request: Request) -> Optional[str]:
        """
        Detect country from HTTP request.
        
        Checks IP address, then falls back to browser language.
        """
        # Get IP address from request
        ip_address = self._get_client_ip(request)
        
        if ip_address:
            country = await self.detect_from_ip(ip_address)
            if country:
                return country
        
        # Fallback to browser language
        accept_language = request.headers.get('accept-language', '')
        if accept_language:
            country = self._parse_accept_language(accept_language)
            if country:
                return country
        
        return None
    
    def _get_client_ip(self, request: Request) -> Optional[str]:
        """Extract client IP from request headers."""
        # Check for proxy headers
        forwarded_for = request.headers.get('x-forwarded-for')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get('x-real-ip')
        if real_ip:
            return real_ip
        
        # Fallback to direct connection
        return request.client.host if request.client else None
    
    def _parse_accept_language(self, accept_language: str) -> Optional[str]:
        """Parse Accept-Language header to extract country code."""
        try:
            # Format: "en-US,en;q=0.9"
            parts = accept_language.split(',')
            for part in parts:
                if '-' in part:
                    lang_country = part.split(';')[0].strip()
                    _, country = lang_country.split('-')
                    return country.upper()
        except Exception as e:
            logger.warning(f"Failed to parse Accept-Language: {e}")
        
        return None
    
    def country_to_currency(self, country_code: str) -> str:
        """Convert country code to currency code."""
        currency = self.COUNTRY_TO_CURRENCY.get(country_code, 'USD')
        
        # Only return supported currencies
        if currency not in self.SUPPORTED_CURRENCIES:
            return 'USD'
        
        return currency
    
    async def detect_currency(self, request: Request, user_preference: Optional[str] = None) -> str:
        """
        Detect currency for user.
        
        Priority:
        1. User preference (from profile)
        2. IP-based detection
        3. Browser language
        4. Default to USD
        """
        # Check user preference first
        if user_preference and user_preference in self.SUPPORTED_CURRENCIES:
            return user_preference
        
        # Detect from request
        country = await self.detect_from_request(request)
        if country:
            return self.country_to_currency(country)
        
        # Default to USD
        return 'USD'


# Global singleton
country_detection_service = CountryDetectionService()
```

### Currency Utilities

**Service:** `core/currency_utils.py`

```python
from typing import Dict, Optional

class CurrencyUtils:
    """Utility functions for currency operations."""
    
    # Currency symbols
    CURRENCY_SYMBOLS = {
        'USD': '$',
        'INR': '₹',
        'EUR': '€',
        'GBP': '£',
        'CAD': 'C$',
        'AUD': 'A$',
        'JPY': '¥',
        'SGD': 'S$',
        'AED': 'د.إ',
    }
    
    # Currency to region mapping
    CURRENCY_TO_REGION = {
        'USD': 'US',
        'INR': 'IN',
        'EUR': 'EU',
        'GBP': 'UK',
        'CAD': 'CA',
        'AUD': 'AU',
        'JPY': 'JP',
        'SGD': 'SG',
        'AED': 'AE',
    }
    
    # Supported currencies
    SUPPORTED_CURRENCIES = {'USD', 'INR'}
    
    @classmethod
    def get_symbol(cls, currency: str) -> str:
        """Get currency symbol."""
        return cls.CURRENCY_SYMBOLS.get(currency, currency)
    
    @classmethod
    def get_region(cls, currency: str) -> Optional[str]:
        """Get region for currency."""
        return cls.CURRENCY_TO_REGION.get(currency)
    
    @classmethod
    def format_price(cls, amount_cents: int, currency: str) -> str:
        """Format price in currency."""
        symbol = cls.get_symbol(currency)
        
        if currency == 'INR':
            # INR uses paise (1/100)
            amount = amount_cents / 100
            return f"{symbol}{amount:.2f}"
        else:
            # USD uses cents (1/100)
            amount = amount_cents / 100
            return f"{symbol}{amount:.2f}"
    
    @classmethod
    def is_supported(cls, currency: str) -> bool:
        """Check if currency is supported."""
        return currency in cls.SUPPORTED_CURRENCIES
```

---

## Regional Pricing Implementation

### Database Schema Update

**Migration:** `migrations/add_regional_pricing.sql`

```sql
-- Add region column to plan_prices
ALTER TABLE plan_prices ADD COLUMN IF NOT EXISTS region VARCHAR(10);

-- Create index on region
CREATE INDEX IF NOT EXISTS idx_plan_prices_region ON plan_prices(region);

-- Insert regional pricing data
-- Example: Different pricing for different regions
-- (This would be populated based on business requirements)
```

### Regional Pricing Service

**Service:** `core/regional_pricing.py`

```python
from typing import Dict, Optional
from backend_app.core.currency_utils import CurrencyUtils

class RegionalPricingService:
    """Service for regional pricing calculations."""
    
    # Regional pricing multipliers (relative to base USD price)
    REGIONAL_MULTIPLIERS = {
        'US': 1.0,      # Base USD price
        'IN': 0.83,     # INR pricing (₹999 vs $12)
        'EU': 1.1,      # EUR pricing (10% premium)
        'UK': 1.15,     # GBP pricing (15% premium)
    }
    
    def get_regional_price(self, base_price_usd_cents: int, currency: str) -> int:
        """
        Calculate regional price based on currency.
        
        Args:
            base_price_usd_cents: Base price in USD cents
            currency: Target currency
        
        Returns:
            Price in target currency's smallest unit
        """
        region = CurrencyUtils.get_region(currency)
        
        if not region:
            # Default to USD
            return base_price_usd_cents
        
        multiplier = self.REGIONAL_MULTIPLIERS.get(region, 1.0)
        
        if currency == 'INR':
            # Convert USD to INR with multiplier
            # Base: $12 = ₹999 (multiplier ~0.83)
            return int(base_price_usd_cents * multiplier * 83.25)
        else:
            # For other currencies, apply multiplier
            return int(base_price_usd_cents * multiplier)
    
    def get_regional_discount(self, currency: str) -> float:
        """
        Get regional discount percentage.
        
        Some regions may have discounts to match local purchasing power.
        """
        # Example: India has 20% discount on annual plans
        if currency == 'INR':
            return 0.20
        
        return 0.0


# Global singleton
regional_pricing_service = RegionalPricingService()
```

---

## Backend API Endpoints

### Get Currency Detection

**Endpoint:** `GET /api/billing/currency`

```python
@router.get("/currency")
async def get_detected_currency(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Get detected currency for user based on IP and preferences.
    """
    from backend_app.core.country_detection import country_detection_service
    
    # Get user preference from profile
    resp = supabase.table("profiles").select("preferred_currency").eq("id", user["id"]).execute()
    user_preference = resp.data[0].get("preferred_currency") if resp.data else None
    
    # Detect currency
    detected_currency = await country_detection_service.detect_currency(
        request, user_preference
    )
    
    return {
        "currency": detected_currency,
        "symbol": CurrencyUtils.get_symbol(detected_currency),
        "user_preference": user_preference,
        "supported_currencies": list(CurrencyUtils.SUPPORTED_CURRENCIES),
    }
```

### Get Pricing

**Endpoint:** `GET /api/billing/pricing`

```python
@router.get("/pricing")
async def get_pricing(
    currency: str = Query("USD", regex="^(USD|INR)$"),
    billing_cycle: str = Query("monthly", regex="^(monthly|annual)$"),
):
    """
    Get pricing for all plans in specified currency.
    """
    from backend_app.core.regional_pricing import regional_pricing_service
    
    # Base prices in USD cents
    BASE_PRICES = {
        'starter': 0,
        'pro': 1200,      # $12
        'business': 2400, # $24
        'enterprise': 9900, # $99
    }
    
    pricing = {}
    for plan_key, base_price in BASE_PRICES.items():
        regional_price = regional_pricing_service.get_regional_price(base_price, currency)
        
        # Apply annual discount
        if billing_cycle == 'annual':
            regional_price = int(regional_price * 0.8)  # 20% off
        
        pricing[plan_key] = {
            "price_cents": regional_price,
            "price_formatted": CurrencyUtils.format_price(regional_price, currency),
            "currency": currency,
            "billing_cycle": billing_cycle,
        }
    
    return {
        "currency": currency,
        "billing_cycle": billing_cycle,
        "plans": pricing,
    }
```

### Update Checkout Endpoint

**Update:** `POST /api/billing/checkout`

```python
@router.post("/checkout")
async def create_checkout_session(
    body: CheckoutRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    background_tasks: BackgroundTasks,
):
    """
    Create checkout session with automatic currency detection.
    """
    from backend_app.core.country_detection import country_detection_service
    from backend_app.core.regional_pricing import regional_pricing_service
    
    # Detect currency if not provided
    if not body.currency:
        detected_currency = await country_detection_service.detect_currency(request)
        body.currency = detected_currency
    
    # Get regional pricing
    base_price = PRICES.get(body.tier.value, {}).get("USD", 0)
    regional_price = regional_pricing_service.get_regional_price(base_price, body.currency)
    
    # ... rest of checkout logic
```

---

## Frontend Integration

### Currency Detection API Client

**File:** `api/modules/currency.js`

```javascript
import { get } from './client';

export async function getDetectedCurrency() {
  return get('/billing/currency');
}

export async function getPricing(currency = 'USD', billingCycle = 'monthly') {
  return get(`/billing/pricing?currency=${currency}&billing_cycle=${billingCycle}`);
}

export async function setCurrencyPreference(currency) {
  return put('/user/profile', { preferred_currency: currency });
}
```

### Currency Selector Component

**File:** `components/CurrencySelector.jsx`

```javascript
import React, { useState, useEffect } from 'react';
import { getDetectedCurrency, setCurrencyPreference } from '../api/modules/currency';

export default function CurrencySelector({ onCurrencyChange }) {
  const [currency, setCurrency] = useState('USD');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadCurrency();
  }, []);

  const loadCurrency = async () => {
    try {
      const data = await getDetectedCurrency();
      setCurrency(data.currency);
      onCurrencyChange?.(data.currency);
    } catch (error) {
      console.error('Failed to load currency:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCurrencyChange = async (newCurrency) => {
    setCurrency(newCurrency);
    onCurrencyChange?.(newCurrency);
    
    try {
      await setCurrencyPreference(newCurrency);
    } catch (error) {
      console.error('Failed to save currency preference:', error);
    }
  };

  if (loading) return <div>Loading...</div>;

  return (
    <div className="currency-selector">
      <select
        value={currency}
        onChange={(e) => handleCurrencyChange(e.target.value)}
        className="currency-select"
      >
        <option value="USD">USD ($)</option>
        <option value="INR">INR (₹)</option>
      </select>
    </div>
  );
}
```

### Update Billing Page

**File:** `pages/Billing.jsx`

```javascript
import CurrencySelector from '../components/CurrencySelector';

export default function Billing() {
  const [currency, setCurrency] = useState('USD');
  const [pricing, setPricing] = useState(null);

  useEffect(() => {
    loadPricing();
  }, [currency]);

  const loadPricing = async () => {
    const data = await getPricing(currency, 'monthly');
    setPricing(data);
  };

  return (
    <div>
      <CurrencySelector onCurrencyChange={setCurrency} />
      {/* Render pricing with selected currency */}
    </div>
  );
}
```

---

## Testing Plan

### Unit Tests
- Test country detection from IP
- Test country detection from Accept-Language header
- Test currency mapping
- Test regional pricing calculation
- Test currency formatting

### Integration Tests
- Test currency detection API endpoint
- Test pricing API endpoint
- Test checkout with detected currency
- Test currency preference saving

### End-to-End Tests
- Test Indian user sees INR pricing
- Test US user sees USD pricing
- Test user can manually change currency
- Test currency preference persists

---

## Rollback Plan

If issues arise:

1. **Disable Detection:** Set environment variable to disable auto-detection
2. **Fallback to USD:** Default all users to USD
3. **Remove Regional Pricing:** Use base USD pricing for all

**Rollback Commands:**
```bash
export DISABLE_CURRENCY_DETECTION=true
export USE_BASE_PRICING_ONLY=true
```

---

## Next Steps

1. Review and approve this implementation plan
2. Begin Phase 8.1 (Country detection service)
3. Proceed through phases 8.2-8.5
4. Complete testing
5. Deploy to staging
6. Monitor for issues
7. Deploy to production

---

**End of Phase 8 Currency Localization**
