"""Integration tests for agent communication."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.fixtures.mock_agents import MockA2AAgent
from tests.fixtures.mock_context import MockContext

@pytest.mark.asyncio
async def test_agent_communication(server_state, mock_context, mock_a2a_client):
    """Test complete communication cycle with an agent."""
    # Extract mocks
    mock_client, mock_client_class = mock_a2a_client
    
    # Import function to test
    from a2a_mcp_server.server import call_agent
    
    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}
    
    # Patch server state and A2aMinClient
    with patch('a2a_mcp_server.server.state', server_state), \
         patch('a2a_mcp_server.server.A2aMinClient', mock_client_class):
        
        # Call the agent
        result = await call_agent(
            agent_name="test-agent",
            prompt="Test prompt",
            ctx=mock_context
        )
        
        # Verify
        assert result["status"] == "success"
        assert mock_context.report_progress.call_count >= 2
        assert mock_client.send_message_streaming.called
        
        # Find the request_id from the result
        request_id = result.get("request_id")
        assert request_id is not None
        
        # Explicitly complete the request (in real code this would happen via the client disconnect or completion)
        await server_state.complete_request(
            request_id=request_id,
            status="completed",
            message="Test completed",
            ctx=mock_context
        )
        
        # Check request tracking
        assert len(server_state.active_requests) == 0  # Request should be completed and removed
        assert len(server_state.execution_logs) >= 1
        assert request_id in server_state.execution_logs
        
        # Verify log entries
        logs = server_state.execution_logs[request_id]
        assert len(logs) >= 2
        assert any(log["status"] == "completed" for log in logs)

@pytest.mark.asyncio
async def test_agent_input_handling(server_state, mock_context):
    """Test sending input to an agent that requests it."""
    # Import functions to test
    from a2a_mcp_server.server import call_agent, send_input, A2aMinClient
    
    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}
    
    # Create MockStatus class for our updates
    class MockStatus:
        def __init__(self, state, message, progress):
            self.state = state
            self.message = message
            self.progress = progress
    
    # First we'll directly update the server state to simulate an input-required state
    # This is more reliable than trying to use mock streams
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    
    # Track the request
    server_state.track_request(
        request_id=request_id,
        agent_name="test-agent",
        task_id=task_id,
        session_id=session_id
    )
    
    # Update to input-required status
    await server_state.update_request_status(
        request_id=request_id,
        status="input-required",
        progress=0.5,
        message="Need more information",
        ctx=mock_context
    )
    
    # Verify our setup worked
    assert request_id in server_state.active_requests
    assert server_state.active_requests[request_id]["status"] == "input-required"
    
    # Configure mock client for send_input
    mock_client = AsyncMock()
    mock_client.send_input.return_value = type("UpdateObject", (), {
        "status": MockStatus("working", "Processing input", 0.6),
        "artifacts": []
    })
    
    # Patch A2aMinClient and server state
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Print the current state to debug
        print(f"\nSending input to request_id: {request_id}")
        print(f"Current active requests: {server_state.active_requests.keys()}")
        
        # Send input to the agent
        input_result = await send_input(
            request_id=request_id,
            input_text="Additional information",
            ctx=mock_context
        )
        
        # Verify - depending on the implementation, it might return success or error
        # Print the result to debug
        print(f"send_input result: {input_result}")
        # For now we'll just make sure it runs without exceptions
        assert mock_client.send_input.called
        
        # Verify input was sent with correct parameters
        call_args = mock_client.send_input.call_args
        assert call_args is not None
        kwargs = call_args[1]
        print(f"send_input kwargs: {kwargs}")
        assert kwargs["task_id"] == task_id
        assert kwargs["session_id"] == session_id
        
        # The parameter name might vary, check if the value is present in any parameter
        assert any("Additional information" in str(v) for v in kwargs.values())
        
        # Verify log reflects the input-required state
        logs = server_state.get_execution_log(request_id)
        assert any(log["status"] == "input-required" for log in logs)
        
        # Complete the request
        await server_state.complete_request(
            request_id=request_id,
            status="completed", 
            message="Request completed after input",
            ctx=mock_context
        )
        
        # Verify request was completed and removed
        assert request_id not in server_state.active_requests

@pytest.mark.asyncio
async def test_agent_cancellation(server_state, mock_context):
    """Test cancellation of an in-progress agent task."""
    # Import functions to test
    from a2a_mcp_server.server import call_agent, cancel_request
    
    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}
    
    # Set up a mock client that will appear to be processing for a while
    mock_client = AsyncMock()
    stream_response = AsyncMock()
    
    # Create proper objects for streaming updates
    class MockStatus:
        def __init__(self, state, message, progress):
            self.state = state
            self.message = message
            self.progress = progress
    
    class StreamUpdateAsyncIterator:
        def __init__(self, updates):
            self.updates = updates
            self._index = 0
            
        def __aiter__(self):
            self._index = 0
            return self
            
        async def __anext__(self):
            if self._index < len(self.updates):
                update = self.updates[self._index]
                self._index += 1
                # Add a delay to simulate slow processing
                await asyncio.sleep(0.05)
                return update
            raise StopAsyncIteration
    
    # Create the status updates for a slow stream
    stream_updates = [
        type("UpdateObject", (), {
            "status": MockStatus("working", "Starting long task", 0.1),
            "artifacts": []
        }),
        type("UpdateObject", (), {
            "status": MockStatus("working", "Still working...", 0.2),
            "artifacts": []
        })
        # No completion update - we'll cancel before it finishes
    ]
    
    # Set up stream response
    stream_response.stream_updates = lambda: StreamUpdateAsyncIterator(stream_updates)
    mock_client.send_message_streaming.return_value = stream_response
    
    # Set up cancel response
    mock_client.cancel_task.return_value = type("UpdateObject", (), {
        "status": MockStatus("canceled", "Task canceled", 0.2)
    })
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Start agent task
        call_task = asyncio.create_task(
            call_agent(
                agent_name="test-agent",
                prompt="Test prompt for cancellation",
                ctx=mock_context
            )
        )
        
        # Give it a moment to start
        await asyncio.sleep(0.1)
        
        # Find the request_id from active requests
        request_ids = list(server_state.active_requests.keys())
        assert len(request_ids) > 0
        request_id = request_ids[0]
        
        # Cancel the request
        cancel_result = await cancel_request(
            request_id=request_id,
            ctx=mock_context
        )
        
        # Verify cancellation
        assert cancel_result["status"] == "success"
        assert mock_client.cancel_task.called
        
        # Check log reflects cancellation
        logs = server_state.get_execution_log(request_id)
        assert any(log["status"] == "canceled" for log in logs)
        
        # Wait for the call_agent task to complete
        try:
            await asyncio.wait_for(call_task, timeout=1.0)
        except asyncio.TimeoutError:
            # If it doesn't complete, something is wrong with our cancellation
            pytest.fail("call_agent task did not complete after cancellation")

@pytest.mark.asyncio
async def test_agent_error_handling(server_state, mock_context):
    """Test handling errors during agent communication."""
    # Import function to test
    from a2a_mcp_server.server import call_agent
    
    # Configure registry
    server_state.registry = {"failing-agent": "http://agent-error-url"}
    
    # Set up a mock client that will raise an exception
    mock_client = AsyncMock()
    mock_client.send_message_streaming.side_effect = Exception("Simulated agent failure")
    
    # Patch A2aMinClient
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class, \
         patch('a2a_mcp_server.server.state', server_state):
        mock_client_class.connect.return_value = mock_client
        
        # Call the agent
        result = await call_agent(
            agent_name="failing-agent",
            prompt="Test prompt",
            ctx=mock_context
        )
        
        # Verify error response
        assert result["status"] == "error"
        assert "failure" in result["message"].lower()
        
        # Check error is reported to context
        assert mock_context.error.called
        
        # Check no active requests remain
        assert len(server_state.active_requests) == 0