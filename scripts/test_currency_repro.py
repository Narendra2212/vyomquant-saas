import asyncio, os, sys
sys.path.insert(0, '.')
from backend_app.core.dependencies import create_request_supabase_async
from backend_app.core.pricing_service import PricingService

os.environ['SUPABASE_URL'] = 'https://wrkexcjqnidkdrayhlsi.supabase.co'
os.environ['SUPABASE_ANON_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indya2V4Y2pxbmlka2RyYXlobHNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ3ODkyOTIsImV4cCI6MjA5MDM2NTI5Mn0.NTXqianuwy4xLw4FMY09Z0Q70wf7KWCIzkejaU2sR8s'

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
