import os
import requests
from dotenv import load_dotenv

load_dotenv()
url = f"{os.environ['SUPABASE_URL']}/pg-meta/default/query"
headers = {
    'apikey': os.environ['SUPABASE_SERVICE_ROLE_KEY'],
    'Authorization': 'Bearer ' + os.environ['SUPABASE_SERVICE_ROLE_KEY']
}
try:
    res = requests.post(url, headers=headers, json={"query": "SELECT 1;"})
    print(res.status_code)
    print(res.text)
except Exception as e:
    print(e)
