import os
import hashlib
import ast
import difflib
import json
import sys

def get_hash(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def analyze_ast(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    tree = ast.parse(content)
    
    classes = []
    functions = []
    imports = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for n in node.names:
                    imports.append(f"{node.module}.{n.name}")
    
    return {
        'classes': set(classes),
        'functions': set(functions),
        'imports': set(imports)
    }

def compare_files(file1, file2):
    try:
        h1, h2 = get_hash(file1), get_hash(file2)
        if h1 == h2:
            return "IDENTICAL", []
        
        ast1, ast2 = analyze_ast(file1), analyze_ast(file2)
        
        diffs = []
        with open(file1, 'r', encoding='utf-8') as f1, open(file2, 'r', encoding='utf-8') as f2:
            l1 = f1.readlines()
            l2 = f2.readlines()
            
        diff_lines = list(difflib.unified_diff(l1, l2, fromfile='legacy', tofile='updated', n=3))
        
        # Heuristics for minor/major
        # If classes/functions are exactly the same, maybe minor
        c_diff = ast1['classes'].symmetric_difference(ast2['classes'])
        f_diff = ast1['functions'].symmetric_difference(ast2['functions'])
        i_diff = ast1['imports'].symmetric_difference(ast2['imports'])
        
        if c_diff or f_diff:
            status = "MAJOR DIFFERENCE"
        elif len(diff_lines) > 50:
            status = "MAJOR DIFFERENCE"
        else:
            status = "MINOR DIFFERENCE"
            
        return status, diff_lines
    except Exception as e:
        return f"ERROR: {e}", []

files_to_check = [
    "bot_telemetry.py",
    "exchange_telemetry.py",
    "security_vault.py",
    "signal_trace_engine.py",
    "telemetry_engine.py",
    "ws_channels.py",
    "ws_event_stream.py"
]

results = {}
for f in files_to_check:
    f1 = os.path.join(r"d:\aerora_quant_backend_updated_final1\backend", f)
    f2 = os.path.join(r"d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1\backend", f)
    status, diffs = compare_files(f1, f2)
    results[f] = {
        'status': status,
        'diff': "".join(diffs[:30]) + ("\n...[truncated]..." if len(diffs) > 30 else "")
    }

with open(r"d:\aerora_quant_backend_updated_final1\diff_results.json", "w") as out:
    json.dump(results, out, indent=2)

print("Done")
