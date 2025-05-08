"""Integration tests for pipeline execution."""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from typing import Callable, Any, Optional

from tests.fixtures.pipeline_templates import (
    create_simple_pipeline,
    create_complex_pipeline,
    create_error_policy_pipeline,
)
from tests.conftest import (
    MockUpdateObject,
    MockStreamResponse,
    MockArtifact,
    MockPart,
    MockStatus,
)

# Default timeout is 3 seconds with checks every 0.01 seconds for faster tests
DEFAULT_TIMEOUT_SECONDS = 3
DEFAULT_CHECK_INTERVAL = 0.01
MAX_TIMEOUT_SECONDS = 5  # Maximum timeout for complex operations


async def wait_for_condition(
    condition_func: Callable[[], bool],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    check_interval: float = DEFAULT_CHECK_INTERVAL,
    timeout_message: str = "Condition was not met within the timeout period",
    fail_on_timeout: bool = True,
    state_logger: Optional[Callable[[], str]] = None,
) -> bool:
    """
    Wait for a condition to be met with a timeout.

    Args:
        condition_func: A callable that returns True when the condition is met
        timeout_seconds: Maximum time to wait in seconds
        check_interval: Time between condition checks in seconds
        timeout_message: Message to include in the assertion error if timeout occurs
        fail_on_timeout: Whether to fail the test if the condition is not met within timeout
        state_logger: Optional function that returns a string describing the current state

    Returns:
        True if the condition was met, False if timeout occurs and fail_on_timeout is False
    """
    start_time = time.time()
    iterations = 0
    state_logs = []

    # Log initial state if available
    if state_logger:
        initial_state = state_logger()
        state_logs.append(f"Initial state at {start_time:.3f}: {initial_state}")

    for iterations in range(int(timeout_seconds / check_interval)):
        # Check condition
        if condition_func():
            elapsed = time.time() - start_time
            if state_logger:
                state_logs.append(f"Condition met after {elapsed:.3f}s ({iterations} checks)")
                print("\n".join(state_logs))
            return True
        
        # Log state periodically if provided
        if state_logger and iterations % 10 == 0:  # Log every 10 iterations
            current_time = time.time()
            elapsed = current_time - start_time
            state_logs.append(f"State at {elapsed:.3f}s (check {iterations}): {state_logger()}")
            
        await asyncio.sleep(check_interval)

    # If we get here, the condition wasn't met within the timeout
    if state_logger:
        current_time = time.time()
        elapsed = current_time - start_time
        state_logs.append(f"Final state at {elapsed:.3f}s (check {iterations}): {state_logger()}")
        state_log_str = "\n".join(state_logs)
        print(f"Condition not met within {timeout_seconds} seconds. State log:\n{state_log_str}")
    
    if fail_on_timeout:
        detailed_message = timeout_message
        if state_logger:
            detailed_message = f"{timeout_message}\nState log:\n{state_log_str}"
        pytest.fail(detailed_message)
    
    return False


# Helper function to get node status consistently
def get_status_value(obj):
    """
    Get the status value from an object that might have a status as an enum or string.

    Args:
        obj: The object with a status property

    Returns:
        The status value as a string
    """
    if not hasattr(obj, "status"):
        return None

    status = obj.status
    return status.value if hasattr(status, "value") else status


