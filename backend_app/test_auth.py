import asyncio
import os
import traceback

from dotenv import load_dotenv

from supabase import create_client

load_dotenv('d:/aerora_quant_backend_updated_final1/backend_app/.env')

supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_ANON_KEY')
supabase = create_client(supabase_url, supabase_key)

async def test():
    try:
        res = supabase.auth.sign_in_with_password({'email': 'narendra@aeroradynamics.in', 'password': os.environ.get('TEST_USER_PASSWORD', 'YOUR_TEST_PASSWORD')})
        print('Login success, token:', res.session.access_token[:20])
        user_res = supabase.auth.get_user(res.session.access_token)
        print('Get user success, ID:', user_res.user.id)
    except Exception:
        print('Error:')
        traceback.print_exc()

asyncio.run(test())
