"""Unit tests for the ServerState class."""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from a2a_mcp_server.server import ServerState


@pytest.mark.asyncio
async def test_track_request(server_state):
    """Test tracking a new request with validation."""
    # Setup
    request_id = "req-123"
    agent_name = "test-agent"
    task_id = "task-123"
    session_id = "session-123"

    # Exercise
    server_state.track_request(
        request_id=request_id,
        agent_name=agent_name,
        task_id=task_id,
        session_id=session_id,
    )

    # Verify
    assert request_id in server_state.active_requests
    request = server_state.active_requests[request_id]
    assert request["agent_name"] == agent_name
    assert request["task_id"] == task_id
    assert request["session_id"] == session_id
    assert request["status"] == "submitted"
    assert request_id in server_state.execution_logs

    # Test duplicate tracking - should not raise an error currently
    # In a real implementation, we might want to add validation for this
    server_state.track_request(
        request_id=request_id,
        agent_name="another-agent",
        task_id="another-task",
        session_id="another-session",
    )

    # The second tracking should overwrite the first
    assert server_state.active_requests[request_id]["agent_name"] == "another-agent"


@pytest.mark.asyncio
async def test_update_request_status(server_state, mock_context):
    """Test updating request status with throttling."""
    # Setup
    request_id = "req-456"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Exercise - first update
    should_send1 = await server_state.update_request_status(
        request_id=request_id,
        status="working",
        progress=0.5,
        message="Working",
        ctx=mock_context,
    )

    # Verify
    assert should_send1 is True
    assert server_state.active_requests[request_id]["status"] == "working"
    assert server_state.active_requests[request_id]["progress"] == 0.5
    assert len(server_state.execution_logs[request_id]) == 1
    assert mock_context.report_progress.called

    # Reset mock
    mock_context.reset_mock()

    # Force update time to simulate throttling
    server_state.last_update_time[request_id] = time.time()

    # Exercise - second update (should be throttled)
    should_send2 = await server_state.update_request_status(
        request_id=request_id,
        status="working",
        progress=0.6,
        message="Still working",
        ctx=mock_context,
    )

    # Verify throttling
    assert should_send2 is False
    assert not mock_context.report_progress.called

    # Exercise - critical update (status change)
    should_send3 = await server_state.update_request_status(
        request_id=request_id,
        status="completed",
        progress=1.0,
        message="Done",
        ctx=mock_context,
    )

    # Verify critical updates bypass throttling
    assert should_send3 is True
    assert mock_context.complete.called


@pytest.mark.asyncio
async def test_update_nonexistent_request(server_state, mock_context):
    """Test updating a request that doesn't exist."""
    # Exercise
    should_send = await server_state.update_request_status(
        request_id="nonexistent",
        status="working",
        progress=0.5,
        message="Working",
        ctx=mock_context,
    )

    # Verify
    assert should_send is False
    assert mock_context.error.called
    assert "unknown request" in mock_context.error.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_complete_request(server_state, mock_context):
    """Test completing a request."""
    # Setup
    request_id = "req-789"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Exercise
    result = await server_state.complete_request(
        request_id=request_id,
        status="completed",
        message="Successfully completed",
        ctx=mock_context,
    )

    # Verify
    assert result["status"] == "completed"
    assert result["progress"] == 1.0
    assert result["last_message"] == "Successfully completed"
    assert request_id not in server_state.active_requests
    assert request_id not in server_state.last_update_time
    assert request_id in server_state.execution_logs
    assert len(server_state.execution_logs[request_id]) > 0
    assert mock_context.complete.called


@pytest.mark.asyncio
async def test_complete_nonexistent_request(server_state, mock_context):
    """Test completing a request that doesn't exist."""
    # Exercise
    result = await server_state.complete_request(
        request_id="nonexistent", status="completed", message="Done", ctx=mock_context
    )

    # Verify
    assert result == {}
    assert mock_context.error.called
    assert "unknown request" in mock_context.error.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_get_request_info(server_state):
    """Test getting request info."""
    # Setup
    request_id = "req-info-test"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Exercise
    info = server_state.get_request_info(request_id)

    # Verify
    assert info is not None
    assert info["agent_name"] == "agent"
    assert info["task_id"] == "task"
    assert info["session_id"] == "session"

    # Non-existent request should return None
    assert server_state.get_request_info("nonexistent") is None


@pytest.mark.asyncio
async def test_get_execution_log(server_state, mock_context):
    """Test getting execution log."""
    # Setup
    request_id = "req-log-test"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Add some status updates
    statuses = ["working", "working", "completed"]
    messages = ["Starting", "Processing", "Finished"]
    progress = [0.2, 0.6, 1.0]

    for status, message, prog in zip(statuses, messages, progress):
        await server_state.update_request_status(
            request_id=request_id,
            status=status,
            progress=prog,
            message=message,
            ctx=mock_context,
        )

    # Exercise
    logs = server_state.get_execution_log(request_id)

    # Verify
    assert len(logs) == 3
    assert logs[0]["status"] == "working"
    assert logs[0]["message"] == "Starting"
    assert logs[2]["status"] == "completed"
    assert logs[2]["message"] == "Finished"

    # Non-existent request should return empty list
    assert server_state.get_execution_log("nonexistent") == []


@pytest.mark.asyncio
async def test_export_execution_log(server_state, mock_context):
    """Test exporting execution log in different formats."""
    # Setup
    request_id = "req-export-test"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Add some status updates
    await server_state.update_request_status(
        request_id=request_id,
        status="working",
        progress=0.5,
        message="Working on task",
        ctx=mock_context,
    )

    await server_state.update_request_status(
        request_id=request_id,
        status="completed",
        progress=1.0,
        message="Task completed",
        ctx=mock_context,
    )

    # Exercise - text format
    text_log = server_state.export_execution_log(request_id, format_type="text")

    # Verify text format
    assert "Execution Log for Request" in text_log
    assert "WORKING" in text_log
    assert "COMPLETED" in text_log
    assert "Working on task" in text_log
    assert "Task completed" in text_log

    # Exercise - JSON format
    json_log = server_state.export_execution_log(request_id, format_type="json")

    # Verify JSON format
    import json

    log_data = json.loads(json_log)
    assert isinstance(log_data, list)
    assert len(log_data) == 2
    assert log_data[0]["status"] == "working"
    assert log_data[1]["status"] == "completed"

    # Non-existent request
    empty_log = server_state.export_execution_log("nonexistent")
    assert empty_log == "No log entries found"


@pytest.mark.asyncio
async def test_update_with_chain_position(server_state, mock_context):
    """Test updating request status with chain position."""
    # Setup
    request_id = "req-chain-test"
    server_state.track_request(
        request_id=request_id, agent_name="agent", task_id="task", session_id="session"
    )

    # Exercise - set chain position
    chain_position = {"current": 2, "total": 3}
    await server_state.update_request_status(
        request_id=request_id,
        status="working",
        progress=0.5,
        message="Working in chain",
        chain_position=chain_position,
        ctx=mock_context,
    )

    # Verify
    request = server_state.active_requests[request_id]
    assert request["chain_position"] == chain_position
    assert request["chain_position"]["current"] == 2
    assert request["chain_position"]["total"] == 3

    # Check execution log
    logs = server_state.get_execution_log(request_id)
    assert logs[0]["chain_position"] == chain_position
