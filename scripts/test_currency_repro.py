import asyncio, os, sys
sys.path.insert(0, '.')
from backend_app.core.dependencies import create_request_supabase_async
from backend_app.core.pricing_service import PricingService

os.environ.setdefault('SUPABASE_URL', 'https://wrkexcjqnidkdrayhlsi.supabase.co')
# SEC-06: this used to hardcode a real Supabase anon JWT for this project, committed to
# git. setdefault, not an unconditional assignment: a caller's own SUPABASE_ANON_KEY
# still wins, and if neither the caller nor the environment supplies one, fail loudly
# instead of carrying a credential in this file.
if not os.environ.get('SUPABASE_ANON_KEY'):
    sys.exit("SUPABASE_ANON_KEY must be set in the environment to run this script.")

async def main():
    import requests
    r = requests.post(f"{os.environ['SUPABASE_URL']}/auth/v1/token?grant_type=password", headers={'apikey': os.environ['SUPABASE_ANON_KEY'], 'Content-Type': 'application/json'}, json={'email': 'test@test.com', 'password': 'test123'})
    token = r.json()['access_token']
    user_id = r.json()['user']['id']
    sb = await create_request_supabase_async(token)
    
    try:
        curr = await PricingService.determine_currency(user_id, sb)
        print('Currency determined:', curr)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
