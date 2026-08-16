import urllib.request
import json

url = 'https://api.github.com/repos/Narendra2212/vyomquant-saas/actions/runs?per_page=5'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        for r in data.get('workflow_runs', []):
            print(f"ID: {r['id']} | Name: {r['name']} | Head: {r['head_sha'][:7]} | Status: {r['status']} | Conclusion: {r['conclusion']}")
except Exception as e:
    print('Error querying GitHub API:', e)
