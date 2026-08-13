"""
Script to trace the RuntimeWarning: coroutine 'get_admin_user' was never awaited
"""
import sys
import asyncio
import os

# Enable unraisable exception hook
unraisable_exceptions = []

def hook_unraisable(exc, obj):
    unraisable_exceptions.append((exc, obj))
    print(f"\n=== UNRAISABLE EXCEPTION ===")
    print(f"Exception: {exc}")
    print(f"Object: {obj}")
    print(f"Object type: {type(obj)}")
    import traceback
    print("=== Current Stack ===")
    traceback.print_stack()

sys.unraisablehook = hook_unraisable

# Add project to path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)
sys.path.insert(0, os.path.join(project_dir, '..'))

# Import the test module to see if it happens during import
print("Importing test_marketplace_pipeline...")
import tests.test_marketplace_pipeline

print("\nForcing garbage collection...")
import gc
gc.collect()

print("\n=== RESULTS ===")
if unraisable_exceptions:
    print(f"Found {len(unraisable_exceptions)} unraisable exceptions")
    for exc, obj in unraisable_exceptions:
        print(f"  - {exc}")
else:
    print("No unraisable exceptions found during import")
