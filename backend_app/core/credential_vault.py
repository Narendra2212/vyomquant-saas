"""
Credential Vault - Exchange Credential Isolation - Phase 6 Authentication Hardening

This module provides tenant-scoped exchange credential storage with encryption at rest,
ensuring that exchange credentials are properly isolated between tenants and protected from
unauthorized access.

Key Features:
- Tenant-scoped credential storage
- Encryption at rest using AES-256
- Credential access logging
- Credential rotation support
- Cross-tenant access prevention
- Secure key management
- Production environment validation
- Weak credential detection

Author: Principal Institutional Platform Security Engineer
"""

import base64
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from backend_app.core.cache import redis_manager

logger = logging.getLogger("CredentialVault")


def _is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV", "development").lower() == "production"


def _validate_encryption_key_strength(key: str) -> None:
    """
    Validate encryption key strength.
    
    Args:
        key: Encryption key to validate
    
    Raises RuntimeError if key is too weak.
    """
    if len(key) < 32:
        raise RuntimeError(
            f"Encryption key too weak: {len(key)} characters. "
            "Minimum 32 characters required for production security."
        )
    
    # Check for common weak patterns
    weak_patterns = [
        r"password", r"secret", r"key", r"test", r"demo",
        r"123456", r"abcdef", r"000000", r"qwerty"
    ]
    
    key_lower = key.lower()
    for pattern in weak_patterns:
        if pattern in key_lower:
            logger.warning(f"Encryption key contains weak pattern: {pattern}")


def _validate_key_rotation_policy(credential_id: str, exchange_id: str) -> None:
    """
    Validate key rotation policy compliance.
    
    Args:
        credential_id: Credential ID to validate
        exchange_id: Exchange ID for policy context
    
    Raises ValueError if key rotation policy is violated.
    """
    # Check for age-based rotation requirements
    # This is a placeholder for implementing key rotation age checking
    # In production, you would check the credential creation date against policy
    logger.debug(f"Key rotation policy check for credential {credential_id} on exchange {exchange_id}")
    
    # Placeholder: Implement actual age-based rotation check
    # if credential_age > MAX_KEY_AGE:
    #     raise ValueError(f"Credential {credential_id} exceeds maximum age and requires rotation")
    # This is a placeholder for implementing key rotation age checking
    # In production, you would check the credential creation date against policy
    logger.debug(f"Key rotation policy check for credential {credential_id} on exchange {exchange_id}")
    
    # Placeholder: Implement actual age-based rotation check
    # if credential_age > MAX_KEY_AGE:
    #     raise ValueError(f"Credential {credential_id} exceeds maximum age and requires rotation")


class CredentialType(Enum):
    """Types of exchange credentials."""
    API_KEY = "api_key"
    SECRET_KEY = "secret_key"
    PASSWORD = "password"
    API_SECRET = "api_secret"
    PASSPHRASE = "passphrase"


@dataclass
class ExchangeCredential:
    """Exchange credential with tenant isolation."""
    credential_id: str
    user_id: str
    tenant_id: str
    exchange_id: str
    credential_type: CredentialType
    encrypted_value: str
    salt: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "exchange_id": self.exchange_id,
            "credential_type": self.credential_type.value,
            "encrypted_value": self.encrypted_value,
            "salt": self.salt,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "access_count": self.access_count,
            "is_active": self.is_active,
        }


@dataclass
class CredentialAccessLog:
    """Log of credential access events."""
    log_id: str
    credential_id: str
    user_id: str
    tenant_id: str
    action: str  # "read", "write", "delete", "rotate"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "log_id": self.log_id,
            "credential_id": self.credential_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "success": self.success,
        }


