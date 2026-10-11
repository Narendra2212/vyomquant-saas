"""Does the ENV-03 boot reporting actually run, and does it leak a value?

`print_config_status()` is called from `main.lifespan`, not at import, so the full
regression's 13k passing tests do not necessarily exercise the lines added to it. This
calls it directly in a local environment (the documented reported-not-fatal case) and then
checks the captured output for any configured value.
"""

import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENV"] = "development"
os.environ["DEV_MODE"] = "true"

from backend_app.core import config  # noqa: E402

print(f"DOTENV_PATH={config.DOTENV_PATH}")

buffer = io.StringIO()
with redirect_stdout(buffer):
    config.print_config_status()
text = buffer.getvalue()

print("--- captured boot report ---")
print(text)
print("--- end ---")

verdicts = config.settings.credential_verdicts()
print(f"GOVERNED={len(verdicts)}")
for name, verdict in sorted(verdicts.items()):
    print(f"  {name}={verdict.value}")

# The report must name variables and verdicts, never values.
leaked = []
for name in verdicts:
    value = os.getenv(name)
    if value and len(value) >= 6 and value in text:
        leaked.append(name)
print(f"LEAKED_VALUES={leaked}")

problems = config.settings.credential_problems()
print(f"PROBLEMS={len(problems)}")
print(f"IS_DEPLOYED={config.settings._is_deployed_environment()}")
# The documented local behaviour: reported, not fatal.
config.settings.assert_credentials_are_production_grade()
print("LOCAL_ASSERT_DID_NOT_RAISE=True")
