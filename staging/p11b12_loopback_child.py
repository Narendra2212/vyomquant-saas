"""Child process: call `_app_cache_url()` and report whether the loopback guard fired."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["REDIS_URL"] = ""

from _pytest.outcomes import Failed  # noqa: E402

import tests.test_webhook_duplicate_delivery_real_redis as mod  # noqa: E402

try:
    url = mod._app_cache_url()
    print("RETURNED:", url)
except Failed as exc:
    print("FAILED-CLOSED:", str(exc)[:220])