@pytest.mark.asyncio
async def test_pipeline_execution(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test pipeline execution with multiple nodes."""
    # Extract mocks
    mock_client, mock_client_class = mock_a2a_client

    # Import function to test
    from a2a_mcp_server.server import execute_pipeline
    # Import from the pipeline package (not the redundant pipeline.py file)
    from a2a_mcp_server.pipeline import PipelineExecutionEngine, PipelineStatus

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Configure registry
    server_state.registry = {
        "agent1": "http://agent1-url",
        "agent2": "http://agent2-url",
    }

    # Get a simple pipeline definition
    pipeline_def = create_simple_pipeline()

    # Create specific test content for node1
    node1_content = "Content from agent1"

    # Create agent-specific mocks
    agent1_client = AsyncMock()
    agent2_client = AsyncMock()

    # Create success updates for agent1 with specific artifacts
    agent1_updates = [
        # Initial working state
        MockUpdateObject("working", "Starting agent1", 0.2),
        # Progress update
        MockUpdateObject("working", "Processing in agent1", 0.6),
        # Completion with artifacts that will be passed to agent2
        MockUpdateObject(
            "completed",
            "Finished agent1",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", node1_content)])],
        ),
    ]

    # Create success updates for agent2
    agent2_updates = [
        # Initial working state
        MockUpdateObject("working", "Starting agent2", 0.2),
        # Progress update
        MockUpdateObject("working", "Processing in agent2", 0.6),
        # Completion
        MockUpdateObject(
            "completed",
            "Finished agent2",
            1.0,
            artifacts=[
                MockArtifact("output", [MockPart("text", "Output from agent2")])
            ],
        ),
    ]

    # Configure agents with MockStreamResponse instances with appropriate delays
    agent1_stream = MockStreamResponse(agent1_updates, delay_between_updates=0.05)
    agent2_stream = MockStreamResponse(agent2_updates, delay_between_updates=0.05)

    agent1_client.send_message_streaming = AsyncMock(return_value=agent1_stream)
    agent2_client.send_message_streaming = AsyncMock(return_value=agent2_stream)

    # Configure client factory to return agent-specific clients
    def mock_connect_for_pipeline(url, *args, **kwargs):
        if url == "http://agent1-url":
            return agent1_client
        elif url == "http://agent2-url":
            return agent2_client
        return mock_client  # Default case

    # Set up the connect method
    mock_client_class.connect = mock_connect_for_pipeline

    # Create a state logger for debugging
    def get_pipeline_state():
        if 'pipeline_id' not in locals():
            return "Pipeline not yet created"
            
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_key in ["node1", "node2"]:
            if node_key in pipeline_state.nodes:
                node = pipeline_state.nodes[node_key]
                status_info.append(f"{node_key} status: {get_status_value(node)}")
            else:
                status_info.append(f"{node_key}: not found")
                
        status_info.append(f"Agent1 called: {agent1_client.send_message_streaming.call_count}")
        status_info.append(f"Agent2 called: {agent2_client.send_message_streaming.call_count}")
        
        return ", ".join(status_info)

    # Patch server state and A2aMinClient
    with (
        patch("a2a_mcp_server.server.state", server_state),
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
    ):
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def, input_text="Test input", ctx=mock_context
        )

        # Verify execution started successfully
        assert result["status"] == "success", "Pipeline execution should start successfully"
        assert "pipeline_id" in result, "Result should contain pipeline_id"
        pipeline_id = result["pipeline_id"]

        # Verify pipeline was stored
        assert pipeline_id in server_state.pipelines, "Pipeline should be stored in server state"

        # Verify progress reporting occurred
        assert mock_context.report_progress.call_count >= 1, "Progress should be reported"

        # Wait for agent1 to be called
        await wait_for_condition(
            lambda: agent1_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent1 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Get the pipeline state
        pipeline_state = server_state.pipelines[pipeline_id]

        # Wait for agent1 to complete
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node1"]) == "completed",
            timeout_message="Node1 did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for agent2 to be called (should happen after node1 completes)
        await wait_for_condition(
            lambda: agent2_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent2 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for the pipeline to complete
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "completed",
            timeout_message="Pipeline did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Verify both agents were called
        assert agent1_client.send_message_streaming.call_count >= 1, (
            "Agent1 should have been called"
        )
        assert agent2_client.send_message_streaming.call_count >= 1, (
            "Agent2 should have been called"
        )

        # Verify agent1 was given the correct input
        agent1_call_args = agent1_client.send_message_streaming.call_args
        assert agent1_call_args is not None, "Agent1 should have been called with arguments"
        assert "message" in agent1_call_args.kwargs, "Agent1 should have received a message"
        agent1_message = agent1_call_args.kwargs["message"]
        assert hasattr(agent1_message, "parts"), "Agent1 message should have parts"
        assert len(agent1_message.parts) > 0, "Agent1 message should have at least one part"
        assert "Test input" in agent1_message.parts[0].text, "Agent1 should have received pipeline input"

        # Verify input mapping from node1 to node2
        agent2_call_args = agent2_client.send_message_streaming.call_args
        assert agent2_call_args is not None, "Agent2 should have been called with arguments"
        assert "message" in agent2_call_args.kwargs, "Agent2 should have received a message"
        agent2_message = agent2_call_args.kwargs["message"]
        assert hasattr(agent2_message, "parts"), "Agent2 message should have parts"
        assert len(agent2_message.parts) > 0, "Agent2 message should have at least one part"
        assert node1_content in agent2_message.parts[0].text, "Agent2 should have received node1's output"

        # Verify final pipeline and node statuses
        assert get_status_value(pipeline_state) == "completed", "Pipeline should be completed"
        assert get_status_value(pipeline_state.nodes["node1"]) == "completed", "Node1 should be completed"
        assert get_status_value(pipeline_state.nodes["node2"]) == "completed", "Node2 should be completed"


@pytest.mark.asyncio
async def test_pipeline_error_handling(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test pipeline error handling with different policies."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    # Import from the pipeline package (not the redundant pipeline.py file)
    from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus, PipelineStatus, ErrorPolicy

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Create a pipeline definition with multiple error policies
    pipeline_def = create_error_policy_pipeline()

    # Configure registry
    server_state.registry = {
        "agent1": "http://agent1-url",
        "agent2": "http://agent2-url",
        "agent3": "http://agent3-url",
        "agent4": "http://agent4-url",
    }

    # Create agent-specific mocks
    agent1_client = AsyncMock()
    agent2_client = AsyncMock()  # This one will fail
    agent3_client = AsyncMock()
    agent4_client = AsyncMock()

    # Configure success updates for agent1
    agent1_updates = [
        # Initial working state
        MockUpdateObject("working", "Starting agent1", 0.3),
        # Completion with artifact
        MockUpdateObject(
            "completed",
            "Success from agent1",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", "Output content from agent1")])],
        ),
    ]

    # Configure failure updates for agent2
    agent2_updates = [
        # First a working update
        MockUpdateObject("working", "Starting agent2", 0.3),
        # Progress update
        MockUpdateObject("working", "Processing in agent2", 0.5),
        # Then a failure update
        MockUpdateObject("failed", "Agent2 encountered an error", 0.5),
    ]

    # Create MockStreamResponse instances for each agent with appropriate delays
    agent1_stream = MockStreamResponse(agent1_updates, delay_between_updates=0.05)
    agent2_stream = MockStreamResponse(agent2_updates, delay_between_updates=0.05)
    
    # We won't use agent3 or agent4 due to fail_fast policy, but configure them anyway
    agent3_client.send_message_streaming = AsyncMock(return_value=MockStreamResponse(
        [MockUpdateObject("completed", "Success", 1.0)], delay_between_updates=0.05
    ))
    agent4_client.send_message_streaming = AsyncMock(return_value=MockStreamResponse(
        [MockUpdateObject("completed", "Success", 1.0)], delay_between_updates=0.05
    ))

    # Configure streaming responses for each agent
    agent1_client.send_message_streaming = AsyncMock(return_value=agent1_stream)
    agent2_client.send_message_streaming = AsyncMock(return_value=agent2_stream)

    # Configure client factory to return agent-specific clients
    def mock_connect_for_error_test(url, *args, **kwargs):
        if url == "http://agent1-url":
            return agent1_client
        elif url == "http://agent2-url":
            return agent2_client
        elif url == "http://agent3-url":
            return agent3_client
        elif url == "http://agent4-url":
            return agent4_client
        return AsyncMock()  # Default case

    # Set up the connect method
    mock_client_class.connect = mock_connect_for_error_test

    # Create a state logger for debugging
    def get_pipeline_state():
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_key in ["node1", "node2", "node3", "node4"]:
            if node_key in pipeline_state.nodes:
                node = pipeline_state.nodes[node_key]
                status_info.append(f"{node_key} status: {get_status_value(node)}")
            else:
                status_info.append(f"{node_key}: not found")
                
        status_info.append(f"Agent1 called: {agent1_client.send_message_streaming.call_count}")
        status_info.append(f"Agent2 called: {agent2_client.send_message_streaming.call_count}")
        status_info.append(f"Agent3 called: {agent3_client.send_message_streaming.call_count}")
        status_info.append(f"Agent4 called: {agent4_client.send_message_streaming.call_count}")
        
        return ", ".join(status_info)

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def, input_text="Test input", ctx=mock_context
        )

        # Verify execution started successfully
        assert result["status"] == "success", "Pipeline execution should start successfully"
        assert "pipeline_id" in result, "Result should contain pipeline_id"
        pipeline_id = result["pipeline_id"]

        # Verify pipeline was created
        assert pipeline_id in server_state.pipelines, "Pipeline should be stored in server state"
        pipeline_state = server_state.pipelines[pipeline_id]

        # Wait for agent1 to be called and complete
        await wait_for_condition(
            lambda: agent1_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent1 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for node1 to complete
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node1"]) == "completed",
            timeout_message="Node1 did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for agent2 to be called (should happen after node1 completes)
        await wait_for_condition(
            lambda: agent2_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent2 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for node2 to fail
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node2"]) == "failed",
            timeout_message="Node2 did not fail within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for the pipeline to reach a failed state
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "failed",
            timeout_message="Pipeline did not reach failed state within timeout",
            state_logger=get_pipeline_state,
        )

        # Check node status directly
        assert get_status_value(pipeline_state.nodes["node1"]) == "completed", (
            "Node1 should be in completed state"
        )
        assert get_status_value(pipeline_state.nodes["node2"]) == "failed", (
            "Node2 should be in failed state"
        )

        # Verify the fail_fast policy prevented execution of node3 and node4
        assert agent3_client.send_message_streaming.call_count == 0, (
            "Agent3 should not have been called due to fail_fast policy"
        )
        assert agent4_client.send_message_streaming.call_count == 0, (
            "Agent4 should not have been called due to fail_fast policy"
        )
        
        # Verify the error policy on node2 is fail_fast
        node2_def = pipeline_state.definition.get_node_by_id("node2")
        assert "error_policy" in node2_def, "Node2 should have an error_policy defined"
        assert node2_def["error_policy"] == ErrorPolicy.FAIL_FAST.value, (
            "Node2 should have fail_fast error policy"
        )
        
        # Verify the pipeline final status is failed
        assert get_status_value(pipeline_state) == "failed", (
            "Pipeline should be in failed state"
        )


@pytest.mark.asyncio
async def test_pipeline_continue_error_policy(
    server_state, mock_context, mock_a2a_client, cleanup_tasks
):
    """Test 'continue' error policy in pipeline execution."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    # Import from the pipeline package (not the redundant pipeline.py file)
    from a2a_mcp_server.pipeline import (
        PipelineExecutionEngine,
        NodeStatus,
        PipelineStatus,
        ErrorPolicy,
    )

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Create a pipeline with continue error policy for testing
    pipeline_def = {
        "name": "Continue Policy Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2",
                "agent_name": "agent2",
                "error_policy": "continue",  # Critical: this node has continue policy
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
            {
                "id": "node3",
                "agent_name": "agent3",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
        ],
        "final_outputs": ["node2", "node3"],
    }

    # Configure registry
    server_state.registry = {
        "agent1": "http://agent1-url",
        "agent2": "http://agent2-url",
        "agent3": "http://agent3-url",
    }

    # Create agent-specific mocks
    agent1_client = AsyncMock()
    agent2_client = AsyncMock()  # This one will fail
    agent3_client = AsyncMock()

    # Create success updates for agent1 - using longer delay to ensure proper sequencing
    agent1_updates = [
        # Initial working update
        MockUpdateObject("working", "Starting agent1", 0.3),
        # Completion with artifact (required for agent2 and agent3 inputs)
        MockUpdateObject(
            "completed",
            "Success from agent1",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", "Output content from agent1")])],
        ),
    ]

    # Create failure updates for agent2 - ensure it clearly shows failure
    agent2_updates = [
        # First a working update
        MockUpdateObject("working", "Starting agent2", 0.3),
        # Progress update
        MockUpdateObject("working", "Processing in agent2", 0.5),
        # Then a failure update - this is critical for testing the continue policy
        MockUpdateObject("failed", "Agent2 encountered an error", 0.5),
    ]

    # Success updates for agent3 - this should be called due to continue policy
    agent3_updates = [
        # Working state
        MockUpdateObject("working", "Starting agent3", 0.3),
        # Completion
        MockUpdateObject(
            "completed",
            "Success from agent3",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", "Output from agent3")])],
        ),
    ]

    # Configure MockStreamResponse instances for each agent with proper delays
    agent1_stream = MockStreamResponse(agent1_updates, delay_between_updates=0.05)
    agent2_stream = MockStreamResponse(agent2_updates, delay_between_updates=0.05)
    agent3_stream = MockStreamResponse(agent3_updates, delay_between_updates=0.05)

    # Set up the streaming responses
    agent1_client.send_message_streaming = AsyncMock(return_value=agent1_stream)
    agent2_client.send_message_streaming = AsyncMock(return_value=agent2_stream)
    agent3_client.send_message_streaming = AsyncMock(return_value=agent3_stream)

    # Configure client factory to return agent-specific clients
    def mock_connect_for_error_test(url, *args, **kwargs):
        if url == "http://agent1-url":
            return agent1_client
        elif url == "http://agent2-url":
            return agent2_client
        elif url == "http://agent3-url":
            return agent3_client
        return AsyncMock()  # Default case

    # Set up the connect method
    mock_client_class.connect = mock_connect_for_error_test

    # Create a state logger for debugging
    def get_pipeline_state():
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_key in ["node1", "node2", "node3"]:
            if node_key in pipeline_state.nodes:
                node = pipeline_state.nodes[node_key]
                status_info.append(f"{node_key} status: {get_status_value(node)}")
            else:
                status_info.append(f"{node_key}: not found")
                
        status_info.append(f"Agent1 called: {agent1_client.send_message_streaming.call_count}")
        status_info.append(f"Agent2 called: {agent2_client.send_message_streaming.call_count}")
        status_info.append(f"Agent3 called: {agent3_client.send_message_streaming.call_count}")
        
        return ", ".join(status_info)

    # Patch A2aMinClient and server state
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def, input_text="Test input", ctx=mock_context
        )

        # Verify execution started successfully
        assert result["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result["pipeline_id"]

        # Wait for agent1 to be called and complete successfully
        await wait_for_condition(
            lambda: agent1_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent1 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for node1 to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node1"]) == "completed",
            timeout_message="Node1 did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for agent2 to be called (should happen after node1 completes)
        await wait_for_condition(
            lambda: agent2_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent2 was not called within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for node2 to fail
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node2"]) == "failed",
            timeout_message="Node2 did not fail within timeout",
            state_logger=get_pipeline_state,
        )

        # After node2 fails, agent3 should be called due to continue policy
        # This is the critical assertion for this test
        await wait_for_condition(
            lambda: agent3_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent3 was not called despite continue policy",
            state_logger=get_pipeline_state,
        )

        # Verify agent3 was called (validation of continue policy)
        assert agent3_client.send_message_streaming.call_count >= 1, (
            "Agent3 should have been called due to continue policy from node2"
        )

        # Wait for node3 to complete
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["node3"]) == "completed",
            timeout_message="Node3 did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Verify node status directly
        assert get_status_value(pipeline_state.nodes["node1"]) == "completed", (
            "Node1 should be in completed state"
        )
        assert get_status_value(pipeline_state.nodes["node2"]) == "failed", (
            "Node2 should be in failed state"
        )
        assert get_status_value(pipeline_state.nodes["node3"]) == "completed", (
            "Node3 should be in completed state"
        )
        
        # Check that the error policy on node2 is indeed "continue"
        node2_def = pipeline_state.definition.get_node_by_id("node2")
        assert "error_policy" in node2_def, "Node2 should have an error_policy defined"
        assert node2_def["error_policy"] == ErrorPolicy.CONTINUE.value, (
            "Node2 should have continue error policy"
        )
        
        # Verify final pipeline status - should be FAILED since one node failed,
        # but execution continued past the failure
        assert get_status_value(pipeline_state) == "failed", (
            "Pipeline should be in failed state when at least one node fails"
        )


@pytest.mark.asyncio
async def test_pipeline_input_handling(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test handling of input requests within a pipeline."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline, send_pipeline_input
    # Import from the pipeline package (not the redundant pipeline.py file)
    from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus, PipelineStatus

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Create a simple pipeline
    pipeline_def = {
        "name": "Input Handling Pipeline",
        "nodes": [{"id": "node1", "agent_name": "input_agent"}],
        "final_outputs": ["node1"],
    }

    # Configure registry
    server_state.registry = {"input_agent": "http://input-agent-url"}

    # Create mock client that will request input
    input_agent_client = AsyncMock()

    # Generate unique IDs for the test
    mock_task_id = f"task-{uuid.uuid4()}"
    mock_session_id = f"session-{uuid.uuid4()}"

    # Create updates for the input request scenario with proper input-required status
    # Use a longer delay to ensure the state transitions are properly processed
    input_request_updates = [
        # Initial working update
        MockUpdateObject("working", "Starting work", 0.3),
        # Working update with progress
        MockUpdateObject("working", "Processing input", 0.6),
        # Request input - CRITICAL for this test
        MockUpdateObject(
            "input-required", 
            "Need additional information to proceed", 
            0.7,
            # No artifacts here - only requesting input
        ),
    ]

    # Configure a custom stream with longer delays to ensure state transitions are observed
    input_stream = MockStreamResponse(input_request_updates, delay_between_updates=0.1)
    input_agent_client.send_message_streaming = AsyncMock(return_value=input_stream)

    # Configure completion mock response for after the input is sent
    input_completion = MockStatus("completed", "Processed input", 1.0)
    
    # Setup the send_input result with required task and session IDs
    send_input_result = MagicMock()
    send_input_result.status = input_completion
    send_input_result.artifacts = [
        MockArtifact("output", [MockPart("text", "Final output after receiving input")])
    ]
    input_agent_client.send_input = AsyncMock(return_value=send_input_result)

    # Configure client factory to return our mock client
    def mock_connect(url, *args, **kwargs):
        if url == "http://input-agent-url":
            return input_agent_client
        return AsyncMock()  # Default case

    # Set up the connect method
    mock_client_class.connect = mock_connect

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Initial input",
            ctx=mock_context,
        )

        # Verify execution started
        assert result["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result["pipeline_id"]
        node_id = "node1"

        # Create a state logger for more detailed debugging
        def get_pipeline_node_state():
            pipeline_state = server_state.pipelines.get(pipeline_id)
            if not pipeline_state:
                return "Pipeline not found"
                
            node_state = pipeline_state.nodes.get(node_id)
            if not node_state:
                return "Node not found"
                
            status = get_status_value(node_state)
            return (
                f"Pipeline status: {get_status_value(pipeline_state)}, "
                f"Node status: {status}, "
                f"Agent called: {input_agent_client.send_message_streaming.call_count} times"
            )

        # Wait for agent to be called
        await wait_for_condition(
            lambda: input_agent_client.send_message_streaming.call_count >= 1,
            timeout_message="Agent was not called within timeout",
            state_logger=get_pipeline_node_state,
        )

        # Verify the agent was called with the initial input
        assert input_agent_client.send_message_streaming.call_count >= 1, (
            "Agent should have been called at least once"
        )
        
        # Wait for the pipeline node to enter input-required state
        # This should happen naturally as the stream updates are processed
        pipeline_state = server_state.pipelines[pipeline_id]

        # Wait for input-required state
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes[node_id]) == "input-required",
            timeout_message="Node did not enter input-required state as expected",
            state_logger=get_pipeline_node_state,
        )
        
        # At this point, we should have a valid node state with input-required status
        node_state = pipeline_state.nodes[node_id]
        assert get_status_value(node_state) == "input-required", (
            "Node should be in input-required state without manual intervention"
        )

        # Verify task_id and session_id are set correctly
        assert node_state.task_id is not None, "Node should have a task_id assigned"
        assert node_state.session_id is not None, "Node should have a session_id assigned"

        # Verify the overall pipeline status reflects the input-required state
        assert get_status_value(pipeline_state) == "input-required", (
            "Pipeline should be in input-required state"
        )

        # Send input to the pipeline node
        input_result = await send_pipeline_input(
            pipeline_id=pipeline_id,
            node_id=node_id,
            input_text="Additional information",
            ctx=mock_context,
        )

        # Verify the input was sent successfully
        assert input_result["status"] == "success", (
            "send_pipeline_input should return success status"
        )

        # Wait for the send_input method to be called with correct parameters
        await wait_for_condition(
            lambda: input_agent_client.send_input.call_count >= 1,
            timeout_message="Agent's send_input was not called within timeout",
            state_logger=get_pipeline_node_state,
        )

        # Verify send_input was called with the correct parameters
        call_args = input_agent_client.send_input.call_args
        assert call_args is not None, "send_input should have been called"
        
        # Verify parameters
        assert "task_id" in call_args.kwargs, "send_input should be called with task_id"
        assert "session_id" in call_args.kwargs, "send_input should be called with session_id"
        assert "message" in call_args.kwargs, "send_input should be called with message"

        # Verify the message content
        message = call_args.kwargs.get("message")
        assert hasattr(message, "parts") and len(message.parts) > 0, (
            "Message should have parts"
        )
        assert "Additional information" in message.parts[0].text, (
            "Input message should contain the correct text"
        )

        # The node should transition to working or completed state after input
        def node_moved_past_input_required():
            status = get_status_value(pipeline_state.nodes[node_id])
            return status in ["working", "completed"]
        
        # Wait for state transition after input
        await wait_for_condition(
            node_moved_past_input_required,
            timeout_message="Node did not transition to working/completed after input",
            state_logger=get_pipeline_node_state,
        )
        
        # Verify the node is no longer in input-required state
        node_status = get_status_value(node_state)
        assert node_status != "input-required", (
            f"Node should no longer be in input-required state, got {node_status}"
        )
        
        # Verify the pipeline reflects the node's status
        pipeline_status = get_status_value(pipeline_state)
        assert pipeline_status != "input-required", (
            f"Pipeline should no longer be in input-required state, got {pipeline_status}"
        )


