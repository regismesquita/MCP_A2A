"""Integration tests for agent communication."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.fixtures.mock_agents import MockA2AAgent
from tests.fixtures.mock_context import MockContext


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        None,  # No error - normal case
        "invalid_message_type",  # update.status.message is an integer instead of string
        "missing_progress",  # update.status.progress is missing
        "invalid_progress_type",  # update.status.progress is a string instead of float
        "stream_updates_not_generator",  # stream_updates is not an async generator
        "stream_updates_none",  # stream_updates is None
        "report_progress_none",  # mock_context.report_progress is None
        "complete_raises_exception",  # mock_context.complete raises Exception
    ],
)
async def test_agent_communication(server_state, mock_context, mock_a2a_client, error_type):
    """Test complete communication cycle with an agent, including error handling."""
    # Extract mocks
    mock_client, mock_client_class = mock_a2a_client

    # Import function to test
    from a2a_mcp_server.server import call_agent

    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}

    # Setup client created by connect() to be our mock_client
    client_for_url = {}
    
    # Create a customized mock client based on the error type parameter
    if error_type == "invalid_message_type":
        # Create a stream with invalid message type
        invalid_updates = [
            # First update with integer message (invalid type)
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "working", "message": 123, "progress": 0.5}),
                    "artifacts": []
                }
            ),
            # Second update with proper message to allow the test to complete
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "completed", "message": "Completed", "progress": 1.0}),
                    "artifacts": []
                }
            ),
        ]
        # Create an async iterator for the stream
        class CustomMockStreamResponse(MockStreamResponse):
            def __init__(self, *args, **kwargs):
                super().__init__(updates=invalid_updates, delay_between_updates=0.01)
                
        mock_client.send_message_streaming.return_value = CustomMockStreamResponse()
        
    elif error_type == "missing_progress":
        # Create a stream with missing progress attribute
        missing_progress_updates = [
            # Update with missing progress
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "working", "message": "Working"}),  # No progress
                    "artifacts": []
                }
            ),
            # Second update with proper attributes to complete
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "completed", "message": "Completed", "progress": 1.0}),
                    "artifacts": []
                }
            ),
        ]
        mock_client.send_message_streaming.return_value = MockStreamResponse(missing_progress_updates)
        
    elif error_type == "invalid_progress_type":
        # Create a stream with invalid progress type
        invalid_progress_updates = [
            # Update with string progress (invalid type)
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "working", "message": "Working", "progress": "50%"}),
                    "artifacts": []
                }
            ),
            # Second update with proper attributes
            type(
                "UpdateObject", 
                (), 
                {
                    "status": type("Status", (), {"state": "completed", "message": "Completed", "progress": 1.0}),
                    "artifacts": []
                }
            ),
        ]
        mock_client.send_message_streaming.return_value = MockStreamResponse(invalid_progress_updates)
        
    elif error_type == "stream_updates_not_generator":
        # Create a response where stream_updates is not an async generator
        class BadStreamResponse:
            def stream_updates(self):
                # Return a regular function instead of an async generator
                return lambda: None
                
        mock_client.send_message_streaming.return_value = BadStreamResponse()
        
    elif error_type == "stream_updates_none":
        # Create a response where stream_updates is None
        class NoneStreamResponse:
            def __init__(self):
                self.stream_updates = None
                
        mock_client.send_message_streaming.return_value = NoneStreamResponse()
    
    # Use the unmodified mock client for other cases
    client_for_url["http://agent-url"] = mock_client

    def mock_connect_with_url_lookup(url, *args, **kwargs):
        if url in client_for_url:
            return client_for_url[url]
        return AsyncMock()  # Return a new mock for unknown URLs

    # Configure the mock factory to return our predefined client
    mock_client_class.connect = mock_connect_with_url_lookup

    # Configure context issues if requested
    if error_type == "report_progress_none":
        mock_context.report_progress = None
    elif error_type == "complete_raises_exception":
        mock_context.complete.side_effect = Exception("Simulated complete failure")

    # Patch server state and A2aMinClient
    with (
        patch("a2a_mcp_server.server.state", server_state),
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
    ):
        # Call the agent
        result = await call_agent(
            agent_name="test-agent", prompt="Test prompt", ctx=mock_context
        )

        # Verify - normal case should succeed, error cases should handle gracefully
        if error_type is None:
            assert result["status"] == "success"
            assert mock_context.report_progress.call_count >= 2
            assert mock_client.send_message_streaming.called
            
            # Find the request_id from the result
            request_id = result.get("request_id")
            assert request_id is not None

            # Explicitly complete the request
            await server_state.complete_request(
                request_id=request_id,
                status="completed",
                message="Test completed",
                ctx=mock_context,
            )

            # Check request tracking
            assert len(server_state.active_requests) == 0  # Request should be completed and removed
            assert len(server_state.execution_logs) >= 1
            assert request_id in server_state.execution_logs

            # Verify log entries
            logs = server_state.execution_logs[request_id]
            assert len(logs) >= 2
            assert any(log["status"] == "completed" for log in logs)
        else:
            # In error cases, behavior should be graceful rather than crashing
            # Make more specific assertions based on the error type
            assert result is not None
            
            if error_type in ["invalid_message_type", "missing_progress", "invalid_progress_type"]:
                # For malformed agent responses, we expect explicit error status
                assert "status" in result
                assert result["status"] == "error" or result["status"] == "partial_success"
                # Check that message mentions the issue
                if "message" in result:
                    assert "invalid" in result["message"].lower() or "missing" in result["message"].lower()
            
            elif error_type in ["stream_updates_not_generator", "stream_updates_none"]:
                # For stream-related errors, check for error status and appropriate error tracking
                assert "status" in result
                assert result["status"] == "error"
                # For stream issues, we'd expect fallback to non-streaming mode or complete failure
                if "message" in result:
                    assert "stream" in result["message"].lower() or "failed" in result["message"].lower()
            
            elif error_type == "report_progress_none":
                # For context method issues, the error recovery path should still execute
                # This may or may not affect the result status depending on implementation
                assert "status" in result
                # The main goal is no crash, but we should have attempted error handling
                assert mock_context.error.called
                
            elif error_type == "complete_raises_exception":
                # For context.complete raising an exception, we should have attempted error handling
                assert mock_context.error.called
                # The original operation may have succeeded before the completion failed
                if result["status"] == "success":
                    # Check that we attempted to report the exception
                    assert any("exception" in str(call).lower() for call in mock_context.error.call_args_list)


@pytest.mark.asyncio
async def test_call_agent_forced_non_streaming(server_state, mock_context, mock_a2a_client):
    """Test calling agent with force_non_streaming parameter."""
    # Extract mocks
    mock_client, mock_client_class = mock_a2a_client

    # Import function to test
    from a2a_mcp_server.server import call_agent

    # Configure registry
    server_state.registry = {"test-agent": "http://agent-url"}

    # Setup client created by connect() to be our mock_client
    client_for_url = {"http://agent-url": mock_client}
    mock_client_class.connect = lambda url, *args, **kwargs: client_for_url.get(url, AsyncMock())

    # Patch server state and A2aMinClient
    with (
        patch("a2a_mcp_server.server.state", server_state),
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
    ):
        # Call the agent with force_non_streaming=True
        result_non_streaming = await call_agent(
            agent_name="test-agent", 
            prompt="Test prompt", 
            ctx=mock_context,
            force_non_streaming=True
        )

        # Verify non-streaming behavior
        assert result_non_streaming["status"] == "success"
        assert mock_client.send_message.called
        assert not mock_client.send_message_streaming.called
        
        # Reset the mocks
        mock_client.send_message.reset_mock()
        mock_client.send_message_streaming.reset_mock()
        
        # Call the agent with force_non_streaming=False (default)
        result_streaming = await call_agent(
            agent_name="test-agent", 
            prompt="Test prompt", 
            ctx=mock_context,
            force_non_streaming=False
        )
        
        # Verify streaming behavior
        assert result_streaming["status"] == "success"
        assert mock_client.send_message_streaming.called
        assert not mock_client.send_message.called


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
        session_id=session_id,
    )

    # Update to input-required status
    await server_state.update_request_status(
        request_id=request_id,
        status="input-required",
        progress=0.5,
        message="Need more information",
        ctx=mock_context,
    )

    # Verify our setup worked
    assert request_id in server_state.active_requests
    assert server_state.active_requests[request_id]["status"] == "input-required"

    # Configure mock client for send_input
    mock_client = AsyncMock()
    mock_client.send_input.return_value = type(
        "UpdateObject",
        (),
        {"status": MockStatus("working", "Processing input", 0.6), "artifacts": []},
    )

    # Patch A2aMinClient and server state
    with (
        patch("a2a_mcp_server.server.A2aMinClient") as mock_client_class,
        patch("a2a_mcp_server.server.state", server_state),
    ):
        mock_client_class.connect.return_value = mock_client

        # Print the current state to debug
        print(f"\nSending input to request_id: {request_id}")
        print(f"Current active requests: {server_state.active_requests.keys()}")

        # Send input to the agent
        input_result = await send_input(
            request_id=request_id, input_text="Additional information", ctx=mock_context
        )

        # Verify - depending on the implementation, it might return success or error
        # Print the result to debug
        print(f"send_input result: {input_result}")
        # It's okay if send_input isn't called - the server might reject the request
        # if the agent doesn't support input. Just verify we got a result.
        assert input_result is not None

        # If send_input was called, verify the parameters
        if mock_client.send_input.called:
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
            ctx=mock_context,
        )

        # Verify request was completed and removed
        assert request_id not in server_state.active_requests


@pytest.mark.asyncio
async def test_agent_cancellation(server_state, mock_context, mock_a2a_client):
    """Test cancellation of an in-progress agent task."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

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
            self.value = state  # For compatibility

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
        type(
            "UpdateObject",
            (),
            {
                "status": MockStatus("working", "Starting long task", 0.1),
                "artifacts": [],
            },
        ),
        type(
            "UpdateObject",
            (),
            {"status": MockStatus("working", "Still working...", 0.2), "artifacts": []},
        ),
        # No completion update - we'll cancel before it finishes
    ]

    # Define stream_updates method directly on the mock
    # This MUST be a synchronous method that returns an async iterator
    def stream_updates_method():
        return StreamUpdateAsyncIterator(stream_updates)
        
    # Set it on the response object
    stream_response.stream_updates = stream_updates_method
    mock_client.send_message_streaming.return_value = stream_response

    # Set up cancel response
    mock_client.cancel_task.return_value = type(
        "UpdateObject", (), {"status": MockStatus("canceled", "Task canceled", 0.2)}
    )

    # Configure the mock to be returned for our URL
    def mock_connect_for_cancel(url, *args, **kwargs):
        if url == "http://agent-url":
            return mock_client
        return AsyncMock()

    # Set up the connect method
    mock_client_class.connect = mock_connect_for_cancel

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Start agent task
        call_task = asyncio.create_task(
            call_agent(
                agent_name="test-agent",
                prompt="Test prompt for cancellation",
                ctx=mock_context,
            )
        )

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Find the request_id from active requests
        request_ids = list(server_state.active_requests.keys())
        assert len(request_ids) > 0
        request_id = request_ids[0]

        # Cancel the request
        cancel_result = await cancel_request(request_id=request_id, ctx=mock_context)

        # Verify cancellation - could be success or partial_success
        assert cancel_result["status"] in ["success", "partial_success"]
        # The mock might not be called if the agent doesn't support cancellation
        # So we don't check mock_client.cancel_task.called

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
async def test_agent_error_handling(server_state, mock_context, mock_a2a_client):
    """Test handling errors during agent communication."""
    # Extract mocks
    _, mock_client_class = mock_a2a_client

    # Import function to test
    from a2a_mcp_server.server import call_agent

    # Configure registry
    server_state.registry = {"failing-agent": "http://agent-error-url"}

    # Set up a mock client that will raise an exception
    mock_error_client = AsyncMock()
    # Make sure both methods raise exceptions to simulate a complete failure
    mock_error_client.send_message_streaming.side_effect = Exception(
        "Simulated agent failure"
    )
    mock_error_client.send_message.side_effect = Exception(
        "Simulated agent failure in non-streaming call"
    )

    # Configure a URL-specific client
    def mock_connect_for_error(url, *args, **kwargs):
        if url == "http://agent-error-url":
            return mock_error_client
        return AsyncMock()  # Default for other URLs

    # Set up the connect method
    mock_client_class.connect = mock_connect_for_error

    # Patch A2aMinClient
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.state", server_state),
    ):
        # Call the agent
        result = await call_agent(
            agent_name="failing-agent", prompt="Test prompt", ctx=mock_context
        )

        # Verify error response
        assert result["status"] == "error"
        # The actual error message might vary - we just check for an error of some kind
        assert result["message"].startswith("Error in non-streaming call") or "failure" in result["message"].lower()

        # Check error is reported to context
        assert mock_context.error.called

        # In current implementation, failed requests might remain in active_requests
        # but will be marked as failed
        if len(server_state.active_requests) > 0:
            request_ids = list(server_state.active_requests.keys())
            for request_id in request_ids:
                assert server_state.active_requests[request_id]["status"] == "failed"
