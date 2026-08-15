import os
import re
import json

def scan_frontend():
    print("================================================================================")
    print("SCANNING ALL FRONTEND COMPONENTS, PAGES, FORMS, AND BUTTONS")
    print("================================================================================")
    
    src_dir = "algo22-terminal/src"
    all_files = []
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                all_files.append(os.path.join(root, f))
                
    print(f"Discovered {len(all_files)} frontend source files.")
    
    # 1. Scan for empty / dummy / simulated handlers
    issues = []
    buttons = []
    forms = []
    
    for fpath in all_files:
        rel_path = os.path.relpath(fpath).replace("\\", "/")
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            
        for idx, line in enumerate(lines, 1):
            # Check empty onClick
            if re.search(r'onClick=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}', line):
                issues.append({
                    "file": rel_path,
                    "line": idx,
                    "type": "EMPTY_ONCLICK",
                    "code": line.strip()
                })
                
            # Check console.log only onClick
            if re.search(r'onClick=\{\s*\(\s*\)\s*=>\s*console\.log', line):
                issues.append({
                    "file": rel_path,
                    "line": idx,
                    "type": "CONSOLE_LOG_ONLY",
                    "code": line.strip()
                })
                
            # Check alert only onClick
            if re.search(r'onClick=\{\s*\(\s*\)\s*=>\s*alert', line):
                issues.append({
                    "file": rel_path,
                    "line": idx,
                    "type": "ALERT_ONLY",
                    "code": line.strip()
                })
                
            # Check setTimeout simulation in handler
            if "setTimeout" in line and not any(ign in rel_path for ign in ["websocket", "utils", "primitives"]):
                issues.append({
                    "file": rel_path,
                    "line": idx,
                    "type": "SETTIMEOUT_SIMULATION",
                    "code": line.strip()
                })
                
            # Check TODO / FIXME
            if "TODO" in line or "FIXME" in line:
                issues.append({
                    "file": rel_path,
                    "line": idx,
                    "type": "TODO_OR_FIXME",
                    "code": line.strip()
                })
                
            # Collect interactive buttons
            btn_match = re.search(r'<[Bb]utton[^>]*onClick=\{([^}]+)\}', line)
            if btn_match:
                buttons.append({
                    "file": rel_path,
                    "line": idx,
                    "handler": btn_match.group(1).strip()
                })
                
            # Collect forms
            form_match = re.search(r'<form[^>]*onSubmit=\{([^}]+)\}', line)
            if form_match:
                forms.append({
                    "file": rel_path,
                    "line": idx,
                    "handler": form_match.group(1).strip()
                })

    print(f"Total Interactive Buttons cataloged: {len(buttons)}")
    print(f"Total Form Submissions cataloged:     {len(forms)}")
    print(f"Total Disconnected/Mock issues found: {len(issues)}")
    
    for iss in issues:
        print(f"  [{iss['type']}] {iss['file']}:{iss['line']} -> {iss['code']}")
        
    os.makedirs("reports", exist_ok=True)
    with open("reports/frontend_scanner_dump.json", "w", encoding="utf-8") as out:
        json.dump({
            "total_files": len(all_files),
            "buttons_count": len(buttons),
            "forms_count": len(forms),
            "issues_count": len(issues),
            "issues": issues,
            "buttons": buttons,
            "forms": forms
        }, out, indent=2)

if __name__ == '__main__':
    scan_frontend()
