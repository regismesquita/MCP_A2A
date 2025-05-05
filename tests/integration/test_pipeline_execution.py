"""Integration tests for pipeline execution."""

import pytest
import asyncio
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

    agent1_client.send_message_streaming.return_value = agent1_stream
    agent2_client.send_message_streaming.return_value = agent2_stream

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
    agent3_client.send_message_streaming.return_value = MockStreamResponse(
        [MockUpdateObject("completed", "Success", 1.0)], delay_between_updates=0.05
    )
    agent4_client.send_message_streaming.return_value = MockStreamResponse(
        [MockUpdateObject("completed", "Success", 1.0)], delay_between_updates=0.05
    )

    # Configure streaming responses for each agent
    agent1_client.send_message_streaming.return_value = agent1_stream
    agent2_client.send_message_streaming.return_value = agent2_stream

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
    agent1_client.send_message_streaming.return_value = agent1_stream
    agent2_client.send_message_streaming.return_value = agent2_stream
    agent3_client.send_message_streaming.return_value = agent3_stream

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
    input_agent_client.send_message_streaming.return_value = input_stream

    # Configure completion mock response for after the input is sent
    input_completion = MockStatus("completed", "Processed input", 1.0)
    
    # Setup the send_input result with required task and session IDs
    send_input_result = MagicMock()
    send_input_result.status = input_completion
    send_input_result.artifacts = [
        MockArtifact("output", [MockPart("text", "Final output after receiving input")])
    ]
    input_agent_client.send_input.return_value = send_input_result

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
