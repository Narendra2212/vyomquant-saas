import os
import re
import json

def frontend_bug_scanner():
    print("================================================================================")
    print("FRONTEND FORENSIC BUG HUNT SCANNER — CODEBASE ADVERSARIAL SCAN")
    print("================================================================================")
    
    src_dir = "algo22-terminal/src"
    all_files = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                all_files.append(os.path.join(root, f))
                
    findings = []
    
    # Adversarial patterns
    patterns = [
        (r'catch\s*\([^\)]*\)\s*\{\s*\}', 'SWALLOWED_CATCH_EMPTY', 'P2'),
        (r'onClick=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}', 'EMPTY_ONCLICK', 'P2'),
        (r'console\.error\([^\)]*\);\s*\}', 'CATCH_CONSOLE_ONLY', 'P3'),
        (r'TODO|FIXME|XXX', 'TODO_FIXME', 'P3'),
        (r'Math\.random\(\)', 'MATH_RANDOM_USAGE', 'P2'),
        (r'setTimeout\([^\)]*,\s*\d+\)', 'SETTIMEOUT_IN_COMPONENT', 'P2'),
        (r'setInterval\([^\)]*,\s*\d+\)', 'SETINTERVAL_IN_COMPONENT', 'P2'),
        (r'window\.addEventListener\([^\)]*\)', 'EVENT_LISTENER_WITHOUT_CLEANUP_CHECK', 'P2'),
        (r'localStorage\.getItem\([^\)]*\)', 'LOCAL_STORAGE_ACCESS', 'P3'),
        (r'sessionStorage\.getItem\([^\)]*\)', 'SESSION_STORAGE_ACCESS', 'P3'),
        (r'dangerouslySetInnerHTML', 'DANGEROUS_HTML_XSS_RISK', 'P1'),
    ]
    
    for fpath in all_files:
        rel = os.path.relpath(fpath, src_dir).replace("\\", "/")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        for idx, line in enumerate(lines, 1):
            for pat, ptype, sev in patterns:
                if re.search(pat, line):
                    findings.append({
                        "file": f"algo22-terminal/src/{rel}",
                        "line": idx,
                        "type": ptype,
                        "severity": sev,
                        "code": line.strip()
                    })
                    
    print(f"Scanned {len(all_files)} frontend files. Discovered {len(findings)} pattern occurrences.")
    
    by_type = {}
    for f in findings:
        t = f['type']
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  {t:35s}: {count:3d}")
        
    os.makedirs("reports", exist_ok=True)
    with open("reports/frontend_bug_hunt_scan.json", "w", encoding="utf-8") as out:
        json.dump(findings, out, indent=2)

if __name__ == '__main__':
    frontend_bug_scanner()
