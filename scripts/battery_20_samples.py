import time
import requests
import json
import statistics
import sys

BASE_URL = "https://d7d88qs4jmch.cloudfront.net"
SUPABASE_URL = "https://wrkexcjqnidkdrayhlsi.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indya2V4Y2pxbmlka2RyYXlobHNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ3ODkyOTIsImV4cCI6MjA5MDM2NTI5Mn0.NTXqianuwy4xLw4FMY09Z0Q70wf7KWCIzkejaU2sR8s"

print("=" * 80)
print("AUTHENTICATING FOR 20-SAMPLE BENCHMARK BATTERY")
print("=" * 80)

auth_resp = requests.post(
    f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
    headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
    json={"email": "test@test.com", "password": "test123"},
    timeout=10
)
if auth_resp.status_code != 200:
    print(f"Authentication failed: {auth_resp.status_code} {auth_resp.text}")
    sys.exit(1)

auth_data = auth_resp.json()
token = auth_data["access_token"]
user_id = auth_data["user"]["id"]
print(f"Authenticated as user {user_id[:8]}...")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 BenchmarkBattery/2.0"
}

endpoints = [
    ("Dashboard 30d (ORIGINAL)", "GET", "/api/dashboard?equity_days=30"),
    ("Dashboard Overview", "GET", "/api/dashboard/overview"),
    ("Signal Trace Audit", "GET", "/api/signal-trace/signals?limit=50&offset=0"),
    ("Strategies List", "GET", "/api/strategies"),
    ("Risk Settings", "GET", "/api/risk/settings"),
    ("Connected Exchanges", "GET", "/api/exchanges"),
    ("Billing Currency", "GET", "/api/billing/currency"),
    ("Billing Plans", "GET", "/api/billing/plans"),
    ("Billing Entitlements", "GET", "/api/billing/entitlements"),
    ("Notifications (ORIGINAL)", "GET", "/api/notifications?limit=100")
]

results = {}

print("\n" + "=" * 80)
print("EXECUTING 20 SAMPLES PER ENDPOINT")
print("=" * 80)

for name, method, path in endpoints:
    url = f"{BASE_URL}{path}"
    samples = []
    statuses = []
    redirects_307_308 = 0
    errors_5xx = 0
    errors_4xx = 0
    
    print(f"\nTesting: {name} ({method} {path})...", flush=True)
    
    for i in range(20):
        t0 = time.perf_counter()
        try:
            resp = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
            elapsed = (time.perf_counter() - t0) * 1000
            status = resp.status_code
            statuses.append(status)
            samples.append(elapsed)
            
            if status in (307, 308):
                redirects_307_308 += 1
            elif status >= 500:
                errors_5xx += 1
            elif status >= 400 and status not in (404,):
                errors_4xx += 1
                
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            samples.append(elapsed)
            statuses.append(0)
            errors_5xx += 1
            print(f"  Sample {i+1} EXCEPTION: {e}")
            
        time.sleep(0.05) # short pacing between requests
        
    samples_sorted = sorted(samples)
    min_val = samples_sorted[0]
    max_val = samples_sorted[-1]
    mean_val = statistics.mean(samples)
    p50_val = statistics.median(samples)
    # 95th percentile index in 20 samples = round(0.95 * 20) - 1 = 18th item (0-indexed)
    p95_val = samples_sorted[int(0.95 * len(samples_sorted)) - 1] if len(samples_sorted) >= 20 else max_val
    
    passed = p95_val < 500.0 and errors_5xx == 0 and redirects_307_308 == 0
    
    results[path] = {
        "name": name,
        "method": method,
        "path": path,
        "samples_count": len(samples),
        "min_ms": round(min_val, 1),
        "p50_ms": round(p50_val, 1),
        "p95_ms": round(p95_val, 1),
        "max_ms": round(max_val, 1),
        "mean_ms": round(mean_val, 1),
        "errors_5xx": errors_5xx,
        "errors_4xx": errors_4xx,
        "redirects_307_308": redirects_307_308,
        "passed": passed
    }
    
    status_str = "PASS" if passed else "FAIL"
    print(f"  [{status_str}] Min: {min_val:.1f}ms | P50: {p50_val:.1f}ms | P95: {p95_val:.1f}ms | Max: {max_val:.1f}ms | Mean: {mean_val:.1f}ms | 5xx: {errors_5xx} | 307/308: {redirects_307_308}")

print("\n" + "=" * 80)
print("SUMMARY TABLE (20 SAMPLES PER ENDPOINT)")
print("=" * 80)
print(f"{'Endpoint':<42} | {'P50 (ms)':<8} | {'P95 (ms)':<8} | {'Max (ms)':<8} | {'5xx':<4} | {'307/308':<7} | {'Result':<6}")
print("-" * 90)

all_battery_pass = True
for path, res in results.items():
    print(f"{res['name']:<42} | {res['p50_ms']:<8.1f} | {res['p95_ms']:<8.1f} | {res['max_ms']:<8.1f} | {res['errors_5xx']:<4} | {res['redirects_307_308']:<7} | {'PASS' if res['passed'] else 'FAIL'}")
    if not res['passed']:
        all_battery_pass = False

print("=" * 80)
print(f"OVERALL BATTERY RESULT: {'ALL PASS' if all_battery_pass else 'FAIL'}")
print("=" * 80)

with open("reports/battery_20_samples.json", "w") as f:
    json.dump(results, f, indent=2)

sys.exit(0 if all_battery_pass else 1)
