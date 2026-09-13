# GoDaddy DNS Configuration Guide — vyomquant.in

Connects the GoDaddy-registered domain `vyomquant.in` to the **actual** live AWS
infrastructure in account `273709947018`, region `ap-southeast-1`.

Every AWS value in this document was read back from the live account with the AWS CLI.
Values that do **not** exist yet are marked `<TO BE GENERATED>` — do not guess them.

---

## 0. STOP — read this first

### 0.1 BLOCKER: the domain is on registry `clientHold`

`vyomquant.in` currently carries the EPP status **`clientHold`** (alongside
`clientDeleteProhibited`, `clientRenewProhibited`, `clientTransferProhibited`,
`clientUpdateProhibited`).

`clientHold` means the registry has **removed the domain from the `.in` zone**. The
consequence is absolute:

| Query | Result |
| :--- | :--- |
| `vyomquant.in` via Google DNS / Cloudflare / any public resolver | `NXDOMAIN` |
| `vyomquant.in` asked directly at `ns53.domaincontrol.com` | answers (zone exists) |

So the zone file at GoDaddy is fine, but **no user on the internet can resolve the domain**,
and — critically — **AWS ACM cannot complete DNS validation**, because ACM validates through
public resolvers. No certificate can be issued while `clientHold` is set.

**Nothing else in this document will work until `clientHold` is lifted.** This is a
registrar/registry action, not a DNS record change. Contact GoDaddy support. For `.in`
domains the usual causes are pending registrant contact verification (an unclicked
verification email), a WHOIS-accuracy hold, or a billing/compliance issue.

### 0.2 Current DNS state at GoDaddy (must be changed)

Read directly from `ns53.domaincontrol.com`:

| Type | Name | Current value | Meaning |
| :--- | :--- | :--- | :--- |
| A | `@` | `3.33.130.190` | GoDaddy **parking** IP |
| A | `@` | `15.197.148.33` | GoDaddy **parking** IP |
| CNAME | `www` | `@` | follows the parked apex |
| — | `api` | does not exist | — |
| — | `app` | does not exist | — |

`http://vyomquant.in` (via `Host:` header against the parking IP) returns GoDaddy's
lander page. The domain is parked; nothing is deployed on it.

Authoritative nameservers: `ns53.domaincontrol.com`, `ns54.domaincontrol.com`.
There is **no Route 53 hosted zone** in the account (`list-hosted-zones` → empty).

### 0.3 All three existing ACM certificates have FAILED

| Region | Certificate ID | Domains | Status |
| :--- | :--- | :--- | :--- |
| `ap-southeast-1` | `45c534c0-cc69-4700-a501-9c8573f48853` | `vyomquant.in`, `www`, `api` | **FAILED** |
| `ap-southeast-1` | `43bcf066-9baf-4910-bdf6-9fbac95a5ef5` | `vyomquant.in`, `*.vyomquant.in` | **FAILED** |
| `us-east-1` | `a919fa1f-2584-4705-b457-fe46af2635b6` | `vyomquant.in`, `www` | **FAILED** |

They failed because their DNS validation CNAMEs were never published and ACM's 72-hour
validation window expired. **Their validation records are dead — do not add them.** A new
`request-certificate` call mints brand-new validation record names/values.

---

## 1. The live AWS infrastructure you are pointing DNS at

```
                        ┌──────────────────────────────────────────┐
 Browser ──────────────▶│ CloudFront  EEOXECPHQ8SR0                │
                        │ d7d88qs4jmch.cloudfront.net              │
                        │ Aliases: NONE   WAF: none   Cert: default│
                        └───────┬──────────────────────┬───────────┘
                    default (/) │                      │ /api/*  /ws/*
                                │                      │ /health* /metrics*
                                │                      │ /openapi.json
                                ▼                      ▼
                   ┌────────────────────┐   ┌──────────────────────────────┐
                   │ S3 vyomquant-      │   │ ALB vyomquant-alb            │
                   │ frontend (OAC)     │   │ listener :80 HTTP only       │
                   │ SPA static assets  │   │ NO :443 listener             │
                   └────────────────────┘   └──────────────┬───────────────┘
                                                           │ vyomquant-api-tg :8000
                                                           ▼
                                            ┌──────────────────────────────┐
                                            │ ECS Fargate                  │
                                            │ vyomquant-cluster            │
                                            │ vyomquant-api-service-…      │
                                            │ 1/1 healthy                  │
                                            └──────────────────────────────┘
```

