import os
import glob

files = glob.glob("tests/*.py")
patched_count = 0

for file in files:
    with open(file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    new_content = content
    if "aerora_quant_backend_updated_final1." in new_content:
        new_content = new_content.replace("aerora_quant_backend_updated_final1.", "")
        with open(file, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Patched imports in: {file}")
        patched_count += 1

print(f"Total patched files: {patched_count}")
