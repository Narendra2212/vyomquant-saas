import sys, os
sys.path.insert(0, r"C:\aerora_quant_backend_updated_final1")
os.chdir(r"C:\aerora_quant_backend_updated_final1")
from backend_app.backend.marketplace import subscriber_operation_guard as g
for attr in ("NODE_PREVIEW", "VERSION_COMPARE", "VERSION_HISTORY"):
    op = getattr(g, attr)
    print(f"{attr}.name = {op.name!r}   contains 'vie': {'vie' in op.name}")
print()
print("CONFIRMED COLLISION" if "vie" in g.NODE_PREVIEW.name else "NOT A COLLISION")
print(f"details.operation is the fixed literal {g.NODE_PREVIEW.name!r}; the generated")
print("Protected_Logic params key was 'vie', which is the substring node_pre[vie]w.")
