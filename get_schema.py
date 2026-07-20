import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()
res = requests.get(
    f"{os.environ['SUPABASE_URL']}/rest/v1/",
    headers={
        'apikey': os.environ['SUPABASE_SERVICE_ROLE_KEY'],
        'Authorization': 'Bearer ' + os.environ['SUPABASE_SERVICE_ROLE_KEY']
    }
)
with open('schema.json', 'w') as f:
    json.dump(res.json(), f, indent=2)
