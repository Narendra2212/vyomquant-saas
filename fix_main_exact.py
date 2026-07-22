import os

filepath = 'backend_app/main.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the end of imports (which is around line 196)
end_imports_idx = 0
for i, line in enumerate(lines):
    if line.startswith("# Shared runtime service status"):
        end_imports_idx = i
        break

# The chunk to move is lines 34 to 50
chunk = lines[34:50]
# delete chunk
del lines[34:50]
# adjust index
end_imports_idx -= (50 - 34)

# insert chunk at end_imports_idx
for i, line in enumerate(chunk):
    lines.insert(end_imports_idx + i, line)

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(lines)
