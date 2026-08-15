"""
backend/strategy_service.py — Strategy Service

Central operational command center for all Strategy operations.

Replaces Bot Monitor infrastructure with Strategy-centric architecture.
A deployed Strategy IS the running trading bot.

PHASE 2: Transform Strategies into operational command center
"""

import inspect
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from backend_app.core.dependencies import create_request_supabase_async, get_telemetry

logger = logging.getLogger("StrategyService")


class StrategyStatus(Enum):
    """Strategy lifecycle status."""
    DRAFT = "draft"
    BACKTESTING = "backtesting"
    VALIDATED = "validated"
    READY = "ready"
    DEPLOYING = "deploying"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ARCHIVED = "archived"


class StrategyEnvironment(Enum):
    """Strategy deployment environment."""
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class StrategyHealth(Enum):
    """Strategy health status."""
    HEALTHY = "healthy"
    WARNING = "warning"
    OFFLINE = "offline"
    ERROR = "error"


class StrategyService:
    """
    Central service for all Strategy operations.
    
    Manages complete Strategy lifecycle:
    - Create, Edit, Clone, Version
    - Backtest, Validate, Deploy
    - Pause, Resume, Stop, Delete
    - Publish, Subscribe, Monitor
    - Logs, Executions, Signals, Orders, Metrics, Risk
    """
    
    def __init__(self):
        self._telemetry = None
    
    def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = get_telemetry()
        return self._telemetry
    
    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res
    
    async def create_strategy(
        self,
        user: dict,
        name: str,
        description: str,
        blueprint: dict,
        execution_graph: Optional[dict] = None,
        exchange: str = None,
        symbol: str = None,
        timeframe: str = None,
        tags: List[str] = None
    ) -> Dict:
        """
        Create a new Strategy.
        
        Returns:
            Strategy record with initial version
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        strategy_id = str(uuid4())
        version_id = str(uuid4())
        
        # Create strategy with version
        strategy_data = {
            "id": strategy_id,
            "user_id": user["id"],
            "name": name,
            "description": description,
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "tags": tags or [],
            "status": StrategyStatus.DRAFT.value,
            "environment": StrategyEnvironment.PAPER.value,
            "current_version": "v1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Create initial version
        version_data = {
            "id": version_id,
            "strategy_id": strategy_id,
            "version": "v1.0",
            "blueprint": blueprint,
            "execution_graph": execution_graph,  # Store compiled execution graph
            "is_draft": True,
            "is_current": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Store strategy
        q1 = sb.table("strategies").insert(strategy_data).execute()
        strategy_result = await q1 if inspect.isawaitable(q1) else q1
        
        # Store version
        q2 = sb.table("strategy_versions").insert(version_data).execute()
        version_result = await q2 if inspect.isawaitable(q2) else q2
        
        logger.info(f"Created strategy {strategy_id} v1.0 for user {user['id']}")
        
        return {
            "strategy": strategy_result.data[0] if strategy_result and strategy_result.data else strategy_data,
            "version": version_result.data[0] if version_result and version_result.data else version_data
        }
    
    async def get_strategy(self, user: dict, strategy_id: str) -> Optional[Dict]:
        """
        Get complete Strategy data with current version.
        
        Returns:
            Strategy with version, deployments, and metrics
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Get strategy
        q1 = sb.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user["id"]).execute()
        strategy_res = await q1 if inspect.isawaitable(q1) else q1
        if not strategy_res or not strategy_res.data:
            return None
        
        strategy = strategy_res.data[0]
        
        # Get current version
        q2 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        version_res = await q2 if inspect.isawaitable(q2) else q2
        version = version_res.data[0] if version_res and version_res.data else None
        
        # Get active deployments
        q3 = (sb.table("strategy_deployments")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .execute())
        deployments_res = await q3 if inspect.isawaitable(q3) else q3
        deployments = deployments_res.data if deployments_res else []
        
        # Get performance metrics
        performance = await self._get_strategy_performance(user, strategy_id)
        
        return {
            "strategy": strategy,
            "version": version,
            "deployments": deployments,
            "performance": performance
        }
    
    async def list_strategies(
        self,
        user: dict,
        status_filter: Optional[str] = None,
        environment_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        List all Strategies for user with optional filters.
        
        Returns:
            List of strategies with summary metrics
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        query = sb.table("strategies").select("*").eq("user_id", user["id"])
        
        if status_filter:
            query = query.eq("status", status_filter)
        if environment_filter:
            query = query.eq("environment", environment_filter)
        
        q1 = query.order("updated_at", desc=True).execute()
        result = await q1 if inspect.isawaitable(q1) else q1
        
        strategies = result.data or [] if result else []
        
        # Enrich with deployment and performance metrics
        enriched = []
        for strategy in strategies:
            # Get current deployment status
            q2 = (sb.table("strategy_deployments")
                               .select("*")
                               .eq("strategy_id", strategy["id"])
                               .eq("status", "running")
                               .execute())
            deployments_res = await q2 if inspect.isawaitable(q2) else q2
            
            is_running = len(deployments_res.data or []) > 0 if deployments_res else False
            
            # Get performance metrics from Telemetry
            try:
                performance = await self._get_strategy_performance(user, strategy["id"])
            except Exception as e:
                logger.warning(f"Failed to fetch performance for strategy {strategy['id']}: {e}")
                performance = None  # Explicitly indicate performance unavailable
            
            enriched.append({
                **strategy,
                "is_running": is_running,
                "deployment_count": len(deployments_res.data or []) if deployments_res else 0,
                "performance": performance
            })
        
        return enriched
    
    async def _get_strategy_performance(self, user: dict, strategy_id: str) -> Dict:
        """
        Get Strategy performance metrics from MetricsService.

        Returns:
            Performance metrics (PnL, ROI, win rate, etc.)

        Raises:
            Exception if performance fetch fails - caller should handle gracefully
        """
        from backend_app.backend.metrics_service import get_metrics_service
        metrics_service = await get_metrics_service()

        return await metrics_service.get_strategy_performance(user["id"], strategy_id)
    
    async def update_strategy(
        self,
        user: dict,
        strategy_id: str,
        updates: Dict
    ) -> Dict:
        """
        Update Strategy (creates new version if blueprint changes).
        
        Returns:
            Updated strategy with new version if applicable
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Check if blueprint is being updated
        blueprint_changed = "blueprint" in updates
        
        # Update strategy metadata
        strategy_updates = {
            **updates,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if "blueprint" in strategy_updates:
            del strategy_updates["blueprint"]  # Handle separately
        
        q1 = sb.table("strategies").update(strategy_updates).eq("id", strategy_id).eq("user_id", user["id"]).execute()
        result = await q1 if inspect.isawaitable(q1) else q1
        
        new_version = None
        if blueprint_changed and sb:
            # Create new version with updated blueprint
            q2 = (sb.table("strategy_versions")
                                  .select("*")
                                  .eq("strategy_id", strategy_id)
                                  .eq("is_current", True)
                                  .execute())
            current_version_res = await q2 if inspect.isawaitable(q2) else q2
            
            if current_version_res and current_version_res.data:
                current = current_version_res.data[0]
                # Increment version
                version_parts = current["version"].replace("v", "").split(".")
                major, minor = int(version_parts[0]), int(version_parts[1]) if len(version_parts) > 1 else 0
                
                if blueprint_changed:
                    major += 1
                    minor = 0
                else:
                    minor += 1
                
                new_version_str = f"v{major}.{minor}"
                
                # Mark old version as not current
                q3 = sb.table("strategy_versions").update({"is_current": False}).eq("id", current["id"]).execute()
                if inspect.isawaitable(q3):
                    await q3
                
                # Create new version
                new_version_data = {
                    "id": str(uuid4()),
                    "strategy_id": strategy_id,
                    "version": new_version_str,
                    "blueprint": updates["blueprint"],
                    "is_draft": True,
                    "is_current": True,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                
                q4 = sb.table("strategy_versions").insert(new_version_data).execute()
                version_result = await q4 if inspect.isawaitable(q4) else q4
                new_version = version_result.data[0] if version_result and version_result.data else new_version_data
                
                # Update strategy current version
                q5 = sb.table("strategies").update({"current_version": new_version_str}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q5):
                    await q5
        
        return {
            "strategy": result.data[0] if result and result.data else {},
            "new_version": new_version
        }
    
    async def get_version_history(
        self,
        user: dict,
        strategy_id: str
    ) -> List[Dict]:
        """
        Get complete version history for a Strategy.

        Returns:
            List of all versions with metadata
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return []
            
        q1 = (sb.table("strategy_versions")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .order("created_at", desc=True)
                 .execute())
        result = await q1 if inspect.isawaitable(q1) else q1
        
        return result.data or [] if result else []
    
    async def compare_versions(
        self,
        user: dict,
        strategy_id: str,
        version_a: str,
        version_b: str
    ) -> Dict:
        """
        Compare two strategy versions.

        Returns:
            Comparison of blueprints and metadata
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}
            
        # Get both versions
        q1 = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_a)
                       .execute())
        version_a_res = await q1 if inspect.isawaitable(q1) else q1
        
        q2 = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_b)
                       .execute())
        version_b_res = await q2 if inspect.isawaitable(q2) else q2
        
        if not version_a_res.data or not version_b_res.data:
            raise ValueError("One or both versions not found")
        
        v_a = version_a_res.data[0]
        v_b = version_b_res.data[0]
        
        # Compare blueprints
        blueprint_a = v_a.get("blueprint", {})
        blueprint_b = v_b.get("blueprint", {})
        
        comparison = {
            "version_a": {
                "version": v_a["version"],
                "created_at": v_a["created_at"],
                "is_current": v_a["is_current"],
                "is_draft": v_a["is_draft"]
            },
            "version_b": {
                "version": v_b["version"],
                "created_at": v_b["created_at"],
                "is_current": v_b["is_current"],
                "is_draft": v_b["is_draft"]
            },
            "differences": {
                "nodes_changed": self._compare_nodes(blueprint_a, blueprint_b),
                "edges_changed": self._compare_edges(blueprint_a, blueprint_b),
                "parameters_changed": self._compare_parameters(blueprint_a, blueprint_b)
            }
        }
        
        return comparison
    
    def _compare_nodes(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare nodes between two blueprints."""
        nodes_a = {n["id"]: n for n in blueprint_a.get("nodes", [])}
        nodes_b = {n["id"]: n for n in blueprint_b.get("nodes", [])}
        
        added = [id for id in nodes_b if id not in nodes_a]
        removed = [id for id in nodes_a if id not in nodes_b]
        modified = [id for id in nodes_a if id in nodes_b and nodes_a[id] != nodes_b[id]]
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified
        }
    
    def _compare_edges(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare edges between two blueprints."""
        edges_a = set((e["source"], e["target"]) for e in blueprint_a.get("edges", []))
        edges_b = set((e["source"], e["target"]) for e in blueprint_b.get("edges", []))
        
        added = list(edges_b - edges_a)
        removed = list(edges_a - edges_b)
        
        return {
            "added": added,
            "removed": removed
        }
    
    def _compare_parameters(self, blueprint_a: dict, blueprint_b: dict) -> Dict:
        """Compare parameters between two blueprints."""
        params_a = blueprint_a.get("parameters", {})
        params_b = blueprint_b.get("parameters", {})
        
        added = {k: v for k, v in params_b.items() if k not in params_a}
        removed = {k: v for k, v in params_a.items() if k not in params_b}
        modified = {k: {"old": params_a[k], "new": params_b[k]} for k in params_a if k in params_b and params_a[k] != params_b[k]}
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified
        }
    
    async def restore_version(
        self,
        user: dict,
        strategy_id: str,
        version: str
    ) -> Dict:
        """
        Restore a previous version as current.

        Creates a new version based on the restored version.

        Returns:
            New version record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}
            
        # Get version to restore
        q1 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        version_res = await q1 if inspect.isawaitable(q1) else q1
        
        if not version_res or not version_res.data:
            raise ValueError(f"Version {version} not found")
        
        restored_version = version_res.data[0]
        
        # Mark current version as not current
        q2 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        current_res = await q2 if inspect.isawaitable(q2) else q2
        
        if current_res and current_res.data:
            q3 = sb.table("strategy_versions").update({"is_current": False}).eq("id", current_res.data[0]["id"]).execute()
            if inspect.isawaitable(q3):
                await q3
        
        # Get current version number to increment
        version_parts = version.replace("v", "").split(".")
        major, minor = int(version_parts[0]), int(version_parts[1]) if len(version_parts) > 1 else 0
        minor += 1
        new_version_str = f"v{major}.{minor}"
        
        # Create new version with restored blueprint
        new_version_data = {
            "id": str(uuid4()),
            "strategy_id": strategy_id,
            "version": new_version_str,
            "blueprint": restored_version["blueprint"],
            "is_draft": True,
            "is_current": True,
            "restored_from": version,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        q4 = sb.table("strategy_versions").insert(new_version_data).execute()
        version_result = await q4 if inspect.isawaitable(q4) else q4
        
        # Update strategy current version
        q5 = sb.table("strategies").update({"current_version": new_version_str}).eq("id", strategy_id).execute()
        if inspect.isawaitable(q5):
            await q5
        
        logger.info(f"Restored version {version} as {new_version_str} for strategy {strategy_id}")
        
        return version_result.data[0] if version_result and version_result.data else new_version_data
    
    async def deploy_version(
        self,
        user: dict,
        strategy_id: str,
        version: str,
        environment: str = "paper"
    ) -> Dict:
        """
        Deploy a specific version of a Strategy.

        Deployment always references an immutable version.

        Returns:
            Deployment record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return {}
            
        # Get version
        q1 = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        version_res = await q1 if inspect.isawaitable(q1) else q1
        
        if not version_res or not version_res.data:
            raise ValueError(f"Version {version} not found")
        
        version_data = version_res.data[0]
        
        # Check quota and entitlements
        from backend_app.core.subscription_engine import SubscriptionEngine, Resource, Feature
        from backend_app.core.subscription_dependencies import get_user_plan
        plan_key = await get_user_plan(user["id"], sb)
        
        if environment == "live":
            has_live = await SubscriptionEngine.check_feature_entitlement(user["id"], plan_key, Feature.LIVE_TRADING.value)
            if not has_live:
                raise ValueError("Live trading requires a paid subscription plan.")
        
        allowed, current_usage, limit = await SubscriptionEngine.reserve_quota(user["id"], plan_key, Resource.BOTS.value)
        if not allowed:
            raise ValueError(f"Quota exceeded for bots: {current_usage}/{limit}. Upgrade your plan to continue.")

        # Create deployment
        deployment_id = str(uuid4())
        deployment_data = {
            "id": deployment_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": version_data["id"],
            "version": version,
            "environment": environment,
            "exchange_id": None,  # Will be set from strategy
            "status": "deploying",
            "worker_region": "us-east-1",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
        deployment_result = await q2 if inspect.isawaitable(q2) else q2
        
        # Get strategy for exchange info
        q3 = sb.table("strategies").select("*").eq("id", strategy_id).execute()
        strategy_res = await q3 if inspect.isawaitable(q3) else q3
        if strategy_res and strategy_res.data:
            deployment_data["exchange_id"] = strategy_res.data[0]["exchange"]
        
        # Update strategy status
        q4 = sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).execute()
        if inspect.isawaitable(q4):
            await q4
        
        # Trigger actual deployment via FleetManager
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=strategy_res.data[0]["symbol"] if strategy_res and strategy_res.data else "BTC/USDT",
                blueprint=version_data["blueprint"]
            )
            
            if success:
                q5 = sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q5):
                    await q5
                
                q6 = sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q6):
                    await q6
            else:
                await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
                q5 = sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q5):
                    await q5
                
                q6 = sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q6):
                    await q6
        
        logger.info(f"Deployed version {version} of strategy {strategy_id}")
        
        return {
            "deployment": deployment_result.data[0] if deployment_result and deployment_result.data else deployment_data,
            "success": success if 'success' in locals() else True,
            "message": message if 'message' in locals() else "Deployment started"
        }
    
    # DEPRECATED: Marketplace operations moved to library.py router
    # Use /api/library/* endpoints instead
    
    async def delete_strategy(self, user: dict, strategy_id: str) -> bool:
        """
        Delete Strategy and all associated data.

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Stop all running deployments first
        await self.stop_all_deployments(user, strategy_id)

        # Delete strategy (cascade delete handled by database)
        if sb:
            q1 = sb.table("strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()
            if inspect.isawaitable(q1):
                await q1

        logger.info(f"Deleted strategy {strategy_id} for user {user['id']}")
        return True
    
    async def clone_strategy(
        self,
        user: dict,
        strategy_id: str,
        new_name: str
    ) -> Dict:
        """
        Clone a Strategy (creates new strategy with same blueprint).

        Returns:
            New strategy record
        """
        # Get original strategy
        original = await self.get_strategy(user, strategy_id)
        if not original:
            raise ValueError(f"Strategy {strategy_id} not found")

        # Create new strategy with cloned blueprint
        blueprint = original["version"]["blueprint"] if original["version"] else {}

        return await self.create_strategy(
            user=user,
            name=new_name,
            description=f"Cloned from {original['strategy']['name']}",
            blueprint=blueprint,
            exchange=original["strategy"]["exchange"],
            symbol=original["strategy"]["symbol"],
            timeframe=original["strategy"]["timeframe"],
            tags=original["strategy"].get("tags", [])
        )
    
    async def deploy_strategy(
        self,
        user: dict,
        strategy_id: str,
        version: Optional[str] = None,
        environment: str = "paper",
        exchange_id: Optional[str] = None
    ) -> Dict:
        """
        Deploy a Strategy (creates running bot instance).

        Returns:
            Deployment record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Get strategy
        strategy = await self.get_strategy(user, strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")
        
        # SECURITY: Validate ML/DL strategies have trained models before deployment
        strategy_data = strategy.get("strategy", {})
        nodes = []
        if isinstance(strategy_data.get("buy_logic"), dict):
            nodes = strategy_data["buy_logic"].get("_nodes", [])
        
        # Check for ML/DL nodes
        ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]
        
        if ml_nodes and not strategy_data.get("ml_model_path"):
            raise ValueError(
                "Strategy contains ML/DL nodes but no trained model reference. "
                "Train the model via POST /api/strategies/train-ml before deployment."
            )
        
        # Get specific version or current
        if version and sb:
            q1 = (sb.table("strategy_versions")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .eq("version", version)
                          .execute())
            version_res = await q1 if inspect.isawaitable(q1) else q1
            version_data = version_res.data[0] if version_res and version_res.data else None
        else:
            version_data = strategy["version"]
        
        if not version_data:
            raise ValueError(f"Version {version} not found")
        
        # Create deployment
        deployment_id = str(uuid4())
        deployment_data = {
            "id": deployment_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": version_data["id"],
            "version": version_data["version"],
            "environment": environment,
            "exchange_id": exchange_id or strategy["strategy"]["exchange"],
            "status": "deploying",
            "worker_region": "us-east-1",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        if sb:
            q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
            deployment_result = await q2 if inspect.isawaitable(q2) else q2
            
            # Update strategy status
            q3 = sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).execute()
            if inspect.isawaitable(q3):
                await q3
        
        # Trigger actual deployment via FleetManager
        # This will start the bot instance
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=strategy["strategy"]["symbol"],
                blueprint=version_data["blueprint"]
            )
            
            if success and sb:
                # Update deployment status to running
                q4 = sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q4):
                    await q4
                
                # Update strategy status
                q5 = sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q5):
                    await q5
            elif sb:
                # Deployment failed
                q4 = sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                if inspect.isawaitable(q4):
                    await q4
                
                q5 = sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
                if inspect.isawaitable(q5):
                    await q5
        
        logger.info(f"Deployed strategy {strategy_id} as deployment {deployment_id}")
        
        return {
            "deployment": deployment_result.data[0] if 'deployment_result' in locals() and deployment_result and deployment_result.data else deployment_data,
            "success": success if 'success' in locals() else True,
            "message": message if 'message' in locals() else "Deployment started"
        }
    
    async def stop_deployment(
        self,
        user: dict,
        deployment_id: str
    ) -> bool:
        """
        Stop a running Strategy deployment.

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return False

        # Get deployment
        q1 = sb.table("strategy_deployments").select("*").eq("id", deployment_id).eq("user_id", user["id"]).execute()
        deployment_res = await q1 if inspect.isawaitable(q1) else q1
        if not deployment_res or not deployment_res.data:
            return False

        deployment = deployment_res.data[0]

        # Stop via FleetManager
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.stop_bot(
                user_id=user["id"],
                symbol=deployment["exchange_id"]
            )
        
        # Update deployment status
        q2 = sb.table("strategy_deployments").update({
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", deployment_id).execute()
        if inspect.isawaitable(q2):
            await q2
        
        # Decrement quota if it was running
        if deployment.get("status") == "running":
            from backend_app.core.subscription_engine import SubscriptionEngine, Resource
            await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
        
        # Check if strategy has other running deployments
        q3 = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", deployment["strategy_id"])
                        .eq("status", "running")
                        .execute())
        running_res = await q3 if inspect.isawaitable(q3) else q3
        
        if running_res and not running_res.data:
            # No more running deployments, update strategy status
            q4 = sb.table("strategies").update({"status": StrategyStatus.STOPPED.value}).eq("id", deployment["strategy_id"]).execute()
            if inspect.isawaitable(q4):
                await q4
        
        logger.info(f"Stopped deployment {deployment_id}")
        return True
    
    async def stop_all_deployments(self, user: dict, strategy_id: str) -> int:
        """
        Stop all running deployments for a Strategy.

        Returns:
            Number of deployments stopped
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        if not sb:
            return 0

        # Get all running deployments
        q1 = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", strategy_id)
                        .eq("status", "running")
                        .execute())
        running_res = await q1 if inspect.isawaitable(q1) else q1

        stopped_count = 0
        for deployment in (running_res.data if running_res else []) or []:
            if await self.stop_deployment(user, deployment["id"]):
                stopped_count += 1

        return stopped_count
    
    async def pause_strategy(self, user: dict, strategy_id: str) -> bool:
        """
        Pause a running Strategy (stops all deployments).

        Returns:
            Success status
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Stop all deployments
        await self.stop_all_deployments(user, strategy_id)
        
        # Update strategy status
        if sb:
            q1 = sb.table("strategies").update({"status": StrategyStatus.PAUSED.value}).eq("id", strategy_id).execute()
            if inspect.isawaitable(q1):
                await q1
        
        logger.info(f"Paused strategy {strategy_id}")
        return True
    
    async def resume_strategy(self, user: dict, strategy_id: str) -> Dict:
        """
        Resume a paused Strategy (redeploys with current version).

        Returns:
            Deployment record
        """
        # Deploy again with current version
        return await self.deploy_strategy(user, strategy_id)
    
    async def get_strategy_metrics(
        self,
        user: dict,
        strategy_id: str
    ) -> Dict:
        """
        Get comprehensive Strategy metrics.

        Returns:
            All performance metrics from backend
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        # Get strategy
        strategy = await self.get_strategy(user, strategy_id)
        if not strategy:
            return {}

        # Get performance from Telemetry
        performance = await self._get_strategy_performance(user, strategy_id)
        
        # Get deployment metrics
        deployments = []
        if sb:
            q1 = (sb.table("strategy_deployments")
                           .select("*")
                           .eq("strategy_id", strategy_id)
                           .execute())
            deployments_res = await q1 if inspect.isawaitable(q1) else q1
            deployments = deployments_res.data if deployments_res and deployments_res.data else []
        
        # Calculate backend metrics
        total_deployments = len(deployments)
        running_deployments = len([d for d in deployments if d.get("status") == "running"])
        
        return {
            "strategy_id": strategy_id,
            "status": strategy["strategy"]["status"],
            "environment": strategy["strategy"]["environment"],
            "current_version": strategy["strategy"]["current_version"],
            "performance": performance,
            "deployments": {
                "total": total_deployments,
                "running": running_deployments,
                "stopped": total_deployments - running_deployments
            },
            "health": self._calculate_strategy_health(performance, running_deployments)
        }
    
    def _calculate_strategy_health(self, performance: Dict, running_deployments: int) -> str:
        """
        Calculate Strategy health based on metrics.
        
        Returns:
            Health status
        """
        if running_deployments == 0:
            return StrategyHealth.OFFLINE.value
        
        pnl = float(performance.get("today_pnl", 0))
        if pnl < -1000:  # Significant loss
            return StrategyHealth.ERROR.value
        elif pnl < -100:
            return StrategyHealth.WARNING.value
        
        return StrategyHealth.HEALTHY.value


# Singleton instance
_strategy_service = None

async def get_strategy_service() -> StrategyService:
    """Get singleton StrategyService instance."""
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service