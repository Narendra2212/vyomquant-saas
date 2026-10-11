# -*- coding: utf-8 -*-
"""
Core Configuration Module

Loads environment variables from .env file and provides typed settings access.
All sensitive values should be stored in .env file, never committed to git.
"""

import logging
import os
from enum import Enum
from typing import ClassVar, Dict, Optional

from dotenv import find_dotenv, load_dotenv

from backend_app.core.secret_validation import (PLACEHOLDER_SUBSTRINGS,
                                                SecretVerdict,
                                                classify_secret,
                                                describe_verdict,
                                                secret_is_usable)

# ══════════════════════════════════════════════════════════════════════════════════════════
#  WHICH .env FILE ACTUALLY WINS  (ENV-03)
# ══════════════════════════════════════════════════════════════════════════════════════════
# This used to be a bare ``load_dotenv()``, and the file it loaded was NOT the one an
# operator would expect. ``load_dotenv()`` with no argument calls ``find_dotenv()``, which
# walks up from the CALLING MODULE's directory — this file's, i.e. ``backend_app/`` — so it
# resolves ``backend_app/.env`` and never reaches the repository-root ``.env`` at all. Both
# files exist in this working tree, and editing the root one has no effect on this process.
#
# That is not a hypothetical: during this audit a required variable was added to the root
# ``.env`` to fix a finding, the fix was recorded as applied, and it was inert, because
# ``backend_app/.env`` is the file that wins.
#
# RESOLUTION IS UNCHANGED, DELIBERATELY. ``load_dotenv(find_dotenv())`` is exactly what
# ``load_dotenv()`` already did — same frame, same walk, same file. Switching the winner to
# the root ``.env`` would silently change which variables are set in every local and CI
# process, which is a behaviour change dressed up as a documentation fix. What changes here
# is that the resolved path is now a named, inspectable constant that ``print_config_status``
# reports at boot, so the file that wins is visible instead of implicit.
#
# ``override=False`` is dotenv's default and is retained: a real environment variable always
# beats the file. That is what keeps these committed placeholders out of a deployed process —
# together with ``.dockerignore``, which excludes ``.env``, ``.env.*`` and
# ``backend_app/.env`` so neither file is in the image at all.
#
# Regression: tests/test_env03_placeholder_secrets_are_invalid.py
DOTENV_PATH: str = find_dotenv()
if DOTENV_PATH:
    load_dotenv(DOTENV_PATH)

logger = logging.getLogger(__name__)


def validate_production_config() -> None:
    """
    Validate that production configuration is properly set.

    THIS FUNCTION HAS NO CALL SITES (ENV-03). Nothing in the application, the entrypoints
    or the CI workflows invokes it; it has been dead since it was written. It is left in
    place and documented rather than deleted because deleting it would remove the record,
    and because it is the second instance of this pattern found in this audit — the first
    was ``safety_config.validate_safety_config_integrity``, also uncalled (TRADE-06). A
    validator nothing calls is not a control, however correct its body is.

    It would also not have caught the finding it looks like it covers: its
    ``if not value`` test is satisfied by ``"YOUR_SUPABASE_SERVICE_ROLE_KEY"``, and its
    placeholder check applies to ``JWT_SECRET`` alone and only ``logger.warning``s.

    THE LIVE CONTROL IS ``Settings.assert_credentials_are_production_grade``, called from
    ``main.lifespan`` before any port is bound. Prefer it.
    """
    env = os.getenv("ENV", "development").lower()
    
    if env == "production":
        critical_vars = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY"),
            "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            "SUPABASE_JWT_SECRET": os.getenv("SUPABASE_JWT_SECRET"),
            "JWT_SECRET": os.getenv("JWT_SECRET"),
        }
        
        missing_vars = [var for var, value in critical_vars.items() if not value]
        
        if missing_vars:
            logger.error(
                f"CRITICAL: Production environment requires all configuration variables. "
                f"Missing: {', '.join(missing_vars)}. "
                f"Application running with degraded security."
            )
        
        # Validate JWT_SECRET is not a weak default (warning only, not error)
        jwt_secret = os.getenv("JWT_SECRET")
        weak_secrets = ["dev-secret-change-in-production", "dummy", "test", "secret"]
        if jwt_secret and any(weak in jwt_secret.lower() for weak in weak_secrets):
            logger.warning(
                f"SECURITY: JWT_SECRET appears to be a weak development secret. "
                f"Production requires a cryptographically strong secret. "
                f"Current value contains: {jwt_secret[:10]}..."
            )
        
        # Additional JWT_SECRET validation (warning only)
        if jwt_secret and len(jwt_secret) < 32:
            logger.warning(
                f"SECURITY: JWT_SECRET is only {len(jwt_secret)} characters. "
                "Recommended minimum is 32 characters for production."
            )
        
        logger.info("[CONFIG] Production configuration validated successfully")
    else:
        logger.info(f"[CONFIG] Development environment detected ({env}), skipping strict validation")


