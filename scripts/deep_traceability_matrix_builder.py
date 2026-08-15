import json
import os
import re

def build_traceability_matrix():
    with open("reports/comprehensive_audit_dump.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    print(f"Loaded {len(data['ui_controls'])} UI controls, {len(data['frontend_api_calls'])} API calls, {len(data['mock_findings'])} mock indicators.")
    
    # 1. Inspect Mock Findings
    print("\n--- MOCK / FALLBACK FINDINGS IN FRONTEND ---")
    for m in data['mock_findings']:
        print(f"  [{m['file']}:{m['line']}] {m['content']}")

    # 2. Inspect Frontend WS Event Types
    print("\n--- FRONTEND WS EVENT CONSUMERS ---")
    events = set(e['event'] for e in data['frontend_ws_handlers'])
    for ev in sorted(events):
        print(f"  Event: '{ev}'")

    # 3. Inspect Backend WS Routes & Emitters
    print("\n--- BACKEND WS ROUTES & EMITTERS ---")
    for ws_ep in data['backend_ws_endpoints']:
        print(f"  WS Endpoint: {ws_ep['path']} (in {ws_ep['file']})")
    print(f"  Total WS Emitters identified: {len(data['backend_ws_emitters'])}")

if __name__ == '__main__':
    build_traceability_matrix()
