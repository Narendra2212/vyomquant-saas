import os

def strip_bom(filepath):
    if not os.path.exists(filepath): return
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('\ufeff', '')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

strip_bom("backend_app/test_auth.py")
strip_bom("backend_app/test_endpoints.py")
strip_bom("backend_app/test_perf.py")

strat = "backend_app/routers/strategies.py"
if os.path.exists(strat):
    with open(strat, 'r', encoding='utf-8') as f:
        content = f.read()
    # rename second get_strategy
    content = content.replace("async def get_strategy(\n    strategy_id: str,", "async def get_strategy_route(\n    strategy_id: str,")
    with open(strat, 'w', encoding='utf-8') as f:
        f.write(content)

orders = "backend_app/routers/orders.py"
if os.path.exists(orders):
    with open(orders, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    if lines[0].startswith("import time"):
        t = lines.pop(0)
        # find docstring end
        doc_end = 0
        in_doc = False
        for i, line in enumerate(lines):
            if line.strip().startswith('"""'):
                if in_doc:
                    doc_end = i + 1
                    break
                else:
                    in_doc = True
        if doc_end > 0:
            lines.insert(doc_end, t)
        with open(orders, 'w', encoding='utf-8') as f:
            f.writelines(lines)
