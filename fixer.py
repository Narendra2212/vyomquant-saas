import json

with open('ruff_remaining.json', encoding='utf-16') as f:
    errors = json.load(f)

print("=== F821 in market_data_validation.py ===")
for e in errors:
    if e['code'] == 'F821' and 'market_data_validation' in e['filename']:
        print(f"Line {e['location']['row']}: {e['message']}")
