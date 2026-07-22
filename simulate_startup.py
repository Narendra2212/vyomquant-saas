import sys
import os

# Emulate the ECS /app context + virtualenv
libs_path = os.path.abspath('test_env_libs')
sys.path.insert(0, libs_path)
sys.path.insert(0, os.path.abspath('.'))

print("Starting simulation of backend_app.main...")

try:
    # Simulating standard environment variables present in production
    os.environ['ENV'] = 'production'
    os.environ['AERORA_MODE'] = 'paper'
    os.environ['SUPABASE_URL'] = 'https://mock.supabase.co'
    os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'mock'
    os.environ['DATABASE_URL'] = 'sqlite:///./algo22.db'
    os.environ['REDIS_URL'] = 'redis://localhost:6379'
    os.environ['MASTER_ENCRYPTION_KEYS'] = 'mock'

    import backend_app.main
    
    # Also test worker startup
    import backend_app.worker

    print("SUCCESS: No ModuleNotFoundErrors or ImportErrors detected during startup phase.")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
