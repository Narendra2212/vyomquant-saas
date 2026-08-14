import os
import re
import json

def audit_frontend_pages():
    pages_dir = "algo22-terminal/src/pages"
    results = []
    
    for f in os.listdir(pages_dir):
        if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
            file_path = os.path.join(pages_dir, f)
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                content = src.read()
                
            # Extract API calls made
            api_calls = re.findall(r'(?:apiClient|axios|fetch|api)\.(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
            direct_calls = re.findall(r'[`\'"](/api/[^`\'"?]+)(?:\?[^`\'"]*)?[`\'"]', content)
            
            results.append({
                "page": f,
                "api_calls": api_calls,
                "endpoints_referenced": list(set(direct_calls))
            })
            
    return results

if __name__ == '__main__':
    data = audit_frontend_pages()
    for p in data:
        print(f"Page: {p['page']} -> {len(p['endpoints_referenced'])} endpoints:")
        for ep in p['endpoints_referenced']:
            print(f"   {ep}")
            
    os.makedirs('reports', exist_ok=True)
    with open('reports/frontend_page_api_map.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
