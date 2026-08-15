import os
import re
import json
import glob
from collections import defaultdict

def run_comprehensive_audit():
    print("Starting Comprehensive Repository-Wide Forensic Audit...")
    
    # 1. SCAN ALL FRONTEND UI COMPONENTS & PAGES
    fe_dir = "algo22-terminal/src"
    fe_files = []
    for root, _, files in os.walk(fe_dir):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                fe_files.append(os.path.join(root, f))
                
    print(f"Discovered {len(fe_files)} frontend source files.")
    
    # Analyze UI Controls in Pages & Components
    ui_controls = []
    mock_findings = []
    fe_api_calls = []
    fe_ws_handlers = []
    
    for fpath in fe_files:
        rel_path = os.path.relpath(fpath).replace("\\", "/")
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            content = "".join(lines)
            
        # Scan for Buttons, Form submits, OnClick handlers
        for idx, line in enumerate(lines, 1):
            btn_match = re.search(r'<button[^>]*onClick=\{([^}]+)\}[^>]*>(.*?)</button>', line, re.IGNORECASE)
            if btn_match:
                ui_controls.append({
                    "file": rel_path,
                    "line": idx,
                    "control": "button",
                    "handler": btn_match.group(1).strip(),
                    "label": re.sub(r'<[^>]+>', '', btn_match.group(2)).strip()
                })
                
            # Scan for Mock / Dummy / Hardcoded patterns
            if any(k in line for k in ["mockData", "dummyData", "MOCK_", "fakeData", "sampleData", "TODO", "FIXME", "Math.random()", "setTimeout"]):
                if not any(ign in rel_path for ign in ["test", "stories", "setupTests", "test-"]):
                    mock_findings.append({
                        "file": rel_path,
                        "line": idx,
                        "content": line.strip()
                    })

        # Scan for API calls (via apiClient, publicGet, axios, fetch)
        api_matches = re.finditer(r'(?:apiClient|client|publicGet|fetch|axios)\.(get|post|put|del|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
        for m in api_matches:
            fe_api_calls.append({
                "file": rel_path,
                "method": m.group(1).upper(),
                "endpoint": m.group(2)
            })
            
        # Scan for direct URL literals like "/api/..."
        direct_urls = re.findall(r'[`\'"](/api/[^`\'"?\s]+)[`\'"]', content)
        for u in set(direct_urls):
            fe_api_calls.append({
                "file": rel_path,
                "method": "DIRECT",
                "endpoint": u
            })

        # Scan for WS subscriptions & event listeners
        ws_subs = re.finditer(r'(?:wsClient|socket|ws)\.(?:subscribe|on|addEventListener)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
        for ws in ws_subs:
            fe_ws_handlers.append({
                "file": rel_path,
                "event": ws.group(1)
            })
            
        # Also check switch(data.type) or if(data.event === ...)
        event_types = re.findall(r'(?:case|data\.type\s*===|data\.event\s*===|data\.channel\s*===)\s*[\'"]([^\'"]+)[\'"]', content)
        for et in set(event_types):
            fe_ws_handlers.append({
                "file": rel_path,
                "event": et
            })

    # 2. SCAN ALL BACKEND ROUTERS & HANDLERS
    be_dir = "backend_app"
    be_files = []
    for root, _, files in os.walk(be_dir):
        for f in files:
            if f.endswith('.py'):
                be_files.append(os.path.join(root, f))
                
    print(f"Discovered {len(be_files)} backend source files.")
    
    be_endpoints = []
    be_ws_endpoints = []
    be_ws_emitters = []
    
    # Map main.py router mounts
    with open("backend_app/main.py", "r", encoding="utf-8") as f:
        main_src = f.read()
        
    mount_matches = re.findall(r'app\.include_router\(\s*([\w\.]+)(?:,\s*prefix=["\']([^"\']+)["\'])?', main_src)
    router_mounts = defaultdict(list)
    for r_var, pfx in mount_matches:
        mod = r_var.split('.')[0]
        router_mounts[mod].append(pfx.strip() if pfx else "")

    for fpath in be_files:
        rel_path = os.path.relpath(fpath).replace("\\", "/")
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            content = "".join(lines)
            
        file_base = os.path.splitext(os.path.basename(fpath))[0]
        prefixes = router_mounts.get(file_base, [""])
        
        # Match route decorators: @router.get("/..."), @app.post("/..."), etc.
        route_matches = re.finditer(r'@(?:router|app)\.(get|post|put|delete|patch|websocket)\s*\(\s*["\']([^"\']+)["\'](?:,\s*response_model=[^,\)]+)?', content)
        for rm in route_matches:
            verb = rm.group(1).upper()
            sub_path = rm.group(2)
            
            if verb == "WEBSOCKET":
                be_ws_endpoints.append({
                    "file": rel_path,
                    "path": sub_path
                })
            else:
                for pfx in prefixes:
                    full_p = f"{pfx}{sub_path}" if sub_path.startswith("/") else f"{pfx}/{sub_path}"
                    full_p = re.sub(r'/+', '/', full_p)
                    if full_p.endswith('/') and len(full_p) > 1:
                        full_p = full_p.rstrip('/')
                    be_endpoints.append({
                        "file": rel_path,
                        "method": verb,
                        "path": full_p,
                        "sub_path": sub_path
                    })

        # Match WebSocket broadcast emissions
        ws_broadcasts = re.finditer(r'(?:ws_manager|websocket_manager|broadcast|publish|RealtimeSync)\.(?:broadcast|publish|send_personal_message|sync_\w+)\s*\(\s*([^,\)]+)', content)
        for wb in ws_broadcasts:
            be_ws_emitters.append({
                "file": rel_path,
                "call": wb.group(0).strip()
            })

    report = {
        "summary": {
            "total_frontend_files": len(fe_files),
            "total_backend_files": len(be_files),
            "total_ui_controls": len(ui_controls),
            "total_frontend_api_calls": len(fe_api_calls),
            "total_frontend_ws_handlers": len(fe_ws_handlers),
            "total_backend_endpoints": len(be_endpoints),
            "total_backend_ws_endpoints": len(be_ws_endpoints),
            "total_mock_indicators": len(mock_findings)
        },
        "ui_controls": ui_controls,
        "mock_findings": mock_findings,
        "frontend_api_calls": fe_api_calls,
        "frontend_ws_handlers": fe_ws_handlers,
        "backend_endpoints": be_endpoints,
        "backend_ws_endpoints": be_ws_endpoints,
        "backend_ws_emitters": be_ws_emitters
    }
    
    os.makedirs("reports", exist_ok=True)
    with open("reports/comprehensive_audit_dump.json", "w", encoding="utf-8") as out:
        json.dump(report, out, indent=2)
        
    print("\nAUDIT INVENTORY SUMMARY:")
    print(f"  Frontend Files Scanned:       {len(fe_files)}")
    print(f"  Backend Files Scanned:        {len(be_files)}")
    print(f"  UI Controls / Buttons:        {len(ui_controls)}")
    print(f"  Frontend API Call Instances:  {len(fe_api_calls)}")
    print(f"  Frontend WS Events Scanned:   {len(fe_ws_handlers)}")
    print(f"  Backend REST Endpoints:       {len(be_endpoints)}")
    print(f"  Backend WS Endpoints:         {len(be_ws_endpoints)}")
    print(f"  Mock / Fallback Indicators:   {len(mock_findings)}")

if __name__ == '__main__':
    run_comprehensive_audit()
