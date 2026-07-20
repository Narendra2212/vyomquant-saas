"""
Replay Divergence Alarm - Phase A

This module detects and alarms on replay divergence for continuous
institutional-grade operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager
from .validation_alert_manager import ValidationAlertManager

logger = logging.getLogger("replay_divergence_alarm")


class ReplayDivergenceAlarm:
    """
    Replay divergence alarm for continuous validation execution.
    
    Detects and alarms on replay divergence for institutional-grade
    operational validation.
    """
    
    def __init__(self):
        """Initialize replay divergence alarm."""
        self.redis = redis_manager
        self.alert_manager = ValidationAlertManager()
    
    async def check_replay_divergence(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for replay divergence.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Divergence check result
        """
        logger.info("Checking replay divergence...")
        
        divergence_detected = False
        divergence_details = []
        
        try:
            # Check for divergence markers
            pattern = f"divergence_marker:{tenant_id}:*" if tenant_id else "divergence_marker:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                divergence = await self.redis.get(key)
                if divergence:
                    divergence_detected = True
                    divergence_details.append({
                        "key": key,
                        "divergence": divergence
                    })
            
            # Check replay state consistency
            pattern = f"replay_state:{tenant_id}:*" if tenant_id else "replay_state:*:*"
            keys = await self.redis.keys(pattern)
            
            replay_states = {}
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    replay_id = key.split(":")[-1]
                    replay_states[replay_id] = state
            
            # Check for inconsistent replay states
            for replay_id, state in replay_states.items():
                if state != "consistent":
                    divergence_detected = True
                    divergence_details.append({
                        "replay_id": replay_id,
                        "state": state,
                        "issue": "inconsistent_replay_state"
                    })
            
            # Check deterministic replay reconstruction
            pattern = f"replay_sequence:{tenant_id}:*" if tenant_id else "replay_sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            replay_sequences = {}
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    replay_id = key.split(":")[-1]
                    replay_sequences[replay_id] = sequence
            
            # Check for sequence gaps
            for replay_id, sequence in replay_sequences.items():
                expected_sequence = int(sequence)
                if expected_sequence > 1:
                    # Check if all previous sequences exist
                    missing_sequences = []
                    for i in range(1, expected_sequence):
                        seq_key = f"replay_execution:{tenant_id}:{replay_id}:{i}" if tenant_id else f"replay_execution:*:{replay_id}:{i}"
                        exists = await self.redis.exists(seq_key)
                        if not exists:
                            missing_sequences.append(i)
                    
                    if missing_sequences:
                        divergence_detected = True
                        divergence_details.append({
                            "replay_id": replay_id,
                            "expected_sequence": expected_sequence,
                            "missing_sequences": missing_sequences,
                            "issue": "replay_sequence_gap"
                        })
            
            result = {
                "divergence_detected": divergence_detected,
                "divergence_details": divergence_details,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if divergence detected
            if divergence_detected:
                logger.critical(f"Replay divergence detected: {divergence_details}")
                await self.alert_manager.trigger_critical_alert(
                    "replay_divergence",
                    divergence_details
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Replay divergence check failed: {e}")
            return {
                "divergence_detected": False,
                "divergence_details": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_deterministic_replay(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check deterministic replay reconstruction.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Deterministic replay check result
        """
        logger.info("Checking deterministic replay reconstruction...")
        
        issues = []
        
        try:
            # Check replay execution IDs are deterministic
            pattern = f"replay_execution_id:{tenant_id}:*" if tenant_id else "replay_execution_id:*:*"
            keys = await self.redis.keys(pattern)
            
            execution_ids = {}
            for key in keys:
                exec_id = await self.redis.get(key)
                if exec_id:
                    replay_id = key.split(":")[-1]
                    if replay_id in execution_ids:
                        if execution_ids[replay_id] != exec_id:
                            issues.append({
                                "replay_id": replay_id,
                                "expected_id": execution_ids[replay_id],
                                "actual_id": exec_id,
                                "issue": "non_deterministic_execution_id"
                            })
                    else:
                        execution_ids[replay_id] = exec_id
            
            # Check replay state transitions are deterministic
            pattern = f"replay_execution_state:{tenant_id}:*" if tenant_id else "replay_execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            state_transitions = {}
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    replay_id = key.split(":")[-1]
                    state_transitions[replay_id] = state
            
            # Check for non-deterministic state transitions
            for replay_id, state in state_transitions.items():
                if state not in ["pending", "running", "completed", "failed"]:
                    issues.append({
                        "replay_id": replay_id,
                        "state": state,
                        "issue": "invalid_replay_state"
                    })
            
            result = {
                "deterministic": len(issues) == 0,
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if non-deterministic
            if not result["deterministic"]:
                logger.warning(f"Non-deterministic replay detected: {issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "deterministic_replay",
                    issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Deterministic replay check failed: {e}")
            return {
                "deterministic": False,
                "issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def preserve_replay_evidence(self, divergence_details: list) -> bool:
        """
        Preserve replay evidence for divergence.
        
        Args:
            divergence_details: List of divergence details
            
        Returns:
            True if preserved successfully
        """
        logger.critical("Preserving replay evidence for divergence")
        
        try:
            # Store evidence
            evidence_key = f"replay_divergence_evidence:{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            evidence_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "divergence_details": divergence_details
            }
            
            await self.redis.set(evidence_key, str(evidence_data))
            
            logger.critical(f"Replay evidence preserved: {evidence_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to preserve replay evidence: {e}")
            return False


# Global instance
_replay_divergence_alarm: ReplayDivergenceAlarm = None


def get_replay_divergence_alarm() -> ReplayDivergenceAlarm:
    """Get or create replay divergence alarm instance."""
    global _replay_divergence_alarm
    if _replay_divergence_alarm is None:
        _replay_divergence_alarm = ReplayDivergenceAlarm()
    return _replay_divergence_alarm
