import json
import re
import os

def verify_all_module_endpoints():
    with open('reports/frontend_api_modules_endpoints.json', 'r', encoding='utf-8') as f:
        fe_endpoints = json.load(f)
        
    with open('reports/backend_routes_scanned.json', 'r', encoding='utf-8') as f:
        be_routes = json.load(f)
        
    # Build complete backend route paths as mounted in main.py
    main_file = "backend_app/main.py"
    with open(main_file, "r", encoding="utf-8") as f:
        content = f.read()

    includes = re.findall(r'app\.include_router\(\s*([\w\.]+)(?:,\s*prefix=["\']([^"\']+)["\'])?(?:,\s*tags=[^)]+)?\)', content)
    module_to_prefixes = {}
    for router_var, prefix in includes:
        mod_name = router_var.split('.')[0]
        pfx = prefix.strip() if prefix else ""
        if mod_name not in module_to_prefixes:
            module_to_prefixes[mod_name] = []
        module_to_prefixes[mod_name].append(pfx)

    # Calculate actual mounted paths for each backend route
    mounted_backend_routes = []
    for r in be_routes:
        file_base = os.path.splitext(os.path.basename(r['file']))[0]
        prefixes = module_to_prefixes.get(file_base, [""])
        
        for prefix in prefixes:
            path = r['path']
            if path.startswith("/"):
                full_path = f"{prefix}{path}" if prefix else path
            else:
                full_path = f"{prefix}/{path}" if prefix else f"/{path}"
                
            # Clean double slashes
            full_path = re.sub(r'/+', '/', full_path)
            if full_path.endswith('/') and len(full_path) > 1:
                full_path = full_path.rstrip('/')
                
            mounted_backend_routes.append({
                "file": r['file'],
                "method": r['method'],
                "path": full_path,
                "raw_path": r['path']
            })

    # Compare Frontend Module Endpoints vs Mounted Backend Routes
    results = []
    for fe in fe_endpoints:
        raw_ep = fe['endpoint']
        # Normalize: replace ${...} with {id} or {param}
        norm_ep = re.sub(r'\$\{[^}]+\}', '{param}', raw_ep)
        norm_ep = re.sub(r'/+', '/', norm_ep)
        if norm_ep.endswith('/') and len(norm_ep) > 1:
            norm_ep = norm_ep.rstrip('/')

        matched = False
        matched_route = None
        for be in mounted_backend_routes:
            be_norm = re.sub(r'\{[^}]+\}', '{param}', be['path'])
            if norm_ep == be_norm:
                matched = True
                matched_route = be
                break
                
        results.append({
            "frontend_module": fe['module'],
            "frontend_function": fe['function_name'],
            "frontend_raw_endpoint": raw_ep,
            "frontend_normalized": norm_ep,
            "matched": matched,
            "matched_backend_route": matched_route
        })

    mismatches = [r for r in results if not r['matched']]
    print(f"Total Frontend Module Endpoints: {len(results)}")
    print(f"Matched Backend Routes: {len(results) - len(mismatches)}")
    print(f"Mismatches / Broken Connections: {len(mismatches)}")
    
    for m in mismatches:
        print(f"\n[MISMATCH] {m['frontend_module']} -> {m['frontend_function']}")
        print(f"  Frontend Calls: {m['frontend_raw_endpoint']}")
        print(f"  Normalized:     {m['frontend_normalized']}")

    with open('reports/frontend_backend_mismatches.json', 'w', encoding='utf-8') as f:
        json.dump(mismatches, f, indent=2)

if __name__ == '__main__':
    verify_all_module_endpoints()