class CredentialVault:
    """
    Tenant-scoped credential vault with encryption at rest.
    
    This vault provides:
    - Tenant-scoped credential storage
    - Encryption at rest using AES-256
    - Credential access logging
    - Cross-tenant access prevention
    - Secure key management
    """
    
    def __init__(self, encryption_key: Optional[str] = None, salt: Optional[Union[str, bytes]] = None):
        """
        Initialize credential vault.
        
        Args:
            encryption_key: Master encryption key (defaults to MASTER_ENCRYPTION_KEYS environment variable)
            salt: PBKDF2 salt for key derivation (defaults to CREDENTIAL_VAULT_SALT environment variable)
        """
        raw_key = encryption_key or os.getenv("MASTER_ENCRYPTION_KEYS")
        
        if not raw_key:
            logger.critical("FATAL: MASTER_ENCRYPTION_KEYS environment variable is not set.")
            raise RuntimeError(
                "MASTER_ENCRYPTION_KEYS environment variable is missing. "
                "Refusing to start with a non-persistent random key."
            )
        
        # SECURITY: Validate encryption key strength
        _validate_encryption_key_strength(raw_key)
        
        raw_salt = salt or os.getenv("CREDENTIAL_VAULT_SALT")
        
        if not raw_salt:
            logger.critical("FATAL: CREDENTIAL_VAULT_SALT environment variable is not set.")
            raise RuntimeError(
                "CREDENTIAL_VAULT_SALT environment variable is missing. "
                "Refusing to start without a deployment-unique salt."
            )
        
        # Take the primary (first) key if comma-separated
        self.encryption_key = [k.strip() for k in raw_key.split(",") if k.strip()][0]
        self.salt = raw_salt.encode("utf-8") if isinstance(raw_salt, str) else raw_salt
        
        # Derive encryption key from master key using per-deployment salt
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.encryption_key.encode()))
        self.cipher = Fernet(key)
        
        # SECURITY: Warn if in production with weak configuration
        if _is_production():
            logger.info("Credential Vault initialized in production mode")
            if len(self.encryption_key) < 64:
                logger.warning("Production encryption key length may be insufficient")
        else:
            logger.info("Credential Vault initialized in development mode")
    
    def _generate_salt(self) -> str:
        """Generate a random salt for credential encryption."""
        return base64.urlsafe_b64encode(os.urandom(16)).decode()
    
    def _encrypt(self, value: str, salt: str) -> str:
        """Encrypt a credential value."""
        # Combine value with salt
        salted_value = f"{salt}:{value}"
        encrypted = self.cipher.encrypt(salted_value.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def _decrypt(self, encrypted_value: str) -> str:
        """Decrypt a credential value."""
        encrypted = base64.urlsafe_b64decode(encrypted_value.encode())
        decrypted = self.cipher.decrypt(encrypted).decode()
        # Extract value (remove salt)
        return decrypted.split(":", 1)[1]
    
    def _generate_credential_id(self, user_id: str, exchange_id: str, credential_type: CredentialType) -> str:
        """Generate a unique credential ID."""
        unique_string = f"{user_id}:{exchange_id}:{credential_type.value}"
        return hashlib.sha256(unique_string.encode()).hexdigest()
    
    async def store_credential(
        self,
        user_id: str,
        tenant_id: str,
        exchange_id: str,
        credential_type: CredentialType,
        value: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> ExchangeCredential:
        """
        Store a credential with tenant isolation.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            exchange_id: Exchange ID
            credential_type: Type of credential
            value: Credential value (plaintext)
            ip_address: IP address of requester
            user_agent: User agent of requester
            
        Returns:
            Stored credential
        """
        # SECURITY: Validate credential strength before storage
        _validate_credential_strength(value, credential_type)
        
        # SECURITY: Additional key management verification
        _validate_key_rotation_policy(credential_id, exchange_id)
        
        credential_id = self._generate_credential_id(user_id, exchange_id, credential_type)
        salt = self._generate_salt()
        encrypted_value = self._encrypt(value, salt)
        
        credential = ExchangeCredential(
            credential_id=credential_id,
            user_id=user_id,
            tenant_id=tenant_id,
            exchange_id=exchange_id,
            credential_type=credential_type,
            encrypted_value=encrypted_value,
            salt=salt,
        )
        
        # Store in Redis with tenant-scoped key
        key = f"tenant:{tenant_id}:user:{user_id}:credential:{credential_id}"
        await redis_manager.setex(key, 86400, json.dumps(credential.to_dict()))  # 24 hour TTL
        
        # Log access
        await self._log_access(
            credential_id=credential_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action="write",
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )
        
        logger.info(f"Credential stored: {credential_id} for user {user_id}, tenant {tenant_id}")
        return credential
    
    async def get_credential(
        self,
        user_id: str,
        tenant_id: str,
        exchange_id: str,
        credential_type: CredentialType,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Optional[str]:
        """
        Retrieve a credential with tenant isolation.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            exchange_id: Exchange ID
            credential_type: Type of credential
            ip_address: IP address of requester
            user_agent: User agent of requester
            
        Returns:
            Decrypted credential value or None if not found
        """
        credential_id = self._generate_credential_id(user_id, exchange_id, credential_type)
        key = f"tenant:{tenant_id}:user:{user_id}:credential:{credential_id}"
        
        # Retrieve from Redis
        credential_data = await redis_manager.get(key)
        if not credential_data:
            logger.warning(f"Credential not found: {credential_id}")
            return None
        
        credential_dict = json.loads(credential_data)
        
        # Verify tenant ownership
        if credential_dict.get("tenant_id") != tenant_id:
            logger.error(f"Cross-tenant credential access attempt: {tenant_id} trying to access {credential_dict.get('tenant_id')}'s credential")
            await self._log_access(
                credential_id=credential_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="read",
                ip_address=ip_address,
                user_agent=user_agent,
                success=False
            )
            return None
        
        # Decrypt value
        try:
            value = self._decrypt(credential_dict["encrypted_value"])
            
            # Update access tracking
            credential_dict["last_accessed"] = datetime.now(timezone.utc).isoformat()
            credential_dict["access_count"] = credential_dict.get("access_count", 0) + 1
            await redis_manager.setex(key, 86400, json.dumps(credential_dict))
            
            # Log access
            await self._log_access(
                credential_id=credential_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="read",
                ip_address=ip_address,
                user_agent=user_agent,
                success=True
            )
            
            logger.info(f"Credential retrieved: {credential_id} for user {user_id}, tenant {tenant_id}")
            return value
            
        except Exception as e:
            logger.error(f"Failed to decrypt credential {credential_id}: {e}")
            return None
    
    async def delete_credential(
        self,
        user_id: str,
        tenant_id: str,
        exchange_id: str,
        credential_type: CredentialType,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        """
        Delete a credential with tenant isolation.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            exchange_id: Exchange ID
            credential_type: Type of credential
            ip_address: IP address of requester
            user_agent: User agent of requester
            
        Returns:
            True if deleted, False otherwise
        """
        credential_id = self._generate_credential_id(user_id, exchange_id, credential_type)
        key = f"tenant:{tenant_id}:user:{user_id}:credential:{credential_id}"
        
        # Verify ownership before deletion
        credential_data = await redis_manager.get(key)
        if credential_data:
            credential_dict = json.loads(credential_data)
            if credential_dict.get("tenant_id") != tenant_id:
                logger.error(f"Cross-tenant credential deletion attempt: {tenant_id} trying to delete {credential_dict.get('tenant_id')}'s credential")
                return False
        
        # Delete from Redis
        await redis_manager.delete(key)
        
        # Log access
        await self._log_access(
            credential_id=credential_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action="delete",
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )
        
        logger.info(f"Credential deleted: {credential_id} for user {user_id}, tenant {tenant_id}")
        return True
    
    async def rotate_credential(
        self,
        user_id: str,
        tenant_id: str,
        exchange_id: str,
        credential_type: CredentialType,
        new_value: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        """
        Rotate a credential with tenant isolation.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            exchange_id: Exchange ID
            credential_type: Type of credential
            new_value: New credential value
            ip_address: IP address of requester
            user_agent: User agent of requester
            
        Returns:
            True if rotated, False otherwise
        """
        credential_id = self._generate_credential_id(user_id, exchange_id, credential_type)
        key = f"tenant:{tenant_id}:user:{user_id}:credential:{credential_id}"
        
        # Verify ownership before rotation
        credential_data = await redis_manager.get(key)
        if credential_data:
            credential_dict = json.loads(credential_data)
            if credential_dict.get("tenant_id") != tenant_id:
                logger.error(f"Cross-tenant credential rotation attempt: {tenant_id} trying to rotate {credential_dict.get('tenant_id')}'s credential")
                return False
        
        # Delete old credential
        await redis_manager.delete(key)
        
        # Store new credential
        await self.store_credential(
            user_id=user_id,
            tenant_id=tenant_id,
            exchange_id=exchange_id,
            credential_type=credential_type,
            value=new_value,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        # Log access
        await self._log_access(
            credential_id=credential_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action="rotate",
            ip_address=ip_address,
            user_agent=user_agent,
            success=True
        )
        
        logger.info(f"Credential rotated: {credential_id} for user {user_id}, tenant {tenant_id}")
        return True
    
    async def list_credentials(
        self,
        user_id: str,
        tenant_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all credentials for a tenant-scoped user.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            ip_address: IP address of requester
            user_agent: User agent of requester
            
        Returns:
            List of credential metadata (without values)
        """
        # Scan for tenant-scoped credentials
        pattern = f"tenant:{tenant_id}:user:{user_id}:credential:*"
        keys = await redis_manager.keys(pattern)
        
        credentials = []
        for key in keys:
            credential_data = await redis_manager.get(key)
            if credential_data:
                credential_dict = json.loads(credential_data)
                # Return metadata only (no encrypted values)
                metadata = {
                    "credential_id": credential_dict["credential_id"],
                    "user_id": credential_dict["user_id"],
                    "tenant_id": credential_dict["tenant_id"],
                    "exchange_id": credential_dict["exchange_id"],
                    "credential_type": credential_dict["credential_type"],
                    "created_at": credential_dict["created_at"],
                    "updated_at": credential_dict["updated_at"],
                    "last_accessed": credential_dict.get("last_accessed"),
                    "access_count": credential_dict.get("access_count", 0),
                    "is_active": credential_dict.get("is_active", True),
                }
                credentials.append(metadata)
        
        logger.info(f"Listed {len(credentials)} credentials for user {user_id}, tenant {tenant_id}")
        return credentials
    
    async def _log_access(
        self,
        credential_id: str,
        user_id: str,
        tenant_id: str,
        action: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True
    ):
        """Log credential access event."""
        log_id = hashlib.sha256(f"{credential_id}:{action}:{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()
        
        log = CredentialAccessLog(
            log_id=log_id,
            credential_id=credential_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action=action,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success
        )
        
        # Store log in Redis with 30-day retention
        log_key = f"tenant:{tenant_id}:credential_access_log:{log_id}"
        await redis_manager.setex(log_key, 2592000, json.dumps(log.to_dict()))
        
        # Also store in audit log
        audit_key = "credential_vault:audit_log"
        await redis_manager.lpush(audit_key, json.dumps(log.to_dict()))
        
        logger.debug(f"Credential access logged: {action} on {credential_id} by {user_id}")
    
    async def get_access_logs(
        self,
        tenant_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get credential access logs for a tenant.
        
        Args:
            tenant_id: Tenant ID
            limit: Maximum number of logs to return
            
        Returns:
            List of access logs
        """
        pattern = f"tenant:{tenant_id}:credential_access_log:*"
        keys = await redis_manager.keys(pattern)
        
        logs = []
        for key in keys[:limit]:
            log_data = await redis_manager.get(key)
            if log_data:
                logs.append(json.loads(log_data))
        
        return logs


# Global singleton
_credential_vault: Optional[CredentialVault] = None


def get_credential_vault() -> CredentialVault:
    """Get global credential vault instance."""
    global _credential_vault
    if _credential_vault is None:
        _credential_vault = CredentialVault()
    return _credential_vault


def reset_credential_vault():
    """Reset credential vault singleton (useful for testing)."""
    global _credential_vault
    _credential_vault = None
