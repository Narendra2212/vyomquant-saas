"""
WebSocket Gap Alarm - Phase A

This module detects and alarms on WebSocket sequence gaps for continuous
institutional-grade operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .validation_alert_manager import ValidationAlertManager

logger = logging.getLogger("websocket_gap_alarm")


class WebSocketGapAlarm:
    """
    WebSocket gap alarm for continuous validation execution.
    
    Detects and alarms on WebSocket sequence gaps for institutional-grade
    operational validation.
    """
    
    def __init__(self):
        """Initialize WebSocket gap alarm."""
        self.redis = redis_manager
        self.alert_manager = ValidationAlertManager()
    
    async def check_sequence_gaps(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for WebSocket sequence gaps.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Sequence gap check result
        """
        logger.info("Checking WebSocket sequence gaps...")
        
        gaps_detected = False
        gap_details = []
        
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
                        gaps_detected = True
                        gap_details.append({
                            "session_id": session_id,
                            "expected_sequence": sequence,
                            "missing_sequences": sorted(missing_sequences),
                            "issue": "websocket_sequence_gap"
                        })
            
            result = {
                "gaps_detected": gaps_detected,
                "gap_details": gap_details,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if gaps detected
            if gaps_detected:
                logger.warning(f"WebSocket sequence gaps detected: {gap_details}")
                await self.alert_manager.trigger_high_priority_alert(
                    "websocket_sequence_gap",
                    gap_details
                )
            
            return result
            
        except Exception as e:
            logger.error(f"WebSocket sequence gap check failed: {e}")
            return {
                "gaps_detected": False,
                "gap_details": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_message_ordering(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check WebSocket message ordering.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Message ordering check result
        """
        logger.info("Checking WebSocket message ordering...")
        
        ordering_issues = []
        
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
                if timestamps != timestamps_sorted:
                    ordering_issues.append({
                        "session_id": session_id,
                        "issue": "non_monotonic_timestamps",
                        "expected": timestamps_sorted,
                        "actual": timestamps
                    })
            
            result = {
                "ordering_correct": len(ordering_issues) == 0,
                "ordering_issues": ordering_issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if ordering issues detected
            if not result["ordering_correct"]:
                logger.warning(f"WebSocket message ordering issues detected: {ordering_issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "websocket_message_ordering",
                    ordering_issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"WebSocket message ordering check failed: {e}")
            return {
                "ordering_correct": False,
                "ordering_issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_timestamp_correctness(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check WebSocket timestamp correctness.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Timestamp correctness check result
        """
        logger.info("Checking WebSocket timestamp correctness...")
        
        timestamp_issues = []
        
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
                            timestamp_issues.append({
                                "key": key,
                                "age_seconds": age_seconds,
                                "issue": "stale_message"
                            })
                    except Exception as e:
                        timestamp_issues.append({
                            "key": key,
                            "error": str(e),
                            "issue": "invalid_timestamp_format"
                        })
            
            result = {
                "timestamps_correct": len(timestamp_issues) == 0,
                "timestamp_issues": timestamp_issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if timestamp issues detected
            if not result["timestamps_correct"]:
                logger.warning(f"WebSocket timestamp issues detected: {timestamp_issues}")
                await self.alert_manager.trigger_normal_alert(
                    "websocket_timestamp",
                    [f"{issue['key']}: {issue['issue']}" for issue in timestamp_issues]
                )
            
            return result
            
        except Exception as e:
            logger.error(f"WebSocket timestamp correctness check failed: {e}")
            return {
                "timestamps_correct": False,
                "timestamp_issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_websocket_replay_safety(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check WebSocket replay safety.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Replay safety check result
        """
        logger.info("Checking WebSocket replay safety...")
        
        replay_issues = []
        
        try:
            # Check replay markers
            pattern = f"ws_replay_marker:{tenant_id}:*" if tenant_id else "ws_replay_marker:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                marker = await self.redis.get(key)
                if not marker:
                    replay_issues.append({
                        "key": key,
                        "issue": "replay_marker_missing"
                    })
            
            # Check for replay divergence
            pattern = f"ws_replay_divergence:{tenant_id}:*" if tenant_id else "ws_replay_divergence:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                divergence = await self.redis.get(key)
                if divergence:
                    replay_issues.append({
                        "key": key,
                        "divergence": divergence,
                        "issue": "websocket_replay_divergence"
                    })
            
            result = {
                "replay_safe": len(replay_issues) == 0,
                "replay_issues": replay_issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if replay issues detected
            if not result["replay_safe"]:
                logger.warning(f"WebSocket replay issues detected: {replay_issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "websocket_replay_safety",
                    replay_issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"WebSocket replay safety check failed: {e}")
            return {
                "replay_safe": False,
                "replay_issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global instance
_websocket_gap_alarm: WebSocketGapAlarm = None


def get_websocket_gap_alarm() -> WebSocketGapAlarm:
    """Get or create WebSocket gap alarm instance."""
    global _websocket_gap_alarm
    if _websocket_gap_alarm is None:
        _websocket_gap_alarm = WebSocketGapAlarm()
    return _websocket_gap_alarm
