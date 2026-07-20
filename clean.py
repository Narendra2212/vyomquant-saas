with open('d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/routers/strategies.py', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
idx = -1
for i, line in enumerate(lines):
    if '@router.post("/{strategy_id}/clone")' in line:
        idx = i
        break
if idx != -1:
    for j in range(idx, -1, -1):
        if 'import json' in lines[j]:
            idx = j
            break
    with open('d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/routers/strategies.py', 'w', encoding='utf-8') as f:
        f.writelines(lines[:idx])
