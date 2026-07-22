import re
import os

# 1. transactional_execution_manager.py
fpath = r'backend_app/backend/transactional_execution_manager.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
# Let's just fix the exact indentation
content = content.replace("if sym in pos_cache:\n                return pos_cache[sym]", "if sym in pos_cache:\n                return pos_cache[sym]")
# Wait, it actually wrote exactly what I replaced. 
# Let's use re.sub for exact spaces
content = re.sub(r'if sym in pos_cache:\s*return pos_cache\[sym\]', 'if sym in pos_cache:\n                return pos_cache[sym]', content)
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. portfolio_management.py duplicate close
fpath = r'backend_app/backend/portfolio_management.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
in_duplicate = False
res = []
for line in lines:
    if 'def close(self, price: float, time: datetime = None):' in line:
        in_duplicate = True
        continue
    if in_duplicate and (line.startswith('    def ') or line.startswith('class ')):
        in_duplicate = False
    if not in_duplicate:
        res.append(line)
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(res)

# 3. reconciliation_worker.py unused imports
fpath = r'backend_app/backend/reconciliation_worker.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
# Just remove the try block imports
res = []
in_try = False
for i, line in enumerate(lines):
    if 'try:' in line and i < len(lines)-1 and 'from backend_app.backend.state_service' in lines[i+1]:
        res.append(line)
        res.append("    STATE_SERVICE_AVAILABLE = True\n")
        in_try = True
        continue
    if in_try:
        if 'STATE_SERVICE_AVAILABLE = True' in line:
            in_try = False
        continue
    res.append(line)
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(res)

# 4. order_execution_engine.py invalid syntax
# 58 |         self.3 = 3
# 59 |         self."UNKNOWN" = "UNKNOWN"
fpath = r'backend_app/backend/order_execution_engine.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("self.3 = 3\n", "")
content = content.replace('self."UNKNOWN" = "UNKNOWN"\n', "")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)
