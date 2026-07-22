import os

def clean_file(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()
    
    # Remove BOM
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

# Clean BOMs
for p in ["backend_app/test_auth.py", "backend_app/test_endpoints.py", "backend_app/test_perf.py"]:
    if os.path.exists(p):
        clean_file(p)

# Fix strategies.py
strat = "backend_app/routers/strategies.py"
with open(strat, 'r', encoding='utf-8') as f:
    lines = f.readlines()
if lines[0].startswith("def validate_dag"):
    val = lines.pop(0)
    lines.append("\n" + val)
    with open(strat, 'w', encoding='utf-8') as f:
        f.writelines(lines)

# Fix API WS Routes
ws = "backend_app/api_ws/ws_routes.py"
with open(ws, 'r', encoding='utf-8') as f:
    lines = f.readlines()
# Move any top injected imports down past docstrings
imports = []
while lines and (lines[0].startswith("import ") or lines[0].startswith("from ")):
    imports.append(lines.pop(0))
if imports:
    # insert them after docstring
    # find first non docstring, non import
    doc_end = 0
    in_doc = False
    for i, line in enumerate(lines):
        if line.strip().startswith('"""'):
            if in_doc:
                in_doc = False
                doc_end = i + 1
            else:
                if i == 0 or (lines[i-1].strip() == '' and doc_end == 0):
                    in_doc = True
    
    if doc_end == 0:
        doc_end = 0
        
    lines = lines[:doc_end] + imports + lines[doc_end:]
    with open(ws, 'w', encoding='utf-8') as f:
        f.writelines(lines)
