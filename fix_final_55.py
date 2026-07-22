import re

# 1. state_persistence.py (syntax error)
fpath = r'backend_app/backend/state_persistence.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'try:' in line and i < len(lines)-1 and 'from sqlalchemy import' in lines[i+1]:
        # just uncomment the block
        lines[i+1] = lines[i+1].replace('#     from', '    from')
        lines[i+2] = lines[i+2].replace('#                             LargeBinary', '                            LargeBinary')
        break
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(lines)
    
# 2. metrics_exporter.py (syntax error)
fpath = r'backend_app/core/metrics_exporter.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'try:' in line and i < len(lines)-1 and 'from prometheus_client import' in lines[i+1]:
        # just uncomment the block
        lines[i+1] = lines[i+1].replace('#     from', '    from')
        lines[i+2] = lines[i+2].replace('#                                    Counter', '                                   Counter')
        lines[i+3] = lines[i+3].replace('#                                    generate_latest', '                                   generate_latest')
        break
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 3. transactional_execution_manager.py E701
fpath = r'backend_app/backend/transactional_execution_manager.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("if sym in pos_cache: return pos_cache[sym]", "if sym in pos_cache:\n            return pos_cache[sym]")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 4. websocket_cluster.py F821
fpath = r'backend_app/backend/websocket_cluster.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
lines.insert(0, "from fastapi import WebSocketDisconnect\n")
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 5. ws_event_stream.py F401
fpath = r'backend_app/backend/ws_event_stream.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("assert_valid_channel,\n", "")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 6. database_scaling.py F821
fpath = r'backend_app/core/database_scaling.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
lines.insert(0, "from sqlalchemy import text\n")
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 7. models/__init__.py F401 Any
fpath = r'backend_app/core/models/__init__.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = re.sub(r'\bAny,\s*', '', content)
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

