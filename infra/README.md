# VyomQuant AWS Infrastructure & Production ALB Deployment Guide

This directory contains production-ready Infrastructure-as-Code (IaC) automation scripts for provisioning an **AWS Application Load Balancer (ALB)**, **ACM SSL/TLS Certificates**, **Target Groups**, **Host-Based Routing**, and attaching existing **ECS Fargate Services** without downtime or service recreation.

---

## 🏗️ Architecture Overview

```
                      [ GoDaddy DNS ]
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   vyomquant.in / www                 api.vyomquant.in
            │                                 │
            └────────────────┬────────────────┘
                             │
                             ▼
              [ AWS Application Load Balancer ]
                             │
                 ┌───────────┴───────────┐
                 │ Port 443 (HTTPS)      │ (ACM Certificate)
                 └───────────┬───────────┘
                             │
             ┌───────────────┴───────────────┐
             │ Host-Based Routing Rules      │
             └───────┬───────────────┬───────┘
                     │               │
  api.vyomquant.in   │               │   vyomquant.in / www
                     ▼               ▼
        ┌──────────────────┐   ┌──────────────────┐
        │ VyomQuant API TG │   │ VyomQuant Web TG │
        │ (Port 8000)      │   │ (Port 8080)      │
        └────────┬─────────┘   └────────┬─────────┘
                 │                      │
                 ▼                      ▼
        ┌──────────────────┐   ┌──────────────────┐
        │ ECS Service:     │   │ ECS Service:     │
        │ vyomquant-api    │   │ vyomquant-web    │
        └──────────────────┘   └──────────────────┘
```

---

## 🛠️ Prerequisites

1. **AWS CLI v2** installed and configured with administrative permissions.
2. Valid AWS credentials set in your environment:
   ```bash
   export AWS_ACCESS_KEY_ID="AKIA..."
   export AWS_SECRET_ACCESS_KEY="..."
   export AWS_REGION="ap-southeast-1"
   ```
3. Existing ECS Fargate Cluster (`vyomquant-cluster`) and ECS Service (`vyomquant-api-service-cjema2sl`).

---

## 🚀 Execution Instructions

All scripts are **idempotent**. Re-running them will reuse existing load balancers, target groups, certificates, and listener rules without creating duplicate infrastructure.

### 1. Execute Master Orchestrator Script

```bash
chmod +x infra/*.sh
./infra/deploy.sh
```

### 2. Individual Component Execution (Optional)

- **ACM SSL Certificate Request & DNS Validation Output**:
  ```bash
  ./infra/acm.sh
  ```
- **ALB, Target Groups & Listener Provisioning**:
  ```bash
  ./infra/alb.sh
  ```
- **Attach ECS Services to Target Groups**:
  ```bash
  ./infra/ecs.sh
  ```

---

## 🌐 GoDaddy DNS Configuration

After running `infra/acm.sh` or `infra/deploy.sh`, follow the exact DNS records guide in [infra/dns.md](file:///c:/aerora_quant_backend_updated_final1/infra/dns.md).

1. Add CNAME records for **ACM DNS Validation** to issue SSL certificate.
2. Add CNAME record for `api.vyomquant.in` pointing to your ALB DNS Name.
3. Configure `vyomquant.in` / `www.vyomquant.in` CNAME or forwarding to ALB DNS Name.

---

## 🔄 GitHub Actions Pipeline Compatibility

The existing GitHub Actions workflow ([03-deploy.yml](file:///c:/aerora_quant_backend_updated_final1/.github/workflows/03-deploy.yml)) will continue to work seamlessly. 

The pipeline updates container images and task definitions via `scripts/ecs_deploy_and_diagnose.py` without interfering with or recreating the Application Load Balancer infrastructure.

---

## ⏪ Rollback Procedure

If you need to detach the Application Load Balancer target groups from ECS services in an emergency:

```bash
./infra/rollback.sh
```
