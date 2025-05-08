"""End-to-end tests for agent workflows.

Note: These tests require running the actual server and agents.
They can be marked as 'manual' or 'slow' and skipped in normal test runs.
"""

import pytest
import subprocess
import time
import requests
import asyncio
import os
import signal
import json
from urllib.parse import urljoin
import uuid

# Mark as manual to skip by default in normal CI runs
pytestmark = pytest.mark.manual


@pytest.fixture(scope="module")
def running_servers():
    """Start the A2A MCP Server and demo agents for E2E tests."""
    # Skip fixture if SKIP_E2E_TESTS environment variable is set
    if os.environ.get("SKIP_E2E_TESTS"):
        pytest.skip("Skipping E2E tests (SKIP_E2E_TESTS is set)")

    # Start Agent 1 - this requires the actual server implementations
    try:
        agent1_proc = subprocess.Popen(
            ["python", "agents/agent_1/agent.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Start Agent 2
        agent2_proc = subprocess.Popen(
            ["python", "agents/agent_2/agent.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Start MCP Server (HTTP transport)
        server_proc = subprocess.Popen(
            ["python", "-m", "a2a_mcp_server", "server", "--transport", "http"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for servers to start
        time.sleep(2)

        # Return processes for cleanup
        servers = {
            "agent1": {"process": agent1_proc, "url": "http://localhost:8001"},
            "agent2": {"process": agent2_proc, "url": "http://localhost:8002"},
            "server": {"process": server_proc, "url": "http://localhost:8000"},
        }

        yield servers

        # Cleanup
        for server_info in servers.values():
            proc = server_info["process"]
            if proc.poll() is None:  # Process is still running
                os.kill(proc.pid, signal.SIGTERM)
                proc.wait()

    except (FileNotFoundError, PermissionError) as e:
        pytest.skip(f"Failed to start required processes: {e}")
        yield None


@pytest.mark.asyncio
async def test_complete_agent_workflow(running_servers):
    """Test complete agent workflow from registration to execution."""
    if running_servers is None:
        pytest.skip("Server fixture failed to initialize")

    # Define server URL
    server_url = running_servers["server"]["url"]
    mcp_endpoint = urljoin(server_url, "/mcp")

    # Register agent
    register_response = requests.post(
        mcp_endpoint,
        json={
            "tool": "a2a_server_registry",
            "arguments": {
                "action": "add",
                "name": "processor",
                "url": running_servers["agent1"]["url"],
            },
        },
    ).json()
    assert register_response["status"] == "success"

    # Call agent
    call_response = requests.post(
        mcp_endpoint,
        json={
            "tool": "call_agent",
            "arguments": {
                "agent_name": "processor",
                "prompt": "Test input for E2E test",
            },
        },
    ).json()
    assert call_response["status"] == "success"
    request_id = call_response["request_id"]

    # Poll for completion and track progress
    completed = False
    final_progress = 0.0
    final_status = None
    for _ in range(10):  # Try up to 10 times
        # Export logs to check status
        logs_response = requests.post(
            mcp_endpoint,
            json={
                "tool": "export_logs",
                "arguments": {"request_id": request_id, "format_type": "json"},
            },
        ).json()

        # Get request info to check current status and progress
        request_info_response = requests.post(
            mcp_endpoint,
            json={
                "tool": "get_request_info",
                "arguments": {"request_id": request_id},
            },
        ).json()

        if "request" in request_info_response:
            req_info = request_info_response["request"]
            if req_info["status"] == "completed":
                final_progress = req_info.get("progress", 0.0)
                final_status = req_info["status"]
                completed = True
                break

        if "log" in logs_response:
            log_entries = json.loads(logs_response["log"])
            
            # Check if any log entry indicates completion
            if any(entry["status"] == "completed" for entry in log_entries):
                # Get the final progress from the completion entry
                for entry in reversed(log_entries):
                    if entry["status"] == "completed":
                        final_progress = entry.get("progress", 0.0)
                        final_status = entry["status"]
                        completed = True
                        break
                
                if completed:
                    break

        # Wait before next poll
        await asyncio.sleep(1)

    # Verify completion
    assert completed, "Agent task did not complete within expected time"
    assert final_status == "completed", f"Final status should be 'completed', got '{final_status}'"
    
    # Verify progress value
    assert abs(final_progress - 1.0) < 0.01, f"Final progress should be 1.0 (or very close), got {final_progress}"
    
    # Verify log entries contain correct information
    logs_response = requests.post(
        mcp_endpoint,
        json={
            "tool": "export_logs",
            "arguments": {"request_id": request_id, "format_type": "json"},
        },
    ).json()
    
    log_entries = json.loads(logs_response["log"])
    
    # Check for progress advancement in the logs
    assert len(log_entries) >= 2, "There should be at least 2 log entries"
    
    # Progress values should generally increase
    progress_values = [entry.get("progress", 0.0) for entry in log_entries]
    assert all(b >= a for a, b in zip(progress_values, progress_values[1:])), "Progress should be non-decreasing"


@pytest.mark.asyncio
async def test_pipeline_execution_workflow(running_servers):
    """Test complete pipeline execution workflow."""
    if running_servers is None:
        pytest.skip("Server fixture failed to initialize")

    # Define server URL
    server_url = running_servers["server"]["url"]
    mcp_endpoint = urljoin(server_url, "/mcp")

    # Register both agents
    for agent_name, agent_info in [
        ("processor", running_servers["agent1"]),
        ("finalizer", running_servers["agent2"]),
    ]:
        register_response = requests.post(
            mcp_endpoint,
            json={
                "tool": "a2a_server_registry",
                "arguments": {
                    "action": "add",
                    "name": agent_name,
                    "url": agent_info["url"],
                },
            },
        ).json()
        assert register_response["status"] == "success"

    # Define test pipeline
    pipeline_definition = {
        "name": "E2E Test Pipeline",
        "nodes": [
            {"id": "process", "agent_name": "processor"},
            {
                "id": "finalize",
                "agent_name": "finalizer",
                "inputs": {
                    "processed_data": {
                        "source_node": "process",
                        "source_artifact": "processed_data",
                    }
                },
            },
        ],
        "final_outputs": ["finalize"],
    }

    # Execute pipeline
    execute_response = requests.post(
        mcp_endpoint,
        json={
            "tool": "execute_pipeline",
            "arguments": {
                "pipeline_definition": pipeline_definition,
                "input_text": "Test input for E2E pipeline test",
            },
        },
    ).json()
    assert execute_response["status"] == "success"
    pipeline_id = execute_response["pipeline_id"]

    # Track node statuses and progress values across polling attempts
    node_transitions = {
        "process": [],  # Will store [status, progress] pairs for each poll
        "finalize": []
    }
    pipeline_progress_values = []  # Track pipeline progress over time

    # Poll pipeline status until completion
    completed = False
    final_pipeline_status = None
    final_pipeline_progress = 0.0
    for _ in range(20):  # Poll with timeout (longer for pipeline)
        status_response = requests.post(
            mcp_endpoint,
            json={
                "tool": "get_pipeline_status",
                "arguments": {"pipeline_id": pipeline_id},
            },
        ).json()

        if "pipeline" in status_response:
            pipeline_status = status_response["pipeline"]["status"]
            pipeline_progress = status_response["pipeline"].get("progress", 0.0)
            pipeline_progress_values.append(pipeline_progress)
            
            # Track node status transitions
            if "nodes" in status_response["pipeline"]:
                for node_id in ["process", "finalize"]:
                    if node_id in status_response["pipeline"]["nodes"]:
                        node = status_response["pipeline"]["nodes"][node_id]
                        node_transitions[node_id].append([
                            node.get("status", "unknown"),
                            node.get("progress", 0.0)
                        ])

            if pipeline_status in ["completed", "failed"]:
                final_pipeline_status = pipeline_status
                final_pipeline_progress = pipeline_progress
                completed = True
                break

        await asyncio.sleep(1)

    # Assertions about completion
    assert completed, "Pipeline did not complete within expected time"
    assert final_pipeline_status == "completed", f"Pipeline failed to complete successfully (status: {final_pipeline_status})"
    
    # Check that final progress is 1.0 (or very close)
    assert abs(final_pipeline_progress - 1.0) < 0.01, f"Final pipeline progress should be 1.0 (or very close), got {final_pipeline_progress}"
    
    # Verify pipeline progress increases over time
    assert len(pipeline_progress_values) > 1, "Should have multiple pipeline progress readings"
    assert pipeline_progress_values[-1] > pipeline_progress_values[0], "Pipeline progress should increase over time"
    
    # Verify node status transitions
    for node_id, transitions in node_transitions.items():
        assert len(transitions) > 0, f"Should have status transitions for node {node_id}"
        assert transitions[-1][0] == "completed", f"Final status for {node_id} should be 'completed', got '{transitions[-1][0]}'"
        assert abs(transitions[-1][1] - 1.0) < 0.01, f"Final progress for {node_id} should be 1.0, got {transitions[-1][1]}"
    
    # Verify final node states
    assert "nodes" in status_response["pipeline"]
    
    # Verify process node completed
    process_node = status_response["pipeline"]["nodes"]["process"]
    assert process_node["status"] == "completed"
    assert len(process_node.get("artifacts", [])) > 0
    
    # Verify finalize node completed
    finalize_node = status_response["pipeline"]["nodes"]["finalize"]
    assert finalize_node["status"] == "completed"
    assert len(finalize_node.get("artifacts", [])) > 0
    
    # Verify artifacts in final output
    assert "artifacts" in finalize_node
    assert isinstance(finalize_node["artifacts"], list)
    assert len(finalize_node["artifacts"]) > 0
    
    # Check artifact structure
    artifact = finalize_node["artifacts"][0]
    assert "name" in artifact
    assert "parts" in artifact
    assert len(artifact["parts"]) > 0
