import os
import re
import json

def run_frontend_census():
    print("Executing Complete Frontend Census & Interaction Forensic Audit...")
    
    src_dir = "algo22-terminal/src"
    files = []
    for root, _, fs in os.walk(src_dir):
        for f in fs:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                files.append(os.path.join(root, f))
                
    # 1. Parse App.jsx for routes
    with open(os.path.join(src_dir, "App.jsx"), "r", encoding="utf-8") as f:
        app_content = f.read()
        
    routes = re.findall(r'<Route\s+path=["\']([^"\']+)["\']\s+element=\{<([^/>\s]+)', app_content)
    print(f"Discovered {len(routes)} top-level and nested routes in App.jsx:")
    for path, comp in routes:
        print(f"  Route '{path}' -> Component <{comp} />")

    # 2. Parse all pages
    pages_dir = os.path.join(src_dir, "pages")
    page_files = [f for f in os.listdir(pages_dir) if f.endswith(('.jsx', '.js'))]
    print(f"\nDiscovered {len(page_files)} Page modules in src/pages/:")
    pages_census = []
    for p in page_files:
        ppath = os.path.join(pages_dir, p)
        with open(ppath, "r", encoding="utf-8", errors="ignore") as f:
            c = f.read()
        
        # Look for buttons, forms, tables, modals, charts
        btn_count = len(re.findall(r'<button|<Button', c))
        form_count = len(re.findall(r'<form', c))
        table_count = len(re.findall(r'<table|<Table|<table', c, re.IGNORECASE))
        chart_count = len(re.findall(r'<ResponsiveContainer|<AreaChart|<LineChart|<BarChart', c))
        api_count = len(re.findall(r'endpoints\.|apiClient\.|publicGet|axios|fetch', c))
        ws_count = len(re.findall(r'wsClient\.|socket\.|useDashboardWebSocket', c))
        
        pages_census.append({
            "page": p,
            "buttons": btn_count,
            "forms": form_count,
            "tables": table_count,
            "charts": chart_count,
            "api_calls": api_count,
            "ws_consumers": ws_count
        })
        print(f"  {p:25s} | Buttons: {btn_count:2d} | Forms: {form_count:2d} | Tables: {table_count:2d} | Charts: {chart_count:2d} | API: {api_count:2d} | WS: {ws_count:2d}")

    # 3. Parse all UI components
    components_dir = os.path.join(src_dir, "components")
    comp_files = []
    for root, _, fs in os.walk(components_dir):
        for f in fs:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                comp_files.append(os.path.relpath(os.path.join(root, f), src_dir).replace("\\", "/"))
    print(f"\nDiscovered {len(comp_files)} Components in src/components/.")

    census_data = {
        "routes": routes,
        "pages": pages_census,
        "components_count": len(comp_files),
        "components": comp_files
    }
    
    os.makedirs("reports", exist_ok=True)
    with open("reports/frontend_census.json", "w", encoding="utf-8") as out:
        json.dump(census_data, out, indent=2)

if __name__ == '__main__':
    run_frontend_census()
