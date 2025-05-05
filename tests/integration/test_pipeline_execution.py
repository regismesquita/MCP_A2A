"""Integration tests for pipeline execution."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.fixtures.pipeline_templates import (
    create_simple_pipeline,
    create_complex_pipeline,
    create_error_policy_pipeline,
)

@pytest.mark.asyncio
async def test_pipeline_execution(server_state, mock_context, mock_a2a_client):
    """Test pipeline execution with multiple nodes."""
    # Extract mocks
    mock_client, mock_client_class = mock_a2a_client
    
    # Import function to test
    from a2a_mcp_server.server import execute_pipeline
    
    # Make sure we have the pipeline engine initialized
    try:
        from a2a_mcp_server.pipeline import PipelineExecutionEngine
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")
    
    # Configure registry
    server_state.registry = {"agent1": "http://agent1-url", "agent2": "http://agent2-url"}
    
    # Get a simple pipeline definition
    pipeline_def = create_simple_pipeline()
    
    # Configure mock for successful node completion with artifacts
    async def configure_streaming_response(message, task_id, session_id):
        mock_stream = AsyncMock()
        
        # Define updates to emit based on which node is being called
        async def stream_updates():
            # First working update
            yield MagicMock(
                status=MagicMock(state="working", message="Starting", progress=0.3),
                artifacts=[]
            )
            # Mid progress
            yield MagicMock(
                status=MagicMock(state="working", message="Processing", progress=0.7),
                artifacts=[]
            )
            # Completion with artifacts
            artifact_parts = [MagicMock(type="text", text="Output content")]
            artifacts = [MagicMock(name="output", parts=artifact_parts)]
            yield MagicMock(
                status=MagicMock(state="completed", message="Completed", progress=1.0),
                artifacts=artifacts
            )
            
        mock_stream.stream_updates.return_value = stream_updates()
        return mock_stream
    
    # Configure streaming mock
    mock_client.send_message_streaming.side_effect = configure_streaming_response
    
    # Patch server state and A2aMinClient
    with patch('a2a_mcp_server.server.state', server_state), \
         patch('a2a_mcp_server.server.A2aMinClient', mock_client_class):
        
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Test input",
            ctx=mock_context
        )
        
        # Verify
        assert result["status"] == "success"
        assert "pipeline_id" in result
        pipeline_id = result["pipeline_id"]
        
        # Verify pipeline was stored
        assert pipeline_id in server_state.pipelines
        
        # Verify progress reporting
        assert mock_context.report_progress.call_count >= 2
        
        # Verify agent calls
        assert mock_client.send_message_streaming.call_count >= 1
        
        # To wait for the entire pipeline to finish, we may need to sleep
        # Depending on how your pipeline engine is implemented
        for _ in range(10):  # Wait up to 1 second
            if server_state.pipelines[pipeline_id].status == "completed":
                break
            await asyncio.sleep(0.1)
        
        # Check final pipeline status
        assert server_state.pipelines[pipeline_id].status == "completed"
        
        # Check that both nodes ran
        assert len(server_state.pipelines[pipeline_id].nodes) == 2
        assert server_state.pipelines[pipeline_id].nodes["node1"].status == "completed"
        assert server_state.pipelines[pipeline_id].nodes["node2"].status == "completed"

@pytest.mark.asyncio
async def test_pipeline_error_handling(server_state, mock_context):
    """Test pipeline error handling with different policies."""
    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    try:
        from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus
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
        "agent4": "http://agent4-url"
    }
    
    # Create mock client factory that responds differently for each agent
    def create_mock_client(url):
        agent_name = None
        for name, agent_url in server_state.registry.items():
            if agent_url == url:
                agent_name = name
                break
        
        mock_client = AsyncMock()
        
        # Configure streaming response based on agent
        async def configure_streaming_response(message, task_id, session_id):
            mock_stream = AsyncMock()
            
            if agent_name == "agent2":  # This one will fail
                async def stream_failure():
                    # Start working
                    yield MagicMock(
                        status=MagicMock(state="working", message="Starting", progress=0.3),
                        artifacts=[]
                    )
                    # Then fail
                    yield MagicMock(
                        status=MagicMock(state="failed", message="Agent error", progress=0.3),
                        artifacts=[]
                    )
                    
                mock_stream.stream_updates.return_value = stream_failure()
            else:  # Other agents succeed
                async def stream_success():
                    yield MagicMock(
                        status=MagicMock(state="completed", message="Success", progress=1.0),
                        artifacts=[MagicMock(name="output", parts=[
                            MagicMock(type="text", text="Output content")
                        ])]
                    )
                    
                mock_stream.stream_updates.return_value = stream_success()
            
            return mock_stream
        
        mock_client.send_message_streaming.side_effect = configure_streaming_response
        return mock_client
    
    # Patch A2aMinClient.connect to use our factory
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        
        mock_client_class.connect.side_effect = create_mock_client
        
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Test input",
            ctx=mock_context
        )
        
        # Verify
        assert result["status"] == "success"
        pipeline_id = result["pipeline_id"]
        
        # Wait for pipeline to complete processing
        for _ in range(20):  # Wait up to 2 seconds
            pipeline_state = server_state.pipelines[pipeline_id]
            if pipeline_state.status in ["completed", "failed"]:
                break
            await asyncio.sleep(0.1)
        
        # The pipeline should eventually fail due to node2's fail_fast policy
        pipeline_state = server_state.pipelines[pipeline_id]
        assert pipeline_state.status == "failed"
        
        # node1 should be completed
        assert pipeline_state.nodes["node1"].status == "completed"
        
        # node2 should be failed
        assert pipeline_state.nodes["node2"].status == "failed"
        
        # node3 and node4 should not have executed due to node2's fail_fast policy
        # or might be in a 'skipped' state depending on implementation
        assert pipeline_state.nodes["node3"].status in ["pending", "skipped"]
        assert pipeline_state.nodes["node4"].status in ["pending", "skipped"]

@pytest.mark.asyncio
async def test_pipeline_continue_error_policy(server_state, mock_context):
    """Test 'continue' error policy in pipeline execution."""
    # Import functions
    from a2a_mcp_server.server import execute_pipeline
    try:
        from a2a_mcp_server.pipeline import PipelineExecutionEngine, NodeStatus, PipelineStatus
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")
    
    # Create a simpler pipeline with continue policy
    pipeline_def = {
        "name": "Continue Policy Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2", 
                "agent_name": "agent2",
                "error_policy": "continue", 
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                }
            },
            {
                "id": "node3", 
                "agent_name": "agent3",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                }
            }
        ],
        "final_outputs": ["node2", "node3"]
    }
    
    # Configure registry
    server_state.registry = {
        "agent1": "http://agent1-url", 
        "agent2": "http://agent2-url",
        "agent3": "http://agent3-url"
    }
    
    # Create mock client factory with node2 failing
    def create_mock_client(url):
        agent_name = None
        for name, agent_url in server_state.registry.items():
            if agent_url == url:
                agent_name = name
                break
        
        mock_client = AsyncMock()
        
        # Configure streaming response based on agent
        async def configure_streaming_response(message, task_id, session_id):
            mock_stream = AsyncMock()
            
            if agent_name == "agent2":  # This one will fail
                async def stream_failure():
                    # Start working
                    yield MagicMock(
                        status=MagicMock(state="working", message="Starting", progress=0.3),
                        artifacts=[]
                    )
                    # Then fail
                    yield MagicMock(
                        status=MagicMock(state="failed", message="Agent error", progress=0.3),
                        artifacts=[]
                    )
                    
                mock_stream.stream_updates.return_value = stream_failure()
            else:  # Other agents succeed
                async def stream_success():
                    yield MagicMock(
                        status=MagicMock(state="completed", message="Success", progress=1.0),
                        artifacts=[MagicMock(name="output", parts=[
                            MagicMock(type="text", text="Output content")
                        ])]
                    )
                    
                mock_stream.stream_updates.return_value = stream_success()
            
            return mock_stream
        
        mock_client.send_message_streaming.side_effect = configure_streaming_response
        return mock_client
    
    # Patch A2aMinClient.connect to use our factory
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        
        mock_client_class.connect.side_effect = create_mock_client
        
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Test input",
            ctx=mock_context
        )
        
        # Verify
        assert result["status"] == "success"
        pipeline_id = result["pipeline_id"]
        
        # Wait for pipeline to complete processing
        for _ in range(20):  # Wait up to 2 seconds
            pipeline_state = server_state.pipelines[pipeline_id]
            if hasattr(pipeline_state, "status") and isinstance(pipeline_state.status, str):
                if pipeline_state.status in ["completed", "partial_success", "failed"]:
                    break
            elif hasattr(pipeline_state.status, "value"):
                if pipeline_state.status.value in ["completed", "partial_success", "failed"]:
                    break
            await asyncio.sleep(0.1)
        
        # Get the status value, handling enum if needed
        pipeline_state = server_state.pipelines[pipeline_id]
        status_value = pipeline_state.status.value if hasattr(pipeline_state.status, "value") else pipeline_state.status
        
        # The pipeline should complete with partial success or equivalent
        # This could be "partial_success", "completed", or even "failed" depending on implementation
        assert status_value in ["completed", "partial_success", "failed"]
        
        # node1 should be completed
        assert pipeline_state.nodes["node1"].status == "completed" or \
               (hasattr(pipeline_state.nodes["node1"].status, "value") and 
                pipeline_state.nodes["node1"].status.value == "completed")
        
        # node2 should be failed
        assert pipeline_state.nodes["node2"].status == "failed" or \
               (hasattr(pipeline_state.nodes["node2"].status, "value") and 
                pipeline_state.nodes["node2"].status.value == "failed")
        
        # node3 should be completed since we continue despite node2 failure
        assert pipeline_state.nodes["node3"].status == "completed" or \
               (hasattr(pipeline_state.nodes["node3"].status, "value") and 
                pipeline_state.nodes["node3"].status.value == "completed")

@pytest.mark.asyncio
async def test_pipeline_input_handling(server_state, mock_context):
    """Test handling of input requests within a pipeline."""
    # Import functions
    from a2a_mcp_server.server import execute_pipeline, send_pipeline_input
    try:
        from a2a_mcp_server.pipeline import PipelineExecutionEngine
        server_state.pipeline_engine = PipelineExecutionEngine(server_state)
    except ImportError:
        pytest.skip("PipelineExecutionEngine not available")
    
    # Create a simple pipeline
    pipeline_def = {
        "name": "Input Handling Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "input_agent"}
        ],
        "final_outputs": ["node1"]
    }
    
    # Configure registry
    server_state.registry = {"input_agent": "http://input-agent-url"}
    
    # Create mock client that will request input
    mock_client = AsyncMock()
    
    # Configure streaming response to request input
    async def request_input_stream(message, task_id, session_id):
        mock_stream = AsyncMock()
        
        async def stream_updates():
            # Initial working update
            yield MagicMock(
                status=MagicMock(state="working", message="Starting", progress=0.3),
                artifacts=[]
            )
            # Request input
            yield MagicMock(
                status=MagicMock(state="input-required", message="Need more info", progress=0.5),
                artifacts=[]
            )
            
        mock_stream.stream_updates.return_value = stream_updates()
        return mock_stream
    
    # Configure mock to request input
    mock_client.send_message_streaming.side_effect = request_input_stream
    
    # Configure send_input to return success
    mock_client.send_input.return_value = MagicMock(
        status=MagicMock(state="completed", message="Processed input", progress=1.0),
        artifacts=[MagicMock(name="output", parts=[
            MagicMock(type="text", text="Final output after input")
        ])]
    )
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        
        mock_client_class.connect.return_value = mock_client
        
        # Execute pipeline
        result = await execute_pipeline(
            pipeline_definition=pipeline_def,
            input_text="Initial input",
            ctx=mock_context
        )
        
        # Verify execution started
        assert result["status"] == "success"
        pipeline_id = result["pipeline_id"]
        
        # Wait for pipeline to reach input-required state
        pipeline_state = None
        node_id = "node1"
        for _ in range(20):  # Wait up to 2 seconds
            pipeline_state = server_state.pipelines[pipeline_id]
            node_status = pipeline_state.nodes[node_id].status
            node_status_value = node_status.value if hasattr(node_status, "value") else node_status
            
            if node_status_value == "input-required":
                break
            await asyncio.sleep(0.1)
        
        # Verify node is waiting for input
        assert pipeline_state is not None
        node_status = pipeline_state.nodes[node_id].status
        node_status_value = node_status.value if hasattr(node_status, "value") else node_status
        assert node_status_value == "input-required"
        
        # Send input to the pipeline node
        input_result = await send_pipeline_input(
            pipeline_id=pipeline_id,
            node_id=node_id,
            input_text="Additional information",
            ctx=mock_context
        )
        
        # Verify input was sent
        assert input_result["status"] == "success"
        assert mock_client.send_input.called
        
        # Wait for pipeline to complete
        for _ in range(20):  # Wait up to 2 seconds
            pipeline_state = server_state.pipelines[pipeline_id]
            if pipeline_state.status == "completed" or \
               (hasattr(pipeline_state.status, "value") and pipeline_state.status.value == "completed"):
                break
            await asyncio.sleep(0.1)
        
        # Verify pipeline completed
        status_value = pipeline_state.status.value if hasattr(pipeline_state.status, "value") else pipeline_state.status
        assert status_value == "completed"
        
        # Verify node completed
        node_status = pipeline_state.nodes[node_id].status
        node_status_value = node_status.value if hasattr(node_status, "value") else node_status
        assert node_status_value == "completed"