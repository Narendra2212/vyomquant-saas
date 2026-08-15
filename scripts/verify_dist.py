import os, sys, glob, re

dist_dir = "algo22-terminal/dist"
index_bundle = glob.glob(f"{dist_dir}/assets/index-*.js")[0]
print(f"Main bundle: {index_bundle}")

with open(index_bundle, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Check Supabase URL
assert "supabase.co" in content, "Supabase URL missing in bundle"
print("OK: Supabase URL found in bundle")

# 2. Check Supabase anon key pattern
assert "eyJ" in content, "Supabase anon key missing in bundle"
print("OK: Supabase anon key found in bundle")

# 3. Security: Check service role key is NOT present
assert "service_role" not in content, "SECURITY: service_role found in bundle!"
print("OK: Security check passed (no service_role)")

# 4. Check index.html exists
assert os.path.exists(f"{dist_dir}/index.html"), "index.html missing"
print("OK: index.html exists")

print("All frontend build verifications PASSED.")