@pytest.mark.asyncio
async def test_pipeline_retry_error_policy(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test retry error policy in pipeline execution with automatic retries."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    # Import from the pipeline package (not the redundant pipeline.py file)
    from a2a_mcp_server.pipeline import (
        PipelineExecutionEngine, 
        NodeStatus, 
        PipelineStatus, 
        ErrorPolicy
    )

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Create a pipeline with retry error policy
    pipeline_def = {
        "name": "Retry Policy Pipeline",
        "nodes": [
            {"id": "NodeA", "agent_name": "agentA"},
            {
                "id": "NodeB", 
                "agent_name": "agentB",
                "error_policy": "retry",  # Set retry policy
                "retry_config": {
                    "max_attempts": 2,  # Will try up to 3 times total (initial + 2 retries)
                    "base_delay_seconds": 0.01,  # Small delay for faster tests
                    "backoff_factor": 1.0  # No exponential backoff for predictable testing
                },
                "inputs": {
                    "data": {"source_node": "NodeA", "source_artifact": "output"}
                }
            }
        ],
        "final_outputs": ["NodeB"]
    }

    # Configure registry
    server_state.registry = {
        "agentA": "http://agentA-url",
        "agentB": "http://agentB-url"
    }

    # Create agent-specific mocks
    agentA_client = AsyncMock()
    agentB_client = AsyncMock()

    # Configure success stream for agentA
    agentA_updates = [
        MockUpdateObject("working", "Starting agentA", 0.3),
        MockUpdateObject(
            "completed",
            "Success from agentA",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", "Output from agentA")])]
        )
    ]
    agentA_client.send_message_streaming.return_value = MockStreamResponse(
        agentA_updates, delay_between_updates=0.02
    )

    # For agentB, we'll create a response factory that fails on first call,
    # then succeeds on second call
    def agentB_response_factory(*args, **kwargs):
        # Check the current retry count to determine the response
        if agentB_client.send_message_streaming.call_count == 1:
            # First call - fail with an exception
            raise Exception("Simulated failure in agentB (first attempt)")
        else:
            # Subsequent calls - return success response
            success_updates = [
                MockUpdateObject("working", "Retry attempt working", 0.5),
                MockUpdateObject(
                    "completed",
                    "Successfully completed on retry",
                    1.0,
                    artifacts=[MockArtifact("result", [MockPart("text", "Retry result from agentB")])]
                )
            ]
            return MockStreamResponse(success_updates, delay_between_updates=0.02)

    # Set up the side effect factory
    agentB_client.send_message_streaming.side_effect = agentB_response_factory

    # Configure client factory
    def mock_connect_for_retry_test(url, *args, **kwargs):
        if url == "http://agentA-url":
            return agentA_client
        elif url == "http://agentB-url":
            return agentB_client
        return AsyncMock()  # Default case

    mock_client_class.connect = mock_connect_for_retry_test

    # Mock asyncio.sleep to track delay times
    sleep_calls = []
    
    async def mock_sleep(delay):
        sleep_calls.append(delay)
        # Use a smaller actual delay for faster tests
        await asyncio.sleep(0.01)
    
    # Create a state logger for debugging
    def get_pipeline_state():
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_key in ["NodeA", "NodeB"]:
            if node_key in pipeline_state.nodes:
                node = pipeline_state.nodes[node_key]
                node_status = get_status_value(node)
                retry_attempts = getattr(node, "retry_attempts", 0)
                status_info.append(f"{node_key} status: {node_status}, retries: {retry_attempts}")
            else:
                status_info.append(f"{node_key}: not found")
                
        status_info.append(f"AgentA calls: {agentA_client.send_message_streaming.call_count}")
        status_info.append(f"AgentB calls: {agentB_client.send_message_streaming.call_count}")
        status_info.append(f"Sleep calls: {sleep_calls}")
        
        return ", ".join(status_info)

    # Patch necessary components
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
        patch("asyncio.sleep", mock_sleep)
    ):
        # Execute the pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Retry test input",
            ctx=mock_context
        )

        # Verify execution started
        assert result["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result["pipeline_id"]
        assert pipeline_id is not None, "Pipeline ID should be returned"

        # Wait for NodeA to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["NodeA"]) == "completed",
            timeout_message="NodeA did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for NodeB to be called
        await wait_for_condition(
            lambda: agentB_client.send_message_streaming.call_count >= 1,
            timeout_message="NodeB (agentB) was not called",
            state_logger=get_pipeline_state,
        )

        # Wait for NodeB to fail on first attempt and trigger retry
        await wait_for_condition(
            lambda: hasattr(pipeline_state.nodes["NodeB"], "retry_attempts") 
                  and pipeline_state.nodes["NodeB"].retry_attempts >= 1,
            timeout_message="NodeB did not attempt retry",
            state_logger=get_pipeline_state,
        )

        # Wait for asyncio.sleep to be called for retry delay
        await wait_for_condition(
            lambda: len(sleep_calls) >= 1,
            timeout_message="No sleep calls were made for retry delay",
            state_logger=get_pipeline_state,
        )

        # Verify retry delay matches configuration
        assert sleep_calls[0] == 0.01, f"First retry delay should be 0.01s, got {sleep_calls[0]}s"

        # Wait for NodeB to be called a second time
        await wait_for_condition(
            lambda: agentB_client.send_message_streaming.call_count >= 2,
            timeout_message="NodeB (agentB) was not called a second time for retry",
            state_logger=get_pipeline_state,
        )

        # Wait for pipeline to complete successfully after retry
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "completed",
            timeout_message="Pipeline did not complete after retry",
            state_logger=get_pipeline_state,
        )

        # Verify final states
        assert get_status_value(pipeline_state.nodes["NodeA"]) == "completed", "NodeA should be completed"
        assert get_status_value(pipeline_state.nodes["NodeB"]) == "completed", "NodeB should be completed after retry"
        assert pipeline_state.nodes["NodeB"].retry_attempts == 1, "NodeB should have recorded 1 retry attempt"
        assert agentB_client.send_message_streaming.call_count == 2, "agentB_client should be called twice (initial + retry)"


