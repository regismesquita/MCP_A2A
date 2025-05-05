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
    loop.close()

# Core fixtures to be defined
@pytest.fixture
def server_state():
    """Create a clean ServerState instance for testing."""
    from a2a_mcp_server.server import ServerState
    return ServerState()

@pytest.fixture
def throttler():
    """Create an UpdateThrottler instance for testing."""
    from a2a_mcp_server.server import UpdateThrottler
    return UpdateThrottler(min_interval=0.01, batch_size=3, max_queue_size=10)

@pytest.fixture
def mock_context():
    """Create a mock FastMCP Context for progress reporting."""
    context = AsyncMock(spec=Context)
    context.report_progress = AsyncMock()
    context.complete = AsyncMock()
    context.error = AsyncMock()
    return context

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
                }
            }
        ],
        "final_outputs": ["node2"]
    }

@pytest.fixture
def mock_a2a_client():
    """Create a mock A2aMinClient with configurable behavior."""
    with patch('a2a_mcp_server.server.A2aMinClient') as mock_client_class:
        client = AsyncMock()
        mock_client_class.connect.return_value = client
        
        # Create a proper mock for streaming functionality
        # We need a class that has a synchronous method returning an async iterator
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
                    return update
                raise StopAsyncIteration
                
        class MockStreamResponse:
            def __init__(self):
                # Create a mock Status object with attributes
                class MockStatus:
                    def __init__(self, state, message, progress):
                        self.state = state
                        self.message = message
                        self.progress = progress
                
                # Create a mock Artifact class
                class MockArtifact:
                    def __init__(self, name, parts):
                        self.name = name
                        self.parts = parts
                        
                    def model_dump(self):
                        return {"name": self.name, "parts": [part.__dict__ for part in self.parts]}
                
                # Create a mock Part class
                class MockPart:
                    def __init__(self, type, text):
                        self.type = type
                        self.text = text
                
                # Use structured objects with proper attributes instead of MagicMocks
                self.updates = [
                    # Initial working state
                    type("UpdateObject", (), {
                        "status": MockStatus("working", "Starting", 0.2),
                        "artifacts": []
                    }),
                    # Mid progress
                    type("UpdateObject", (), {
                        "status": MockStatus("working", "Processing", 0.6),
                        "artifacts": []
                    }),
                    # Completion
                    type("UpdateObject", (), {
                        "status": MockStatus("completed", "Finished", 1.0),
                        "artifacts": [
                            MockArtifact("result", [MockPart("text", "Result content")])
                        ]
                    })
                ]
                
                # Create a sync method that returns our async iterator
                def stream_updates():
                    return StreamUpdateAsyncIterator(self.updates)
                
                # Use it as a non-async method
                self.stream_updates = stream_updates
        
        # Create the mock stream response
        mock_stream = MockStreamResponse()
        
        # Configure the client to return our mock stream
        client.send_message_streaming.return_value = mock_stream
        
        # Configure other methods
        client.send_input = AsyncMock()
        client.cancel_task = AsyncMock()
        
        return client, mock_client_class