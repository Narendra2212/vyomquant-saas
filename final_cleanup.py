import re

# 1. backend_app/core/models/__init__.py F401s
fpath = r'backend_app/core/models/__init__.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()

# I will just write a regex to remove these words.
to_remove = ["BaseModel", "Dict", "EmailStr", "Enum", "Field", "List", "Literal", "Optional", "datetime", "validator"]
for w in to_remove:
    # replace " w," or ", w" or "w,"
    content = re.sub(rf'\b{w},\s*', '', content)
    content = re.sub(rf',\s*\b{w}\b', '', content)

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. backend_app/core/unified_execution_engine.py E701, E722
fpath = r'backend_app/core/unified_execution_engine.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("class OrderConfirmation: pass", "class OrderConfirmation:\n    pass")
content = content.replace("class ReconciliationResult: pass", "class ReconciliationResult:\n    pass")
content = content.replace("except:", "except Exception:")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 3. backend_app/main.py F811
fpath = r'backend_app/main.py'
with open(fpath, 'r', encoding='utf-8') as f:
    lines = f.readlines()
# Remove duplicate ExecutionFlags import in main.py
for i, line in enumerate(lines):
    if 'from backend_app.core.feature_flags import ExecutionContext, ExecutionFlags' in line:
        lines[i] = 'from backend_app.core.feature_flags import ExecutionContext\n'
        break
with open(fpath, 'w', encoding='utf-8') as f:
    f.writelines(lines)
