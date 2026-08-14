import os
import re
import json

def audit_full_frontend_features():
    src_dir = "algo22-terminal/src"
    findings = []
    
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                file_path = os.path.join(root, f)
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as src:
                    content = src.read()

                # Check 1: WebSocket connection paths
                ws_matches = re.findall(r'[\'"`](\/ws\/[^\'"`]+)[\'"`]', content)
                
                # Check 2: API calls
                api_matches = re.findall(r'[\'"`](\/api\/[^\'"`?]+)(?:\?[^\'"`]*)?[\'"`]', content)

                # Check 3: Hardcoded mock indicators / flags
                mock_indicators = []
                if "MOCK" in content or "mockData" in content or "dummyData" in content or "USE_MOCK" in content:
                    # check if mock is active in non-test code
                    mock_matches = re.findall(r'(?:const|let|var)\s+(?:USE_MOCK|useMock|mockData|dummyData)\s*=\s*([^;]+);', content)
                    for mm in mock_matches:
                        mock_indicators.append(mm.strip())

                findings.append({
                    "file": os.path.relpath(file_path),
                    "ws_endpoints": list(set(ws_matches)),
                    "api_endpoints": list(set(api_matches)),
                    "mock_indicators": mock_indicators
                })
                
    return findings

if __name__ == '__main__':
    res = audit_full_frontend_features()
    
    all_ws = set()
    all_api = set()
    files_with_mocks = []
    
    for r in res:
        all_ws.update(r['ws_endpoints'])
        all_api.update(r['api_endpoints'])
        if r['mock_indicators']:
            files_with_mocks.append(r)
            
    print(f"Total Unique WebSocket Endpoints in Frontend: {len(all_ws)}")
    for ws in sorted(all_ws):
        print(f"   WS: {ws}")
        
    print(f"\nTotal Unique API Endpoints in Frontend: {len(all_api)}")
    print(f"\nFiles with Mock Indicators: {len(files_with_mocks)}")
    for f in files_with_mocks:
        print(f"   Mock in {f['file']}: {f['mock_indicators']}")

    os.makedirs('reports', exist_ok=True)
    with open('reports/frontend_full_feature_audit.json', 'w', encoding='utf-8') as out:
        json.dump({
            "all_ws_endpoints": list(all_ws),
            "all_api_endpoints": list(all_api),
            "files_with_mocks": files_with_mocks
        }, out, indent=2)
