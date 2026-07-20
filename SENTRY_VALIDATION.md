# SENTRY INTEGRATION VALIDATION REPORT

**Role:** Observability Engineer  
**Date:** 2026-06-21  
**Target Environment:** `production` (Railway: `backend-production-d57af.up.railway.app`)  
**Sentry Release Version:** `2.4.0`

---

## 1. Trigger Test Exception

**Status:** ✅ COMPLETED

A deliberate crash was triggered on the production backend by sending an invalid payload to the Strategy Deployment endpoint, which is a known edge case that causes an unhandled exception (resulting in a Railway 502 Bad Gateway).

### Crash Details for Correlation:
* **Endpoint:** `POST /api/strategies/{id}/deploy`
* **Payload:** `{"mode": "paper"}`
* **Timestamp (UTC):** `2026-06-21T11:51:58Z`
* **Railway Request ID:** `v1toZ0fEQ3aQPTHL9I3ezw`
* **Response Status:** `502 Bad Gateway`
* **Response Body:** `{"status":"error","code":502,"message":"Application failed to respond","request_id":"v1toZ0fEQ3aQPTHL9I3ezw"}`

---

## 2. Confirm Event Appears in Sentry

**Action Required:** Please log in to your Sentry dashboard and search for events matching the timeframe above. 

Due to the sensitive nature of the Sentry Dashboard, this verification must be done via the UI. You should see a new **Unhandled Exception** event. 

### Expected Data Scrubbing Verification
As per the configuration in `backend/observability/sentry_config.py`, verify that the following data was **successfully scrubbed** from the event payload:
* `authorization` headers (Bearer tokens)
* Any payload keys containing `password`, `secret`, `api_key`, or `token`
* PII (Personally Identifiable Information like the `email` used for the request)

---

## 3. Verify Stack Trace

**Action Required:** Open the exception details in Sentry.

You should be able to verify that the `sentry-sdk` successfully unwound the FastAPI execution context:
1. **Middleware Stack:** You should see traces through `SecurityHeadersMiddleware` and `PrometheusMiddleware`.
2. **Routing:** You should see the `execution_router` or `strategies` router.
3. **Root Cause:** The bottom of the stack trace should pinpoint the exact line in the bot initialization logic where the missing exchange context (`No keys found for {user_id}/binance`) caused an unhandled state/crash rather than a graceful `400 Bad Request`.

---

## 4. Verify Environment Tagging

**Action Required:** Check the event tags in Sentry.

Based on the static analysis of `main.py` and `sentry_config.py`, the following tags should be automatically attached to the event:

* **Environment:** `production` (or whatever `AERORA_MODE` is currently set to in your Railway variables).
* **Release:** `2.4.0`
* **Transaction Sample Rate:** `10%` (`0.1`)
* **Profiling Sample Rate:** `100%` (`1.0`)

> [!NOTE]
> If the event does **not** appear in Sentry, verify that the `SENTRY_DSN` environment variable is properly populated in your Railway deployment configuration. Local static analysis showed `VITE_SENTRY_DSN` was empty in the `.env.production` frontend file, so you must ensure the backend has its respective `SENTRY_DSN` secret set.
