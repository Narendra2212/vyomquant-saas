import os
import re
import json

def forensic_bug_scanner():
    print("================================================================================")
    print("FORENSIC BUG HUNT SCANNER — CODEBASE PATTERN ANALYSIS")
    print("================================================================================")
    
    backend_dir = "backend_app"
    all_files = []
    for root, _, files in os.walk(backend_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('__pycache__'):
                all_files.append(os.path.join(root, f))
                
    findings = []
    
    # Patterns to hunt
    patterns = [
        (r'except\s+Exception:\s*pass', 'SWALLOWED_EXCEPTION_BARE_PASS', 'P1'),
        (r'except:\s*pass', 'SWALLOWED_BARE_EXCEPT_PASS', 'P1'),
        (r'NotImplementedError', 'NOT_IMPLEMENTED_ERROR', 'P2'),
        (r'TODO', 'TODO_COMMENT', 'P3'),
        (r'FIXME', 'FIXME_COMMENT', 'P2'),
        (r'XXX', 'XXX_COMMENT', 'P2'),
        (r'return\s+None\s+#\s*fallback', 'FALLBACK_RETURN_NONE', 'P2'),
        (r'return\s+\{\}\s*#\s*fallback', 'FALLBACK_RETURN_EMPTY_DICT', 'P2'),
        (r'mock|dummy|fake|simulate', 'MOCK_OR_SIMULATION_KEYWORD', 'P2'),
    ]
    
    for fpath in all_files:
        rel = os.path.relpath(fpath, backend_dir).replace("\\", "/")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        for idx, line in enumerate(lines, 1):
            for pat, ptype, sev in patterns:
                if re.search(pat, line, re.IGNORECASE):
                    # Filter out purely benign docstrings or test fixtures if explicitly test files
                    if "test" in rel and ptype in ["MOCK_OR_SIMULATION_KEYWORD", "TODO_COMMENT"]:
                        continue
                    findings.append({
                        "file": f"backend_app/{rel}",
                        "line": idx,
                        "type": ptype,
                        "severity": sev,
                        "code": line.strip()
                    })
                    
    print(f"Discovered {len(findings)} potential defect patterns across {len(all_files)} backend files.")
    
    # Categorize by type
    by_type = {}
    for f in findings:
        t = f['type']
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  {t:30s}: {count:3d}")
        
    os.makedirs("reports", exist_ok=True)
    with open("reports/backend_bug_hunt_scan.json", "w", encoding="utf-8") as out:
        json.dump(findings, out, indent=2)

if __name__ == '__main__':
    forensic_bug_scanner()
