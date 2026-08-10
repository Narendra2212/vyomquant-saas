"""
backend/strategy_service.py — Strategy Service

Central operational command center for all Strategy operations.

Replaces Bot Monitor infrastructure with Strategy-centric architecture.
A deployed Strategy IS the running trading bot.

PHASE 2: Transform Strategies into operational command center
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from backend_app.core.dependencies import create_request_supabase, get_telemetry

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
    LIVE = "live"
    CLOUD = "cloud"
    LOCAL = "local"


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
    
    def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        return create_request_supabase(user.get("access_token"))
    
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
        sb = self._get_supabase(user)
        
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
        strategy_result = sb.table("strategies").insert(strategy_data).execute()
        
        # Store version
        version_result = sb.table("strategy_versions").insert(version_data).execute()
        
        logger.info(f"Created strategy {strategy_id} v1.0 for user {user['id']}")
        
        return {
            "strategy": strategy_result.data[0] if strategy_result.data else strategy_data,
            "version": version_result.data[0] if version_result.data else version_data
        }
    
    async def get_strategy(self, user: dict, strategy_id: str) -> Optional[Dict]:
        """
        Get complete Strategy data with current version.
        
        Returns:
            Strategy with version, deployments, and metrics
        """
        sb = self._get_supabase(user)
        
        # Get strategy
        strategy_res = sb.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user["id"]).execute()
        if not strategy_res.data:
            return None
        
        strategy = strategy_res.data[0]
        
        # Get current version
        version_res = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        
        current_version = version_res.data[0] if version_res.data else None
        
        # Get deployments
        deployments_res = (sb.table("strategy_deployments")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .order("created_at", desc=True)
                          .execute())
        
        # Get backtests
        backtests_res = (sb.table("strategy_backtests")
                        .select("*")
                        .eq("strategy_id", strategy_id)
                        .order("created_at", desc=True)
                        .execute())
        
        return {
            "strategy": strategy,
            "version": current_version,
            "deployments": deployments_res.data or [],
            "backtests": backtests_res.data or []
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
        sb = self._get_supabase(user)
        
        query = sb.table("strategies").select("*").eq("user_id", user["id"])
        
        if status_filter:
            query = query.eq("status", status_filter)
        if environment_filter:
            query = query.eq("environment", environment_filter)
        
        result = query.order("updated_at", desc=True).execute()
        
        strategies = result.data or []
        
        # Enrich with deployment and performance metrics
        enriched = []
        for strategy in strategies:
            # Get current deployment status
            deployments_res = (sb.table("strategy_deployments")
                              .select("*")
                              .eq("strategy_id", strategy["id"])
                              .eq("status", "running")
                              .execute())
            
            is_running = len(deployments_res.data or []) > 0
            
            # Get performance metrics from Telemetry
            try:
                performance = await self._get_strategy_performance(user, strategy["id"])
            except Exception as e:
                logger.warning(f"Failed to fetch performance for strategy {strategy['id']}: {e}")
                performance = None  # Explicitly indicate performance unavailable
            
            enriched.append({
                **strategy,
                "is_running": is_running,
                "deployment_count": len(deployments_res.data or []),
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
        sb = self._get_supabase(user)
        
        # Check if blueprint is being updated
        blueprint_changed = "blueprint" in updates
        
        # Update strategy metadata
        strategy_updates = {
            **updates,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if "blueprint" in strategy_updates:
            del strategy_updates["blueprint"]  # Handle separately
        
        result = sb.table("strategies").update(strategy_updates).eq("id", strategy_id).eq("user_id", user_id).execute()
        
        new_version = None
        if blueprint_changed:
            # Create new version with updated blueprint
            current_version_res = (sb.table("strategy_versions")
                                  .select("*")
                                  .eq("strategy_id", strategy_id)
                                  .eq("is_current", True)
                                  .execute())
            
            if current_version_res.data:
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
                sb.table("strategy_versions").update({"is_current": False}).eq("id", current["id"]).execute()
                
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
                
                version_result = sb.table("strategy_versions").insert(new_version_data).execute()
                new_version = version_result.data[0] if version_result.data else new_version_data
                
                # Update strategy current version
                sb.table("strategies").update({"current_version": new_version_str}).eq("id", strategy_id).execute()
        
        return {
            "strategy": result.data[0] if result.data else {},
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
        sb = self._get_supabase(user)
        
        result = (sb.table("strategy_versions")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .order("created_at", desc=True)
                 .execute())
        
        return result.data or []
    
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
        sb = self._get_supabase(user)
        
        # Get both versions
        version_a_res = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_a)
                       .execute())
        
        version_b_res = (sb.table("strategy_versions")
                       .select("*")
                       .eq("strategy_id", strategy_id)
                       .eq("version", version_b)
                       .execute())
        
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
        sb = self._get_supabase(user)
        
        # Get version to restore
        version_res = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        
        if not version_res.data:
            raise ValueError(f"Version {version} not found")
        
        restored_version = version_res.data[0]
        
        # Mark current version as not current
        current_res = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("is_current", True)
                      .execute())
        
        if current_res.data:
            sb.table("strategy_versions").update({"is_current": False}).eq("id", current_res.data[0]["id"]).execute()
        
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
        
        version_result = sb.table("strategy_versions").insert(new_version_data).execute()
        
        # Update strategy current version
        sb.table("strategies").update({"current_version": new_version_str}).eq("id", strategy_id).execute()
        
        logger.info(f"Restored version {version} as {new_version_str} for strategy {strategy_id}")
        
        return version_result.data[0] if version_result.data else new_version_data
    
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
        sb = self._get_supabase(user)
        
        # Get version
        version_res = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", strategy_id)
                      .eq("version", version)
                      .execute())
        
        if not version_res.data:
            raise ValueError(f"Version {version} not found")
        
        version_data = version_res.data[0]
        
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
        
        deployment_result = sb.table("strategy_deployments").insert(deployment_data).execute()
        
        # Get strategy for exchange info
        strategy_res = sb.table("strategies").select("*").eq("id", strategy_id).execute()
        if strategy_res.data:
            deployment_data["exchange_id"] = strategy_res.data[0]["exchange"]
        
        # Update strategy status
        sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).execute()
        
        # Trigger actual deployment via FleetManager
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=strategy_res.data[0]["symbol"] if strategy_res.data else "BTC/USDT",
                blueprint=version_data["blueprint"]
            )
            
            if success:
                sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                
                sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
            else:
                sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                
                sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
        
        logger.info(f"Deployed version {version} of strategy {strategy_id}")
        
        return {
            "deployment": deployment_result.data[0] if deployment_result.data else deployment_data,
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
        sb = self._get_supabase(user)

        # Stop all running deployments first
        await self.stop_all_deployments(user, strategy_id)

        # Delete strategy (cascade delete handled by database)
        result = sb.table("strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()

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
        sb = self._get_supabase(user)

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
        if version:
            version_res = (sb.table("strategy_versions")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .eq("version", version)
                          .execute())
            version_data = version_res.data[0] if version_res.data else None
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
        
        deployment_result = sb.table("strategy_deployments").insert(deployment_data).execute()
        
        # Update strategy status
        sb.table("strategies").update({"status": StrategyStatus.DEPLOYING.value}).eq("id", strategy_id).execute()
        
        # Trigger actual deployment via FleetManager
        # This will start the bot instance
        from backend_app.core.state import app_state
        if hasattr(app_state, 'fleet'):
            success, message = await app_state.fleet.start_bot(
                user_id=user["id"],
                symbol=strategy["strategy"]["symbol"],
                blueprint=version_data["blueprint"]
            )
            
            if success:
                # Update deployment status to running
                sb.table("strategy_deployments").update({
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", deployment_id).execute()
                
                # Update strategy status
                sb.table("strategies").update({"status": StrategyStatus.RUNNING.value}).eq("id", strategy_id).execute()
            else:
                # Deployment failed
                sb.table("strategy_deployments").update({
                    "status": "failed",
                    "error_message": message
                }).eq("id", deployment_id).execute()
                
                sb.table("strategies").update({"status": StrategyStatus.FAILED.value}).eq("id", strategy_id).execute()
        
        logger.info(f"Deployed strategy {strategy_id} as deployment {deployment_id}")
        
        return {
            "deployment": deployment_result.data[0] if deployment_result.data else deployment_data,
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
        sb = self._get_supabase(user)

        # Get deployment
        deployment_res = sb.table("strategy_deployments").select("*").eq("id", deployment_id).eq("user_id", user["id"]).execute()
        if not deployment_res.data:
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
        sb.table("strategy_deployments").update({
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", deployment_id).execute()
        
        # Check if strategy has other running deployments
        running_res = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", deployment["strategy_id"])
                        .eq("status", "running")
                        .execute())
        
        if not running_res.data:
            # No more running deployments, update strategy status
            sb.table("strategies").update({"status": StrategyStatus.STOPPED.value}).eq("id", deployment["strategy_id"]).execute()
        
        logger.info(f"Stopped deployment {deployment_id}")
        return True
    
    async def stop_all_deployments(self, user: dict, strategy_id: str) -> int:
        """
        Stop all running deployments for a Strategy.

        Returns:
            Number of deployments stopped
        """
        sb = self._get_supabase(user)

        # Get all running deployments
        running_res = (sb.table("strategy_deployments")
                        .select("*")
                        .eq("strategy_id", strategy_id)
                        .eq("status", "running")
                        .execute())

        stopped_count = 0
        for deployment in running_res.data or []:
            if await self.stop_deployment(user, deployment["id"]):
                stopped_count += 1

        return stopped_count
    
    async def pause_strategy(self, user: dict, strategy_id: str) -> bool:
        """
        Pause a running Strategy (stops all deployments).

        Returns:
            Success status
        """
        sb = self._get_supabase(user)

        # Stop all deployments
        await self.stop_all_deployments(user, strategy_id)
        
        # Update strategy status
        sb.table("strategies").update({"status": StrategyStatus.PAUSED.value}).eq("id", strategy_id).execute()
        
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
        sb = self._get_supabase(user)

        # Get strategy
        strategy = await self.get_strategy(user, strategy_id)
        if not strategy:
            return {}

        # Get performance from Telemetry
        performance = await self._get_strategy_performance(user, strategy_id)
        
        # Get deployment metrics
        deployments_res = (sb.table("strategy_deployments")
                          .select("*")
                          .eq("strategy_id", strategy_id)
                          .execute())
        
        # Calculate backend metrics
        total_deployments = len(deployments_res.data or [])
        running_deployments = len([d for d in deployments_res.data or [] if d["status"] == "running"])
        
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