@pytest.mark.asyncio
async def test_pipeline_retry_exhaustion(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test retry error policy with exhaustion of max retry attempts."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    from a2a_mcp_server.pipeline import (
        PipelineExecutionEngine, 
        NodeStatus, 
        PipelineStatus, 
        ErrorPolicy
    )

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Create a pipeline with retry error policy but with a low max_attempts
    pipeline_def = {
        "name": "Retry Exhaustion Pipeline",
        "nodes": [
            {"id": "NodeA", "agent_name": "agentA"},
            {
                "id": "NodeB", 
                "agent_name": "agentB",
                "error_policy": "retry",  # Set retry policy
                "retry_config": {
                    "max_attempts": 1,  # Will try only once more after initial failure
                    "base_delay_seconds": 0.01,
                    "backoff_factor": 1.0
                },
                "inputs": {
                    "data": {"source_node": "NodeA", "source_artifact": "output"}
                }
            }
        ],
        "final_outputs": ["NodeB"]
    }

    # Configure registry
    server_state.registry = {
        "agentA": "http://agentA-url",
        "agentB": "http://agentB-url"
    }

    # Create agent-specific mocks
    agentA_client = AsyncMock()
    agentB_client = AsyncMock()

    # Configure success stream for agentA
    agentA_updates = [
        MockUpdateObject("working", "Starting agentA", 0.3),
        MockUpdateObject(
            "completed",
            "Success from agentA",
            1.0,
            artifacts=[MockArtifact("output", [MockPart("text", "Output from agentA")])]
        )
    ]
    agentA_client.send_message_streaming.return_value = MockStreamResponse(agentA_updates)

    # For agentB, create a side effect that always fails
    def always_fails(*args, **kwargs):
        raise Exception(f"Simulated failure in agentB (attempt #{agentB_client.send_message_streaming.call_count})")

    agentB_client.send_message_streaming.side_effect = always_fails

    # Configure client factory
    def mock_connect_for_retry_test(url, *args, **kwargs):
        if url == "http://agentA-url":
            return agentA_client
        elif url == "http://agentB-url":
            return agentB_client
        return AsyncMock()

    mock_client_class.connect = mock_connect_for_retry_test

    # Mock asyncio.sleep to track delays
    sleep_calls = []
    
    async def mock_sleep(delay):
        sleep_calls.append(delay)
        # Use a smaller actual delay for faster tests
        await asyncio.sleep(0.005)
    
    # Create a state logger for debugging
    def get_pipeline_state():
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_key in ["NodeA", "NodeB"]:
            if node_key in pipeline_state.nodes:
                node = pipeline_state.nodes[node_key]
                node_status = get_status_value(node)
                retry_attempts = getattr(node, "retry_attempts", 0)
                status_info.append(f"{node_key} status: {node_status}, retries: {retry_attempts}")
            else:
                status_info.append(f"{node_key}: not found")
                
        status_info.append(f"AgentA calls: {agentA_client.send_message_streaming.call_count}")
        status_info.append(f"AgentB calls: {agentB_client.send_message_streaming.call_count}")
        status_info.append(f"Sleep calls: {sleep_calls}")
        
        return ", ".join(status_info)

    # Patch necessary components
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
        patch("asyncio.sleep", mock_sleep)
    ):
        # Execute the pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Retry exhaustion test input",
            ctx=mock_context
        )

        # Verify execution started
        assert result["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result["pipeline_id"]
        assert pipeline_id is not None, "Pipeline ID should be returned"

        # Wait for NodeA to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["NodeA"]) == "completed",
            timeout_message="NodeA did not complete within timeout",
            state_logger=get_pipeline_state,
        )

        # Wait for NodeB to be called initially
        await wait_for_condition(
            lambda: agentB_client.send_message_streaming.call_count >= 1,
            timeout_message="NodeB (agentB) was not called initially",
            state_logger=get_pipeline_state,
        )

        # Wait for retry to be attempted
        await wait_for_condition(
            lambda: agentB_client.send_message_streaming.call_count >= 2,
            timeout_message="NodeB retry was not attempted",
            state_logger=get_pipeline_state,
        )

        # Wait for NodeB to reach max retries and fail
        await wait_for_condition(
            lambda: (hasattr(pipeline_state.nodes["NodeB"], "retry_attempts") and 
                    pipeline_state.nodes["NodeB"].retry_attempts >= 1 and
                    get_status_value(pipeline_state.nodes["NodeB"]) == "failed"),
            timeout_message="NodeB did not reach failed state after max retries",
            state_logger=get_pipeline_state,
        )

        # Wait for pipeline to be marked as failed
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "failed",
            timeout_message="Pipeline did not reach failed state after retry exhaustion",
            state_logger=get_pipeline_state,
        )

        # Verify final states
        assert get_status_value(pipeline_state.nodes["NodeA"]) == "completed", "NodeA should be completed"
        assert get_status_value(pipeline_state.nodes["NodeB"]) == "failed", "NodeB should be failed after retry exhaustion"
        assert pipeline_state.nodes["NodeB"].retry_attempts == 1, "NodeB should have recorded 1 retry attempt"
        assert agentB_client.send_message_streaming.call_count == 2, "agentB_client should be called twice (initial + retry)"
        assert mock_context.error.called, "Context error should be called when pipeline fails"


