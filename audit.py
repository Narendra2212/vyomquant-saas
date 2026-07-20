import os
import re

search_terms = {
    "TODO": "LOW",
    "FIXME": "MEDIUM",
    "mock": "HIGH",
    "placeholder": "LOW",
    "sampleData": "HIGH",
    "Math.random": "HIGH",
    "localhost": "CRITICAL",
    "127.0.0.1": "CRITICAL",
    "testnet": "MEDIUM",
}

def audit_directory(root_dir):
    findings = []
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if "venv" in dirpath or "node_modules" in dirpath or ".git" in dirpath or "__pycache__" in dirpath:
            continue
            
        for file in filenames:
            if file.endswith(('.py', '.js', '.ts', '.env', '.json', 'Dockerfile', '.yml', '.yaml')):
                filepath = os.path.join(dirpath, file)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            for term, severity in search_terms.items():
                                if term.lower() in line.lower():
                                    if term == "testnet" and "testnet=True" in line:
                                        pass
                                    findings.append({
                                        "severity": severity,
                                        "term": term,
                                        "file": filepath,
                                        "line": i + 1,
                                        "content": line.strip()[:100]
                                    })
                except Exception:
                    pass
    return findings

if __name__ == "__main__":
    findings = audit_directory(r"d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1")
    
    severity_map = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    for f in findings:
        severity_map[f['severity']].append(f)
        
    print("\n--- DEPLOYMENT AUDIT REPORT ---\n")
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        print(f"[{sev}]: {len(severity_map[sev])} findings")
        for i, f in enumerate(severity_map[sev][:5]):
            print(f"  {f['term']} -> {os.path.basename(f['file'])}:{f['line']} | {f['content']}")
        if len(severity_map[sev]) > 5:
            print(f"  ... and {len(severity_map[sev]) - 5} more.")
        print("")