class Environment(Enum):
    """Application environment types"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


class Settings:
    """
    Application settings loaded from environment variables.
    
    Usage:
        from backend_app.core.config import settings
        
        supabase_url = settings.SUPABASE_URL
        is_dev = settings.ENV == Environment.DEVELOPMENT
    """
    
    # ==========================================
    # SUPABASE CONFIGURATION
    # ==========================================
    SUPABASE_URL: Optional[str] = os.getenv("SUPABASE_URL")
    SUPABASE_ANON_KEY: Optional[str] = os.getenv("SUPABASE_ANON_KEY")
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_JWT_SECRET: Optional[str] = os.getenv("SUPABASE_JWT_SECRET")  # FAIL-CLOSED: No dummy defaults
    
    # ==========================================
    # QUESTDB CONFIGURATION
    # ==========================================
    QUESTDB_HOST: str = os.getenv("QUESTDB_HOST", "127.0.0.1")
    QUESTDB_PORT: int = int(os.getenv("QUESTDB_PORT", 9000))
    QUESTDB_USER: Optional[str] = os.getenv("QUESTDB_USER")
    QUESTDB_PASSWORD: Optional[str] = os.getenv("QUESTDB_PASSWORD")
    
    # ==========================================
    # APPLICATION ENVIRONMENT
    # ==========================================
    ENV: str = os.getenv("ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    DEV_MODE: bool = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    
    # ==========================================
    # SECURITY
    # ==========================================
    JWT_SECRET: str = os.getenv("JWT_SECRET")  # FAIL-CLOSED: No default for production
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
    
    # ==========================================
    # API CONFIGURATION
    # ==========================================
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", 8000))
    API_WORKERS: int = int(os.getenv("API_WORKERS", 1))
    
    # ==========================================
    # CORS CONFIGURATION
    # ==========================================
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    
    @property
    def cors_origins_list(self) -> list:
        """Parse CORS origins as list"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    # ==========================================
    # WEBSOCKET CONFIGURATION
    # ==========================================
    WS_HEARTBEAT_INTERVAL: int = int(os.getenv("WS_HEARTBEAT_INTERVAL", 30))
    WS_MAX_CONNECTIONS: int = int(os.getenv("WS_MAX_CONNECTIONS", 1000))
    
    # ==========================================
    # TRADING ENGINE CONFIGURATION
    # ==========================================
    DEFAULT_INITIAL_CAPITAL: float = float(os.getenv("DEFAULT_INITIAL_CAPITAL", 10000.0))
    DEFAULT_RISK_PER_TRADE: float = float(os.getenv("DEFAULT_RISK_PER_TRADE", 0.01))
    DEFAULT_MAX_DRAWDOWN: float = float(os.getenv("DEFAULT_MAX_DRAWDOWN", 0.20))
    DEFAULT_DAILY_LOSS_LIMIT: float = float(os.getenv("DEFAULT_DAILY_LOSS_LIMIT", 0.05))
    
    # ==========================================
    # EXCHANGE CONFIGURATION
    # ==========================================
    EXCHANGE_ID: Optional[str] = os.getenv("EXCHANGE_ID")  # No default - must be explicitly configured
    EXCHANGE_API_KEY: Optional[str] = os.getenv("EXCHANGE_API_KEY")
    EXCHANGE_SECRET: Optional[str] = os.getenv("EXCHANGE_SECRET")
    
    # ==========================================
    # LOGGING CONFIGURATION
    # ==========================================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # ==========================================
    # CACHE & RATE LIMITING
    # ==========================================
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", 60))
    
    # ==========================================
    # FEATURE FLAGS
    # ==========================================
    ENABLE_ML_TRAINING: bool = os.getenv("ENABLE_ML_TRAINING", "true").lower() in ("true", "1", "yes")
    ENABLE_LIVE_TRADING: bool = os.getenv("ENABLE_LIVE_TRADING", "false").lower() in ("true", "1", "yes")
    ENABLE_NOTIFICATIONS: bool = os.getenv("ENABLE_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
    
    # ==========================================
    # NOTIFICATION CONFIGURATION
    # ==========================================
    SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv("DISCORD_WEBHOOK_URL")
    EMAIL_SMTP_HOST: Optional[str] = os.getenv("EMAIL_SMTP_HOST")
    EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", 587))
    EMAIL_USER: Optional[str] = os.getenv("EMAIL_USER")
    EMAIL_PASSWORD: Optional[str] = os.getenv("EMAIL_PASSWORD")
    
    # ==========================================
    # VALIDATION METHODS
    # ==========================================
    def validate_required(self) -> list:
        """
        Validate that all required settings are present.
        Returns list of missing required variables.
        """
        required = {
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_SERVICE_ROLE_KEY": self.SUPABASE_SERVICE_ROLE_KEY,
            "SUPABASE_JWT_SECRET": self.SUPABASE_JWT_SECRET,
        }
        
        if not self.DEV_MODE:
            # Additional requirements for production
            required.update({
                "JWT_SECRET": self.JWT_SECRET if self.JWT_SECRET != "dev-secret-change-in-production" else None,
                "SUPABASE_ANON_KEY": self.SUPABASE_ANON_KEY,
            })
            
        missing = [k for k, v in required.items() if not v]
        return missing

    # ══════════════════════════════════════════════════════════════════════
    #  JWT SECRET STRENGTH  (BUG-012)
    # ══════════════════════════════════════════════════════════════════════

    #: RFC 7518 §3.2: an HMAC-SHA256 key must be at least 256 bits. A shorter key
    #: weakens HS256 regardless of how it was generated.
    MIN_JWT_SECRET_BYTES: ClassVar[int] = 32

    #: Substrings that indicate a placeholder rather than a generated secret. Matched
    #: case-insensitively against the whole value.
    #:
    #: ONE VOCABULARY (ENV-03). This used to be a private six-line tuple here, while the
    #: rest of the credential checks in this codebase had no placeholder vocabulary at all
    #: and were spelled ``if not value:``. Two lists for one concept is how
    #: ``detect_ml_nodes`` ended up with two spellings of "model node" and a gate that
    #: missed half of them, so this now points at the shared list in
    #: ``core/secret_validation.py``. The entries the JWT tests pin — ``default``,
    #: ``password``, ``insecure``, ``dev-secret``, ``changeme`` — are all still in it.
    #:
    #: ``example`` was narrowed to ``example.com`` / ``example.org`` / ``example.net`` when
    #: the list moved: as a bare substring it would also condemn any real credential
    #: containing the letters "example", and the placeholders it was written for are all
    #: domains.
    WEAK_JWT_SECRET_PATTERNS: ClassVar[tuple] = PLACEHOLDER_SUBSTRINGS

    def jwt_secret_problems(self) -> list:
        """Every reason the configured ``JWT_SECRET`` is unfit, as a list of sentences.

        Returns an empty list when the secret is acceptable. Deliberately returns ALL
        problems rather than the first, so an operator rotating the secret does not have
        to fix one, redeploy, and discover the next.

        **No secret value, and no fragment of one, appears in the returned text.** Only
        its length and the NAME of the pattern it matched. The whole point of this check
        is handling a secret safely, so it must not be the thing that leaks it.
        """
        problems = []
        secret = self.JWT_SECRET

        if not secret:
            return ["JWT_SECRET is not set."]

        # Length is measured in BYTES, not characters: RFC 7518's requirement is on key
        # material, and a multi-byte character contributes more bytes than characters.
        # len(str) would overstate the strength of an ASCII secret by exactly zero and
        # understate a non-ASCII one, so bytes is both correct and never more lenient.
        length_bytes = len(secret.encode("utf-8"))
        if length_bytes < self.MIN_JWT_SECRET_BYTES:
            problems.append(
                f"JWT_SECRET is {length_bytes} bytes; RFC 7518 section 3.2 requires at "
                f"least {self.MIN_JWT_SECRET_BYTES} bytes for HS256."
            )

        lowered = secret.lower()
        matched = [p for p in self.WEAK_JWT_SECRET_PATTERNS if p in lowered]
        if matched:
            # THE MATCHED PATTERN IS NOT NAMED, ON PURPOSE.
            #
            # Naming it discloses a known substring of the secret into the boot log and
            # the crash reporter -- the two places a secret must not appear -- and
            # materially shrinks an attacker's search space. The count is enough for an
            # operator to act, because `WEAK_JWT_SECRET_PATTERNS` is right here in
            # source for them to read.
            #
            # Caught by `test_the_error_names_no_part_of_the_secret`, which found a
            # 9-character fragment of the secret in this very message.
            problems.append(
                f"JWT_SECRET matches {len(matched)} known placeholder pattern(s) from "
                f"Settings.WEAK_JWT_SECRET_PATTERNS, so it is not a generated secret. "
                f"The matching pattern is deliberately not named here, because doing so "
                f"would write part of the secret to the log."
            )

        if len(set(secret)) < 8:
            problems.append(
                f"JWT_SECRET uses only {len(set(secret))} distinct characters, which is "
                f"not consistent with a randomly generated value."
            )

        return problems

    def assert_jwt_secret_is_production_grade(self) -> None:
        """Raise unless ``JWT_SECRET`` is fit for a production deployment.

        FAILS THE PROCESS, DELIBERATELY. This used to be two ``logger.warning`` calls
        and the application started anyway -- so a 24-byte secret (which is what this
        deployment actually had, measured during the pre-launch audit) shipped with
        nothing but a line in a log nobody reads at boot.

        Why a hard failure is the right severity: ``JWT_SECRET`` is the HMAC key. Its
        strength is the floor on the cost of forging a token, and the audit found a code
        path where a token signed with it was accepted as proof of identity for an
        arbitrary ``sub`` and an arbitrary ``app_metadata.role``. That path is now closed
        (see ``auth_middleware._test_token_fallback_allowed``), which is why this is P1
        rather than P0 -- but the key is still in HMAC use and a short key is still
        cheaper to attack. A deployment that cannot authenticate safely should not
        accept traffic.

        Only enforced for a deployed environment. A developer running with
        ``DEV_MODE=true`` or an unset/benign ``ENV`` is unaffected, because the goal is
        to stop a weak secret reaching customers, not to make local work harder.

        Raises:
            RuntimeError: Naming every problem, and no part of the secret itself.

        Regression: tests/test_jwt_secret_is_production_grade.py
        """
        if not self._is_deployed_environment():
            return

        problems = self.jwt_secret_problems()
        if not problems:
            return

        raise RuntimeError(
            "FATAL: JWT_SECRET is not fit for a deployed environment.\n  - "
            + "\n  - ".join(problems)
            + "\n\nGenerate one with:  python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"\n"
            "Store it in the deployment's secret manager (this application reads "
            "JWT_SECRET from the ECS `secrets` block, not from `environment`), then "
            "redeploy. The value must never be committed or logged."
        )

    def _is_deployed_environment(self) -> bool:
        """Whether this process is serving a deployed environment.

        Reads the real environment variables rather than ``self.ENV``, whose default is
        ``"development"`` -- the same defect that disarmed the authentication gate
        (BUG-002). An unset ``ENV`` is treated as DEPLOYED here, which is the opposite
        default from that gate and is deliberate: there, defaulting to "development"
        opened a bypass, so the safe answer was "not a test environment"; here, the safe
        answer to "is somebody's money behind this?" is yes.

        ``DEV_MODE=true`` is the single explicit opt-out, and
        ``core/dependencies._is_production_safe`` already refuses that combination with
        ``ENV=production``.
        """
        if os.getenv("DEV_MODE", "false").strip().lower() in ("true", "1", "yes"):
            return False
        env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
        return env not in ("development", "dev", "local", "test", "testing")

    # ══════════════════════════════════════════════════════════════════════
    #  CREDENTIAL CONFIGURATION  (ENV-03)
    # ══════════════════════════════════════════════════════════════════════

    #: Credentials a deployed environment cannot run without. MISSING, EMPTY and
    #: PLACEHOLDER are all fatal for these.
    #:
    #: Chosen from the code that already refuses without them, not from a wish list:
    #:   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- api_key_vault.py:80 refuses to arm the
    #:       vault, and validate_config() below already calls them required
    #:   MASTER_ENCRYPTION_KEYS                   -- api_key_vault.py:84 raises
    #:       EnvironmentError and credential_vault.py:267 raises RuntimeError
    #:   SUPABASE_JWT_SECRET, JWT_SECRET          -- validate_required() already requires
    #:       them; JWT_SECRET's STRENGTH is enforced separately by
    #:       assert_jwt_secret_is_production_grade
    #:   SUPABASE_ANON_KEY                        -- validate_required() requires it
    #:       whenever DEV_MODE is off
    REQUIRED_DEPLOYED_CREDENTIALS: ClassVar[tuple] = (
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "JWT_SECRET",
        "MASTER_ENCRYPTION_KEYS",
    )

    #: Credentials that are optional, but which must not be a PLACEHOLDER if set. MISSING is
    #: accepted (the feature is simply off); EMPTY and PLACEHOLDER are fatal in a deployed
    #: environment.
    #:
    #: STRIPE_SECRET_KEY is the clearest case in the whole finding.
    #: ``billing._validate_keys`` accepts a Stripe key only when it starts with ``sk_live_``
    #: — precisely so a test-mode key cannot grant a paid entitlement — and
    #: ``.env.example``'s own placeholder is ``sk_live_your-stripe-secret-key``, which
    #: passes that check. A prefix is not a credential.
    PLACEHOLDER_REJECTED_CREDENTIALS: ClassVar[tuple] = (
        "DATABASE_URL",
        "CREDENTIAL_VAULT_SALT",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
    )

    def credential_verdicts(self) -> Dict[str, SecretVerdict]:
        """Every credential this module governs, mapped to its verdict.

        Reads ``os.environ`` live rather than the attributes above. ``Settings`` resolves
        its class attributes at IMPORT time, and this check runs during application
        startup, so a live read is the one that describes the process that is about to
        serve traffic. It is also uniform: ``MASTER_ENCRYPTION_KEYS``, ``DATABASE_URL`` and
        the gateway secrets have no ``Settings`` attribute at all, and half-reading from
        ``self`` and half from the environment is how a check ends up disagreeing with the
        thing it checks.

        Returns verdicts, not booleans, so a caller can tell "forgotten" from "set to
        nothing" from "template value left in place" — three different operator mistakes.
        """
        names = (
            tuple(self.REQUIRED_DEPLOYED_CREDENTIALS)
            + tuple(self.PLACEHOLDER_REJECTED_CREDENTIALS)
        )
        return {name: classify_secret(os.getenv(name)) for name in names}

    def credential_problems(self) -> list:
        """Every reason the configured credentials are unfit for a deployed environment.

        Empty list when they are acceptable. Returns ALL problems rather than the first,
        for the same reason ``jwt_secret_problems`` does: an operator fixing configuration
        should not have to redeploy once per missing variable.

        **No credential value, and no fragment of one, appears in the returned text** —
        only the variable's NAME and its verdict. See ``secret_validation.describe_verdict``.

        This is the check the whole finding is about. Every credential gate in this codebase
        was spelled ``if not value:``, and ``bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")`` is
        ``True``, so the placeholders committed in ``backend_app/.env`` satisfied all of
        them. Presence is not configuration.
        """
        verdicts = self.credential_verdicts()
        problems = []

        for name in self.REQUIRED_DEPLOYED_CREDENTIALS:
            verdict = verdicts[name]
            if not verdict.is_usable:
                problems.append(describe_verdict(name, verdict))

        for name in self.PLACEHOLDER_REJECTED_CREDENTIALS:
            verdict = verdicts[name]
            # MISSING is fine here: these are optional. EMPTY is not — an empty value is a
            # secret-manager reference that resolved to nothing, which is a broken
            # deployment pretending to be an unconfigured one.
            if verdict in (SecretVerdict.PLACEHOLDER, SecretVerdict.EMPTY):
                problems.append(describe_verdict(name, verdict))

        return problems

    def assert_credentials_are_production_grade(self) -> None:
        """Raise unless every governed credential is actually configured.

        FAILS THE PROCESS, DELIBERATELY, AND ONLY IN A DEPLOYED ENVIRONMENT.

        Production fails closed: a deployment whose service-role key is the string
        ``YOUR_SUPABASE_SERVICE_ROLE_KEY`` cannot serve traffic, because every request it
        would answer about a user's data or money would be answered by a client that cannot
        authenticate. Starting anyway produces 500s that look like an outage rather than a
        misconfiguration, which is strictly worse than not starting.

        Development keeps its documented local behaviour unchanged: ``_is_deployed_environment``
        returns ``False`` for ``ENV`` in development/dev/local/test/testing, or for an
        explicit ``DEV_MODE=true``, so a developer with the committed placeholders in
        ``backend_app/.env`` is unaffected and the test lane still boots. The placeholders
        are still *reported* there — ``print_config_status`` prints them — so the state is
        visible without being fatal. That asymmetry is the point: the same configuration is
        a warning locally and a refusal in production.

        An UNSET ``ENV`` counts as DEPLOYED, matching ``assert_jwt_secret_is_production_grade``
        and for the same reason: the safe answer to "is somebody's money behind this?" is yes.

        Raises:
            RuntimeError: naming every unfit variable, and no part of any value.

        Regression: tests/test_env03_placeholder_secrets_are_invalid.py
        """
        if not self._is_deployed_environment():
            return

        problems = self.credential_problems()
        if not problems:
            return

        raise RuntimeError(
            "FATAL: this deployment's credentials are not configured.\n  - "
            + "\n  - ".join(problems)
            + "\n\nA non-empty string is not a credential: the templates in this "
            "repository ship values such as YOUR_SUPABASE_SERVICE_ROLE_KEY and "
            "sk_live_your-stripe-secret-key, which are truthy and which pass a "
            "prefix check. Set each variable from the deployment's secret manager "
            "(this application reads them from the ECS `secrets` block, not from "
            "`environment`), then redeploy. No value may be committed or logged."
        )

    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.ENV.lower() == "production"
    
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.ENV.lower() in ("development", "dev")
    
    def get_database_url(self) -> str:
        """Construct QuestDB connection URL"""
        auth = ""
        if self.QUESTDB_USER and self.QUESTDB_PASSWORD:
            auth = f"{self.QUESTDB_USER}:{self.QUESTDB_PASSWORD}@"
        return f"http://{auth}{self.QUESTDB_HOST}:{self.QUESTDB_PORT}"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """
    Dependency injection function for FastAPI.
    
    Usage in FastAPI:
        @app.get("/config")
        def get_config(settings: Settings = Depends(get_settings)):
            return {"env": settings.ENV}
    """
    return settings


# ==========================================
# SAFE VALIDATION (WITH FALLBACK MODE)
# ==========================================
def validate_config():
    """
    Validate configuration and return status.
    Does NOT crash - allows fallback mode for gradual migration.

    ENV-03: THESE CHECKS WERE TRUTHINESS TESTS AND NOW CLASSIFY THE VALUE. They used to be
    ``if not settings.SUPABASE_URL``, which ``"https://YOUR_PROJECT_REF.supabase.co"``
    satisfies - so with the committed placeholders in place this function reported
    ``mode: "production"`` and ``is_configured: True``, and ``print_config_status`` printed
    ``Supabase: CONNECTED (project: YOUR_PROJECT_REF)`` two lines above a block listing
    thirteen unconfigured credentials. Measured in ``staging/probe_config_status.py``.

    Reporting a placeholder as CONNECTED is the same defect as accepting one, in the one
    place an operator looks to find out. Nothing outside this module reads ``config_status``
    or calls this function, so this is a reporting change only; the fail-closed control is
    ``Settings.assert_credentials_are_production_grade``.

    ``QUESTDB_HOST`` keeps a plain truthiness test: it has a real default
    (``127.0.0.1``) and is a hostname rather than a credential, so the placeholder
    vocabulary does not apply to it.
    """
    missing = []
    warnings = []

    if not secret_is_usable(settings.SUPABASE_URL):
        missing.append("SUPABASE_URL")

    if not secret_is_usable(settings.SUPABASE_SERVICE_ROLE_KEY):
        missing.append("SUPABASE_SERVICE_ROLE_KEY")

    if not secret_is_usable(settings.SUPABASE_ANON_KEY):
        warnings.append("SUPABASE_ANON_KEY")

    # Check QuestDB
    if not settings.QUESTDB_HOST:
        warnings.append("QUESTDB_HOST")

    return {
        "is_configured": len(missing) == 0,
        "missing_required": missing,
        "missing_optional": warnings,
        "mode": "production" if len(missing) == 0 else "fallback"
    }


def print_config_status():
    """Print startup configuration status (without exposing secrets)"""
    status = validate_config()
    
    print("\n" + "=" * 50)
    print(" SYSTEM MODE:", status["mode"].upper())
    print("=" * 50)
    
    # [SECURITY] DEV_MODE SAFETY CHECK
    dev_mode = os.environ.get("DEV_MODE", "false").lower() == "true"
    env = os.environ.get("ENV", "").lower()
    production_like_envs = {"production", "prod", "live", "staging"}
    
    if dev_mode and env in production_like_envs:
        logger.critical(
            "[CRITICAL] DEV_MODE=true is set in a production-like environment (ENV=%s). "
            "This causes Redis and other backends to use in-memory mocks instead of real connections, "
            "silently bypassing durability guarantees. This is a dangerous misconfiguration.",
            env.upper()
        )
        print(f" [WARNING] DEV_MODE=true detected with ENV={env.upper()}")
        print(f"     This enables in-memory mocks for Redis and other backends.")
        print(f"     Expected ENV values for DEV_MODE: development, testing, dev")
        print(f"     Actual ENV value: {env.upper()}")
    elif dev_mode:
        print(f" DEV_MODE: ON (appropriate for ENV={env.upper()})")
    else:
        print(f" DEV_MODE: OFF")
    
    # Supabase status
    #
    # ENV-03: classified, not truthiness-tested. This read
    # `if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY`, so with the
    # committed placeholders it printed `Supabase: CONNECTED (project: YOUR_PROJECT_REF)` --
    # presenting the placeholder itself as the project reference it had supposedly
    # extracted.
    if secret_is_usable(settings.SUPABASE_URL) and secret_is_usable(
        settings.SUPABASE_SERVICE_ROLE_KEY
    ):
        # Mask the URL - show only project ref
        url_parts = settings.SUPABASE_URL.split("//")
        if len(url_parts) > 1:
            project_ref = url_parts[1].split(".")[0] if "." in url_parts[1] else "***"
        else:
            project_ref = "***"
        print(f" Supabase: CONNECTED (project: {project_ref})")
    else:
        print(" Supabase: NOT CONFIGURED (fallback mode)")
        if status["missing_required"]:
            print(f"   Missing: {', '.join(status['missing_required'])}")
    
    # QuestDB status
    if settings.QUESTDB_HOST:
        print(f" QuestDB: {settings.QUESTDB_HOST}:{settings.QUESTDB_PORT}")
    else:
        print(" QuestDB: NOT CONFIGURED (fallback mode)")
    
    # Environment
    print(f" Environment: {settings.ENV}")
    print(f" Debug Mode: {'ON' if settings.DEBUG else 'OFF'}")

    # ── Which .env file won, and what the credentials actually are (ENV-03) ──────────
    # The resolved path is printed because it is NOT the one an operator expects:
    # `find_dotenv()` walks up from `backend_app/`, so `backend_app/.env` wins and the
    # repository-root `.env` is never read. Editing the wrong file is a silent no-op, and
    # this line is what makes that visible.
    print(f" Dotenv file: {DOTENV_PATH or '(none found)'}")

    # Verdicts, never values. In a deployed environment
    # `assert_credentials_are_production_grade` has already refused the boot by the time
    # anyone reads this; locally it is the documented warning-only behaviour, so the
    # placeholders are reported and the process continues.
    unfit = {
        name: verdict
        for name, verdict in settings.credential_verdicts().items()
        if not verdict.is_usable
    }
    if unfit:
        print(f" Credentials: {len(unfit)} NOT CONFIGURED")
        for name, verdict in sorted(unfit.items()):
            print(f"     {name}: {verdict.value}")
        if not settings._is_deployed_environment():
            # ASCII only. This is boot output on Windows, where stdout can be cp1252 and a
            # non-ASCII character raises UnicodeEncodeError -- which would make the
            # configuration reporter the thing that crashes the boot it reports on. Seen as
            # `???` in staging/probe_CFG.txt before this was changed.
            print("     (local environment - reported, not fatal)")
    else:
        print(" Credentials: all governed variables configured")

    print("=" * 50 + "\n")
    
    return status


# Validate on import but don't crash
config_status = validate_config()
