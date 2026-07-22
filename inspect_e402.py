import json
import ast

with open('ruff_latest2.json', encoding='utf-16') as f:
    d = json.load(f)

e402_files = set(x['filename'] for x in d if x['code'] == 'E402')

for fpath in e402_files:
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        first_non_import_stmt = None
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            # Check if it's a docstring
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Str, ast.Constant)):
                continue
            first_non_import_stmt = stmt
            break
        
        if first_non_import_stmt:
            print(f"{fpath}: Line {first_non_import_stmt.lineno}: {ast.unparse(first_non_import_stmt)}")
    except Exception as e:
        pass
