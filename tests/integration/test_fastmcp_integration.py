"""Integration tests for FastMCP integration."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.fixtures.mock_context import MockContext
from fastmcp import Context

@pytest.mark.asyncio
async def test_context_progress_reporting(server_state, mock_context):
    """Test progress reporting through FastMCP Context."""
    # Import function to test
    from a2a_mcp_server.server import call_agent
    
    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}
    
    # Create a mock client with various progress states
    mock_client = AsyncMock()
    stream_response = AsyncMock()
    
    # Create progress updates
    async def progress_stream():
        progress_steps = [
            (0.1, "Starting"),
            (0.3, "Processing data"),
            (0.5, "Analyzing results"),
            (0.7, "Formatting output"),
            (0.9, "Finalizing"),
            (1.0, "Complete")
        ]
        
        for progress, message in progress_steps:
            yield MagicMock(
                status=MagicMock(
                    state="working" if progress < 1.0 else "completed",
                    message=message,
                    progress=progress
                ),
                artifacts=[] if progress < 1.0 else [
                    MagicMock(name="result", parts=[
                        MagicMock(type="text", text="Final result")
                    ])
                ]
            )
            await asyncio.sleep(0.01)  # Small delay to simulate real timing
    
    stream_response.stream_updates.return_value = progress_stream()
    mock_client.send_message_streaming.return_value = stream_response
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Use a custom context to track progress updates
        custom_context = MockContext()
        
        # Call the agent
        result = await call_agent(
            agent_name="test-agent",
            prompt="Test prompt",
            ctx=custom_context
        )
        
        # Verify
        assert result["status"] == "success"
        
        # Check that progress reports were sent to the context
        assert custom_context.get_progress_count() >= 3
        
        # Check that the final progress is complete
        assert custom_context.get_completion_count() == 1
        
        # Check that there were no errors
        assert custom_context.get_error_count() == 0
        
        # Verify progress values were increasing
        progress_values = [report["current"] for report in custom_context.progress_reports]
        assert all(b >= a for a, b in zip(progress_values, progress_values[1:]))
        
        # Verify completion message
        assert "Complete" in custom_context.completions[0]["message"]

@pytest.mark.asyncio
async def test_context_error_reporting(server_state):
    """Test error reporting through FastMCP Context."""
    # Import function to test
    from a2a_mcp_server.server import call_agent
    
    # Configure registry
    server_state.registry = {"error-agent": "http://error-agent-url"}
    
    # Create a mock client that fails
    mock_client = AsyncMock()
    mock_client.send_message_streaming.side_effect = Exception("Simulated agent error")
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Use a custom context to track error updates
        custom_context = MockContext()
        
        # Call the agent
        result = await call_agent(
            agent_name="error-agent",
            prompt="Test prompt",
            ctx=custom_context
        )
        
        # Verify
        assert result["status"] == "error"
        
        # Check that error was reported to the context
        assert custom_context.get_error_count() >= 1
        
        # Verify error message
        assert "error" in custom_context.errors[0]["message"].lower()
        assert "agent" in custom_context.errors[0]["message"].lower()

@pytest.mark.asyncio
async def test_tool_registration():
    """Test FastMCP tool registration."""
    # Import the mcp instance to check if tools are registered
    from a2a_mcp_server.server import mcp
    
    # Check that essential tools are registered
    tools = mcp.get_tools() if hasattr(mcp, "get_tools") else getattr(mcp, "tools", {})
    
    assert tools is not None
    
    # Check that key tools are registered
    expected_tools = [
        "a2a_server_registry",
        "list_agents",
        "call_agent",
        "execute_pipeline"
    ]
    
    # Check each expected tool
    for tool_name in expected_tools:
        assert tool_name in tools, f"Tool {tool_name} is not registered"

@pytest.mark.asyncio
async def test_pipeline_progress_reporting(server_state):
    """Test pipeline progress reporting through FastMCP Context."""
    # Import function to test
    from a2a_mcp_server.server import execute_pipeline
    try:
        from a2a_mcp_server.pipeline import PipelineExecutionEngine
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")
    
    # Get a simple pipeline definition
    pipeline_def = {
        "name": "Progress Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2", 
                "agent_name": "agent2", 
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                }
            }
        ],
        "final_outputs": ["node2"]
    }
    
    # Configure registry
    server_state.registry = {"agent1": "http://agent1-url", "agent2": "http://agent2-url"}
    
    # Create a mock client that reports progress
    mock_client = AsyncMock()
    
    # Progress reporting function
    async def progress_stream(message, task_id, session_id):
        mock_stream = AsyncMock()
        
        # Define progress updates based on the agent
        async def stream_updates():
            agent_name = "agent1" if "agent1" in mock_client_class.connect.call_args[0][0] else "agent2"
            progress_steps = [
                (0.2, f"Starting {agent_name}"),
                (0.5, f"Processing with {agent_name}"),
                (1.0, f"Completed {agent_name}")
            ]
            
            for progress, message in progress_steps:
                yield MagicMock(
                    status=MagicMock(
                        state="working" if progress < 1.0 else "completed",
                        message=message,
                        progress=progress
                    ),
                    artifacts=[] if progress < 1.0 else [
                        MagicMock(name="output", parts=[
                            MagicMock(type="text", text=f"Output from {agent_name}")
                        ])
                    ]
                )
                await asyncio.sleep(0.01)
        
        mock_stream.stream_updates.return_value = stream_updates()
        return mock_stream
    
    mock_client.send_message_streaming.side_effect = progress_stream
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Use a custom context to track progress updates
        custom_context = MockContext()
        
        # Execute the pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Test input",
            ctx=custom_context
        )
        
        # Verify execution started
        assert result["status"] == "success"
        
        # Wait for pipeline to complete
        await asyncio.sleep(0.5)  # Give time for all progress updates
        
        # Check that progress reports were sent
        assert custom_context.get_progress_count() >= 4  # At least a few updates
        
        # Check that completion was reported
        assert custom_context.get_completion_count() >= 1
        
        # Check that there were no errors
        assert custom_context.get_error_count() == 0
        
        # Verify progress values were generally increasing
        progress_values = [report["current"] for report in custom_context.progress_reports]
        
        # Progress may not be strictly increasing due to the way pipeline progress is calculated
        # But the final progress should be near 1.0
        assert progress_values[-1] > 0.9