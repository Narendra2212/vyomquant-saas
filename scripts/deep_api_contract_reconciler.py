import os
import re
import json

def analyze_main_routers():
    main_file = "backend_app/main.py"
    with open(main_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Pattern for include_router(..., prefix="...")
    includes = re.findall(r'app\.include_router\(\s*([\w\.]+)(?:,\s*prefix=["\']([^"\']+)["\'])?(?:,\s*tags=[^)]+)?\)', content)
    router_prefixes = {}
    for router_var, prefix in includes:
        router_prefixes[router_var.strip()] = prefix.strip() if prefix else ""
        
    print(f"Discovered {len(router_prefixes)} router inclusions in backend_app/main.py:")
    for r, p in router_prefixes.items():
        print(f"  {r} -> prefix: '{p}'")
    return router_prefixes

def reconcile_endpoints():
    with open('reports/frontend_api_calls.json', 'r', encoding='utf-8') as f:
        fe_calls = json.load(f)
    with open('reports/backend_routes_scanned.json', 'r', encoding='utf-8') as f:
        be_routes = json.load(f)

    # Collect unique frontend endpoints
    fe_endpoints = set()
    for item in fe_calls:
        ep = item['endpoint']
        # Normalize parameterized paths e.g. /api/strategies/${id} -> /api/strategies/{id}
        norm_ep = re.sub(r'\$\{[^}]+\}', '{id}', ep)
        norm_ep = re.sub(r'\/[0-9a-fA-F-]{36}', '/{id}', norm_ep)
        fe_endpoints.add(norm_ep)

    # Collect backend endpoints
    be_endpoints = set()
    for item in be_routes:
        be_endpoints.add(item['path'])

    print(f"\nUnique Normalized Frontend Endpoints: {len(fe_endpoints)}")
    print(f"Unique Backend Route Paths: {len(be_endpoints)}")

    # Check for direct matches or potential mismatches
    unmatched_fe = []
    for fe_ep in sorted(fe_endpoints):
        matched = False
        for be_ep in be_endpoints:
            # Check if backend path matches frontend path
            be_norm = re.sub(r'\{[^}]+\}', '{id}', be_ep)
            if fe_ep == be_norm or fe_ep.startswith(be_norm):
                matched = True
                break
        if not matched:
            unmatched_fe.append(fe_ep)

    print(f"\nPotential Unmatched Frontend Calls: {len(unmatched_fe)}")
    for u in unmatched_fe:
        print(f"  [UNMATCHED] {u}")

    with open('reports/unmatched_frontend_calls.json', 'w', encoding='utf-8') as f:
        json.dump(unmatched_fe, f, indent=2)

if __name__ == '__main__':
    analyze_main_routers()
    reconcile_endpoints()
