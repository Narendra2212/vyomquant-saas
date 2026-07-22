import json
from collections import defaultdict

with open('ruff_remaining.json', encoding='utf-16') as f:
    errors = json.load(f)

files = defaultdict(list)
for e in errors:
    # Normalize paths for easy reading
    fname = e['filename'].split('backend_app')[-1].lstrip('\\/')
    files[fname].append(e)

with open('ruff_summary.txt', 'w', encoding='utf-8') as out:
    for fname, errs in sorted(files.items(), key=lambda item: len(item[1]), reverse=True):
        out.write(f"\n=== {fname} ({len(errs)} errors) ===\n")
        for e in errs:
            row = e['location']['row']
            code = e['code']
            msg = e['message']
            out.write(f"  Line {row}: [{code}] {msg}\n")
