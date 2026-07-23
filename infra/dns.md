# GoDaddy DNS Configuration Guide for VyomQuant

This document provides the exact DNS records required to connect your GoDaddy domain (`vyomquant.in`) to the AWS Application Load Balancer (ALB) and validate AWS Certificate Manager (ACM) SSL/TLS certificates.

---

## 1. Required GoDaddy DNS Records

Log in to your **GoDaddy Domain Control Center** → **Manage DNS** for `vyomquant.in` and add/update the following records:

### A. Core Routing Records

| Type | Host (Name) | Points To (Value) | TTL | Description |
| :--- | :--- | :--- | :--- | :--- |
| **CNAME** | `api` | `<ALB-DNS-NAME>` *(e.g. `vyomquant-alb-123456789.ap-southeast-1.elb.amazonaws.com`)* | 1/2 Hour (1800s) | Routes `api.vyomquant.in` to FastAPI ECS Backend |
| **CNAME** | `www` | `@` *(or `<ALB-DNS-NAME>`)* | 1/2 Hour (1800s) | Routes `www.vyomquant.in` to Frontend |
| **A / Forwarding** | `@` | ALB IP / GoDaddy Domain Forwarding to `https://www.vyomquant.in` | 1/2 Hour (1800s) | Main domain routing |

> **Note on Root Domain (@) A Records**: GoDaddy does not support CNAME records on the root `@` domain. You can either:
> 1. Use GoDaddy's **Domain Forwarding** feature to redirect `http://vyomquant.in` → `https://www.vyomquant.in`.
> 2. Use AWS Route53 for DNS hosting (ALIAS record pointing directly to AWS ALB without IP maintenance).

---

### B. ACM SSL Certificate Validation CNAME Records

AWS ACM requires CNAME records to verify domain ownership before issuing the SSL/TLS certificate for HTTPS. Run `./infra/acm.sh` to generate the exact dynamic values for your certificate.

| Host (Name) | Record Type | Value (Points to) | TTL | Domain Covered |
| :--- | :--- | :--- | :--- | :--- |
| `_x1y2z3...vyomquant.in.` | **CNAME** | `_a1b2c3...acm-validations.aws.` | 1/2 Hour | `vyomquant.in` |
| `_d4e5f6...www.vyomquant.in.` | **CNAME** | `_g7h8i9...acm-validations.aws.` | 1/2 Hour | `www.vyomquant.in` |
| `_j0k1l2...api.vyomquant.in.` | **CNAME** | `_m3n4o5...acm-validations.aws.` | 1/2 Hour | `api.vyomquant.in` |

*Note: Omit the domain suffix `.vyomquant.in.` when pasting into GoDaddy Host field if GoDaddy automatically appends your domain name.*

---

## 2. Verification Steps

Once DNS records are saved in GoDaddy:

1. **Verify DNS Propagation**:
   ```bash
   dig +short CNAME api.vyomquant.in
   nslookup api.vyomquant.in
   ```
2. **Verify ACM Validation**:
   ```bash
   aws acm describe-certificate --certificate-arn <CERT_ARN> --region ap-southeast-1 --query "Certificate.Status"
   ```
   *Expected Status:* `"ISSUED"`

3. **Test Endpoint HTTPS Connectivity**:
   ```bash
   curl -Iv https://api.vyomquant.in/health
   ```
