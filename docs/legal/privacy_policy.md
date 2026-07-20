# Privacy Policy — Aerora Quant Platform Beta

*Effective Date: June 5, 2026*

Aerora Dynamics ("we", "us", or "our") is committed to protecting your privacy. This Privacy Policy explains how we collect, store, and process your information when you use the Aerora Quant Platform.

## 1. Information We Collect
- **Account Information**: Sign-up and profile data (email address, username) managed securely via Supabase Auth.
- **Exchange Keys**: API keys and secrets for exchange connectivity (e.g., Binance, dYdX, OKX). These are encrypted locally using AES-256 and never stored in plain text.
- **Telemetry Data**: Backtesting logs, latency metrics, and performance history recorded to monitor and optimize system stability.

## 2. How We Use Your Information
- To authenticate your access and enforce rate limits.
- To execute simulated paper trades and route automated orders to your connected exchange endpoints.
- To send critical system alerts and notifications via active channels (Email, Discord, Telegram) based on your notification settings.

## 3. Data Storage & Custody
- **Authentication**: Managed via Supabase, subject to their security controls.
- **Billing History**: Subscription records, default payment methods, and invoices are stored securely in our cloud-hosted Supabase database.
- **Telemetry**: Time-series logs are stored in our secure database instance.

## 4. Sharing of Data
We do not sell, rent, or trade your personal data or API configurations with third parties. Data is shared only with payment processors (Stripe/Razorpay) to process checkouts and webhook-based subscription status updates.

## 5. Security Measures
We use industry-standard security protocols, including AES-256 encryption for exchange vault keys, JWT verification, and Role-Based Access Control (RLS) to safeguard database records.

## 6. Your Rights
You may inspect, modify, or request the deletion of your profile data or saved exchange keys at any time via the User Profile and Exchange Vault settings in the terminal UI.