**The single most important fact for DNS design:** CloudFront is *already* a reverse proxy
for the API. It serves the SPA from S3 at `/` and forwards `/api/*`, `/ws/*`, `/health*`,
`/metrics*` and `/openapi.json` to the ALB. The production frontend therefore talks to the
API **same-origin** today (`VITE_API_URL=https://d7d88qs4jmch.cloudfront.net`), which is why
no CORS configuration is involved in the current deployment.

### 1.1 Verified DNS targets

| Purpose | Value | How verified |
| :--- | :--- | :--- |
| CloudFront distribution ID | `EEOXECPHQ8SR0` | `cloudfront get-distribution` |
| CloudFront domain name | `d7d88qs4jmch.cloudfront.net` | `cloudfront get-distribution` |
| ALB DNS name | `vyomquant-alb-1008390777.ap-southeast-1.elb.amazonaws.com` | `elbv2 describe-load-balancers` |
| ALB canonical hosted zone ID | `Z1LMS91P8CMLE5` | `elbv2 describe-load-balancers` |
| CloudFront ALIAS hosted zone ID | `Z2FDTNDATAQYW2` | AWS-published global constant for CloudFront |

### 1.2 What does NOT exist yet

- No CloudFront alternate domain names (`Aliases.Quantity = 0`)
- No ALB HTTPS (`:443`) listener, therefore no host-based routing rules
- No issued ACM certificate
- No `vyomquant-frontend-service` in ECS (`describe-services` → `MISSING`)
- Target group `vyomquant-web-tg` exists but is **orphaned**: `LoadBalancerArns = []`,
  port `3000`, health-check path `//`. Nothing serves it. **Never route a hostname to it** —
  it will return `503`.

---

## 2. Choose an architecture before touching DNS

### Option A — one CloudFront distribution, same-origin API *(recommended first step)*

Add `vyomquant.in`, `www.vyomquant.in` and `app.vyomquant.in` as **aliases on the existing
distribution `EEOXECPHQ8SR0`**. The SPA serves the landing page at `/` and the authenticated
app at `/app/*` (both already exist in the same React bundle — see `algo22-terminal/src/App.jsx`).
The API stays on the same origin via the existing `/api/*` cache behaviour.

- Requires: **one `us-east-1` ACM certificate** (CloudFront only accepts `us-east-1` certs)
- Requires: **no** ALB change, **no** HTTPS listener, **no** CORS change, **no** backend redeploy
- `api.vyomquant.in` is not needed for the app to work
- Frontend config: `VITE_API_URL=https://app.vyomquant.in` (or leave empty for same-origin)
- Lowest blast radius. Nothing about the running trading backend changes.

### Option B — dedicated `api.vyomquant.in` on the ALB

Additionally point `api.vyomquant.in` at the ALB with an `ap-southeast-1` certificate and a
new `:443` listener. This is what `infra/acm.sh` + `infra/alb.sh` implement.

- Requires: **`ap-southeast-1`** ACM certificate + new ALB HTTPS listener + host rules
- Makes browser traffic **cross-origin** → `CORS_ORIGINS` on the ECS task definition
  **must** be updated (it is currently `https://app.vyomquant.com,https://vyomquant.com`,
  i.e. `.com`, and a preflight from `https://app.vyomquant.in` is verified to return `400`
  with no `Access-Control-Allow-Origin`)
- Useful for third-party webhooks and non-browser API clients that should not go through
  the SPA's edge

**Recommendation:** do Option A to get the domain live safely, then add Option B when you
actually need a separate API hostname.

### 2.1 Three traps that will break production

1. **Do not add a blanket `HTTP :80 → HTTPS` redirect on the ALB.** CloudFront's ALB origin
   uses `OriginProtocolPolicy: http-only` on port 80. A `301` on `:80` would break every
   `/api/*` and `/ws/*` request the live SPA makes. Correct order: attach the cert to the
   ALB → change the CloudFront ALB origin to `api.vyomquant.in` with `https-only` (the
   `*.elb.amazonaws.com` name is **not** on the certificate, so SNI must use the custom
   hostname) → *only then* redirect `:80`.
