import os
import re
import json

router_dir = 'backend_app/routers'
routes = []

endpoint_pattern = re.compile(r'@router\.(get|post|put|delete|patch)\([\'"]([^\'"]+)[\'"]')
table_pattern = re.compile(r'\.(?:table|from_?)\([\'"]([a-zA-Z0-9_]+)[\'"]')
rpc_pattern = re.compile(r'\.rpc\([\'"]([a-zA-Z0-9_]+)[\'"]')

for f in sorted(os.listdir(router_dir)):
    if f.endswith('.py') and not f.startswith('__'):
        filepath = os.path.join(router_dir, f).replace('\\', '/')
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
            content = file.read()
            lines = content.splitlines()
            current_endpoint = None
            current_method = None
            current_fn = None
            current_tables = set()
            current_rpcs = set()
            
            for line_idx, line in enumerate(lines):
                ep_match = endpoint_pattern.search(line)
                if ep_match:
                    if current_endpoint:
                        routes.append({
                            'file': filepath,
                            'method': current_method.upper(),
                            'path': current_endpoint,
                            'function': current_fn,
                            'tables': sorted(list(current_tables)),
                            'rpcs': sorted(list(current_rpcs))
                        })
                    current_method = ep_match.group(1)
                    current_endpoint = ep_match.group(2)
                    current_tables = set()
                    current_rpcs = set()
                
                fn_match = re.match(r'async def ([a-zA-Z0-9_]+)\(', line) or re.match(r'def ([a-zA-Z0-9_]+)\(', line)
                if fn_match and current_endpoint and not current_fn:
                    current_fn = fn_match.group(1)
                
                for tm in table_pattern.finditer(line):
                    current_tables.add(tm.group(1))
                for rm in rpc_pattern.finditer(line):
                    current_rpcs.add(rm.group(1))
            
            if current_endpoint:
                routes.append({
                    'file': filepath,
                    'method': current_method.upper(),
                    'path': current_endpoint,
                    'function': current_fn,
                    'tables': sorted(list(current_tables)),
                    'rpcs': sorted(list(current_rpcs))
                })

print(f"Total Routes Discovered: {len(routes)}")
with open('reports/routes_to_db_map.json', 'w', encoding='utf-8') as out:
    json.dump(routes, out, indent=2)

for r in routes[:25]:
    if r['tables'] or r['rpcs']:
        print(f"[{r['method']}] {r['path']} -> Tables: {r['tables']}, RPCs: {r['rpcs']}")