@pytest.mark.asyncio
async def test_pipeline_input_mapping_scenarios(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test various pipeline input mapping scenarios."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus, PipelineStatus

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # SCENARIO 1: Initial Input Test
    # Define a single-node pipeline
    single_node_pipeline = {
        "name": "Initial Input Test Pipeline",
        "nodes": [
            {"id": "NodeX", "agent_name": "agentX"}
        ],
        "final_outputs": ["NodeX"]
    }

    # Configure registry
    server_state.registry = {
        "agentX": "http://agentX-url",
        "agentP": "http://agentP-url",
        "agentQ": "http://agentQ-url",
        "agentS": "http://agentS-url",
        "agentT": "http://agentT-url"
    }

    # Create agent mocks
    agentX_client = AsyncMock()
    agentP_client = AsyncMock()
    agentQ_client = AsyncMock()
    agentS_client = AsyncMock()
    agentT_client = AsyncMock()

    # Configure success responses for agents
    agentX_updates = [
        MockUpdateObject("working", "Processing initial input", 0.5),
        MockUpdateObject(
            "completed",
            "Successfully processed input",
            1.0,
            artifacts=[
                MockArtifact("result", [MockPart("text", "Processed initial input")])
            ]
        )
    ]
    agentX_client.send_message_streaming.return_value = MockStreamResponse(agentX_updates)

    # Store all captured inputs for verification
    captured_inputs = {}

    # Create a custom side effect to capture the exact input for all agents
    def capture_input(agent_name):
        def _capture_input(*args, **kwargs):
            # Capture the input message
            message = kwargs.get("message")
            captured_inputs[agent_name] = message
            
            # Return appropriate response
            if agent_name == "agentP":
                return MockStreamResponse([
                    MockUpdateObject("working", f"Processing in {agent_name}", 0.5),
                    MockUpdateObject(
                        "completed",
                        f"Completed in {agent_name}",
                        1.0,
                        artifacts=[
                            MockArtifact("art1", [MockPart("text", f"Artifact 1 from {agent_name}")]),
                            MockArtifact("art2", [MockPart("text", f"Artifact 2 from {agent_name}")])
                        ]
                    )
                ])
            elif agent_name == "agentS":
                return MockStreamResponse([
                    MockUpdateObject("working", f"Processing in {agent_name}", 0.5),
                    MockUpdateObject(
                        "completed",
                        f"Completed in {agent_name}",
                        1.0,
                        artifacts=[
                            # Note: Deliberately NOT including "missing_art" to test missing artifact handling
                            MockArtifact("present_art", [MockPart("text", f"Present artifact from {agent_name}")])
                        ]
                    )
                ])
            else:
                # Default response for other agents
                return MockStreamResponse([
                    MockUpdateObject("working", f"Processing in {agent_name}", 0.5),
                    MockUpdateObject(
                        "completed",
                        f"Completed in {agent_name}",
                        1.0,
                        artifacts=[
                            MockArtifact("output", [MockPart("text", f"Output from {agent_name}")])
                        ]
                    )
                ])
        
        return _capture_input

    # Configure all agents to capture inputs
    agentX_client.send_message_streaming.side_effect = capture_input("agentX")
    agentP_client.send_message_streaming.side_effect = capture_input("agentP")
    agentQ_client.send_message_streaming.side_effect = capture_input("agentQ")
    agentS_client.send_message_streaming.side_effect = capture_input("agentS")
    agentT_client.send_message_streaming.side_effect = capture_input("agentT")

    # Configure client factory
    def mock_connect_for_input_tests(url, *args, **kwargs):
        if url == "http://agentX-url":
            return agentX_client
        elif url == "http://agentP-url":
            return agentP_client
        elif url == "http://agentQ-url":
            return agentQ_client
        elif url == "http://agentS-url":
            return agentS_client
        elif url == "http://agentT-url":
            return agentT_client
        return AsyncMock()

    mock_client_class.connect = mock_connect_for_input_tests

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Create a state logger to track pipeline progress
        def get_pipeline_state():
            if 'pipeline_id' not in locals():
                return "Pipeline not yet created"
            
            pipeline_state = server_state.pipelines.get(pipeline_id)
            if not pipeline_state:
                return "Pipeline not found"
            
            return f"Pipeline status: {get_status_value(pipeline_state)}"

        # SCENARIO 1: Test string initial input
        initial_text_input = "test_string"
        result1 = await execute_pipeline(
            pipeline_definition=single_node_pipeline,
            input_text=initial_text_input,
            ctx=mock_context
        )
        
        # Verify execution started
        assert result1["status"] == "success"
        pipeline_id = result1["pipeline_id"]
        
        # Wait for pipeline to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "completed",
            timeout_message="Pipeline (string input) did not complete",
            state_logger=get_pipeline_state,
        )
        
        # Verify agent received the initial input
        assert "agentX" in captured_inputs, "agentX should have received input"
        agent_x_message_text = next(
            (part.text for part in captured_inputs["agentX"].parts 
             if hasattr(part, "text")), 
            None
        )
        assert agent_x_message_text and initial_text_input in agent_x_message_text, (
            f"agentX should have received '{initial_text_input}' in message"
        )
        
        # Clear captured inputs for next test
        captured_inputs.clear()
        
        # SCENARIO 1b: Test dictionary initial input with content field
        initial_dict_input = {"data": {"content": "test_dict_string", "type": "text"}}
        result1b = await execute_pipeline(
            pipeline_definition=single_node_pipeline,
            initial_input=initial_dict_input,  # Using dictionary input
            ctx=mock_context
        )
        
        # Verify execution started
        assert result1b["status"] == "success"
        pipeline_id = result1b["pipeline_id"]
        
        # Wait for pipeline to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "completed",
            timeout_message="Pipeline (dict input) did not complete",
            state_logger=get_pipeline_state,
        )
        
        # Verify agent received the initial input's content
        assert "agentX" in captured_inputs, "agentX should have received input"
        agent_x_message_text = next(
            (part.text for part in captured_inputs["agentX"].parts 
             if hasattr(part, "text")), 
            None
        )
        assert agent_x_message_text and "test_dict_string" in agent_x_message_text, (
            "agentX should have received 'test_dict_string' in message from dictionary input"
        )
        
        # Clear captured inputs for next test
        captured_inputs.clear()
        
        # SCENARIO 2: Implicit All Outputs - NodeP produces two artifacts, NodeQ gets both
        two_node_implicit_pipeline = {
            "name": "Implicit All Outputs Pipeline",
            "nodes": [
                {"id": "NodeP", "agent_name": "agentP"},
                {
                    "id": "NodeQ", 
                    "agent_name": "agentQ",
                    "inputs": {},  # No explicit mapping - should get all artifacts
                    "depends_on": ["NodeP"]  # Explicit dependency to ensure order
                }
            ],
            "final_outputs": ["NodeQ"]
        }
        
        # Execute the implicit inputs pipeline
        result2 = await execute_pipeline(
            pipeline_definition=two_node_implicit_pipeline,
            input_text="Implicit input test",
            ctx=mock_context
        )
        
        # Verify execution started
        assert result2["status"] == "success"
        pipeline_id = result2["pipeline_id"]
        
        # Wait for NodeP to complete
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["NodeP"]) == "completed",
            timeout_message="NodeP did not complete",
        )
        
        # Wait for NodeQ to be called after NodeP
        await wait_for_condition(
            lambda: "agentQ" in captured_inputs,
            timeout_message="NodeQ (agentQ) was not called after NodeP",
        )
        
        # Wait for pipeline to complete
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "completed",
            timeout_message="Pipeline (implicit inputs) did not complete",
        )
        
        # Verify NodeQ received both artifacts from NodeP
        agent_q_message_text = next(
            (part.text for part in captured_inputs["agentQ"].parts 
             if hasattr(part, "text")), 
            None
        )
        assert agent_q_message_text, "agentQ should have received a text message"
        assert "Artifact 1 from agentP" in agent_q_message_text, (
            "agentQ should have received 'Artifact 1 from agentP'"
        )
        assert "Artifact 2 from agentP" in agent_q_message_text, (
            "agentQ should have received 'Artifact 2 from agentP'"
        )
        
        # Clear captured inputs for next test
        captured_inputs.clear()
        
        # SCENARIO 3: Explicit Mapping - Artifact Missing
        missing_artifact_pipeline = {
            "name": "Missing Artifact Pipeline",
            "nodes": [
                {"id": "NodeS", "agent_name": "agentS"},
                {
                    "id": "NodeT", 
                    "agent_name": "agentT",
                    "inputs": {
                        "missing_data": {"source_node": "NodeS", "source_artifact": "missing_art"}
                    }
                }
            ],
            "final_outputs": ["NodeT"]
        }
        
        # Create a log capture to check for warnings about missing artifacts
        warning_logs = []
        
        # Patch logging to capture warnings
        with patch("logging.warning") as mock_warning:
            mock_warning.side_effect = lambda msg, *args, **kwargs: warning_logs.append(msg % args if args else msg)
            
            # Execute the missing artifact pipeline
            result3 = await execute_pipeline(
                pipeline_definition=missing_artifact_pipeline,
                input_text="Missing artifact test",
                ctx=mock_context
            )
            
            # Verify execution started
            assert result3["status"] == "success"
            pipeline_id = result3["pipeline_id"]
            
            # Wait for NodeS to complete
            pipeline_state = server_state.pipelines[pipeline_id]
            await wait_for_condition(
                lambda: get_status_value(pipeline_state.nodes["NodeS"]) == "completed",
                timeout_message="NodeS did not complete",
            )
            
            # Wait for NodeT to be called
            await wait_for_condition(
                lambda: "agentT" in captured_inputs,
                timeout_message="NodeT (agentT) was not called",
            )
            
            # Check the pipeline state - it may complete or fail depending on NodeT's implementation
            try:
                await wait_for_condition(
                    lambda: get_status_value(pipeline_state) in ["completed", "failed"],
                    timeout_message="Pipeline did not reach final state",
                    timeout_seconds=1.0,  # Short timeout as we just want to check final state
                )
            except:
                # It's okay if this times out, we're mainly concerned with the input mapping
                pass
            
            # Verify warning was logged about missing artifact
            assert any("missing_art" in log for log in warning_logs), (
                "A warning should be logged about the missing artifact 'missing_art'"
            )
            
            # Verify NodeT received something for the missing artifact input
            # (could be empty, placeholder, or error message - implementation dependent)
            agent_t_message_text = next(
                (part.text for part in captured_inputs["agentT"].parts 
                 if hasattr(part, "text")), 
                None
            )
            assert agent_t_message_text is not None, "agentT should have received some message"
            
            # Verify that the present artifact was NOT implicitly passed through
            # This is critical for testing explicit mapping behavior - only mapped artifacts should be included
            assert "Present artifact from agentS" not in agent_t_message_text, (
                "agentT should NOT have received the present_art artifact that wasn't explicitly mapped"
            )