2. **ALB host-header rules do not see the viewer's hostname.** The distribution uses the
   managed `AllViewerExceptHostHeader` origin request policy, so the ALB receives
   `Host: vyomquant-alb-1008390777…`. Host-based rules will not match CloudFront-proxied
   traffic. Keep the `:80` listener's default action forwarding to `vyomquant-api-tg`.
3. **Do not `terraform apply` the root `terraform/` module.** Its active `.tf` files declare
   a *parallel* VPC/ALB/ECS/CloudFront stack with `-production`-suffixed names that match
   nothing live, its `domain_name` default is the placeholder `example.com`, its
   `enable_custom_domain` default is `false`, and its local state file predates the current
   file contents. Applying it would build a second stack, not modify this one.

---

## 3. Option A — exact GoDaddy DNS records

### 3.1 GoDaddy cannot put a CNAME on the apex

`vyomquant.in` (the `@` record) cannot be a CNAME — that is a DNS protocol restriction, and
GoDaddy offers no `ALIAS`/`ANAME` alternative. Pick one:

**A1 — Move DNS hosting to Route 53 (recommended: apex serves the site directly)**

1. In AWS: create a public hosted zone for `vyomquant.in` (none exists today).
2. Add an **A / ALIAS** record for `@` → CloudFront `d7d88qs4jmch.cloudfront.net`
   (hosted zone ID `Z2FDTNDATAQYW2`), plus an **AAAA / ALIAS** to the same target
   (the distribution has `IsIPV6Enabled: true`).
3. Recreate `www` and `app` as ALIAS or CNAME records to the same distribution.
4. At GoDaddy, change the **nameservers** to the four Route 53 NS values from step 1.
5. Add the ACM validation CNAMEs in Route 53 instead of GoDaddy.

**A2 — Keep GoDaddy DNS (no nameserver change; apex becomes a redirect)**

Use GoDaddy **Domain Forwarding** for `@` → `https://www.vyomquant.in` (permanent 301,
forward-only). `https://vyomquant.in` then *redirects* rather than serving the SPA directly.
Everything else is a normal CNAME.

### 3.2 Records to ADD (Option A, path A2 — keeping GoDaddy DNS)

| TYPE | NAME | VALUE | PURPOSE | TTL |
| :--- | :--- | :--- | :--- | :--- |
| CNAME | `www` | `d7d88qs4jmch.cloudfront.net` | Serves the SPA (landing + app) via CloudFront | 600 |
| CNAME | `app` | `d7d88qs4jmch.cloudfront.net` | `app.vyomquant.in` → authenticated trading app | 600 |
| Forwarding | `@` | `https://www.vyomquant.in` (301, forward only) | Apex → www, because GoDaddy has no apex CNAME | n/a |
| CNAME | `<TO BE GENERATED>` | `<TO BE GENERATED>` | ACM DNS validation for `vyomquant.in` | 600 |
| CNAME | `<TO BE GENERATED>` | `<TO BE GENERATED>` | ACM DNS validation for `www.vyomquant.in` | 600 |
| CNAME | `<TO BE GENERATED>` | `<TO BE GENERATED>` | ACM DNS validation for `app.vyomquant.in` | 600 |

Use a short TTL (600 s) during cutover; raise to 3600 s once stable.

### 3.3 Records to REMOVE

| TYPE | NAME | VALUE | Why |
| :--- | :--- | :--- | :--- |
| A | `@` | `3.33.130.190` | GoDaddy parking IP — conflicts with forwarding/ALIAS |
| A | `@` | `15.197.148.33` | GoDaddy parking IP — conflicts with forwarding/ALIAS |

Remove these only at the moment of cutover, after the certificate is `ISSUED`.

### 3.4 Records that must NOT be changed

- **`NS`** — `ns53.domaincontrol.com` / `ns54.domaincontrol.com` (unless deliberately
  migrating to Route 53 per path A1)
