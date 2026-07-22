import json
import os
import re

with open('ruff_remaining.json', encoding='utf-16') as f:
    errors = json.load(f)

# Group errors by file
files = {}
for e in errors:
    fname = e['filename']
    if fname not in files:
        files[fname] = []
    files[fname].append(e)

for fname, errs in files.items():
    with open(fname, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Sort errors descending by line number to avoid shifting lines affecting previous errors
    errs.sort(key=lambda x: (x['location']['row'], x['location']['column']), reverse=True)
    
    for e in errs:
        row = e['location']['row'] - 1
        col = e['location']['column'] - 1
        code = e['code']
        
        if code == 'E722':
            # Do not use bare except
            lines[row] = lines[row].replace('except:', 'except Exception:')
            
        elif code == 'F841':
            # Local variable assigned to but never used
            # Very basic: if it's "var = ", we just remove "var = "
            var_name = e['message'].split('`')[1]
            match = re.match(rf'^(\s*){var_name}\s*=\s*(.*)', lines[row])
            if match:
                lines[row] = match.group(1) + match.group(2) + '\n'

        elif code == 'E741':
            # Ambiguous variable name 'l'
            # Let's change 'l' to 'low'
            lines[row] = re.sub(r'\bl\b', 'low', lines[row])
            
        elif code == 'F811':
            # Redefinition of unused name
            # If it's an import, maybe we can ignore. If it's a def, maybe we can delete the def or rename it?
            # E.g. get_strategy redefined
            pass

    with open(fname, 'w', encoding='utf-8') as f:
        f.writelines(lines)
