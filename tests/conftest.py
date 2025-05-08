import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastmcp import Context


# Set up event loop for all tests
@pytest.fixture(scope="session")
def event_loop():
    """Create and return an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    
    # Clean up any remaining tasks before closing the loop
    pending = asyncio.all_tasks(loop=loop)
    if pending:
        # Give pending tasks one final chance to complete
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    
    loop.close()

@pytest.fixture
async def cleanup_tasks():
    """Fixture to ensure all dangling tasks are cleaned up after each test."""
    # Store the tasks that were already running
    existing_tasks = set(asyncio.all_tasks())
    
    # Run the test
    yield
    
    # After test, collect all tasks that weren't running before
    pending = set(asyncio.all_tasks()) - existing_tasks
    if pending:
        # Cancel all pending tasks started during the test
        for task in pending:
            task.cancel()
        
        # Wait until all tasks are cancelled
        await asyncio.gather(*pending, return_exceptions=True)
        
        # Log any tasks we needed to cancel
        if len(pending) > 0:
            print(f"Cleaned up {len(pending)} hanging task(s) after test")


# Core fixtures to be defined
@pytest.fixture
async def server_state():
    """Create a clean ServerState instance for testing with proper cleanup."""
    from a2a_mcp_server.server import ServerState

    # Create a fresh state instance
    state = ServerState()
    
    # Return the state for test to use
    yield state
    
    # Clean up any remaining tasks or resources after test completion
    # This ensures tests don't interfere with each other
    for pipeline_id in list(state.pipelines.keys()):
        # Cancel any active tasks by setting status to canceled
        # Import from the pipeline package (not the redundant pipeline.py file)
        from a2a_mcp_server.pipeline import PipelineStatus
        pipeline_state = state.pipelines[pipeline_id]
        pipeline_state.status = PipelineStatus.CANCELED
        
    # Clear all active requests
    for request_id in list(state.active_requests.keys()):
        state.active_requests.pop(request_id, None)
        
    # Clear throttler queues
    state.update_throttler.clear_all()


@pytest.fixture
def throttler():
    """Create an UpdateThrottler instance for testing."""
    from a2a_mcp_server.server import UpdateThrottler

    return UpdateThrottler(min_interval=0.01, batch_size=3, max_queue_size=10)


@pytest.fixture
def mock_context():
    """Create a mock FastMCP Context for progress reporting.
    
    The default implementation uses AsyncMock, but for more robust testing,
    consider using the MockContext class from fixtures.mock_context which
    provides better tracking and configurable failure modes.
    """
    # Import the enhanced MockContext
    from tests.fixtures.mock_context import MockContext
    
    # Create and return the enhanced MockContext instance
    # This provides better tracking and configurable failure modes
    return MockContext()


@pytest.fixture
def valid_pipeline_def():
    """Create a valid pipeline definition for testing."""
    return {
        "name": "Test Pipeline",
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


# Create reusable mock classes for a2a_min response objects
class MockStatus:
    """Mock for a2a_min Status object."""

    def __init__(self, state, message="", progress=0.0):
        self.state = state
        self.message = message
        self.progress = progress
        # Some tests access value directly
        self.value = state


class MockPart:
    """Mock for a2a_min message part."""

    def __init__(self, type, text):
        self.type = type
        self.text = text

    def model_dump(self):
        """Return serialized representation as dict."""
        return {"type": self.type, "text": self.text}


class MockArtifact:
    """Mock for a2a_min artifact object."""

    def __init__(self, name, parts):
        self.name = name
        self.parts = parts

    def model_dump(self):
        """Return serialized representation as dict."""
        return {
            "name": self.name,
            "parts": [
                part.model_dump() if hasattr(part, "model_dump") else vars(part)
                for part in self.parts
            ],
        }


class MockUpdateObject:
    """Mock for update object returned in a2a_min streaming response.
    
    Enhanced to support testing with invalid types for message and progress:
    - status_message can be a string or any other type (for robustness testing)
    - status_progress can be a float or any other type (for robustness testing)
    - status_progress can be omitted entirely by setting omit_progress=True
    """

    def __init__(
        self, status_state, status_message="", status_progress=0.0, artifacts=None,
        omit_progress=False
    ):
        # Create a status object with potentially invalid types for testing robustness
        if omit_progress:
            # Creates a status without the progress attribute
            self.status = type(
                "Status", 
                (), 
                {"state": status_state, "message": status_message, "value": status_state}
            )
        else:
            # Normal status with all attributes
            self.status = MockStatus(status_state, status_message, status_progress)
        
        self.artifacts = artifacts or []


class StreamUpdateAsyncIterator:
    """Async iterator for streaming updates."""

    def __init__(self, updates, delay_between_updates=0.005):
        """
        Initialize the async iterator with updates and configurable delay.
        
        Args:
            updates: List of update objects to yield
            delay_between_updates: Time in seconds to wait between yielding updates.
                                   Default is very small (5ms) to keep tests fast while
                                   still allowing for proper async behavior testing.
        """
        self.updates = updates
        self._index = 0
        self.delay_between_updates = delay_between_updates

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index < len(self.updates):
            update = self.updates[self._index]
            self._index += 1
            # Introduce a minimal delay to allow event loop to process other tasks
            # while still keeping tests fast
            await asyncio.sleep(self.delay_between_updates)
            return update
        raise StopAsyncIteration


class MockStreamResponse:
    """Mock for a2a_min streaming response objects.

    This class properly models the pattern used in the server code:
        streaming_task = await client.send_message_streaming(...)
        stream_updates = await streaming_task.stream_updates()
        async for update in stream_updates:
            # Process updates
            
    Enhanced with configurable failure modes for robust testing:
    - stream_updates can be set to None
    - stream_updates can be made to return a non-async-generator
    - stream_updates can be a different type entirely
    """

    def __init__(self, updates=None, delay_between_updates=0.01, stream_updates_mode="normal"):
        """Create a mock stream response with the specified updates.

        Args:
            updates: List of MockUpdateObject instances. If None, default updates are used.
            delay_between_updates: Delay in seconds between yielding updates. Useful for
                simulating realistic network and processing delays.
            stream_updates_mode: Mode for configuring stream_updates method:
                - "normal": Returns a proper async iterator (default)
                - "none": Sets stream_updates to None
                - "not_async_gen": Makes stream_updates return a regular function
                - "function": Makes stream_updates a regular function instead of a method
                
        If no updates provided, uses a default set of updates that go from
        working to completed with a result artifact.
        """
        # Use default updates if none provided
        if updates is None:
            self.updates = [
                # Initial working state
                MockUpdateObject("working", "Starting", 0.2),
                # Mid progress
                MockUpdateObject("working", "Processing", 0.6),
                # Completion
                MockUpdateObject(
                    "completed",
                    "Finished",
                    1.0,
                    artifacts=[
                        MockArtifact("result", [MockPart("text", "Result content")])
                    ],
                ),
            ]
        else:
            self.updates = updates
            
        self.delay_between_updates = delay_between_updates
        
        # Configure stream_updates based on mode
        if stream_updates_mode == "normal":
            # Normal behavior - stream_updates returns an async iterator
            self.stream_updates = self._normal_stream_updates
        elif stream_updates_mode == "none":
            # Set stream_updates to None for testing robustness
            self.stream_updates = None
        elif stream_updates_mode == "not_async_gen":
            # Make stream_updates return a regular function instead of an async generator
            self.stream_updates = self._not_async_gen_stream_updates
        elif stream_updates_mode == "function":
            # Replace stream_updates with a regular function
            self.stream_updates = lambda: None

    def _normal_stream_updates(self):
        """Return an async iterator of updates (normal behavior)."""
        return StreamUpdateAsyncIterator(self.updates, self.delay_between_updates)
    
    def _not_async_gen_stream_updates(self):
        """Return a regular function instead of an async generator."""
        return lambda: None


@pytest.fixture
def mock_a2a_client():
    """Create a mock A2aMinClient with configurable behavior.

    Returns:
        A tuple with (example_client, mock_client_class) where:
        - example_client: A pre-configured mock client instance
        - mock_client_class: The mock class that can be used to create more instances
    """
    # Setup direct mocks
    mock_client_class = MagicMock()
    mock_resolver_class = MagicMock()

    # Setup mock resolver
    mock_resolver = MagicMock()
    mock_resolver.get_agent_card.return_value = MagicMock(
        name="Mock Agent",
        description="Mock agent for testing",
        contact_email="test@example.com",
        id="mock-agent-id",
        version="1.0.0",
    )
    mock_resolver_class.return_value = mock_resolver

    # Setup factory function for the client
    def mock_connect(url, *args, **kwargs):
        # Create a client instance configured for this URL
        client = AsyncMock()

        # Configure streaming response
        mock_stream = MockStreamResponse()
        client.send_message_streaming.return_value = mock_stream

        # Configure input response
        input_response = MagicMock()
        input_response.status = MockStatus("completed", "Input processed", 1.0)
        input_response.artifacts = [
            MockArtifact("input_result", [MockPart("text", "Processed input content")])
        ]
        client.send_input.return_value = input_response

        # Configure cancel response
        cancel_response = MagicMock()
        cancel_response.status = MockStatus("canceled", "Task canceled", 0.0)
        client.cancel_task.return_value = cancel_response

        # Add some metadata for inspection in tests
        client._url = url

        return client

    # Set up the connect method as a class method
    mock_client_class.connect = mock_connect

    # Create an example client for tests
    example_client = mock_connect("http://example.com")

    # Create patchers for use within test functions
    with (
        patch("a2a_mcp_server.server.A2aMinClient", mock_client_class),
        patch("a2a_mcp_server.server.A2ACardResolver", mock_resolver_class),
    ):
        # Yield the client and class for tests to use
        yield example_client, mock_client_class
