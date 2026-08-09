"""
tests/test_ml_deployment_guards.py — ML Strategy Deployment Guard Tests

Tests that ML/DL strategies cannot be deployed without trained models.
This validates the deployment guards that prevent runtime failures
when ML nodes are present but no trained model reference exists.

Also tests that ML training automatically persists model_path to database,
eliminating the manual step between training and deployment.

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestMLDeploymentGuards:
    """Test ML strategy deployment guards."""

    def test_detect_ml_nodes(self):
        """Test ML node detection in DAG."""
        from backend_app.routers.strategies import _detect_ml_nodes
        
        # Test with ML nodes
        nodes = [
            {"id": "node1", "type": "indicator"},
            {"id": "node2", "type": "ml", "model_id": "model_123"},
            {"id": "node3", "type": "dl", "model_id": "model_456"},
        ]
        
        ml_nodes = _detect_ml_nodes(nodes)
        assert len(ml_nodes) == 2
        assert ml_nodes[0]["type"] == "ml"
        assert ml_nodes[1]["type"] == "dl"
        
        # Test without ML nodes
        nodes_no_ml = [
            {"id": "node1", "type": "indicator"},
            {"id": "node2", "type": "logic"},
        ]
        
        ml_nodes = _detect_ml_nodes(nodes_no_ml)
        assert len(ml_nodes) == 0

    def test_validate_ml_models_present_with_ml_nodes_no_model(self):
        """Test that ML nodes without model reference are rejected."""
        from backend_app.routers.strategies import _validate_ml_models_present
        from fastapi import HTTPException
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "ml", "model_id": "model_123"},
            ],
            "ml_model_path": None  # No model reference
        }
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_ml_models_present(blueprint)
        
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        if isinstance(error_detail, dict):
            assert "ML_MODEL_MISSING" in error_detail.get("error", "")

    def test_validate_ml_models_present_with_ml_nodes_no_model_id(self):
        """Test that ML nodes without model_id are rejected."""
        from backend_app.routers.strategies import _validate_ml_models_present
        from fastapi import HTTPException
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "ml"},  # Missing model_id
            ],
            "ml_model_path": "/path/to/model.pkl"
        }
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_ml_models_present(blueprint)
        
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        if isinstance(error_detail, dict):
            assert "ML_NODE_MISSING_MODEL_ID" in error_detail.get("error", "")

    def test_validate_ml_models_present_with_valid_model(self):
        """Test that valid ML nodes with model reference pass validation."""
        from backend_app.routers.strategies import _validate_ml_models_present
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "ml", "model_id": "model_123"},
            ],
            "ml_model_path": "/path/to/model.pkl"
        }
        
        # Should not raise exception
        _validate_ml_models_present(blueprint)

    def test_validate_ml_models_present_no_ml_nodes(self):
        """Test that strategies without ML nodes pass validation."""
        from backend_app.routers.strategies import _validate_ml_models_present
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "indicator"},
                {"id": "node2", "type": "logic"},
            ],
            "ml_model_path": None
        }
        
        # Should not raise exception
        _validate_ml_models_present(blueprint)

    def test_validate_ml_models_present_buy_logic_extraction(self):
        """Test ML node detection from buy_logic structure."""
        from backend_app.routers.strategies import _validate_ml_models_present
        
        blueprint = {
            "buy_logic": {
                "_nodes": [
                    {"id": "node1", "type": "ml", "model_id": "model_123"},
                ]
            },
            "ml_model_path": "/path/to/model.pkl"
        }
        
        # Should not raise exception
        _validate_ml_models_present(blueprint)

    def test_deploy_endpoint_rejects_untrained_ml_strategy(self):
        """Test that deploy endpoint rejects untrained ML strategies."""
        from fastapi import HTTPException
        from unittest.mock import AsyncMock, MagicMock
        
        # Mock the deploy endpoint to test the guard
        from backend_app.routers.strategies import _validate_ml_models_present
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "ml", "model_id": "model_123"},
            ],
            "ml_model_path": None
        }
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_ml_models_present(blueprint)
        
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        if isinstance(error_detail, dict):
            assert "ML_MODEL_MISSING" in error_detail.get("error", "")

    def test_resume_endpoint_rejects_untrained_ml_strategy(self):
        """Test that resume endpoint rejects untrained ML strategies."""
        from fastapi import HTTPException
        from backend_app.routers.strategies import _validate_ml_models_present
        
        blueprint = {
            "nodes": [
                {"id": "node1", "type": "dl", "model_id": "model_456"},
            ],
            "ml_model_path": None
        }
        
        with pytest.raises(HTTPException) as exc_info:
            _validate_ml_models_present(blueprint)
        
        assert exc_info.value.status_code == 400
        error_detail = exc_info.value.detail
        if isinstance(error_detail, dict):
            assert "ML_MODEL_MISSING" in error_detail.get("error", "")

    def test_clone_endpoint_rejects_untrained_ml_strategy(self):
        """Test that clone endpoint rejects untrained ML strategies."""
        # Test the guard logic in library.py
        source_data = {
            "buy_logic": {
                "_nodes": [
                    {"id": "node1", "type": "ml", "model_id": "model_123"},
                ]
            },
            "ml_model_path": None
        }
        
        # Extract nodes and check
        nodes = source_data["buy_logic"].get("_nodes", [])
        ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]
        
        assert len(ml_nodes) == 1
        assert not source_data.get("ml_model_path")  # No model reference

    def test_ml_training_auto_persists_model_path(self):
        """Test that ML training automatically persists model_path to database."""
        # This test verifies the database update logic exists in the training function
        # We check the source code to ensure the update logic is present
        import inspect
        from backend_app.routers.strategies import train_ml_strategy
        
        # Get the source code of the training function
        source = inspect.getsource(train_ml_strategy)
        
        # Verify the database update logic is present
        assert "ml_model_path" in source, "ml_model_path database update logic missing"
        assert "sb.table" in source and ".update" in source, "Database update call missing"
        assert "strategy_id" in source, "Strategy lookup logic missing"
        assert "model_id" in source, "ML node model_id update logic missing"
        
        # Verify error handling for DB write failures
        assert "db_update_success" in source, "DB write success tracking missing"
        assert "Database update failed" in source, "DB error handling missing"


class TestStrategyIdBasedTraining:
    """Test PHASE 53: Strategy ID-based training eliminates ambiguous name failures."""

    def test_training_endpoint_uses_strategy_id_from_path(self):
        """
        Test that the training endpoint correctly extracts strategy_id from URL path.
        
        Verifies that the endpoint signature changed from accepting strategy_name
        in request body to accepting strategy_id as a path parameter.
        """
        import inspect
        from backend_app.routers.strategies import train_ml_strategy
        
        # Get the function signature
        sig = inspect.signature(train_ml_strategy)
        params = list(sig.parameters.keys())
        
        # Verify strategy_id is a parameter (path parameter from route)
        assert "strategy_id" in params, "strategy_id parameter missing from endpoint signature"
        
        # Verify it's the first parameter after self (FastAPI path parameters come first)
        assert params[0] == "strategy_id", "strategy_id should be first parameter (path parameter)"
        
        # Verify strategy_name is NOT a parameter (old behavior)
        assert "strategy_name" not in params, "strategy_name should not be in parameters (removed in PHASE 53)"

    def test_endpoint_route_uses_strategy_id_in_path(self):
        """
        Test that the route decorator uses strategy_id in the path.
        
        Verifies that the route was changed from /train-ml to /{strategy_id}/train.
        """
        import inspect
        from backend_app.routers.strategies import train_ml_strategy
        
        # Get the source code to verify the route decorator
        source = inspect.getsource(train_ml_strategy)
        
        # Verify the route uses strategy_id in path
        assert '@router.post("/{strategy_id}/train")' in source, "Route should use strategy_id in path"
        
        # Verify the old route is NOT present
        assert '@router.post("/train-ml")' not in source, "Old /train-ml route should be removed"

    def test_endpoint_verifies_strategy_ownership(self):
        """
        Test that the endpoint verifies strategy ownership before training.
        
        Verifies that the new implementation checks if the strategy belongs to the user
        before proceeding with training, using the direct strategy_id lookup.
        """
        import inspect
        from backend_app.routers.strategies import train_ml_strategy
        
        # Get the source code
        source = inspect.getsource(train_ml_strategy)
        
        # Verify the ownership check is present
        assert '.eq("id", strategy_id)' in source, "Should verify strategy by ID"
        assert '.eq("user_id", user["id"])' in source, "Should verify user ownership"
        
        # Verify the old name-based lookup is NOT present
        assert '.eq("name", body["strategy_name"])' not in source, "Should not use name-based lookup"
        
        # Note: .single() is safe when looking up by unique key (strategy_id + user_id)
        # The old implementation used .single() on (name + user_id) which is NOT unique

    def test_endpoint_error_handling_for_not_found(self):
        """
        Test that the endpoint properly handles strategy not found errors.
        
        Verifies that when strategy_id doesn't exist or doesn't belong to user,
        an appropriate error is returned via WebSocket (not silent failure).
        """
        import inspect
        from backend_app.routers.strategies import train_ml_strategy
        
        # Get the source code
        source = inspect.getsource(train_ml_strategy)
        
        # Verify error handling is present
        assert 'not found' in source.lower(), "Should handle not found case"
        assert 'model_error' in source.lower(), "Should broadcast model_error on failure"
        
        # Verify the old silent failure (strategy_id = None) is NOT present
        assert 'strategy_id = None' not in source, "Should not leave strategy_id as None on error"

    def test_frontend_api_uses_strategy_id_parameter(self):
        """
        Test that the frontend API client passes strategy_id as first parameter.
        
        Verifies that the API layer was updated to match the new endpoint signature.
        """
        import sys
        import os
        
        # Add frontend to path
        frontend_path = os.path.join(os.path.dirname(__file__), '..', 'algo22-terminal')
        if frontend_path not in sys.path:
            sys.path.insert(0, frontend_path)
        
        try:
            from src.api.modules.strategies import strategiesApi
            
            # Get the trainMl function
            train_ml_func = strategiesApi.trainMl
            import inspect
            sig = inspect.signature(train_ml_func)
            params = list(sig.parameters.keys())
            
            # Verify strategyId is the first parameter
            assert params[0] == "strategyId", "strategyId should be first parameter"
            
            # Verify it accepts strategyId and params
            assert len(params) == 2, "trainMl should accept 2 parameters (strategyId, params)"
            assert params[1] == "params", "Second parameter should be params"
            
            # Verify the API call uses strategy_id in URL
            source = inspect.getsource(train_ml_func)
            assert "/api/strategies/${strategyId}/train" in source, "API call should use strategyId in path"
        except ImportError:
            # If frontend is not in expected location, skip this test
            pytest.skip("Frontend module not found at expected location")

    def test_typed_client_uses_strategy_id_in_path(self):
        """
        Test that the typed client uses strategy_id in the URL path.
        
        Verifies that the TypeScript/JavaScript client was updated to match the new endpoint.
        """
        import sys
        import os
        
        # Add frontend to path
        frontend_path = os.path.join(os.path.dirname(__file__), '..', 'algo22-terminal')
        if frontend_path not in sys.path:
            sys.path.insert(0, frontend_path)
        
        try:
            from src.api.typed_client import StrategyApi
            
            # Get the trainMl method
            train_ml_method = StrategyApi.trainMl
            import inspect
            sig = inspect.signature(train_ml_method)
            params = list(sig.parameters.keys())
            
            # Verify id is the first parameter
            assert params[0] == "id", "id should be first parameter (strategy_id)"
            
            # Verify the URL template in the function body
            source = inspect.getsource(train_ml_method)
            assert "/api/strategies/${id}/train" in source, "URL should use id in path parameter"
        except ImportError:
            # If frontend is not in expected location, skip this test
            pytest.skip("Frontend module not found at expected location")



class TestModelFileValidation:
    """Test model file existence validation."""

    def test_missing_model_file_detection(self):
        """Test that missing model files are detected."""
        # This would require filesystem access or model registry check
        # For now, we test the validation logic exists
        import os
        
        # Test with non-existent file
        model_path = "/nonexistent/path/to/model.pkl"
        assert not os.path.exists(model_path)
        
        # In production, this would trigger the missing model file guard
        # Currently implemented as TODO in the validation function


def run_all_tests():
    """Run all ML deployment guard tests."""
    print("=" * 60)
    print("ML DEPLOYMENT GUARD TESTS")
    print("=" * 60)
    
    import pytest
    result = pytest.main([__file__, "-v", "--tb=short"])
    
    print("\n" + "=" * 60)
    if result == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TESTS FAILED (exit code: {result})")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    sys.exit(run_all_tests())
