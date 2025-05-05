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
            stderr=subprocess.PIPE
        )
        
        # Start Agent 2
        agent2_proc = subprocess.Popen(
            ["python", "agents/agent_2/agent.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Start MCP Server (HTTP transport)
        server_proc = subprocess.Popen(
            ["python", "-m", "a2a_mcp_server", "server", "--transport", "http"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for servers to start
        time.sleep(2)
        
        # Return processes for cleanup
        servers = {
            "agent1": {"process": agent1_proc, "url": "http://localhost:8001"},
            "agent2": {"process": agent2_proc, "url": "http://localhost:8002"},
            "server": {"process": server_proc, "url": "http://localhost:8000"}
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
    register_response = requests.post(mcp_endpoint, json={
        "tool": "a2a_server_registry",
        "arguments": {
            "action": "add",
            "name": "processor",
            "url": running_servers["agent1"]["url"]
        }
    }).json()
    assert register_response["status"] == "success"
    
    # Call agent
    call_response = requests.post(mcp_endpoint, json={
        "tool": "call_agent",
        "arguments": {
            "agent_name": "processor",
            "prompt": "Test input for E2E test"
        }
    }).json()
    assert call_response["status"] == "success"
    request_id = call_response["request_id"]
    
    # Poll for completion
    completed = False
    for _ in range(10):  # Try up to 10 times
        # Export logs to check status
        logs_response = requests.post(mcp_endpoint, json={
            "tool": "export_logs",
            "arguments": {
                "request_id": request_id,
                "format_type": "json"
            }
        }).json()
        
        if "log" in logs_response:
            log_entries = json.loads(logs_response["log"])
            if any(entry["status"] == "completed" for entry in log_entries):
                completed = True
                break
                
        # Wait before next poll
        await asyncio.sleep(1)
    
    assert completed, "Agent task did not complete within expected time"

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
        ("finalizer", running_servers["agent2"])
    ]:
        register_response = requests.post(mcp_endpoint, json={
            "tool": "a2a_server_registry",
            "arguments": {
                "action": "add",
                "name": agent_name,
                "url": agent_info["url"]
            }
        }).json()
        assert register_response["status"] == "success"
    
    # Define test pipeline
    pipeline_definition = {
        "name": "E2E Test Pipeline",
        "nodes": [
            {
                "id": "process",
                "agent_name": "processor"
            },
            {
                "id": "finalize",
                "agent_name": "finalizer",
                "inputs": {
                    "processed_data": {
                        "source_node": "process",
                        "source_artifact": "processed_data"
                    }
                }
            }
        ],
        "final_outputs": ["finalize"]
    }
    
    # Execute pipeline
    execute_response = requests.post(mcp_endpoint, json={
        "tool": "execute_pipeline",
        "arguments": {
            "pipeline_definition": pipeline_definition,
            "input_text": "Test input for E2E pipeline test"
        }
    }).json()
    assert execute_response["status"] == "success"
    pipeline_id = execute_response["pipeline_id"]
    
    # Poll pipeline status until completion
    completed = False
    for _ in range(20):  # Poll with timeout (longer for pipeline)
        status_response = requests.post(mcp_endpoint, json={
            "tool": "get_pipeline_status",
            "arguments": {
                "pipeline_id": pipeline_id
            }
        }).json()
        
        if "pipeline" in status_response and status_response["pipeline"]["status"] in ["completed", "failed"]:
            completed = True
            break
            
        await asyncio.sleep(1)
    
    assert completed, "Pipeline did not complete within expected time"
    assert status_response["pipeline"]["status"] == "completed", "Pipeline failed to complete successfully"
    
    # Verify final outputs
    assert "nodes" in status_response["pipeline"]
    finalize_node = status_response["pipeline"]["nodes"]["finalize"]
    assert finalize_node["status"] == "completed"
    assert len(finalize_node["artifacts"]) > 0