# The WebSocket upgrade is refused at the CDN, not by the application

**Status: diagnosed, not fixed.** The remedy is a CloudFront behaviour change, which is an
infrastructure action and is not in this repository. A deploy-time gate that fails on this
condition *is* in this repository — see the last step of `.github/workflows/06-frontend-deploy.yml`.

## The symptom

Every authenticated page of the terminal shows a red `CONNECTION FAILED` chip and the banner
*"Not connected to the trading engine. Live positions, orders and P&L below may be out of date."*
Meanwhile the REST API, the pricing catalogue, sign-in and the whole SPA work normally. That split
is what makes it easy to misread as a frontend bug or an auth problem.

The banner and the chip are `components/shell/ConnectionStatusIndicator.jsx` reporting the shared
`websocketClient` socket's real state. They are not a stale flag: the socket genuinely never opens.

## What was ruled out

| Hypothesis | Evidence against |
|---|---|
| `/ws/*` is not routed to the backend | `GET /ws/telemetry` **without** upgrade headers returns `404` carrying `server: uvicorn` and a JSON body. The origin answered, so the path is forwarded. |
| The two clients read the session from different stores | `apiClient.js` and `websocketClient.js` both read `sessionStorage.getItem("token")`. Same key, same store. |
| The ticket endpoint is broken | `POST /api/auth/ws-ticket` returns `401` unauthenticated, which is correct. It is mounted and reachable. |
| The deploy-ordering shim is masking a backend without `verify_ws_ticket` | `WS_TICKET_FALLBACK` would switch the session to the legacy `?token=` credential on a 4001/1006 refusal, and that path would then connect. It does not, because the refusal never comes from the application. |

## The measurement that settles it

Same host, same path, one header different:

```
GET /ws/telemetry                              → 404  server: uvicorn        (origin answered)
GET /ws/telemetry  + Upgrade: websocket        → 403  no server header,
                                                      Content-Length: 0      (CDN answered)
```

Reproduce:

```
curl -i -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' \
     -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     https://<host>/ws/telemetry
```

A **passing** response carries `server: uvicorn`. An unauthenticated handshake is *expected* to be
refused — every route in `backend_app/api_ws/ws_routes.py` closes before `accept()` when there is no
credential, and an ASGI server renders that as HTTP 403 — so a 403 **from the origin** is a pass.
A 403 with no origin header is the failure. The two are indistinguishable by status code, which is
why the header is the test.

Observed on both distributions: the custom domain and `d7d88qs4jmch.cloudfront.net`.

## Why it is at the edge

`scripts/add_ws_behavior_cloudfront.py` created the `/ws/*` behaviour on distribution
`EEOXECPHQ8SR0` by **cloning the `/api/*` behaviour** and widening `AllowedMethods`. Cloning
carried over `/api/*`'s cache and origin-request configuration, which does not forward the
handshake headers. CloudFront strips `Upgrade` and `Connection`, concludes the request is not a
valid WebSocket upgrade, and answers `403` itself without consulting the origin. The path pattern
is right; the header policy is not.

(Consistent with the adjacent observation that bare `/ws` returns the SPA's `index.html`: the
behaviour pattern is `/ws/*`, which does not match a path with no trailing segment, so bare `/ws`
falls through to the default S3 behaviour.)

## Remedy

On the `/ws/*` behaviour, for every distribution that serves the app:

1. Attach an origin request policy that forwards **all** viewer headers (`AllViewer`), or
   explicitly forward `Upgrade`, `Connection`, `Sec-WebSocket-Key`, `Sec-WebSocket-Version`,
   `Sec-WebSocket-Extensions` and `Sec-WebSocket-Protocol`.
2. Use a cache policy that does not cache (`CachingDisabled`). A handshake must never be served
   from an edge cache.
3. Keep `GET` and `HEAD` allowed — the handshake is a `GET`.

## A second finding, recorded while measuring this

The deployed bundle has **both** base URLs baked to the superseded distribution:

```
apiBaseUrl = "https://d7d88qs4jmch.cloudfront.net"
wsBaseUrl  = "wss://d7d88qs4jmch.cloudfront.net"
```

So a page served from the custom domain makes every API call and every socket attempt
cross-origin to the old distribution. It currently works — the SPA document is served from S3 with
no CSP, so the `connect-src` list on API responses does not constrain the page — but it is a
configuration nobody intends, and it depends on a distribution the deploy workflow's own comments
describe as retained only for rollback.

`VITE_API_URL` and `VITE_WS_URL` are GitHub Actions secrets. The build-time gate in
`06-frontend-deploy.yml` greps the bundle for an allowed API hostname and
`d7d88qs4jmch.cloudfront.net` is **on** that allow-list by design, so a stale secret passes it.
A hostname allow-list could not have caught the upgrade failure either, which is why the new gate
exercises the handshake instead of inspecting a string.
