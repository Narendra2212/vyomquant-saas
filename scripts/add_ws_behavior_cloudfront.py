import json
import subprocess
import sys

# Call aws cli directly from python
res = subprocess.run(
    ["aws", "cloudfront", "get-distribution-config", "--id", "EEOXECPHQ8SR0"],
    capture_output=True,
    text=True,
    encoding="utf-8"
)

if res.returncode != 0:
    print("Error getting distribution config:", res.stderr)
    sys.exit(1)

data = json.loads(res.stdout)
etag = data['ETag']
dist_config = data['DistributionConfig']

print(f"Current ETag: {etag}")
print(f"Current Cache Behaviors: {dist_config['CacheBehaviors']['Quantity']}")

items = dist_config['CacheBehaviors']['Items']
existing_patterns = [b['PathPattern'] for b in items]
print("Existing path patterns:", existing_patterns)

if '/ws/*' not in existing_patterns:
    # Clone /api/* behavior
    api_behavior = None
    for b in items:
        if b['PathPattern'] == '/api/*':
            api_behavior = json.loads(json.dumps(b))
            break
    
    if api_behavior:
        ws_behavior = api_behavior
        ws_behavior['PathPattern'] = '/ws/*'
        # Ensure all methods allowed (for WebSocket handshake and HTTP upgrade)
        ws_behavior['AllowedMethods'] = {
            "Quantity": 7,
            "Items": ["HEAD", "DELETE", "POST", "GET", "OPTIONS", "PUT", "PATCH"],
            "CachedMethods": {
                "Quantity": 2,
                "Items": ["HEAD", "GET"]
            }
        }
        # Insert /ws/* behavior at the beginning
        items.insert(0, ws_behavior)
        dist_config['CacheBehaviors']['Quantity'] = len(items)
        print("Added /ws/* cache behavior pointing to ALB!")
        
        # Save updated config
        with open('cf_updated_config.json', 'w', encoding='utf-8') as f:
            json.dump(dist_config, f, indent=2)
            
        print("Updating CloudFront distribution...")
        cmd = [
            "aws", "cloudfront", "update-distribution",
            "--id", "EEOXECPHQ8SR0",
            "--distribution-config", "file://cf_updated_config.json",
            "--if-match", etag
        ]
        update_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        print("Return code:", update_res.returncode)
        if update_res.returncode == 0:
            print("Successfully updated CloudFront distribution with /ws/* behavior!")
        else:
            print("Error updating CloudFront:")
            print(update_res.stderr[:500])
    else:
        print("Could not find /api/* behavior to clone")
else:
    print("/ws/* behavior already exists")
