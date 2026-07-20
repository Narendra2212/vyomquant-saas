import asyncio
from supabase import create_client, Client

SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_KEY = "YOUR_SUPABASE_SERVICE_ROLE_KEY"

async def main():
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # 1. Fetch latest user from auth.users (via admin api)
    users = supabase.auth.admin.list_users()
    print("Total users in auth:", len(users))
    latest_user = sorted(users, key=lambda u: u.created_at, reverse=True)[0]
    print(f"Latest user: id={latest_user.id}, email={latest_user.email}, created_at={latest_user.created_at}")
    
    # 2. Fetch from profiles table
    resp = supabase.table("profiles").select("*").eq("id", latest_user.id).execute()
    print("Profile table query result:", resp.data)
    
    # 3. Fetch all profiles
    all_profiles = supabase.table("profiles").select("*").limit(5).execute()
    print("Some profiles:", all_profiles.data)

if __name__ == "__main__":
    asyncio.run(main())
