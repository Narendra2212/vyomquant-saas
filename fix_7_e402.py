import ast

files_to_fix = [
    r'backend_app\core\dag_task_queue.py',
    r'backend_app\main.py',
    r'backend_app\backend\observability\load_testing\tracing_overhead_benchmark.py',
    r'backend_app\backend\event_router.py',
    r'backend_app\backend\observability\optimized_metrics_exporter.py',
    r'backend_app\api\main.py',
    r'backend_app\core\unified_execution_engine.py'
]

for filepath in files_to_fix:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    try:
        tree = ast.parse(content)
    except Exception:
        continue
        
    first_non_import_stmt = None
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Str, ast.Constant)):
            continue
        first_non_import_stmt = stmt
        break
        
    if not first_non_import_stmt:
        continue
        
    # Find last import
    last_import_line = 0
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            if getattr(stmt, 'end_lineno', stmt.lineno) > last_import_line:
                last_import_line = getattr(stmt, 'end_lineno', stmt.lineno)
                
    if not last_import_line:
        continue
        
    start = first_non_import_stmt.lineno - 1
    end = getattr(first_non_import_stmt, 'end_lineno', first_non_import_stmt.lineno)
    
    if start >= last_import_line:
        # Already below imports
        continue
        
    lines = content.split('\n')
    
    # We must also move any related code in main.py up to line 50.
    if 'main.py' in filepath and 'backend_app\\main.py' in filepath:
        # custom logic for main.py: move sys.path and env logic
        chunk = lines[34:50]
        del lines[34:50]
        # the imports have shifted up by 16 lines
        last_import_line -= 16
        lines = lines[:last_import_line] + chunk + lines[last_import_line:]
    else:
        chunk = lines[start:end]
        del lines[start:end]
        # adjusting last_import_line because we deleted lines before it
        last_import_line -= (end - start)
        lines = lines[:last_import_line] + [''] + chunk + [''] + lines[last_import_line:]
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
