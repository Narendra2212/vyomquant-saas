import sys
import os
import traceback

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, os.getcwd())

try:
    print("Testing supabase import...", flush=True)
    import supabase
    print("Supabase imported!", flush=True)
    
    print("Testing backend_app.routers.auth import...", flush=True)
    from backend_app.routers import auth
    print("Auth router imported successfully!", flush=True)
except Exception as e:
    print(f"Failed: {e}", flush=True)
    traceback.print_exc()
