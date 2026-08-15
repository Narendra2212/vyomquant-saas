import urllib.request
import re
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 1. Fetch CloudFront index.html
req = urllib.request.Request(
    'https://d7d88qs4jmch.cloudfront.net/',
    headers={'User-Agent': 'Mozilla/5.0'}
)
html = urllib.request.urlopen(req, context=ctx).read().decode('utf-8')
print("Index HTML:")
print(html)

js_files = re.findall(r'/assets/[a-zA-Z0-9_\-\.]+\.js', html)
print("\nJS Assets found:", js_files)

for js_path in js_files:
    js_url = f"https://d7d88qs4jmch.cloudfront.net{js_path}"
    req_js = urllib.request.Request(js_url, headers={'User-Agent': 'Mozilla/5.0'})
    content = urllib.request.urlopen(req_js, context=ctx).read().decode('utf-8', errors='ignore')
    print(f"\n--- {js_path} (size={len(content)} bytes) ---")
    
    # Search for localhost, api, ws
    localhost_matches = re.findall(r'.{0,40}localhost.{0,40}', content)
    if localhost_matches:
        print("  Found 'localhost' in bundle:")
        for m in localhost_matches[:5]:
            print("   ", m)
    
    api_matches = re.findall(r'.{0,30}apiBaseUrl.{0,30}', content)
    if api_matches:
        print("  Found 'apiBaseUrl' in bundle:")
        for m in api_matches[:5]:
            print("   ", m)

# 2. Test CloudFront /api/health
try:
    req_api = urllib.request.Request(
        'https://d7d88qs4jmch.cloudfront.net/api/health',
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    api_resp = urllib.request.urlopen(req_api, context=ctx)
    print("\nCloudFront /api/health Status:", api_resp.status)
    print("Response:", api_resp.read().decode('utf-8'))
except Exception as e:
    print("\nCloudFront /api/health Error:", e)

# 3. Test ALB direct /api/health
try:
    req_alb = urllib.request.Request(
        'http://vyomquant-alb-1008390777.ap-southeast-1.elb.amazonaws.com/api/health',
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    alb_resp = urllib.request.urlopen(req_alb, context=ctx)
    print("\nALB /api/health Status:", alb_resp.status)
    print("Response:", alb_resp.read().decode('utf-8'))
except Exception as e:
    print("\nALB /api/health Error:", e)
