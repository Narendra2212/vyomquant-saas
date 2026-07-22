"""
Event Signing Layer for Cryptographic Integrity

Provides HMAC-based event signing, hash chain verification,
and cryptographic integrity validation for immutable journal.

Author: Principal Distributed Execution Engineer
"""

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .immutable_journal import ExecutionEvent

logger = logging.getLogger("event_signing")


@dataclass
class SigningKey:
    """Cryptographic signing key with version management."""
    key_id: str
    key_data: bytes
    version: int
    created_at: datetime
    expires_at: datetime
    is_active: bool = True


@dataclass
class SigningResult:
    """Result of event signing operation."""
    success: bool
    event_hash: str
    previous_hash: str
    signature: str
    signing_time: datetime
    error: Optional[str] = None


@dataclass
class VerificationResult:
    """Result of event verification operation."""
    success: bool
    is_valid: bool
    error: Optional[str] = None
    verification_time: datetime = None
    details: Dict[str, Any] = None


class EventSigner:
    """Cryptographic event signer with hash chain verification."""
    
    def __init__(self):
        self.current_key: Optional[SigningKey] = None
        self.key_history: List[SigningKey] = []
        
        # Signing configuration
        self.algorithm = "sha256"
        self.key_rotation_interval = 86400 * 30  # 30 days
        self.signature_version = 1
        
        # In-memory cache for performance
        self.hash_cache: Dict[str, str] = {}
        self.signature_cache: Dict[str, str] = {}
        
        logger.info("Event signer initialized")
    
    async def initialize(self) -> bool:
        """Initialize event signer with key management."""
        try:
            # Generate initial signing key
            await self._generate_signing_key()
            
            logger.info("Event signer initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize event signer: {e}")
            return False
    
    async def sign_event(self, event: ExecutionEvent, 
                        previous_event_hash: str = "0" * 64) -> SigningResult:
        """Sign event with cryptographic integrity verification."""
        try:
            time.time()
            
            # Generate event hash
            event_hash = await self._generate_event_hash(event)
            
            # Generate HMAC signature
            signature_input = f"{event_hash}:{""}:{datetime.now(timezone.utc).isoformat()}"
            signature = hmac.new(
                self.current_key.key_data,
                signature_input.encode('utf-8'),
                getattr(hashlib, self.algorithm)
            ).hexdigest()
            
            signing_time = datetime.now(timezone.utc)
            
            # Cache results for performance
            self.hash_cache[event.header.event_id] = event_hash
            self.signature_cache[event.header.event_id] = signature
            
            logger.debug(f"Signed event {event.header.event_id}")
            
            return SigningResult(
                success=True,
                event_hash=event_hash,
                previous_hash=previous_event_hash,
                signature=signature,
                signing_time=signing_time
            )
            
        except Exception as e:
            logger.error(f"Failed to sign event {event.header.event_id}: {e}")
            return SigningResult(
                success=False,
                event_hash="",
                previous_hash="",
                signature="",
                signing_time=datetime.now(timezone.utc),
                error=str(e)
            )
    
    async def verify_event(self, event: ExecutionEvent,
                        expected_previous_hash: str = None) -> VerificationResult:
        """Verify event integrity with cryptographic checks."""
        try:
            verification_start = time.time()
            
            # Check event hash
            expected_hash = await self._generate_event_hash(event)
            if event.signature.event_hash != expected_hash:
                return VerificationResult(
                    success=False,
                    is_valid=False,
                    error="Event hash mismatch",
                    verification_time=datetime.now(timezone.utc),
                    details={
                        "expected_hash": expected_hash,
                        "actual_hash": event.signature.event_hash
                    }
                )
            
            # Check previous hash chain
            if expected_previous_hash is not None:
                if event.signature.previous_event_hash != expected_previous_hash:
                    return VerificationResult(
                        success=False,
                        is_valid=False,
                        error="Previous hash chain mismatch",
                        verification_time=datetime.now(timezone.utc),
                        details={
                            "expected_previous_hash": expected_previous_hash,
                            "actual_previous_hash": event.signature.previous_event_hash
                        }
                    )
            
            # Verify HMAC signature
            signature_input = f"{event.signature.event_hash}:{event.signature.previous_event_hash}:{event.signature.signature_timestamp.isoformat()}"
            expected_signature = hmac.new(
                self.current_key.key_data,
                signature_input.encode('utf-8'),
                getattr(hashlib, self.algorithm)
            ).hexdigest()
            
            if event.signature.signature != expected_signature:
                return VerificationResult(
                    success=False,
                    is_valid=False,
                    error="HMAC signature verification failed",
                    verification_time=datetime.now(timezone.utc),
                    details={
                        "expected_signature": expected_signature,
                        "actual_signature": event.signature.signature
                    }
                )
            
            # Check signature timestamp (prevent replay attacks)
            now = datetime.now(timezone.utc)
            signature_age = (now - event.signature.signature_timestamp).total_seconds()
            
            if signature_age > 3600:  # 1 hour
                return VerificationResult(
                    success=False,
                    is_valid=False,
                    error="Signature timestamp too old",
                    verification_time=datetime.now(timezone.utc),
                    details={
                        "signature_age_seconds": signature_age,
                        "max_age_seconds": 3600
                    }
                )
            
            verification_time = datetime.now(timezone.utc)
            
            logger.debug(f"Verified event {event.header.event_id}")
            
            return VerificationResult(
                success=True,
                is_valid=True,
                verification_time=verification_time,
                details={
                    "verification_duration_ms": (verification_time - verification_start) * 1000
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to verify event {event.header.event_id}: {e}")
            return VerificationResult(
                success=False,
                is_valid=False,
                error=str(e),
                verification_time=datetime.now(timezone.utc)
            )
    
    async def verify_hash_chain(self, events: List[ExecutionEvent]) -> VerificationResult:
        """Verify complete hash chain for multiple events."""
        try:
            if not events:
                return VerificationResult(
                    success=True,
                    is_valid=True,
                    verification_time=datetime.now(timezone.utc),
                    details={"message": "No events to verify"}
                )
            
            # Sort events by sequence
            sorted_events = sorted(events, key=lambda e: e.header.sequence_id)
            
            # Verify hash chain
            for i, event in enumerate(sorted_events):
                expected_previous_hash = "0" * 64 if i == 0 else sorted_events[i-1].signature.event_hash
                
                verification = await self.verify_event(event, expected_previous_hash)
                if not verification.is_valid:
                    return VerificationResult(
                        success=False,
                        is_valid=False,
                        error=f"Hash chain broken at event {event.header.event_id}",
                        verification_time=datetime.now(timezone.utc),
                        details={
                            "broken_at_sequence": event.header.sequence_id,
                            "broken_at_event_id": event.header.event_id,
                            "verification_error": verification.error
                        }
                    )
            
            return VerificationResult(
                success=True,
                is_valid=True,
                verification_time=datetime.now(timezone.utc),
                details={
                    "verified_events": len(sorted_events),
                    "hash_chain_valid": True
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to verify hash chain: {e}")
            return VerificationResult(
                success=False,
                is_valid=False,
                error=str(e),
                verification_time=datetime.now(timezone.utc)
            )
    
    async def rotate_signing_key(self) -> bool:
        """Rotate signing key for security."""
        try:
            # Generate new key
            new_key = await self._generate_signing_key()
            
            # Deactivate old key
            if self.current_key:
                self.current_key.is_active = False
                self.key_history.append(self.current_key)
            
            # Set new key as current
            self.current_key = new_key
            
            logger.info(f"Rotated signing key to {new_key.key_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to rotate signing key: {e}")
            return False
    
    async def get_signing_key_info(self) -> Dict[str, Any]:
        """Get current signing key information."""
        try:
            if not self.current_key:
                return {"error": "No signing key available"}
            
            return {
                "key_id": self.current_key.key_id,
                "version": self.current_key.version,
                "created_at": self.current_key.created_at.isoformat(),
                "expires_at": self.current_key.expires_at.isoformat(),
                "is_active": self.current_key.is_active,
                "algorithm": self.algorithm,
                "signature_version": self.signature_version,
                "key_history_count": len(self.key_history)
            }
            
        except Exception as e:
            logger.error(f"Failed to get signing key info: {e}")
            return {"error": str(e)}
    
    async def _generate_signing_key(self) -> SigningKey:
        """Generate new signing key for event signing."""
        try:
            key_id = str(uuid.uuid4())
            key_data = f"signing_key_{uuid.uuid4().hex[:16]}".encode('utf-8')
            
            new_key = SigningKey(
                key_id=key_id,
                key_data=key_data,
                version=self.signature_version,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc).timestamp() + self.key_rotation_interval,
                is_active=True
            )
            
            logger.info(f"Generated new signing key {key_id}")
            return new_key
            
        except Exception as e:
            logger.error(f"Failed to generate signing key: {e}")
            raise
    
    async def _generate_event_hash(self, event: ExecutionEvent) -> str:
        """Generate SHA-256 hash of event content."""
        try:
            # Create canonical representation
            canonical_data = {
                "header": asdict(event.header),
                "payload": event.payload,
                "metadata": asdict(event.metadata)
            }
            
            # Generate hash
            hash_input = json.dumps(canonical_data, sort_keys=True, separators=(',', ':'))
            event_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
            
            return event_hash
            
        except Exception as e:
            logger.error(f"Failed to generate event hash: {e}")
            raise
    
    async def verify_signature_timestamp(self, event: ExecutionEvent) -> bool:
        """Verify signature timestamp is within acceptable range."""
        try:
            now = datetime.now(timezone.utc)
            signature_age = (now - event.signature.signature_timestamp).total_seconds()
            
            # Check if signature is too old (replay attack prevention)
            if signature_age > 3600:  # 1 hour
                return False
            
            # Check if signature is from future (clock skew)
            if signature_age < -300:  # 5 minutes
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to verify signature timestamp: {e}")
            return False
    
    async def get_verification_statistics(self) -> Dict[str, Any]:
        """Get verification statistics for monitoring."""
        try:
            return {
                "cache_size": {
                    "hash_cache": len(self.hash_cache),
                    "signature_cache": len(self.signature_cache)
                },
                "current_key_info": await self.get_signing_key_info(),
                "algorithm": self.algorithm,
                "signature_version": self.signature_version,
                "key_rotation_interval_hours": self.key_rotation_interval / 3600,
                "key_history_count": len(self.key_history)
            }
            
        except Exception as e:
            logger.error(f"Failed to get verification statistics: {e}")
            return {"error": str(e)}


# Global event signer instance
event_signer = EventSigner()