- **`SOA`** — registrar-managed
- Any **`MX`**, **`TXT` (SPF/DKIM/DMARC)** or mail-provider `CNAME` records, if email is
  ever configured on this domain. Deleting these silently breaks email delivery.
- The ACM validation CNAMEs, **after** the certificate is issued — ACM re-checks them for
  automatic renewal. Leaving them in place forever is correct.

---

## 4. Option B — additional records for `api.vyomquant.in`

Only add these if you are also doing Option B.

| TYPE | NAME | VALUE | PURPOSE | TTL |
| :--- | :--- | :--- | :--- | :--- |
| CNAME | `api` | `vyomquant-alb-1008390777.ap-southeast-1.elb.amazonaws.com` | `api.vyomquant.in` → ALB → ECS API | 600 |
| CNAME | `<TO BE GENERATED>` | `<TO BE GENERATED>` | ACM DNS validation for `api.vyomquant.in` (`ap-southeast-1` cert) | 600 |

Option B additionally requires, on the AWS side:

1. An `ap-southeast-1` certificate covering `api.vyomquant.in` (`infra/acm.sh`).
2. An ALB `:443` HTTPS listener with that certificate (`infra/alb.sh`).
3. `CORS_ORIGINS` on the `vyomquant-api` task definition updated to include
   `https://vyomquant.in`, `https://www.vyomquant.in`, `https://app.vyomquant.in`.
4. Re-read §2.1 before touching the `:80` listener.

---

## 5. Certificate request

Run from a shell with AWS credentials for account `273709947018`:

```bash
# Option A — CloudFront requires us-east-1
AWS_REGION=us-east-1 ./infra/acm.sh

# Option B — ALB requires the ALB's own region
AWS_REGION=ap-southeast-1 ./infra/acm.sh
```

`infra/acm.sh` prints the exact validation CNAME host/value pairs to paste into DNS. It
ignores `FAILED` certificates and will request a fresh one (see the status filter in that
script).

---

## 6. Verification

Nothing below can pass until `clientHold` is lifted (§0.1).

```bash
# 1. Domain resolves publicly at all (this is the clientHold gate)
nslookup vyomquant.in 8.8.8.8

# 2. Validation records are published
nslookup -type=CNAME <VALIDATION_HOST> 8.8.8.8

# 3. Certificate reaches ISSUED
aws acm describe-certificate --certificate-arn <CERT_ARN> \
  --region <us-east-1|ap-southeast-1> --query "Certificate.Status"

# 4. Hostnames resolve to the right AWS resource
nslookup -type=CNAME app.vyomquant.in 8.8.8.8   # → d7d88qs4jmch.cloudfront.net
nslookup -type=CNAME api.vyomquant.in 8.8.8.8   # → vyomquant-alb-…elb.amazonaws.com  (Option B)

# 5. HTTPS + TLS chain
curl -Iv https://app.vyomquant.in/
curl -s  https://app.vyomquant.in/health

# 6. CORS, only relevant for Option B
curl -s -o /dev/null -D - -X OPTIONS https://api.vyomquant.in/api/market/health \
  -H "Origin: https://app.vyomquant.in" -H "Access-Control-Request-Method: GET"
#   expect: HTTP/1.1 200 and  access-control-allow-origin: https://app.vyomquant.in
```

Current baseline for comparison (verified working today):

```bash
curl -s https://d7d88qs4jmch.cloudfront.net/health
# {"status":"ok","mode":"production","services":{"redis":"connected", …}}
```

---

## 7. Rollback

DNS changes are reversible; certificates and listeners are additive.

| Change | Rollback |
| :--- | :--- |
| CloudFront aliases added | Remove the aliases; `d7d88qs4jmch.cloudfront.net` keeps working throughout |
| `www` / `app` / `api` CNAME added | Delete the record; TTL 600 s means ≤10 min of propagation |
| Apex parking A records removed | Re-add `3.33.130.190` and `15.197.148.33` |
| ALB `:443` listener added | `elbv2 delete-listener`; the `:80` listener CloudFront uses is untouched |
| `CORS_ORIGINS` changed | Redeploy the previous task definition revision (`vyomquant-api:145` at the time of writing) |

Do **not** use `infra/rollback.sh` for DNS work — it detaches the target group from the ECS
service, which takes the API offline.
