import json
import subprocess
import sys

DIST_ID = "EEOXECPHQ8SR0"
CACHING_DISABLED_POLICY = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

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

target_patterns = ["/ws/*", "/api/*", "/health*", "/metrics*", "/openapi.json"]

for b in dist_config["CacheBehaviors"]["Items"]:
    pattern = b["PathPattern"]
    if pattern in target_patterns:
        print(f"Upgrading behavior for {pattern} to modern CachePolicy + OriginRequestPolicy...")
        b.pop("ForwardedValues", None)
        b.pop("MinTTL", None)
        b.pop("DefaultTTL", None)
        b.pop("MaxTTL", None)
        b["CachePolicyId"] = CACHING_DISABLED_POLICY
        b["OriginRequestPolicyId"] = ALL_VIEWER_EXCEPT_HOST
        b["AllowedMethods"] = {
            "Quantity": 7,
            "Items": ["HEAD", "DELETE", "POST", "GET", "OPTIONS", "PUT", "PATCH"],
            "CachedMethods": {
                "Quantity": 2,
                "Items": ["HEAD", "GET"]
            }
        }

with open("cf_policy_update.json", "w", encoding="utf-8") as f:
    json.dump(dist_config, f, indent=2)

print("Applying distribution update to CloudFront...")
cmd = [
    "aws", "cloudfront", "update-distribution",
    "--id", DIST_ID,
    "--distribution-config", "file://cf_policy_update.json",
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
