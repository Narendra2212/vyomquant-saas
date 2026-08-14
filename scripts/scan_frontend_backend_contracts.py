import os
import re
import json

def scan_frontend_api_calls():
    frontend_dir = "algo22-terminal/src"
    api_calls = []
    
    # Patterns to match API calls in React/JS/TS files
    patterns = [
        r'(?:axios|apiClient|api|client|fetch|http)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
        r'fetch\s*\(\s*[`\'"]([^`\'"]+)[`\'"](?:\s*,\s*\{[^}]*method:\s*[`\'"]([A-Z]+)[`\'"])?',
        r'axios\s*\(\s*\{\s*url:\s*[`\'"]([^`\'"]+)[`\'"]\s*,\s*method:\s*[`\'"]([A-Za-z]+)[`\'"]',
        r'(?:useQuery|useMutation)\s*\(\s*\[?[`\'"]([^`\'"]+)[`\'"]',
        r'[`\'"](/api/[^`\'"]+)[`\'"]'
    ]
    
    for root, _, files in os.walk(frontend_dir):
        for f in files:
            if f.endswith(('.js', '.jsx', '.ts', '.tsx')):
                file_path = os.path.join(root, f)
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                    content = src.read()
                    
                    # Search for direct endpoint references
                    matches = re.findall(r'[`\'"](/api/[^`\'"?]+)(?:\?[^`\'"]*)?[`\'"]', content)
                    for m in matches:
                        # try to identify method nearby
                        api_calls.append({
                            "file": os.path.relpath(file_path),
                            "endpoint": m,
                            "raw": m
                        })
                        
    return api_calls

def scan_backend_routes():
    backend_dir = "backend_app"
    routes = []
    
    for root, _, files in os.walk(backend_dir):
        for f in files:
            if f.endswith('.py'):
                file_path = os.path.join(root, f)
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                    lines = src.readlines()
                    for idx, line in enumerate(lines):
                        m = re.search(r'@(?:router|app)\.(get|post|put|delete|patch|websocket)\s*\(\s*["\']([^"\']+)["\']', line)
                        if m:
                            method = m.group(1).upper()
                            path = m.group(2)
                            routes.append({
                                "file": os.path.relpath(file_path),
                                "line": idx + 1,
                                "method": method,
                                "path": path
                            })
    return routes

if __name__ == '__main__':
    fe = scan_frontend_api_calls()
    be = scan_backend_routes()
    
    os.makedirs('reports', exist_ok=True)
    with open('reports/frontend_api_calls.json', 'w', encoding='utf-8') as f:
        json.dump(fe, f, indent=2)
    with open('reports/backend_routes_scanned.json', 'w', encoding='utf-8') as f:
        json.dump(be, f, indent=2)
        
    print(f"Scanned {len(fe)} frontend API references and {len(be)} backend route definitions.")
