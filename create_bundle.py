import os
import zipfile
import subprocess
import glob

def run_cmd(cmd):
    return subprocess.check_output(cmd, shell=True, text=True, errors='ignore')

def main():
    print("Creating launch_audit_bundle...")
    
    # 1. Project tree
    try:
        tree = run_cmd("tree /F /A D:\\aerora_quant_backend_updated_final1\\aerora_quant_backend_updated_final1")
        with open("project_tree.txt", "w") as f:
            f.write(tree)
    except Exception as e:
        print(e)
        
    # Create launch blockers report
    with open("launch_blockers.txt", "w") as f:
        f.write("LAUNCH BLOCKERS & FAILED TESTS\n")
        f.write("==============================\n")
        f.write("- API keys for Binance, Bybit, and OKX testnets are currently missing or 'dummy_api_key', causing CCXT testnet execution to fail authentication.\n")
        f.write("- 'websockets.asyncio' import error in 'security_vault.py', which causes fatal failures in some async flows.\n")

    # Gather files
    files_to_zip = []
    
    if os.path.exists("project_tree.txt"):
        files_to_zip.append("project_tree.txt")
    if os.path.exists("launch_blockers.txt"):
        files_to_zip.append("launch_blockers.txt")
        
    # 2. requirements.txt
    if os.path.exists("requirements.txt"):
        files_to_zip.append("requirements.txt")
        
    # 3. docker-compose files
    docker_files = glob.glob("docker-compose*.yml")
    files_to_zip.extend(docker_files)
    
    # 4. .env.example
    if os.path.exists(".env.example"):
        files_to_zip.append(".env.example")
        
    # 5. runtime validation reports
    # These include the scripts we ran
    scripts = glob.glob("verify_*.py") + glob.glob("live_exec_*.py")
    files_to_zip.extend(scripts)
    
    # 8. execution path diagrams
    # we might not have visual diagrams, but I can write a text representation
    with open("execution_path_diagram.txt", "w") as f:
        f.write("EXECUTION PATH DIAGRAM\n")
        f.write("======================\n")
        f.write("Strategy Signal -> BotRunner\n")
        f.write("BotRunner -> ExecutionEngine.execute_with_idempotency\n")
        f.write("ExecutionEngine (Idempotency Check) -> ExecutionRecordRepository\n")
        f.write("ExecutionEngine -> ExecutionEngine._execute_trade_internal\n")
        f.write("ExecutionEngine -> CCXTExchangeExecutor.place_order\n")
        f.write("CCXTExchangeExecutor -> CCXT (ccxt.create_order)\n")
        f.write("CCXT -> Exchange API\n")
    files_to_zip.append("execution_path_diagram.txt")
    
    # 9. exchange integration files
    if os.path.exists("aerora_quant_backend_updated_final1/backend/exchange_executor.py"):
        files_to_zip.append("aerora_quant_backend_updated_final1/backend/exchange_executor.py")
    if os.path.exists("aerora_quant_backend_updated_final1/core/execution_engine.py"):
        files_to_zip.append("aerora_quant_backend_updated_final1/core/execution_engine.py")
        
    # 10. authentication and RLS files
    rls_files = ["enable_rls.py", "test_service_role.py", "test_service_role_2.py", "test_service_role_3.py", "frontend_validation_suite.py"]
    for rf in rls_files:
        if os.path.exists(rf):
            files_to_zip.append(rf)

    # Compress into zip
    zip_name = "launch_audit_bundle.zip"
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files_to_zip:
            if os.path.exists(f):
                zf.write(f)
                
    print(f"Bundle {zip_name} created with {len(files_to_zip)} files.")
    print(f"Size: {os.path.getsize(zip_name) / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()
