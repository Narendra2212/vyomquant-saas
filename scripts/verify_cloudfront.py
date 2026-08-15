import urllib.request, re, os, glob

# 1. Read local dist/index.html
with open('algo22-terminal/dist/index.html', 'r', encoding='utf-8') as f:
    local_html = f.read()

# Extract script src and css href
local_js = re.findall(r'src="/assets/([^"]+)"', local_html)
local_css = re.findall(r'href="/assets/([^"]+)"', local_html)
print(f"Local assets: JS={local_js}, CSS={local_css}")

# 2. Fetch from CloudFront with cache-busting header
req = urllib.request.Request('https://d7d88qs4jmch.cloudfront.net/index.html', headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
with urllib.request.urlopen(req) as resp:
    remote_html = resp.read().decode('utf-8')

remote_js = re.findall(r'src="/assets/([^"]+)"', remote_html)
remote_css = re.findall(r'href="/assets/([^"]+)"', remote_html)
print(f"CloudFront assets: JS={remote_js}, CSS={remote_css}")

# 3. Compare
assert local_js == remote_js, f"Mismatch in JS bundle: local {local_js} vs remote {remote_js}"
assert local_css == remote_css, f"Mismatch in CSS bundle: local {local_css} vs remote {remote_css}"
print("VERIFIED: CloudFront is serving the exact newly built frontend bundle!")

# 4. Fetch the main JS bundle from CloudFront
main_js_url = f"https://d7d88qs4jmch.cloudfront.net/assets/{local_js[0]}"
print(f"Fetching {main_js_url}...")
req_js = urllib.request.Request(main_js_url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req_js) as resp:
    remote_js_content = resp.read().decode('utf-8')

assert len(remote_js_content) > 100000, "Remote JS content too small"
print(f"OK: Main bundle fetched successfully ({len(remote_js_content)} bytes).")
