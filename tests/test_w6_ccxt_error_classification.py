"""W-6 (surfaced by this session's EXT-01 corpus synthesis, independently re-verified
against current source): _handle_ccxt_error misclassified a transient network/timeout
failure as a non-retryable InsufficientFundsError whenever its message happened to
mention "balance" -- exactly what a failed fetch_balance call's own error text does,
regardless of why it failed.

Root cause: the classifier checked "insufficient"/"balance" before "network"/"timeout"/
"connection" in a fixed substring-match order, so a message like "Request timeout
fetching balance" matched the funds check first. InsufficientFundsError has
retryable=False; NetworkError has retryable=True -- so the misclassification flips a
transient, retryable failure into a non-retryable one.

Confirmed via grep that `.retryable` is read in exactly one place in this codebase (a log
line), and that no caller does `isinstance(error, InsufficientFundsError)` or
`isinstance(error, NetworkError)` anywhere -- so this bug's real blast radius is
telemetry/log accuracy, not order safety or an actual retry decision, matching the
corpus's own characterisation.

Fix: classify by ccxt's own exception type first (a type cannot be fooled by what
operation name happens to appear in the message), falling back to substring matching --
reordered so network/timeout is checked before insufficient/balance -- only for
exceptions that are not a recognised ccxt type.
"""

from __future__ import annotations

import ccxt.async_support as ccxt
import pytest

from backend_app.backend.exchange_executor import (
    AuthenticationError,
    CCXTExchangeExecutor,
    InsufficientFundsError,
    InvalidSymbolError,
    NetworkError,
    RateLimitError,
)


@pytest.fixture()
def executor() -> CCXTExchangeExecutor:
    return CCXTExchangeExecutor(
        exchange_id="binance",
        api_key="fake-key",
        api_secret="fake-secret",
        sandbox=True,
    )


class TestClassificationByRealCcxtType:
    """The exact W-6 scenario: a real ccxt type whose message mentions an unrelated
    word must still be classified by its type, not the word."""

    def test_a_request_timeout_mentioning_balance_is_retryable_network_error(self, executor):
        error = ccxt.RequestTimeout("Request timeout fetching balance")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, NetworkError)
        assert classified.retryable is True, (
            "a RequestTimeout whose message happens to mention 'balance' must not be "
            "misclassified as a non-retryable InsufficientFundsError"
        )

    def test_a_network_error_mentioning_insufficient_is_still_network(self, executor):
        """Same bug, the other trigger word."""
        error = ccxt.NetworkError("insufficient response from exchange, connection reset")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, NetworkError)
        assert classified.retryable is True

    def test_a_real_insufficient_funds_error_is_still_classified_correctly(self, executor):
        """The fix must not overcorrect: a genuine InsufficientFunds rejection must stay
        non-retryable."""
        error = ccxt.InsufficientFunds("Account has insufficient balance for this order")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, InsufficientFundsError)
        assert classified.retryable is False

    def test_rate_limit_exceeded_is_classified_before_the_broader_network_error(self, executor):
        """ccxt nests RateLimitExceeded under NetworkError; the more specific type must
        win, matching this platform's own distinct RATE_LIMIT handling (retry_after)."""
        error = ccxt.RateLimitExceeded("too many requests, slow down")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, RateLimitError)
        assert classified.retryable is True

    def test_bad_symbol_is_classified_before_the_broader_exchange_error(self, executor):
        error = ccxt.BadSymbol("balance of trade pairs: symbol not found")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, InvalidSymbolError)
        assert classified.retryable is False

    def test_authentication_error_is_still_classified_by_type(self, executor):
        error = ccxt.AuthenticationError("invalid API-key, IP, or permissions for action")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, AuthenticationError)
        assert classified.retryable is False


class TestFallbackForNonCcxtExceptions:
    """Non-ccxt exceptions (asyncio.TimeoutError, aiohttp/OS errors) still fall through
    to the substring classifier -- reordered, but still the fallback for everything
    that is not a recognised ccxt type."""

    def test_a_plain_timeout_error_with_no_ccxt_type_is_still_network_error(self, executor):
        error = TimeoutError("connection timed out while fetching balance")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, NetworkError)
        assert classified.retryable is True

    def test_a_plain_runtime_error_naming_insufficient_funds_is_still_classified(self, executor):
        error = RuntimeError("order rejected: insufficient funds")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, InsufficientFundsError)
        assert classified.retryable is False

    def test_a_plain_authentication_message_is_still_classified(self, executor):
        error = RuntimeError("authentication failed for apiKey=xxx")
        classified = executor._handle_ccxt_error(error)

        assert isinstance(classified, AuthenticationError)
