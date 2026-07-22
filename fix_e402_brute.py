import json
import os
import re

with open('ruff_latest2.json', encoding='utf-16') as f:
    d = json.load(f)

e402_files = set(x['filename'] for x in d if x['code'] == 'E402')

import_re = re.compile(r'^(import |from [a-zA-Z0-9_\.]+ import )')

for filepath in e402_files:
    if not os.path.exists(filepath):
        continue
        
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Split by line but keep newlines to avoid stripping trailing whitespace if not needed
    lines = content.split('\n')
    
    imports = []
    others = []
    
    i = 0
    in_multiline = False
    buf = ""
    
    # We will gather ALL imports in the entire file
    while i < len(lines):
        line = lines[i]
        
        if in_multiline:
            buf += line + '\n'
            if ')' in line:
                imports.append(buf)
                in_multiline = False
                buf = ""
            i += 1
            continue
            
        if import_re.match(line):
            if '(' in line and ')' not in line:
                in_multiline = True
                buf = line + '\n'
            else:
                imports.append(line + '\n')
            i += 1
            continue
            
        others.append(line + '\n')
        i += 1
        
    # Put imports at the top
    # But wait, if there's a module docstring, we want it before imports
    doc_end = 0
    if others and others[0].startswith('"""'):
        for j in range(1, len(others)):
            if '"""' in others[j]:
                doc_end = j + 1
                break
    elif others and others[0].strip() == '' and len(others) > 1 and others[1].startswith('"""'):
        for j in range(2, len(others)):
            if '"""' in others[j]:
                doc_end = j + 1
                break
                
    final_lines = others[:doc_end] + imports + others[doc_end:]
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(final_lines)
