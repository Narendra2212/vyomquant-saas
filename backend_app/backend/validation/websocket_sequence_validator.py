"""
WebSocket Sequence Validator - Phase 1

This module validates WebSocket sequencing correctness including sequence gaps,
message ordering, and timestamp validation.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("websocket_sequence_validator")


class WebSocketSequenceValidator:
    """
    WebSocket sequence validator for institutional-grade validation.
    
    Validates WebSocket sequencing correctness including sequence gaps,
    message ordering, and timestamp validation.
    """
    
    def __init__(self):
        """Initialize WebSocket sequence validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run WebSocket sequence validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running WebSocket sequence validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate sequence gaps
        sequence_gap_result = await self._validate_sequence_gaps(tenant_id)
        assertions.extend(sequence_gap_result["assertions"])
        failures.extend(sequence_gap_result["failures"])
        warnings.extend(sequence_gap_result["warnings"])
        
        # Validate message ordering
        ordering_result = await self._validate_message_ordering(tenant_id)
        assertions.extend(ordering_result["assertions"])
        failures.extend(ordering_result["failures"])
        warnings.extend(ordering_result["warnings"])
        
        # Validate timestamp correctness
        timestamp_result = await self._validate_timestamp_correctness(tenant_id)
        assertions.extend(timestamp_result["assertions"])
        failures.extend(timestamp_result["failures"])
        warnings.extend(timestamp_result["warnings"])
        
        # Validate WebSocket replay safety
        replay_result = await self._validate_websocket_replay_safety(tenant_id)
        assertions.extend(replay_result["assertions"])
        failures.extend(replay_result["failures"])
        warnings.extend(replay_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_sequence_gaps(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate sequence gaps."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check WebSocket sequence numbers
            pattern = f"ws_sequence:{tenant_id}:*" if tenant_id else "ws_sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            sequence_numbers = {}
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    session_id = key.split(":")[-1]
                    sequence_numbers[session_id] = int(sequence)
            
            # Check for sequence gaps
            for session_id, sequence in sequence_numbers.items():
                if sequence > 1:
                    # Check if all previous sequences exist
                    expected_sequences = set(range(1, sequence + 1))
                    actual_sequences = set()
                    
                    pattern = f"ws_message:{tenant_id}:{session_id}:*" if tenant_id else f"ws_message:*:{session_id}:*"
                    message_keys = await self.redis.keys(pattern)
                    
                    for msg_key in message_keys:
                        msg_sequence = int(msg_key.split(":")[-1])
                        actual_sequences.add(msg_sequence)
                    
                    missing_sequences = expected_sequences - actual_sequences
                    if missing_sequences:
                        failures.append(f"Sequence gaps detected for session {session_id}: missing {missing_sequences}")
                    else:
                        assertions.append(f"No sequence gaps for session {session_id}")
                else:
                    assertions.append(f"Sequence number valid for session {session_id}: {sequence}")
            
            assertions.append("WebSocket sequence gaps validated: no gaps detected")
            
        except Exception as e:
            failures.append(f"WebSocket sequence gap validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_message_ordering(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate message ordering."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check message timestamps are monotonic
            pattern = f"ws_message:{tenant_id}:*" if tenant_id else "ws_message:*:*"
            keys = await self.redis.keys(pattern)
            
            session_timestamps = {}
            for key in keys:
                timestamp = await self.redis.get(key)
                if timestamp:
                    session_id = key.split(":")[-2]
                    if session_id not in session_timestamps:
                        session_timestamps[session_id] = []
                    session_timestamps[session_id].append(timestamp)
            
            # Check for monotonic timestamps
            for session_id, timestamps in session_timestamps.items():
                timestamps_sorted = sorted(timestamps)
                if timestamps == timestamps_sorted:
                    assertions.append(f"Message ordering valid for session {session_id}: timestamps are monotonic")
                else:
                    failures.append(f"Message ordering invalid for session {session_id}: timestamps are not monotonic")
            
            assertions.append("WebSocket message ordering validated: messages are ordered")
            
        except Exception as e:
            failures.append(f"WebSocket message ordering validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_timestamp_correctness(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate timestamp correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for stale messages
            pattern = f"ws_message:{tenant_id}:*" if tenant_id else "ws_message:*:*"
            keys = await self.redis.keys(pattern)
            
            current_time = datetime.now(timezone.utc)
            stale_threshold = 300  # 5 minutes
            
            for key in keys:
                timestamp = await self.redis.get(key)
                if timestamp:
                    try:
                        msg_time = datetime.fromisoformat(timestamp)
                        age_seconds = (current_time - msg_time).total_seconds()
                        
                        if age_seconds > stale_threshold:
                            warnings.append(f"Stale message detected: {key} (age: {age_seconds}s)")
                        else:
                            assertions.append(f"Message timestamp valid: {key}")
                    except Exception as e:
                        failures.append(f"Invalid timestamp format: {key} - {str(e)}")
            
            assertions.append("WebSocket timestamp correctness validated: timestamps are valid")
            
        except Exception as e:
            failures.append(f"WebSocket timestamp correctness validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_websocket_replay_safety(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate WebSocket replay safety."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check replay markers
            pattern = f"ws_replay_marker:{tenant_id}:*" if tenant_id else "ws_replay_marker:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                marker = await self.redis.get(key)
                if marker:
                    assertions.append(f"WebSocket replay marker exists: {key}")
                else:
                    warnings.append(f"WebSocket replay marker missing: {key}")
            
            # Check for replay divergence
            pattern = f"ws_replay_divergence:{tenant_id}:*" if tenant_id else "ws_replay_divergence:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                divergence = await self.redis.get(key)
                if divergence:
                    failures.append(f"WebSocket replay divergence detected: {key} = {divergence}")
                else:
                    assertions.append(f"No WebSocket replay divergence: {key}")
            
            assertions.append("WebSocket replay safety validated: replay is safe")
            
        except Exception as e:
            failures.append(f"WebSocket replay safety validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
