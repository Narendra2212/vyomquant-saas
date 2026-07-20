import os
import glob

files = glob.glob("tests/*.py")
patched_count = 0

for file in files:
    with open(file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    new_content = content
    
    # Variations of the path to replace
    patterns = [
        "r'c:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1'",
        "'c:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1'",
        "r\"c:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1\"",
        "\"c:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1\"",
        "r'C:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1'",
        "'C:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1'",
        "r\"C:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1\"",
        "\"C:\\Users\\user\\Desktop\\aerora_quant_backend_updated_final1\""
    ]
    
    replaced = False
    for pat in patterns:
        if pat in new_content:
            new_content = new_content.replace(pat, "os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))")
            replaced = True
            
    if replaced:
        # Ensure import os is present
        if "import os" not in new_content:
            new_content = new_content.replace("import sys", "import sys\nimport os")
        with open(file, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Patched: {file}")
        patched_count += 1

print(f"Total patched files: {patched_count}")
