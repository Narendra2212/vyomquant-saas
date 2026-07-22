import os

filepath = 'backend_app/main.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Collect imports vs non-imports
imports = []
others = []

# Exclude module docstrings at top
doc_idx = 0
in_doc = False
for i, line in enumerate(lines):
    if line.strip().startswith('"""') or line.strip().endswith('"""'):
        if '"""' in line.strip() and len(line.strip()) > 3 and line.strip().count('"""') == 2:
            pass # one line docstring
        elif in_doc:
            in_doc = False
            doc_idx = i + 1
        else:
            in_doc = True
            
if doc_idx == 0:
    for i, line in enumerate(lines):
        if '"""' in line:
            if in_doc:
                in_doc = False
                doc_idx = i + 1
                break
            else:
                in_doc = True

doc_lines = lines[:doc_idx]
rest = lines[doc_idx:]

for line in rest:
    if line.startswith("import ") or line.startswith("from "):
        imports.append(line)
    elif line.startswith("    ") and (line.strip().startswith("import ") or line.strip().startswith("from ")):
        # Probably inside a function, let's just treat as others to not break syntax
        others.append(line)
    elif line.strip() == '' and not others:
        imports.append(line) # keep spaces between imports
    else:
        others.append(line)

final = doc_lines + imports + others

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(final)
