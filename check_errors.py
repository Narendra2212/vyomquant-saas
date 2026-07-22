import json
import re

with open('ruff_remaining.json', encoding='utf-16') as f:
    errors = json.load(f)

for e in errors:
    fname = e['filename']
    row = e['location']['row'] - 1
    code = e['code']
    msg = e['message']
    
    with open(fname, 'r', encoding='utf-8') as f2:
        lines = f2.readlines()
        
    if code == 'F601':
        print(f"F601 in {fname}:{row+1}")
        print(f"Line: {lines[row]}")
        
    if code == 'F821':
        # Let's print out what needs fixing in F821
        var_name = msg.split('`')[1]
        print(f"F821 in {fname}:{row+1} - {var_name}")
