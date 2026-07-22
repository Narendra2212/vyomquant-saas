import os
import re

filepath = 'backend_app/main.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# I will extract all lines starting with import or from, except inside docstrings or multiline strings
lines = content.split('\n')
imports = ["import os", "import sys"]
others = []

in_doc = False
in_multiline_import = False
buf = []

i = 0
while i < len(lines):
    line = lines[i]
    
    if line.strip().startswith('"""') or line.strip().endswith('"""'):
        if '"""' in line.strip() and len(line.strip()) > 3 and line.strip().count('"""') == 2:
            others.append(line)
        elif in_doc:
            in_doc = False
            others.append(line)
        else:
            in_doc = True
            others.append(line)
        i += 1
        continue
        
    if in_doc:
        others.append(line)
        i += 1
        continue
        
    if in_multiline_import:
        buf.append(line)
        if ')' in line:
            imports.extend(buf)
            in_multiline_import = False
            buf = []
        i += 1
        continue
        
    # Check if it's an import statement
    # BUT only if it's at the start of a line (no indentation)
    if line.startswith("import ") or line.startswith("from "):
        if '(' in line and ')' not in line:
            in_multiline_import = True
            buf.append(line)
        else:
            imports.append(line)
        i += 1
        continue
        
    if line.startswith("import os") or line.startswith("import sys"):
        i += 1
        continue
        
    others.append(line)
    i += 1

# Now we construct the file
doc_end = 0
for i, line in enumerate(others):
    if '"""' in line:
        if i > 0:
            doc_end = i + 1
            break
            
final_lines = others[:doc_end] + ['\n'] + sorted(list(set(imports))) + ['\n'] + others[doc_end:]

with open(filepath, 'w', encoding='utf-8') as f:
    f.write('\n'.join(final_lines))

