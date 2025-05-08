"""Mock A2A agent classes and fixtures for testing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any, List, Optional, AsyncGenerator


class MockA2AStreamResponse:
    """Simulates an A2A streaming response."""

    def __init__(self, updates=None):
        """Initialize with specified updates or defaults.

        Args:
            updates: Optional list of update objects to yield during streaming
        """
        self.updates = updates or []

    async def stream_updates(self) -> AsyncGenerator:
        """Stream the configured updates."""
        for update in self.updates:
            yield update
            await asyncio.sleep(0.01)  # Small delay to simulate network


class MockA2AAgent:
    """Mock A2A agent for testing."""

    def __init__(self, name: str, url: str, responses: Optional[Dict[str, Any]] = None):
        """Initialize mock agent.

        Args:
            name: Agent name
            url: Agent URL
            responses: Dictionary of method -> response mappings
        """
        self.name = name
        self.url = url
        self.responses = responses or {}
        self.calls = []

    def configure_stream_response(self, states=None, artifacts=None):
        """Configure streaming response sequence.

        Args:
            states: List of (state, message, progress) tuples
            artifacts: Optional list of artifacts to include in final update
        """
        states = states or [
            ("working", "Starting task", 0.2),
            ("working", "Processing", 0.6),
            ("completed", "Task completed", 1.0),
        ]

        artifacts = artifacts or []

        updates = []
        for i, (state, message, progress) in enumerate(states):
            # Add artifacts only to the final state by default
            state_artifacts = [] if i < len(states) - 1 else artifacts
            updates.append(
                MagicMock(
                    status=MagicMock(state=state, message=message, progress=progress),
                    artifacts=state_artifacts,
                )
            )

        stream_response = MockA2AStreamResponse(updates)
        self.configure_response("send_message_streaming", stream_response)

    def configure_response(self, method: str, response):
        """Configure a method response.

        Args:
            method: Method name
            response: Response to return
        """
        self.responses[method] = response

    def configure_error(self, method: str, exception: Exception):
        """Configure a method to raise an exception.

        Args:
            method: Method name
            exception: Exception to raise
        """
        self.responses[method] = exception

    def get_client_mock(self):
        """Get a mock A2aMinClient configured with this agent's responses."""
        client = AsyncMock()

        # Record calls
        def record_call(method, *args, **kwargs):
            self.calls.append((method, args, kwargs))
            response = self.responses.get(method)
            if isinstance(response, Exception):
                raise response
            return response

        # Configure methods
        for method in [
            "send_message",
            "send_message_streaming",
            "send_input",
            "cancel_task",
            "get_agent_card",
        ]:
            setattr(
                client,
                method,
                AsyncMock(
                    side_effect=lambda *args, method=method, **kwargs: record_call(
                        method, *args, **kwargs
                    )
                ),
            )

        return client
