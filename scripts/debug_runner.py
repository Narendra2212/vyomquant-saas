import traceback
import sys
import os
sys.path.insert(0, os.path.abspath("."))

try:
    from scripts import execute_forensic_tests_a_and_b as tests
    import asyncio
    asyncio.run(tests.main())
except Exception as e:
    traceback.print_exc(file=sys.stdout)
