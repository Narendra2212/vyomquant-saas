import json
import subprocess
import sys

DIST_ID = "EEOXECPHQ8SR0"
FUNCTION_ARN = "arn:aws:cloudfront::273709947018:function/spa-rewrite"

res = subprocess.run(
    ["aws", "cloudfront", "get-distribution-config", "--id", DIST_ID],
    capture_output=True,
    text=True,
    encoding="utf-8"
)

if res.returncode != 0:
    print("Error getting distribution config:", res.stderr)
    sys.exit(1)

data = json.loads(res.stdout)
etag = data["ETag"]
dist_config = data["DistributionConfig"]

print(f"Current ETag: {etag}")

# 1. Attach Function to DefaultCacheBehavior
default_behavior = dist_config["DefaultCacheBehavior"]
default_behavior["FunctionAssociations"] = {
    "Quantity": 1,
    "Items": [
        {
            "FunctionARN": FUNCTION_ARN,
            "EventType": "viewer-request"
        }
    ]
}
print("Attached spa-rewrite function to DefaultCacheBehavior (viewer-request)")

# 2. Clean CustomErrorResponses so API and WebSocket 4xx/5xx codes are NOT masked as 200 HTML
dist_config["CustomErrorResponses"] = {
    "Quantity": 0,
    "Items": []
}
print("Removed CustomErrorResponses masking (APIs will now return real HTTP status codes)")

# Save updated config
with open("cf_spa_update.json", "w", encoding="utf-8") as f:
    json.dump(dist_config, f, indent=2)

print("Applying distribution update to CloudFront...")
cmd = [
    "aws", "cloudfront", "update-distribution",
    "--id", DIST_ID,
    "--distribution-config", "file://cf_spa_update.json",
    "--if-match", etag
]
update_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

if update_res.returncode == 0:
    update_data = json.loads(update_res.stdout)
    print(f"SUCCESS: Distribution updated! New ETag: {update_data.get('ETag')}, Status: {update_data.get('Distribution', {}).get('Status')}")
else:
    print("Error updating CloudFront:")
    print(update_res.stderr)
    sys.exit(1)
