"""
Full System Validation Runtime
Principal Institutional Distributed Systems Validation Engineer

Orchestrates all validation suites for institutional-grade deployment readiness.
"""

import asyncio
import io
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Force UTF-8 output on Windows to prevent charmap codec errors
# This must happen before any logging is configured
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("FullSystemValidation")

class ValidationSuite:
    """Base class for validation suites."""
    
    def __init__(self, name: str):
        self.name = name
        self.results = {
            "suite_name": name,
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0,
            "errors": [],
            "warnings": [],
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0
        }
    
    async def run(self) -> Dict[str, Any]:
        """Run the validation suite."""
        logger.info(f"🔍 Starting validation suite: {self.name}")
        self.results["start_time"] = datetime.utcnow().isoformat()
        start = time.time()
        
        try:
            await self._execute_tests()
        except Exception as e:
            logger.error(f"❌ Suite {self.name} failed with exception: {e}")
            self.results["errors"].append(f"Suite exception: {str(e)}")
        
        self.results["end_time"] = datetime.utcnow().isoformat()
        self.results["duration_seconds"] = time.time() - start
        logger.info(f"✅ Completed validation suite: {self.name} ({self.results['duration_seconds']:.2f}s)")
        
        return self.results
    
    async def _execute_tests(self):
        """Override in subclasses to implement specific tests."""
        raise NotImplementedError

class FullSystemValidationRuntime:
    """Main runtime orchestrator for all validation suites."""
    
    def __init__(self):
        self.suites: List[ValidationSuite] = []
        self.overall_results = {
            "validation_id": f"VAL-{int(time.time())}",
            "start_time": None,
            "end_time": None,
            "total_duration_seconds": 0,
            "suites": [],
            "summary": {
                "total_suites": 0,
                "passed_suites": 0,
                "failed_suites": 0,
                "total_tests": 0,
                "total_passed": 0,
                "total_failed": 0,
                "total_errors": 0,
                "deployment_ready": False
            }
        }
    
    def register_suite(self, suite: ValidationSuite):
        """Register a validation suite."""
        self.suites.append(suite)
        logger.info(f"Registered validation suite: {suite.name}")
    
    async def run_all(self) -> Dict[str, Any]:
        """Run all registered validation suites."""
        logger.info("🚀 Starting Full System Validation Runtime")
        self.overall_results["start_time"] = datetime.utcnow().isoformat()
        start = time.time()
        
        for suite in self.suites:
            try:
                results = await suite.run()
                self.overall_results["suites"].append(results)
            except Exception as e:
                logger.error(f"Failed to run suite {suite.name}: {e}")
                self.overall_results["suites"].append({
                    "suite_name": suite.name,
                    "error": str(e),
                    "tests_failed": 1
                })
        
        self.overall_results["end_time"] = datetime.utcnow().isoformat()
        self.overall_results["total_duration_seconds"] = time.time() - start
        
        # Calculate summary
        self._calculate_summary()
        
        logger.info(f"🎉 Full System Validation Complete")
        logger.info(f"   Total Suites: {self.overall_results['summary']['total_suites']}")
        logger.info(f"   Passed: {self.overall_results['summary']['passed_suites']}")
        logger.info(f"   Failed: {self.overall_results['summary']['failed_suites']}")
        logger.info(f"   Deployment Ready: {self.overall_results['summary']['deployment_ready']}")
        
        return self.overall_results
    
    def _calculate_summary(self):
        """Calculate overall summary statistics."""
        summary = self.overall_results["summary"]
        summary["total_suites"] = len(self.suites)
        
        for suite_result in self.overall_results["suites"]:
            if suite_result.get("error"):
                summary["failed_suites"] += 1
            elif suite_result.get("tests_failed", 0) > 0:
                summary["failed_suites"] += 1
            else:
                summary["passed_suites"] += 1
            
            summary["total_tests"] += suite_result.get("tests_run", 0)
            summary["total_passed"] += suite_result.get("tests_passed", 0)
            summary["total_failed"] += suite_result.get("tests_failed", 0)
            summary["total_errors"] += len(suite_result.get("errors", []))
        
        # Deployment ready if no critical failures
        summary["deployment_ready"] = (
            summary["failed_suites"] == 0 and
            summary["total_errors"] == 0
        )

async def main():
    """Main entry point."""
    runtime = FullSystemValidationRuntime()
    
    # Import and register validation suites
    try:
        from backend_validation_suite import BackendValidationSuite
        runtime.register_suite(BackendValidationSuite())
    except ImportError:
        logger.warning("BackendValidationSuite not found, skipping")
    
    try:
        from frontend_validation_suite import FrontendValidationSuite
        runtime.register_suite(FrontendValidationSuite())
    except ImportError:
        logger.warning("FrontendValidationSuite not found, skipping")
    
    try:
        from websocket_validation_suite import WebSocketValidationSuite
        runtime.register_suite(WebSocketValidationSuite())
    except ImportError:
        logger.warning("WebSocketValidationSuite not found, skipping")
    
    try:
        from orchestration_validation_suite import OrchestrationValidationSuite
        runtime.register_suite(OrchestrationValidationSuite())
    except ImportError:
        logger.warning("OrchestrationValidationSuite not found, skipping")
    
    try:
        from replay_validation_suite import ReplayValidationSuite
        runtime.register_suite(ReplayValidationSuite())
    except ImportError:
        logger.warning("ReplayValidationSuite not found, skipping")
    
    try:
        from ml_pipeline_validation_suite import MLPipelineValidationSuite
        runtime.register_suite(MLPipelineValidationSuite())
    except ImportError:
        logger.warning("MLPipelineValidationSuite not found, skipping")
    
    try:
        from database_validation_suite import DatabaseValidationSuite
        runtime.register_suite(DatabaseValidationSuite())
    except ImportError:
        logger.warning("DatabaseValidationSuite not found, skipping")
    
    try:
        from sandbox_execution_validation import SandboxExecutionValidationSuite
        runtime.register_suite(SandboxExecutionValidationSuite())
    except ImportError:
        logger.warning("SandboxExecutionValidationSuite not found, skipping")
    
    # Run all validations
    results = await runtime.run_all()
    
    # Save results
    import json
    output_path = Path("validation_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Results saved to {output_path}")
    
    # Exit with appropriate code
    sys.exit(0 if results["summary"]["deployment_ready"] else 1)

if __name__ == "__main__":
    asyncio.run(main())
