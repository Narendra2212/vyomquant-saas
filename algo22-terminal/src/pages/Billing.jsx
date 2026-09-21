import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  CreditCard, CheckCircle, Star, Zap, Globe, Award, ArrowUpRight, Plus, AlertTriangle, XCircle, Loader2, TrendingUp, Bot, Cpu, Database, BarChart3, Shield, Crown, ChevronRight, RefreshCw, ExternalLink, AlertCircle, X, ChevronDown, MapPin
} from "lucide-react";
import { api, isAuthenticated } from "../api";
import wsClient from "../websocketClient";
import { SectionH, PanelTitle, Tag2 } from "../components/common/primitives";
import { token } from "../design/tokens";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

export default function Billing() {
  const [currentPlan, setCurrentPlan] = useState(null);
  const [billingHistory, setBillingHistory] = useState([]);
  const [savedMethods, setSavedMethods] = useState([]);
  const [currency, setCurrency] = useState("USD");
  const [currencySymbol, setCurrencySymbol] = useState("$");
  const [countryCode, setCountryCode] = useState("US");
  const [countryName, setCountryName] = useState("United States");
  const [currencySource, setCurrencySource] = useState("ip");
  const [checkoutCurrency, setCheckoutCurrency] = useState("USD");
  const [isDirectCheckout, setIsDirectCheckout] = useState(true);
  const [supportedCurrencies, setSupportedCurrencies] = useState([
    { code: "USD", name: "US Dollar", symbol: "$" },
    { code: "INR", name: "Indian Rupee", symbol: "₹" },
    { code: "EUR", name: "Euro", symbol: "€" },
    { code: "GBP", name: "British Pound", symbol: "£" },
    { code: "JPY", name: "Japanese Yen", symbol: "¥" },
    { code: "CAD", name: "Canadian Dollar", symbol: "CA$" },
    { code: "AUD", name: "Australian Dollar", symbol: "A$" },
    { code: "SGD", name: "Singapore Dollar", symbol: "S$" },
    { code: "CHF", name: "Swiss Franc", symbol: "CHF" },
    { code: "AED", name: "UAE Dirham", symbol: "AED" },
    { code: "BRL", name: "Brazilian Real", symbol: "R$" },
    { code: "MXN", name: "Mexican Peso", symbol: "Mex$" },
    { code: "ZAR", name: "South African Rand", symbol: "R" },
    { code: "KRW", name: "South Korean Won", symbol: "₩" },
    { code: "HKD", name: "Hong Kong Dollar", symbol: "HK$" },
    { code: "SEK", name: "Swedish Krona", symbol: "kr" },
    { code: "NOK", name: "Norwegian Krone", symbol: "kr" },
    { code: "DKK", name: "Danish Krone", symbol: "kr" },
    { code: "PLN", name: "Polish Zloty", symbol: "zł" },
    { code: "CZK", name: "Czech Koruna", symbol: "Kč" },
    { code: "TRY", name: "Turkish Lira", symbol: "₺" },
  ]);
  const [isCurrencyDropdownOpen, setIsCurrencyDropdownOpen] = useState(false);
  const [isLoadingBilling, setIsLoadingBilling] = useState(true);
  const [isLoadingPlans, setIsLoadingPlans] = useState(false);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  const [billingError, setBillingError] = useState("");
  const [plans, setPlans] = useState([]);
  const [usage, setUsage] = useState({});
  const [quotas, setQuotas] = useState({});
  const [subscriptionStatus, setSubscriptionStatus] = useState("active");
  const [renewalDate, setRenewalDate] = useState(null);
  const [cancelAtPeriodEnd, setCancelAtPeriodEnd] = useState(false);
  const [isCancelLoading, setIsCancelLoading] = useState(false);
  const [isResumeLoading, setIsResumeLoading] = useState(false);
  const [isPortalLoading, setIsPortalLoading] = useState(false);
  const [actionSuccess, setActionSuccess] = useState("");

  const loadPlans = useCallback(async (currOverride) => {
    setIsLoadingPlans(true);
    try {
      const response = await api.billing.getPlans(currOverride);
      if (response && response.plans) {
        setPlans(response.plans);
        if (response.currency) setCurrency(response.currency);
        if (response.currency_symbol) setCurrencySymbol(response.currency_symbol);
        if (response.country) setCountryCode(response.country);
        if (response.country_name) setCountryName(response.country_name);
        if (response.currency_source) setCurrencySource(response.currency_source);
        if (response.checkout_currency) setCheckoutCurrency(response.checkout_currency);
        if (response.is_direct_checkout !== undefined) setIsDirectCheckout(response.is_direct_checkout);
        if (response.supported_currencies && response.supported_currencies.length > 0) {
          setSupportedCurrencies(response.supported_currencies);
        }
      }
    } catch (err) {
      console.error("Failed to load localized plans:", err);
    } finally {
      setIsLoadingPlans(false);
    }
  }, []);

  useEffect(() => {
    loadPlans();
  }, [loadPlans]);

  const loadBilling = useCallback(async () => {
    setIsLoadingBilling(true);
    setBillingError("");
    try {
      const [entitlementsRes, invoicesRes, methodsRes] = await Promise.allSettled([
        api.billing.getEntitlements(),
        api.billing.getInvoices(),
        api.billing.getPaymentMethods()
      ]);

      if (entitlementsRes.status === 'fulfilled' && entitlementsRes.value) {
        const data = entitlementsRes.value.data || entitlementsRes.value;
        if (data && data.plan) {
          setCurrentPlan({
            id: data.plan,
            name: data.plan.charAt(0).toUpperCase() + data.plan.slice(1),
            features: data.features || [],
          });
          setUsage(data.usage || {});
          setQuotas(data.quotas || {});
          setSubscriptionStatus(data.subscription_status || "active");
          setRenewalDate(data.renewal_date);
          setCancelAtPeriodEnd(data.cancel_at_period_end || false);
        }
      }
      if (invoicesRes.status === 'fulfilled' && invoicesRes.value) {
        const invList = invoicesRes.value.data || invoicesRes.value;
        setBillingHistory(Array.isArray(invList) ? invList : []);
      }
      if (methodsRes.status === 'fulfilled' && methodsRes.value) {
        const mList = methodsRes.value.data || methodsRes.value;
        setSavedMethods(Array.isArray(mList) ? mList : []);
      }
    } catch (err) {
      console.error("Failed loading billing data:", err);
      setBillingError("Unable to sync billing profile with the server.");
    } finally {
      setIsLoadingBilling(false);
    }
  }, []);

  useEffect(() => {
    loadBilling();
  }, [loadBilling]);

  /**
   * The currency the page is showing, readable from the socket handler below.
   *
   * A ref rather than a dependency, because `currency` in the effect's dependency array is
   * what made a currency change tear the socket down and build a new one — once per pick.
   * The handler needs the *current* value at the moment a frame arrives, which is what a ref
   * is for; the connection does not need rebuilding to learn it.
   */
  const currencyRef = useRef(currency);
  useEffect(() => {
    currencyRef.current = currency;
  }, [currency]);

  /*
    Real-time subscription updates, over the one session socket.
    production-launch-hardening task 8.1. Requirements 1.22, 1.23 / 2.22, 2.23.

    This effect used to call `new WebSocket` itself and schedule its own reconnect from inside
    `onclose` — and `onclose` is exactly what the effect's cleanup fires, so tearing the page
    down was the event that armed the next connection. There was no id to clear and no
    `clearTimeout` anywhere in the effect, so five seconds after the page was gone a socket
    opened that nothing held a reference to.

    Both halves are gone, and neither is replaced by a local fix:

    - The socket is the shared client's. `acquire` / `release` are refcounted
      (`websocketClient.js:705-737`), so this page is a *hold* on the one session connection
      rather than a second connection. The last release closes it.
    - The reconnect is the shared client's too. It already owns capped jittered backoff and —
      the part the handler here got wrong — the distinction between an intentional teardown
      (`release` → `disconnect`, which disables reconnection before it closes) and a dropped
      connection. Deleting the scheduling outright is the fix; a `clearTimeout` would have
      kept a second reconnect implementation alive.

    Unchanged on purpose: the route is still `/ws/user/{user_id}` (`ws_routes.py:641`), which
    is where the backend publishes what a Stripe webhook produces.

    ── THE CREDENTIAL AND THE STORE (tasks 8.2, 8.3 — Requirements 1.21 / 2.21, 3.9) ────────

    Neither half of what this comment used to say is true any more, so neither is repeated:
    the credential is no longer read here at all, and it does not travel as `?token=`.
    `wsClient.acquire` mints a single-use ≤ 30 s ticket over HTTPS for every connection
    attempt (`websocketClient.js` §THE SOCKET CREDENTIAL), so this page has no credential to
    hold. What it needs is narrower: *whether a session exists*, and *whose it is*.

    **Whether** comes from `isAuthenticated()` — `apiClient`'s own helper, re-exported by
    `src/api`. That is the point of task 8.3. This effect used to read
    `localStorage.getItem('token')` while the axios request interceptor read
    `sessionStorage.getItem("token")`, which is two stores for one session: every writer in
    the app (`apiClient`'s `onAuthStateChange`, `AuthPage`, sign-out) writes `sessionStorage`,
    so the `localStorage` read was never once satisfied and this effect has never run in a
    real session. Calling the HTTP client's own helper rather than re-reading a store makes
    "one store" structural — there is no second read here to drift.

    **Whose** comes from `GET /api/auth/me` (`api.auth.getMe`, `routers/auth.py:123`), not
    from storage. `localStorage.getItem('userId')` was the second dead guard: nothing in
    `src/` writes `userId`, anywhere, so it was always null. `/api/auth/me` is the right
    replacement rather than `supabase.auth.getUser()` for two reasons — it is authenticated
    by the very token the socket ticket will be minted from, so a resolvable id and a usable
    session are one fact rather than two; and supabase-js persists its own session in
    `localStorage` (`sb-<ref>-auth-token`, which `AccountMenu.jsx:535` clears by hand), so
    asking it would reintroduce exactly the second store this task removes. The `id` it
    returns is the JWT `sub`, which is what `_resolve_ws_subject` compares the path segment
    against (`ws_routes.py:729-743`) — so the id and the route agree by construction.

    The resolve is a request, so it is asynchronous, and the effect may be torn down while it
    is in flight. `cancelled` is checked after the await and `held` records whether a hold was
    ever taken: an unmount mid-resolve therefore acquires nothing and releases nothing. A
    resolve that fails, or that answers without an id, is a *reported* no-subscription — the
    reason is logged and no socket is opened. It is never a placeholder path.
  */
  useEffect(() => {
    // No session, no socket — and no ticket request either. Every route in `ws_routes.py`
    // fails closed, so this is not the client deciding authorisation; it is the client not
    // asking a signed-out user's question.
    if (!isAuthenticated()) return undefined;

    let cancelled = false;
    let held = false;
    let releases = [];

    /*
      The five frame kinds that mean "your entitlements changed, re-read them". Two are keyed
      on `type` and three on `event`, which is the backend's shape, not a choice made here —
      preserved exactly, because these five are the reason the socket exists: a webhook lands,
      the backend publishes, and the page re-reads without the trader refreshing.
    */
    const namesABillingChange = (message) =>
      message.type === 'subscription_update' ||
      message.type === 'plan_changed' ||
      message.event === 'subscription_cancelled' ||
      message.event === 'cancellation_reversed' ||
      message.event === 'payment_failed';

    // One frame, one refresh. `processMessage` hands the same object to an event-type
    // subscriber and then to a channel subscriber (`websocketClient.js:303-330`), and this
    // page holds both routings, so a frame naming the channel would otherwise refresh twice.
    const alreadyRefreshed = new WeakSet();

    const handleBillingFrame = (message) => {
      if (!message || typeof message !== 'object') return;
      if (!namesABillingChange(message)) return;
      if (alreadyRefreshed.has(message)) return;
      alreadyRefreshed.add(message);
      loadBilling();
      loadPlans(currencyRef.current);
    };

    const subscribe = async () => {
      let userId;
      try {
        const response = await api.auth.getMe();
        // `apiClient`'s `get` returns the body; the `.data` unwrap is the same defensive
        // read `loadBilling` above makes, for the same reason.
        const profile = (response && response.data) || response;
        userId = (profile && profile.id) || null;
      } catch (err) {
        console.error(
          'Billing: no real-time subscription — GET /api/auth/me failed, so the session has ' +
          `no resolvable user id: ${err?.message || 'unknown error'}`,
        );
        return;
      }

      // Unmounted, or the effect re-ran, while the profile request was in flight. Taking a
      // hold now would be a hold nothing releases.
      if (cancelled) return;

      if (!userId) {
        console.error(
          'Billing: no real-time subscription — GET /api/auth/me answered without an `id`, ' +
          'and /ws/user/{user_id} has no correct value to stand in for it.',
        );
        return;
      }

      wsClient.acquire(`/ws/user/${userId}`);
      held = true;
      releases = [
        // The server-side hold, refcounted per channel by the shared client.
        wsClient.subscribeChannel('billing', handleBillingFrame),
        // And the client-side routing for the frames that name a type rather than a channel,
        // which is how the two `type` kinds above arrive.
        wsClient.subscribe('subscription_update', handleBillingFrame),
        wsClient.subscribe('plan_changed', handleBillingFrame),
      ];
    };

    subscribe();

    return () => {
      cancelled = true;
      releases.forEach((release) => release());
      releases = [];
      if (held) {
        held = false;
        wsClient.release();
      }
    };
  }, [loadBilling, loadPlans]);

  const handleCheckout = useCallback(async (planId) => {
    if (isCheckoutLoading) return;
    setBillingError("");
    setIsCheckoutLoading(planId);
    try {
      const data = await api.billing.createCheckout({ tier: planId, currency });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        throw new Error("Payment gateway URL not provided by backend.");
      }
    } catch (err) {
      console.error("Checkout error:", err);
      const detail = err?.response?.data?.detail;
      const message = typeof detail === 'string' ? detail : "Checkout initialization failed.";
      setBillingError(message);
    } finally {
      setIsCheckoutLoading("");
    }
  }, [isCheckoutLoading, currency]);

  const handleCurrencyChange = useCallback(async (newCurrency) => {
    setCurrency(newCurrency);
    setIsCurrencyDropdownOpen(false);
    try {
      await api.billing.setCurrency(newCurrency);
      await loadPlans(newCurrency);
    } catch (err) {
      console.error("Failed to save currency preference:", err);
    }
  }, [loadPlans]);

  const handleCancel = useCallback(async () => {
    if (isCancelLoading) return;
    setIsCancelLoading(true);
    setBillingError("");
    setActionSuccess("");
    try {
      const result = await api.billing.cancelSubscription();
      setActionSuccess(result?.detail || "Subscription cancellation scheduled.");
      await loadBilling();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setBillingError(typeof detail === 'string' ? detail : "Failed to cancel subscription.");
    } finally {
      setIsCancelLoading(false);
    }
  }, [isCancelLoading, loadBilling]);

  const handleResume = useCallback(async () => {
    if (isResumeLoading) return;
    setIsResumeLoading(true);
    setBillingError("");
    setActionSuccess("");
    try {
      const result = await api.billing.resumeSubscription();
      setActionSuccess(result?.detail || "Subscription resumed successfully.");
      await loadBilling();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setBillingError(typeof detail === 'string' ? detail : "Failed to resume subscription.");
    } finally {
      setIsResumeLoading(false);
    }
  }, [isResumeLoading, loadBilling]);

  const handleOpenPortal = useCallback(async () => {
    if (isPortalLoading) return;
    setIsPortalLoading(true);
    setBillingError("");
    try {
      const data = await api.billing.openPortal();
      if (data && data.url) {
        window.open(data.url, '_blank', 'noopener,noreferrer');
      } else {
        throw new Error("Billing portal URL not returned by server.");
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setBillingError(typeof detail === 'string' ? detail : "Failed to open billing portal.");
    } finally {
      setIsPortalLoading(false);
    }
  }, [isPortalLoading]);

  const fmtDate = useCallback((d) => {
    if (!d) return "N/A";
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }, []);

  const defaultMethod = useMemo(() => savedMethods.find((m) => m.is_default) || savedMethods[0], [savedMethods]);

  const getUsagePercent = useCallback((resource) => {
    const used = usage[resource] || 0;
    const limit = quotas[resource] || 0;
    if (limit === 0 || limit === -1 || limit === Infinity) return 0;
    return Math.min((used / limit) * 100, 100);
  }, [usage, quotas]);

  const getUsageColor = useCallback((percent) => {
    if (percent >= 90) return token.status.loss.fg;
    if (percent >= 70) return "#f59e0b";
    return token.status.profit.fg;
  }, []);

  const formatPlanPrice = useCallback((plan) => {
    if (!plan) return `${currencySymbol}0`;
    const price = plan.localized_price !== undefined ? plan.localized_price : 0;
    const decimals = plan.decimals !== undefined ? plan.decimals : (currency === "JPY" || currency === "KRW" ? 0 : 2);
    const sym = plan.currency_symbol || currencySymbol;
    return `${sym}${Number(price).toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
  }, [currencySymbol, currency]);

  const isPaymentFailed = subscriptionStatus === "past_due" || subscriptionStatus === "payment_failed";
  const isCancelled = subscriptionStatus === "cancelled";
  const isFreePlan = currentPlan?.id === "free" || !currentPlan?.id;

  return (
    <div style={{ padding: 24, overflowY: "auto", flex: 1, background: token.surface.canvas }}>
      <SectionH title="Subscription & Billing" sub="Manage your plan, localized pricing, and payment methods" />

      {!!billingError && (
        <div style={{ marginBottom: 16, background: `${token.status.loss.fg}12`, border: `1px solid ${token.status.loss.fg}44`, color: token.status.loss.fg, borderRadius: 8, padding: "12px 16px", fontSize: 12, fontFamily: "monospace", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span><AlertTriangle size={14} style={{ display: "inline", marginRight: 8, verticalAlign: "middle" }} /> {billingError}</span>
          <button onClick={() => setBillingError("")} style={{ background: "transparent", border: "none", color: token.status.loss.fg, cursor: "pointer", padding: 4 }}><X size={14} /></button>
        </div>
      )}

      {!!actionSuccess && (
        <div style={{ marginBottom: 16, background: `${token.status.profit.fg}12`, border: `1px solid ${token.status.profit.fg}44`, color: token.status.profit.fg, borderRadius: 8, padding: "12px 16px", fontSize: 12, fontFamily: "monospace", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span><CheckCircle size={14} style={{ display: "inline", marginRight: 8, verticalAlign: "middle" }} /> {actionSuccess}</span>
          <button onClick={() => setActionSuccess("")} style={{ background: "transparent", border: "none", color: token.status.profit.fg, cursor: "pointer", padding: 4 }}><X size={14} /></button>
        </div>
      )}

      {/* Payment Failure Alert */}
      {isPaymentFailed && (
        <div style={{ marginBottom: 16, background: `${token.status.loss.fg}10`, border: `1px solid ${token.status.loss.fg}55`, borderRadius: 12, padding: "16px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <AlertCircle size={20} style={{ color: token.status.loss.fg, flexShrink: 0 }} />
            <div>
              <div style={{ color: token.status.loss.fg, fontWeight: 800, fontSize: 13, fontFamily: "monospace" }}>PAYMENT FAILED — ACTION REQUIRED</div>
              <div style={{ color: token.content.secondary, fontSize: 12, marginTop: 4 }}>Your subscription payment failed. Update your payment method to restore full access.</div>
            </div>
          </div>
          <button
            onClick={handleOpenPortal}
            disabled={isPortalLoading}
            style={{ background: token.status.loss.fg, color: "#fff", border: "none", borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 800, cursor: isPortalLoading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", flexShrink: 0 }}
          >
            {isPortalLoading ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />}
            Update Payment Method
          </button>
        </div>
      )}

      {isLoadingBilling ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ background: token.surface.panel, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 24, height: 120 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
              <div style={{ width: 200, height: 24, background: token.surface.inset, borderRadius: 4 }} />
              <div style={{ width: 100, height: 32, background: token.surface.inset, borderRadius: 16 }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
              {[1, 2, 3].map(i => (
                <div key={i} style={{ height: 60, background: token.surface.raised, borderRadius: 8 }} />
              ))}
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
            {[1, 2, 3, 4].map(i => (
              <div key={i} style={{ background: token.surface.panel, border: `1px solid ${token.line.default}`, borderRadius: 12, padding: 24, height: 320 }} />
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* Current Plan Overview */}
          <Card className="p-6 mb-6" style={{ border: `1px solid ${isPaymentFailed ? token.status.loss.fg : token.brand.base}40`, background: `linear-gradient(135deg, ${token.surface.raised} 0%, ${token.surface.panel} 100%)` }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ background: `${isPaymentFailed ? token.status.loss.fg : token.brand.base}20`, borderRadius: 12, padding: 12 }}>
                  {currentPlan?.id === "enterprise" ? <Crown size={24} style={{ color: token.brand.base }} /> :
                   currentPlan?.id === "pro" ? <Star size={24} style={{ color: token.brand.base }} /> :
                   currentPlan?.id === "starter" ? <Zap size={24} style={{ color: token.brand.base }} /> :
                   <Shield size={24} style={{ color: token.brand.base }} />}
                </div>
                <div>
                  <Tag2 c="cyan" style={{ marginBottom: 4 }}>CURRENT PLAN</Tag2>
                  <h2 style={{ color: token.content.primary, fontWeight: 900, fontSize: 24, textTransform: "capitalize" }}>
                    {currentPlan?.name || "Free"}
                  </h2>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
                    <Tag2 c={
                      subscriptionStatus === "active" ? "green" :
                      subscriptionStatus === "trial" ? "cyan" :
                      subscriptionStatus === "past_due" || subscriptionStatus === "payment_failed" ? "red" :
                      subscriptionStatus === "cancelled" ? "red" : "orange"
                    }>
                      {subscriptionStatus?.toUpperCase()?.replace("_", " ") || "ACTIVE"}
                    </Tag2>
                    {renewalDate && !cancelAtPeriodEnd && (
                      <span style={{ color: token.content.muted, fontSize: 11, fontFamily: "monospace" }}>
                        Renews {fmtDate(renewalDate)}
                      </span>
                    )}
                    {cancelAtPeriodEnd && (
                      <span style={{ color: token.status.loss.fg, fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>
                        Cancels {renewalDate ? fmtDate(renewalDate) : "at period end"}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                {/* Location Tag */}
                <div style={{ display: "flex", alignItems: "center", gap: 6, background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 8, padding: "6px 12px", fontSize: 11, fontFamily: "monospace", color: token.content.secondary }}>
                  <MapPin size={12} style={{ color: token.brand.base }} />
                  <span>{countryName} ({currency})</span>
                  <Tag2 c={currencySource === "ip" ? "cyan" : "green"} style={{ marginLeft: 4, fontSize: 9, padding: "1px 6px" }}>
                    {currencySource === "ip" ? "Auto-detected" : "Preferred"}
                  </Tag2>
                </div>

                {/* Searchable / Selectable Currency Dropdown */}
                <div style={{ position: "relative" }}>
                  <button
                    onClick={() => setIsCurrencyDropdownOpen(!isCurrencyDropdownOpen)}
                    style={{
                      background: token.surface.inset,
                      border: `1px solid ${token.brand.base}50`,
                      color: token.brand.base,
                      borderRadius: 8,
                      padding: "6px 14px",
                      fontSize: 11,
                      fontFamily: "monospace",
                      fontWeight: 700,
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      transition: "all 0.2s",
                    }}
                  >
                    <Globe size={13} />
                    <span>{currencySymbol} {currency}</span>
                    <ChevronDown size={13} />
                  </button>

                  {isCurrencyDropdownOpen && (
                    <div
                      style={{
                        position: "absolute",
                        top: "100%",
                        right: 0,
                        marginTop: 6,
                        background: token.surface.raised,
                        border: `1px solid ${token.line.default}`,
                        borderRadius: 10,
                        padding: 6,
                        width: 220,
                        maxHeight: 280,
                        overflowY: "auto",
                        zIndex: 50,
                        boxShadow: `0 8px 30px #00000088`,
                      }}
                    >
                      <div style={{ padding: "4px 8px", fontSize: 10, color: token.content.muted, fontFamily: "monospace", fontWeight: 700, textTransform: "uppercase", borderBottom: `1px solid ${token.line.default}40`, marginBottom: 4 }}>
                        Select Currency
                      </div>
                      {supportedCurrencies.map((c) => (
                        <button
                          key={c.code}
                          onClick={() => handleCurrencyChange(c.code)}
                          style={{
                            width: "100%",
                            textAlign: "left",
                            background: currency === c.code ? `${token.brand.base}20` : "transparent",
                            color: currency === c.code ? token.brand.base : token.content.primary,
                            border: "none",
                            borderRadius: 6,
                            padding: "6px 10px",
                            fontSize: 11,
                            fontFamily: "monospace",
                            fontWeight: currency === c.code ? 800 : 500,
                            cursor: "pointer",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                          }}
                          onMouseEnter={(e) => {
                            if (currency !== c.code) e.currentTarget.style.background = token.surface.inset;
                          }}
                          onMouseLeave={(e) => {
                            if (currency !== c.code) e.currentTarget.style.background = "transparent";
                          }}
                        >
                          <span>{c.code} ({c.symbol})</span>
                          <span style={{ color: token.content.muted, fontSize: 10 }}>{c.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Manage Payment Method via Stripe Portal */}
                {!isFreePlan && (
                  <button
                    onClick={handleOpenPortal}
                    disabled={isPortalLoading}
                    title="Open Stripe Billing Portal to manage payment method"
                    style={{ background: `${token.brand.base}15`, border: `1px solid ${token.brand.base}40`, color: token.brand.base, borderRadius: 8, padding: "8px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: isPortalLoading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", gap: 6, transition: "all 0.2s" }}
                  >
                    {isPortalLoading ? <Loader2 size={12} className="animate-spin" /> : <ExternalLink size={12} />}
                    Manage Billing
                  </button>
                )}
              </div>
            </div>

            {/* Usage Summary */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginBottom: 20 }}>
              {[
                { label: "Strategies", key: "strategies", used: usage.strategies || 0, limit: quotas.strategies || 0, icon: Database },
                { label: "Live Bots", key: "bots", used: usage.bots || 0, limit: quotas.bots || 0, icon: Bot },
                { label: "ML Models", key: "ml_trainings", used: usage.ml_trainings || 0, limit: quotas.ml_trainings || 0, icon: Cpu },
                { label: "Marketplace", key: "marketplace_published", used: usage.marketplace_published || 0, limit: quotas.marketplace_published || 0, icon: TrendingUp },
              ].map((item) => {
                const isUnlimited = item.limit === -1 || item.limit === Infinity;
                const percent = isUnlimited ? 0 : getUsagePercent(item.key);
                const color = getUsageColor(percent);
                const displayLimit = isUnlimited ? "∞" : item.limit;
                return (
                  <div key={item.label} style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 10, padding: 16 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <item.icon size={16} style={{ color: token.content.muted }} />
                      <span style={{ color: token.content.secondary, fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>{item.label}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 8 }}>
                      <span style={{ color: token.content.primary, fontSize: 20, fontWeight: 900 }}>{item.used}</span>
                      <span style={{ color: token.content.muted, fontSize: 12, fontFamily: "monospace" }}>/ {displayLimit}</span>
                    </div>
                    <div style={{ height: 6, background: token.surface.panel, borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ height: "100%", background: color, width: `${percent}%`, transition: "width 0.3s ease" }} />
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Subscription Lifecycle Actions */}
            {!isFreePlan && (
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", borderTop: `1px solid ${token.line.default}20`, paddingTop: 16, marginTop: 4 }}>
                {cancelAtPeriodEnd ? (
                  <button
                    onClick={handleResume}
                    disabled={isResumeLoading}
                    style={{ background: `${token.status.profit.fg}20`, border: `1px solid ${token.status.profit.fg}50`, color: token.status.profit.fg, borderRadius: 8, padding: "9px 18px", fontSize: 11, fontFamily: "monospace", fontWeight: 800, cursor: isResumeLoading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", gap: 8, transition: "all 0.2s" }}
                  >
                    {isResumeLoading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                    Resume Subscription
                  </button>
                ) : (
                  !isCancelled && !isPaymentFailed && (
                    <button
                      onClick={handleCancel}
                      disabled={isCancelLoading}
                      style={{ background: `${token.status.loss.fg}10`, border: `1px solid ${token.status.loss.fg}35`, color: token.status.loss.fg, borderRadius: 8, padding: "9px 18px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: isCancelLoading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", gap: 8, transition: "all 0.2s", opacity: isCancelLoading ? 0.7 : 1 }}
                    >
                      {isCancelLoading ? <Loader2 size={13} className="animate-spin" /> : <XCircle size={13} />}
                      Cancel Subscription
                    </button>
                  )
                )}
                <button
                  onClick={loadBilling}
                  style={{ background: "transparent", border: `1px solid ${token.line.default}`, color: token.content.muted, borderRadius: 8, padding: "9px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 600, cursor: "pointer", display: "flex", alignItems: "center", gap: 6, transition: "all 0.2s" }}
                >
                  <RefreshCw size={12} />
                  Refresh
                </button>
              </div>
            )}
          </Card>

          {/* Pricing Cards */}
          <div style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
              <h3 style={{ color: token.content.primary, fontSize: 18, fontWeight: 900 }}>Available Plans</h3>
              {isLoadingPlans && (
                <span style={{ color: token.content.muted, fontSize: 11, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                  <Loader2 size={12} className="animate-spin" /> Updating pricing...
                </span>
              )}
            </div>

            {plans.length === 0 ? (
              <div style={{ color: token.content.muted, fontSize: 12, fontFamily: "monospace", textAlign: "center", padding: 24 }}>Loading plans...</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
                {plans.map((p) => {
                  const isActive = currentPlan?.id === p.id;
                  const isProcessingThis = isCheckoutLoading === p.id;
                  const formattedPrice = formatPlanPrice(p);
                  const hasDifferentCheckoutCurr = !p.is_direct_checkout && p.checkout_currency && p.checkout_currency !== p.currency;

                  return (
                    <div key={p.id} style={{
                      background: token.surface.raised,
                      border: `2px solid ${p.recommended || isActive ? token.brand.base : token.line.default}`,
                      borderRadius: 16, padding: 24, position: "relative",
                      display: "flex", flexDirection: "column",
                      boxShadow: p.recommended ? `0 0 40px ${token.brand.base}15` : "none",
                      transition: "all 0.3s ease",
                      cursor: isActive ? "default" : "pointer",
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) {
                        e.currentTarget.style.transform = "translateY(-8px)";
                        e.currentTarget.style.boxShadow = `0 12px 40px ${token.brand.base}20`;
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive) {
                        e.currentTarget.style.transform = "translateY(0)";
                        e.currentTarget.style.boxShadow = p.recommended ? `0 0 40px ${token.brand.base}15` : "none";
                      }
                    }}
                    >
                      {p.recommended && !isActive && (
                        <div style={{ position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)", background: token.brand.base, color: "#000", fontSize: 10, fontWeight: 900, letterSpacing: 1, padding: "4px 12px", borderRadius: 999 }}>RECOMMENDED</div>
                      )}
                      {isActive && (
                        <div style={{ position: "absolute", top: -12, left: "50%", transform: "translateX(-50%)", background: token.status.profit.fg, color: "#000", fontSize: 10, fontWeight: 900, letterSpacing: 1, padding: "4px 12px", borderRadius: 999 }}>CURRENT</div>
                      )}

                      <div style={{ color: token.content.primary, fontWeight: 900, fontSize: 22, marginBottom: 4 }}>{p.name}</div>
                      <div style={{ color: token.content.muted, fontSize: 12, fontFamily: "monospace", marginBottom: 16 }}>{p.description}</div>

                      <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: hasDifferentCheckoutCurr ? 4 : 20 }}>
                        <span style={{ color: token.brand.base, fontWeight: 900, fontSize: 36, lineHeight: 1 }}>{formattedPrice}</span>
                        <span style={{ color: token.content.muted, fontSize: 12, fontFamily: "monospace" }}>/month</span>
                      </div>

                      {hasDifferentCheckoutCurr && (
                        <div style={{ color: token.content.muted, fontSize: 10, fontFamily: "monospace", marginBottom: 16 }}>
                          Billed as ${p.checkout_price || p.base_price} {p.checkout_currency} at checkout
                        </div>
                      )}

                      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24, flex: 1 }}>
                        {(p.features || []).slice(0, 6).map((feat, fi) => (
                          <div key={fi} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <CheckCircle size={14} style={{ color: token.brand.base, flexShrink: 0 }} />
                            <span style={{ color: token.content.secondary, fontSize: 12, fontFamily: "monospace" }}>{feat.replace(/_/g, " ")}</span>
                          </div>
                        ))}
                      </div>

                      <button
                        disabled={!!isCheckoutLoading || isActive || isPaymentFailed}
                        onClick={() => handleCheckout(p.id)}
                        title={isPaymentFailed ? "Resolve payment issue first to change plan" : undefined}
                        style={{
                          width: "100%", background: isActive ? `${token.status.profit.fg}20` : token.brand.base, color: isActive ? token.status.profit.fg : "#000",
                          border: `1px solid ${isActive ? `${token.status.profit.fg}40` : "transparent"}`, borderRadius: 10, padding: "14px",
                          fontSize: 12, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase", letterSpacing: 1,
                          cursor: !!isCheckoutLoading || isActive || isPaymentFailed ? "not-allowed" : "pointer",
                          opacity: (!!isCheckoutLoading && !isProcessingThis) || isPaymentFailed ? 0.5 : 1,
                          transition: "all 0.3s ease",
                        }}
                      >
                        {isActive ? "Current Plan" : isProcessingThis ? <Loader2 size={14} className="animate-spin" /> : "Subscribe"}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Payment Methods & Billing History */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(400px, 1fr))", gap: 16 }}>
            <Card className="p-6">
              <PanelTitle title="Payment Methods" />
              {!defaultMethod ? (
                <div style={{ background: token.surface.inset, border: `1px dashed ${token.line.default}`, borderRadius: 10, padding: 24, color: token.content.muted, fontSize: 12, fontFamily: "monospace", textAlign: "center", marginTop: 16 }}>No payment methods on file.</div>
              ) : (
                <div style={{ background: token.surface.inset, border: `1px solid ${token.line.default}`, borderRadius: 10, padding: 16, display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
                  <div style={{ background: "linear-gradient(135deg,#1a1f71,#003087)", borderRadius: 8, padding: 10, flexShrink: 0 }}>
                    <CreditCard size={20} style={{ color: "#fff" }} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ color: token.content.primary, fontWeight: 700, fontSize: 13 }}>{defaultMethod.brand} •••• {defaultMethod.last4}</div>
                    <div style={{ color: token.content.muted, fontSize: 11, fontFamily: "monospace" }}>Expires {defaultMethod.expiry_month}/{defaultMethod.expiry_year}</div>
                  </div>
                  {defaultMethod.is_default && <Tag2 c="green">DEFAULT</Tag2>}
                </div>
              )}
              <button
                onClick={handleOpenPortal}
                disabled={isPortalLoading}
                style={{ width: "100%", marginTop: 16, background: `${token.brand.base}10`, border: `1px solid ${token.brand.base}30`, color: token.brand.base, borderRadius: 8, padding: "10px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, cursor: isPortalLoading ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, transition: "all 0.2s" }}
              >
                {isPortalLoading ? <Loader2 size={13} className="animate-spin" /> : <ExternalLink size={13} />}
                Manage via Stripe Portal
              </button>
            </Card>

            <Card className="p-6">
              <PanelTitle title="Billing History" />
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace", marginTop: 16 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${token.line.default}` }}>
                    {["Invoice", "Date", "Amount", "Status"].map(h => <th key={h} style={{ color: token.content.muted, fontWeight: 900, padding: "8px 12px", textAlign: "left", fontSize: 9, letterSpacing: 1, textTransform: "uppercase" }}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {billingHistory.length === 0 ? (
                    <tr><td colSpan={4} style={{ padding: "16px 12px", color: token.content.muted, textAlign: "center" }}>No invoices found.</td></tr>
                  ) : billingHistory.slice(0, 5).map(inv => (
                    <tr key={inv.id} style={{ borderBottom: `1px solid ${token.line.default}20` }}>
                      <td style={{ padding: "10px 12px", color: token.brand.base, cursor: "pointer", fontFamily: "monospace" }}>{String(inv.id).slice(0, 8)}...</td>
                      <td style={{ padding: "10px 12px", color: token.content.secondary }}>{fmtDate(inv.date)}</td>
                      <td style={{ padding: "10px 12px", color: token.content.primary, fontWeight: 700 }}>
                        {inv.currency === "INR" ? `₹${Number(inv.amtINR || 0).toLocaleString()}` : `$${Number(inv.amtUSD || 0).toLocaleString()}`}
                      </td>
                      <td style={{ padding: "10px 12px" }}><Tag2 c={inv.status === "paid" ? "green" : inv.status === "pending" ? "orange" : "red"}>{inv.status}</Tag2></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
