#!/usr/bin/env python3
"""
Test script to verify the fix for the 'Context' object has no attribute 'complete' error.
This script creates a mock context without a complete method and exercises the code paths
that previously caused errors.
"""

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

# Set up logging to see debug information
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
)
logger = logging.getLogger("test_script")

# Import the safe_context_call function from server.py
from a2a_mcp_server.server import safe_context_call
from a2a_mcp_server.pipeline.engine import NodeExecutionContext

class IncompleteContext:
    """Mock context that intentionally lacks a 'complete' method."""
    
    def __init__(self):
        self.report_progress_called = False
        self.error_called = False
        
    async def report_progress(self, progress_value):
        """Mock progress reporting."""
        logger.info(f"report_progress called with {progress_value}")
        self.report_progress_called = True
        
    async def error(self, message):
        """Mock error reporting."""
        logger.info(f"error called with: {message}")
        self.error_called = True
        
    # Intentionally NOT implementing complete()

class MockPipelineState:
    """Mock pipeline state for testing NodeExecutionContext."""
    
    def __init__(self):
        self.nodes = {
            "test_node": MagicMock(
                node_id="test_node",
                status="working",
                progress=0.5,
            )
        }
        self.progress = 0.5
        self.message = "Test message"
        
    def update_status(self):
        logger.info("update_status called")
        
    def update_progress(self):
        logger.info("update_progress called")

async def test_safe_context_call():
    """Test the safe_context_call function with a context missing the complete method."""
    ctx = IncompleteContext()
    
    logger.info("Testing safe_context_call with non-existent method 'complete'")
    await safe_context_call(ctx, "complete", "Test completion message")
    
    if ctx.report_progress_called:
        logger.info("SUCCESS: report_progress was called as a fallback!")
    else:
        logger.error("FAIL: report_progress was not called as a fallback")
        
    # Reset for next test
    ctx.report_progress_called = False
    
    logger.info("Testing safe_context_call with existing method 'report_progress'")
    await safe_context_call(ctx, "report_progress", 0.7)
    
    if ctx.report_progress_called:
        logger.info("SUCCESS: report_progress was called directly!")
    else:
        logger.error("FAIL: report_progress was not called directly")

async def test_node_execution_context():
    """Test the NodeExecutionContext with a parent context missing the complete method."""
    pipeline_state = MockPipelineState()
    incomplete_ctx = IncompleteContext()
    
    node_ctx = NodeExecutionContext(pipeline_state, "test_node", incomplete_ctx)
    
    logger.info("Testing NodeExecutionContext.complete()")
    await node_ctx.complete("Test node completion")
    
    if incomplete_ctx.report_progress_called:
        logger.info("SUCCESS: Parent context's report_progress was called as a fallback!")
    else:
        logger.error("FAIL: Parent context's report_progress was not called")
        
    # Reset for next test
    incomplete_ctx.report_progress_called = False
    
    logger.info("Testing NodeExecutionContext.error()")
    await node_ctx.error("Test error message")
    
    if incomplete_ctx.error_called or incomplete_ctx.report_progress_called:
        logger.info("SUCCESS: Parent context's error or report_progress was called!")
    else:
        logger.error("FAIL: Neither error nor report_progress was called")

async def main():
    """Run all tests."""
    logger.info("=== Testing safe_context_call ===")
    await test_safe_context_call()
    
    logger.info("\n=== Testing NodeExecutionContext ===")
    await test_node_execution_context()
    
    logger.info("\nAll tests completed!")

if __name__ == "__main__":
    asyncio.run(main())