@pytest.mark.asyncio
async def test_pipeline_node_execution_robustness(server_state, mock_context, mock_a2a_client, cleanup_tasks):
    """Test pipeline node execution robustness with error handling and timeouts."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus, PipelineStatus

    try:
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")

    # Define a simple pipeline for testing unexpected errors
    error_pipeline = {
        "name": "Error Handling Pipeline",
        "nodes": [{"id": "error_node", "agent_name": "error_agent"}],
        "final_outputs": ["error_node"]
    }

    # Define a pipeline with timeout for testing timeout handling
    timeout_pipeline = {
        "name": "Timeout Handling Pipeline",
        "nodes": [{
            "id": "timeout_node", 
            "agent_name": "timeout_agent",
            "timeout": 0.1  # Very short timeout for testing
        }],
        "final_outputs": ["timeout_node"]
    }

    # Configure registry
    server_state.registry = {
        "error_agent": "http://error-agent-url",
        "timeout_agent": "http://timeout-agent-url"
    }

    # Create agent mocks
    error_agent_client = AsyncMock()
    timeout_agent_client = AsyncMock()

    # Configure error agent to raise an unexpected ValueError
    error_agent_client.send_message_streaming.side_effect = ValueError("Unexpected error in agent")

    # Configure timeout agent to sleep longer than its timeout
    async def timeout_response(*args, **kwargs):
        # Sleep for longer than the node's timeout
        await asyncio.sleep(0.5)
        # This should never be reached in the test
        return MockStreamResponse([
            MockUpdateObject("completed", "This should timeout before completing", 1.0)
        ])
    
    timeout_agent_client.send_message_streaming.side_effect = timeout_response

    # Configure client factory
    def mock_connect_for_robustness_test(url, *args, **kwargs):
        if url == "http://error-agent-url":
            return error_agent_client
        elif url == "http://timeout-agent-url":
            return timeout_agent_client
        return AsyncMock()

    mock_client_class.connect = mock_connect_for_robustness_test

    # Create a state logger for debugging
    def get_pipeline_state():
        pipeline_state = server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            return "Pipeline not found"
            
        status_info = []
        status_info.append(f"Pipeline status: {get_status_value(pipeline_state)}")
        
        for node_id in pipeline_state.nodes:
            node = pipeline_state.nodes[node_id]
            node_status = get_status_value(node)
            status_info.append(f"{node_id} status: {node_status}")
                
        return ", ".join(status_info)

    # Patch necessary components
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # PART 1: Test unexpected ValueError handling
        # Execute the error pipeline
        result1 = await execute_pipeline(
            pipeline_definition=error_pipeline,
            input_text="Error test input",
            ctx=mock_context
        )

        # Verify execution started
        assert result1["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result1["pipeline_id"]

        # Wait for error_node to fail due to ValueError
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["error_node"]) == "failed",
            timeout_message="error_node did not reach failed state as expected",
            state_logger=get_pipeline_state,
        )

        # Wait for pipeline to be marked as failed
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "failed",
            timeout_message="Pipeline did not reach failed state after node error",
            state_logger=get_pipeline_state,
        )

        # Verify error was reported to context
        assert mock_context.error.called, "Context error should be called when pipeline fails"
        
        # Reset context for next test
        mock_context.reset_mock()

        # PART 2: Test timeout handling
        # Execute the timeout pipeline
        result2 = await execute_pipeline(
            pipeline_definition=timeout_pipeline,
            input_text="Timeout test input",
            ctx=mock_context
        )

        # Verify execution started
        assert result2["status"] == "success", "Pipeline execution should start successfully"
        pipeline_id = result2["pipeline_id"]

        # Wait for timeout_node to fail due to timeout
        pipeline_state = server_state.pipelines[pipeline_id]
        await wait_for_condition(
            lambda: get_status_value(pipeline_state.nodes["timeout_node"]) == "failed",
            timeout_message="timeout_node did not reach failed state after timeout",
            state_logger=get_pipeline_state,
            timeout_seconds=1.0,  # Increase timeout for this check to ensure node has time to timeout
        )

        # Wait for pipeline to be marked as failed
        await wait_for_condition(
            lambda: get_status_value(pipeline_state) == "failed",
            timeout_message="Pipeline did not reach failed state after node timeout",
            state_logger=get_pipeline_state,
        )

        # Verify timeout error was reported to context
        assert mock_context.error.called, "Context error should be called when pipeline times out"
        error_msg = mock_context.error.call_args[0][0].lower()
        assert "timeout" in error_msg or "timed out" in error_msg, (
            f"Error message should mention timeout, got: {error_msg}"
        )
