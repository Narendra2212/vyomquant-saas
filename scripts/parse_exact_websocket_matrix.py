import os
import re
import json

def parse_websocket_matrix():
    print("Extracting WebSocket Architecture Matrix...")
    
    # 1. Backend WebSocket Handlers & Channels
    be_ws_routes = []
    ws_routes_file = "backend_app/api_ws/ws_routes.py"
    with open(ws_routes_file, "r", encoding="utf-8") as f:
        content = f.read()
        
    routes = re.findall(r'@ws_router\.websocket\(\s*["\']([^"\']+)["\']\s*\)\s*\nasync def (\w+)', content)
    for path, func in routes:
        be_ws_routes.append({
            "path": path,
            "handler": func,
            "file": ws_routes_file
        })

    # Also check health websocket
    health_ws_file = "backend_app/routers/health_websocket.py"
    if os.path.exists(health_ws_file):
        with open(health_ws_file, "r", encoding="utf-8") as f:
            hcontent = f.read()
        hroutes = re.findall(r'@router\.websocket\(\s*["\']([^"\']+)["\']\s*\)\s*\nasync def (\w+)', hcontent)
        for path, func in hroutes:
            be_ws_routes.append({
                "path": f"/health{path}",
                "handler": func,
                "file": health_ws_file
            })

    # 2. Backend Event Emitters (RealtimeSync, telemetry_broadcaster, ws_manager)
    be_emitters = []
    be_files = []
    for root, _, files in os.walk("backend_app"):
        for f in files:
            if f.endswith('.py'):
                be_files.append(os.path.join(root, f))
                
    for fpath in be_files:
        rel_path = os.path.relpath(fpath).replace("\\", "/")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        for idx, line in enumerate(lines, 1):
            if "RealtimeSync.sync_" in line or "broadcast(" in line or "send_personal_message(" in line or "ws_manager." in line:
                be_emitters.append({
                    "file": rel_path,
                    "line": idx,
                    "code": line.strip()
                })

    # 3. Frontend WS Listeners (wsClient.on, event handlers)
    fe_listeners = []
    fe_files = []
    for root, _, files in os.walk("algo22-terminal/src"):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                fe_files.append(os.path.join(root, f))
                
    for fpath in fe_files:
        rel_path = os.path.relpath(fpath).replace("\\", "/")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            content = "".join(lines)
            
        matches = re.finditer(r'wsClient\.on\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:async\s*)?\(([^)]*)\)\s*=>', content)
        for m in matches:
            fe_listeners.append({
                "file": rel_path,
                "event": m.group(1),
                "params": m.group(2)
            })

        # Also search normalizeTelemetryEvent or case '...' inside message dispatchers
        case_matches = re.finditer(r'case\s+[\'"]([a-zA-Z0-9_\-\:]+)[\'"]\s*:', content)
        for cm in case_matches:
            fe_listeners.append({
                "file": rel_path,
                "event": cm.group(1),
                "params": "switch-case"
            })

    print(f"Backend WebSocket Endpoints: {len(be_ws_routes)}")
    for bws in be_ws_routes:
        print(f"  Route: {bws['path']} -> {bws['handler']} ({bws['file']})")

    print(f"\nBackend Emitters Found: {len(be_emitters)}")
    print(f"Frontend WS Listeners Found: {len(fe_listeners)}")
    
    unique_fe_events = sorted(list(set(l['event'] for l in fe_listeners)))
    print(f"\nUnique Frontend Events ({len(unique_fe_events)}):")
    for ev in unique_fe_events:
        matching_listeners = [l['file'] for l in fe_listeners if l['event'] == ev]
        print(f"  '{ev}' -> consumed in {len(matching_listeners)} places ({matching_listeners[0]})")

    matrix = {
        "backend_ws_routes": be_ws_routes,
        "backend_emitters_count": len(be_emitters),
        "frontend_listeners": fe_listeners,
        "unique_fe_events": unique_fe_events
    }
    
    with open("reports/websocket_matrix.json", "w", encoding="utf-8") as out:
        json.dump(matrix, out, indent=2)

if __name__ == '__main__':
    parse_websocket_matrix()
