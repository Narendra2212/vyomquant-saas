import re
import json

def parse_api_client():
    with open("algo22-terminal/src/apiClient.js", "r", encoding="utf-8") as f:
        content = f.read()

    # Find methods like: async getStrategies(...) { return this.get('/api/strategies', ...); }
    # or getStrategies: async (...) => ...
    # or this.get/post/put/delete('/api/...')
    
    calls = []
    # Match this.get(...), this.post(...), this.put(...), this.delete(...)
    matches = re.finditer(r'(?:this|apiClient|client)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
    for m in matches:
        method = m.group(1).upper()
        path = m.group(2)
        calls.append({
            "http_method": method,
            "path": path
        })
        
    # Also match methods defined on apiClient object / class
    func_matches = re.finditer(r'(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{[^}]*?(?:this|apiClient)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
    methods = []
    for fm in func_matches:
        methods.append({
            "method_name": fm.group(1),
            "http_verb": fm.group(2).upper(),
            "endpoint": fm.group(3)
        })

    return calls, methods

if __name__ == '__main__':
    calls, methods = parse_api_client()
    print(f"Discovered {len(calls)} raw HTTP calls in apiClient.js")
    print(f"Discovered {len(methods)} named API client helper methods:")
    for m in methods:
        print(f"  apiClient.{m['method_name']}() -> {m['http_verb']} {m['endpoint']}")

    with open('reports/api_client_methods.json', 'w', encoding='utf-8') as f:
        json.dump({"raw_calls": calls, "methods": methods}, f, indent=2)
