import json
with open('ruff_latest2.json', encoding='utf-16') as f:
    d = json.load(f)
for x in d:
    if x['code'] == 'E402' and 'main.py' in x['filename']:
        print(f"{x['filename']}:{x['location']['row']}: {x['message']}")
