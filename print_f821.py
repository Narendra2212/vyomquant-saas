import json
with open('ruff_latest.json', encoding='utf-16') as f:
    d = json.load(f)
for x in d:
    if x['code'] == 'F821':
        print(f"{x['filename']}:{x['location']['row']}: {x['message']}")
