import os
import re
import json

def audit_api_modules():
    modules_dir = "algo22-terminal/src/api/modules"
    endpoints = []
    
    for f in os.listdir(modules_dir):
        if f.endswith('.js'):
            file_path = os.path.join(modules_dir, f)
            with open(file_path, 'r', encoding='utf-8') as src:
                content = src.read()
                
            # Match get, post, put, del, patch calls
            matches = re.finditer(r'(\w+):\s*(?:async\s*)?\([^)]*\)\s*=>\s*(?:get|post|put|del|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
            for m in matches:
                endpoints.append({
                    "module": f,
                    "function_name": m.group(1),
                    "endpoint": m.group(2)
                })
                
            # Also match direct method declarations
            matches2 = re.finditer(r'(?:export\s+)?(?:const|function|async function)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*(?:get|post|put|del|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
            for m in matches2:
                endpoints.append({
                    "module": f,
                    "function_name": m.group(1),
                    "endpoint": m.group(2)
                })
                
    return endpoints

if __name__ == '__main__':
    eps = audit_api_modules()
    print(f"Extracted {len(eps)} endpoints from src/api/modules:")
    for e in eps:
        print(f"  [{e['module']}] {e['function_name']} -> {e['endpoint']}")
        
    os.makedirs('reports', exist_ok=True)
    with open('reports/frontend_api_modules_endpoints.json', 'w', encoding='utf-8') as f:
        json.dump(eps, f, indent=2)
