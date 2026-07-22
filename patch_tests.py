import os
import glob

test_files = glob.glob('backend_app/test_*.py')
for fpath in test_files:
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Just add fallback dummy values for tests if missing
    content = content.replace('supabase_url = os.environ.get("SUPABASE_URL")', 'supabase_url = os.environ.get("SUPABASE_URL", "http://dummy.url")')
    content = content.replace('supabase_key = os.environ.get("SUPABASE_KEY")', 'supabase_key = os.environ.get("SUPABASE_KEY", "dummy_key")')
    
    # Also if it uses os.getenv
    content = content.replace('supabase_url = os.getenv("SUPABASE_URL")', 'supabase_url = os.getenv("SUPABASE_URL", "http://dummy.url")')
    content = content.replace('supabase_key = os.getenv("SUPABASE_KEY")', 'supabase_key = os.getenv("SUPABASE_KEY", "dummy_key")')

    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
