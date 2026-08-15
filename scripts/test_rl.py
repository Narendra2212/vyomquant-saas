import sys
import traceback

sys.path.insert(0, '.')

try:
    print("1. importing os, logging, slowapi...")
    import os, logging
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    print("2. importing safety_config...")
    from backend_app.core.safety_config import get_vyomquant_mode
    print("3. all dependencies ok! importing rate_limit...")
    import backend_app.core.rate_limit as rl
    print("4. rate_limit imported successfully! limiter:", rl.limiter)
except BaseException as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
