"""Integration tests for FastMCP integration."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.fixtures.mock_context import MockContext
from fastmcp import Context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_issue",
    [
        None,  # No issue - normal behavior
        "report_progress_none",  # report_progress is None
        "complete_none",  # complete is None
        "report_progress_raises",  # report_progress raises Exception
        "complete_raises",  # complete raises Exception
        "report_progress_non_callable",  # report_progress is not a callable
    ],
)
async def test_context_progress_reporting(server_state, mock_context, context_issue):
    """Test progress reporting through FastMCP Context, including robust error handling."""
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
            (1.0, "Complete"),
        ]

        for progress, message in progress_steps:
            yield MagicMock(
                status=MagicMock(
                    state="working" if progress < 1.0 else "completed",
                    message=message,
                    progress=progress,
                ),
                artifacts=[]
                if progress < 1.0
                else [
                    MagicMock(
                        name="result",
                        parts=[MagicMock(type="text", text="Final result")],
                    )
                ],
            )
            await asyncio.sleep(0.01)  # Small delay to simulate real timing

    # stream_updates should be a synchronous method that returns an async generator
    stream_response.stream_updates = progress_stream
    mock_client.send_message_streaming.return_value = stream_response

    # Use a custom context to track progress updates
    custom_context = MockContext()
    
    # Configure context issues based on test parameters
    if context_issue == "report_progress_none":
        custom_context.report_progress = None
    elif context_issue == "complete_none":
        custom_context.complete = None
    elif context_issue == "report_progress_raises":
        custom_context.report_progress.side_effect = Exception("Simulated report_progress failure")
    elif context_issue == "complete_raises":
        custom_context.complete.side_effect = Exception("Simulated complete failure")
    elif context_issue == "report_progress_non_callable":
        custom_context.report_progress = 123  # Not a callable

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient") as mock_client_class,
        patch("a2a_mcp_server.server.state", server_state),
    ):
        mock_client_class.connect.return_value = mock_client

        # Call the agent
        result = await call_agent(
            agent_name="test-agent", prompt="Test prompt", ctx=custom_context
        )

        # Verify - should complete without exception even with context issues
        assert result["status"] == "success"
        
        # Normal behavior checks for normal case
        if context_issue is None:
            # Check that progress reports were sent to the context
            assert custom_context.get_progress_count() >= 3
            
            # Check that the final progress is complete
            assert custom_context.get_completion_count() == 1
            
            # Check that there were no errors
            assert custom_context.get_error_count() == 0
            
            # Verify progress values were increasing
            progress_values = [
                report["current"] for report in custom_context.progress_reports
            ]
            assert all(b >= a for a, b in zip(progress_values, progress_values[1:]))
            
            # Verify completion message
            assert "Complete" in custom_context.completions[0]["message"]
            
            # Verify that report_progress was called with a single float argument
            # representing the progress
            for call_args in custom_context.report_progress.call_args_list:
                # Extract args and kwargs
                args, kwargs = call_args
                # First argument should be a float
                assert isinstance(args[0], float), f"Progress should be a float, got {type(args[0])}"
                assert 0.0 <= args[0] <= 1.0, f"Progress should be between 0.0 and 1.0, got {args[0]}"
        
        # For error cases, just verify they didn't crash the execution
        # We're not testing specific behaviors because that would be too implementation-specific
        # and would make the tests brittle


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_issue",
    [
        None,  # No issue - normal behavior
        "error_none",  # error method is None
        "error_raises",  # error method raises Exception
        "error_non_callable",  # error method is not a callable
    ],
)
async def test_context_error_reporting(server_state, context_issue):
    """Test error reporting through FastMCP Context with robust error handling."""
    # Import function to test
    from a2a_mcp_server.server import call_agent

    # Configure registry
    server_state.registry = {"error-agent": "http://error-agent-url"}

    # Create a mock client that fails
    mock_client = AsyncMock()
    mock_client.send_message_streaming.side_effect = Exception("Simulated agent error")

    # Use a custom context to track error updates
    custom_context = MockContext()
    
    # Configure context issues based on test parameter
    if context_issue == "error_none":
        custom_context.error = None
    elif context_issue == "error_raises":
        custom_context.error.side_effect = Exception("Simulated error method failure")
    elif context_issue == "error_non_callable":
        custom_context.error = 123  # Not a callable

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient") as mock_client_class,
        patch("a2a_mcp_server.server.state", server_state),
    ):
        mock_client_class.connect.return_value = mock_client

        # Call the agent
        result = await call_agent(
            agent_name="error-agent", prompt="Test prompt", ctx=custom_context
        )

        # Verify - all cases should complete without exception
        assert result["status"] == "error"
        
        # For normal case, verify detailed error reporting
        if context_issue is None:
            # Check that error was reported to the context
            assert custom_context.get_error_count() >= 1
            
            # Verify error message
            assert "error" in custom_context.errors[0]["message"].lower()
            assert "agent" in custom_context.errors[0]["message"].lower()


# This test was replaced by the parametrized version above.


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
        "execute_pipeline",
    ]

    # Check each expected tool
    for tool_name in expected_tools:
        assert tool_name in tools, f"Tool {tool_name} is not registered"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context_issue",
    [
        None,  # No issue - normal behavior
        "report_progress_none",  # report_progress is None
        "complete_none",  # complete is None
    ],
)
async def test_pipeline_progress_reporting(server_state, context_issue):
    """Test pipeline progress reporting through FastMCP Context with robust error handling."""
    # Import function to test
    from a2a_mcp_server.server import execute_pipeline

    try:
        # Import from the pipeline package (not the redundant pipeline.py file)
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
                },
            },
        ],
        "final_outputs": ["node2"],
    }

    # Configure registry
    server_state.registry = {
        "agent1": "http://agent1-url",
        "agent2": "http://agent2-url",
    }

    # Create a mock client that reports progress
    mock_client = AsyncMock()

    # Progress reporting function
    async def progress_stream(message, task_id, session_id):
        mock_stream = AsyncMock()

        # Define progress updates based on the agent
        async def stream_updates():
            agent_name = (
                "agent1"
                if "agent1" in mock_client_class.connect.call_args[0][0]
                else "agent2"
            )
            progress_steps = [
                (0.2, f"Starting {agent_name}"),
                (0.5, f"Processing with {agent_name}"),
                (1.0, f"Completed {agent_name}"),
            ]

            for progress, message in progress_steps:
                yield MagicMock(
                    status=MagicMock(
                        state="working" if progress < 1.0 else "completed",
                        message=message,
                        progress=progress,
                    ),
                    artifacts=[]
                    if progress < 1.0
                    else [
                        MagicMock(
                            name="output",
                            parts=[
                                MagicMock(type="text", text=f"Output from {agent_name}")
                            ],
                        )
                    ],
                )
                await asyncio.sleep(0.01)

        # stream_updates should be a synchronous method that returns an async generator
        mock_stream.stream_updates = stream_updates
        return mock_stream

    mock_client.send_message_streaming.side_effect = progress_stream

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient") as mock_client_class,
        patch("a2a_mcp_server.server.state", server_state),
    ):
        mock_client_class.connect.return_value = mock_client

        # Use a custom context to track progress updates
        custom_context = MockContext()
        
        # Configure context issues based on test parameters
        if context_issue == "report_progress_none":
            custom_context.report_progress = None
        elif context_issue == "complete_none":
            custom_context.complete = None

        # Execute the pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Test input",
            ctx=custom_context,
        )

        # Verify execution started
        assert result["status"] == "success", "Pipeline execution should start successfully regardless of context issues"

        # Wait for pipeline to complete
        await asyncio.sleep(0.5)  # Give time for all progress updates

        # For normal case, verify detailed behavior
        if context_issue is None:
            # Check that progress reports were sent
            assert custom_context.get_progress_count() >= 4, "Should have at least 4 progress updates"

            # Check that completion was reported
            assert custom_context.get_completion_count() >= 1, "Should have at least 1 completion"

            # Check that there were no errors
            assert custom_context.get_error_count() == 0, "Should have no errors in normal operation"

            # Verify progress values were generally increasing
            progress_values = [
                report["current"] for report in custom_context.progress_reports
            ]

            # Progress may not be strictly increasing due to the way pipeline progress is calculated
            # But the final progress should be near 1.0
            assert progress_values[-1] > 0.9, "Final progress should be near 1.0"
            
            # Verify that report_progress was called with a single float argument
            # representing the overall progress
            for call_args in custom_context.report_progress.call_args_list:
                # Extract args 
                args, kwargs = call_args
                # First argument should be a float progress value
                assert isinstance(args[0], float), f"Progress should be a float, got {type(args[0])}"
                assert 0.0 <= args[0] <= 1.0, f"Progress should be between 0.0 and 1.0, got {args[0]}"
        
        # For error cases, just verify they didn't crash the execution
        # We're only testing that the code handles these errors gracefully
