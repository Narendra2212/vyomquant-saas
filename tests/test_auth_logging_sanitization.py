"""
tests/test_auth_logging_sanitization.py

Unit tests verifying that:
1. core/auth_middleware.py contains no raw print() calls and uses structured logging.
2. Frontend auth components (App.jsx, apiClient.js) contain no un-gated email logging or raw token console outputs.
"""

import pathlib


def test_auth_middleware_has_no_print_calls():
    middleware_path = pathlib.Path("backend_app/core/auth_middleware.py")
    assert middleware_path.exists(), "backend_app/core/auth_middleware.py missing"

    content = middleware_path.read_text(encoding="utf-8")

    # Assert no raw print statements remain
    assert "print(f\"" not in content, "auth_middleware.py still contains raw print() calls"
    assert "print(" not in content, "auth_middleware.py still contains raw print() calls"

    # Assert structured logger is initialized and used
    assert 'logger = logging.getLogger("AuthMiddleware")' in content, "auth_middleware.py missing structured logger"
    assert "logger.warning" in content, "auth_middleware.py missing logger.warning"
    assert "logger.debug" in content, "auth_middleware.py missing logger.debug"


def test_frontend_auth_logs_sanitized():
    app_jsx_path = pathlib.Path("algo22-terminal/src/App.jsx")
    assert app_jsx_path.exists(), "algo22-terminal/src/App.jsx missing"
    app_content = app_jsx_path.read_text(encoding="utf-8")

    # Assert AUTH DIAGNOSTIC console logs are gated and do not output email addresses
    assert 'console.log("🔐 AUTH DIAGNOSTIC: Starting signup flow",' not in app_content, "App.jsx still logs raw signup diagnostic emails"
    assert 'console.log("🔐 AUTH DIAGNOSTIC: Starting signin flow",' not in app_content, "App.jsx still logs raw signin diagnostic emails"

    api_client_path = pathlib.Path("algo22-terminal/src/apiClient.js")
    assert api_client_path.exists(), "algo22-terminal/src/apiClient.js missing"
    api_content = api_client_path.read_text(encoding="utf-8")

    # Assert raw token substrings are not logged in apiClient.js
    assert "token.substring" not in api_content, "apiClient.js still logs token substrings"
