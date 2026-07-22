import json
from collections import defaultdict, Counter

with open('ruff_remaining.json', encoding='utf-16') as f:
    errors = json.load(f)

print(f"Total remaining errors: {len(errors)}")

c = Counter(e['code'] for e in errors)
print("\nBy code:")
for code, count in c.most_common():
    print(f"  {code}: {count}")

f821 = defaultdict(int)
e402 = defaultdict(int)
f841 = defaultdict(int)

for e in errors:
    fname = e['filename'].split('backend_app')[-1].lstrip('\\/')
    if e['code'] == 'F821':
        f821[fname] += 1
    elif e['code'] == 'E402':
        e402[fname] += 1
    elif e['code'] == 'F841':
        f841[fname] += 1

print("\nTop F821 files:")
for k, v in sorted(f821.items(), key=lambda item: item[1], reverse=True)[:10]:
    print(f"  {k}: {v}")

print("\nTop E402 files:")
for k, v in sorted(e402.items(), key=lambda item: item[1], reverse=True)[:10]:
    print(f"  {k}: {v}")
