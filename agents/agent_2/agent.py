#!/usr/bin/env python3
"""
Agent 2: Finalizer
Second agent in chain that finalizes processing and produces output
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Define task state constants
class TaskState:
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

# Create FastAPI app
app = FastAPI(title="Agent 2: Finalizer")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory state for tasks
tasks = {}

# Model classes for requests and responses
class Message(BaseModel):
    role: str
    parts: List[Dict[str, Any]]

class TaskRequest(BaseModel):
    id: str
    sessionId: str
    message: Message

class TaskStatus(BaseModel):
    state: str
    message: Optional[str] = None
    progress: Optional[float] = None

class TaskSendResponse(BaseModel):
    id: str
    status: TaskStatus
    artifacts: Optional[List[Dict[str, Any]]] = None

# Utility function to simulate processing
async def finalize_data(task_id: str, text: str) -> None:
    """
    Simulate data finalization with progress updates.
    This function updates the task's status as it processes.
    """
    # Start processing
    tasks[task_id]["status"] = {
        "state": TaskState.WORKING,
        "message": "Starting finalization (0%)",
        "progress": 0.0
    }
    
    # For 5 steps (0-100% in 20% increments) - faster than Agent 1
    for i in range(1, 6):
        # Simulate work
        await asyncio.sleep(0.75)
        
        # Update progress
        progress = i / 5
        tasks[task_id]["status"] = {
            "state": TaskState.WORKING,
            "message": f"Finalizing ({int(progress*100)}%)",
            "progress": progress
        }
        
        # Check if task was canceled
        if tasks[task_id]["status"]["state"] == TaskState.CANCELED:
            return
    
    # Complete processing if not canceled
    if tasks[task_id]["status"]["state"] != TaskState.CANCELED:
        # Process the text (simple transformation for demo)
        finalized_text = f"FINALIZED: {text} [COMPLETE]"
        
        # Store result
        tasks[task_id]["artifacts"] = [{
            "name": "final_result",
            "parts": [{"type": "text", "text": finalized_text}],
            "metadata": {"chain_position": "2/2"}
        }]
        
        # Update status to completed
        tasks[task_id]["status"] = {
            "state": TaskState.COMPLETED,
            "message": "Finalization completed successfully",
            "progress": 1.0
        }

# A2A Protocol Endpoints

@app.get("/.well-known/agent.json")
async def get_agent_card():
    """Return the agent card from .well-known/agent.json"""
    try:
        with open(os.path.join(os.path.dirname(__file__), ".well-known", "agent.json")) as f:
            return json.load(f)
    except Exception as e:
        logger.exception(f"Error loading agent card: {e}")
        return {"error": str(e)}

@app.post("/")
async def handle_json_rpc(request: Request):
    """
    Main JSON-RPC endpoint implementing A2A protocol.
    Handles tasks/send, tasks/sendSubscribe, tasks/sendInput, tasks/cancel methods.
    """
    try:
        # Parse JSON-RPC request
        data = await request.json()
        method = data.get("method", "")
        params = data.get("params", {})
        
        logger.debug(f"Received JSON-RPC method: {method}")
        
        # Handle method
        if method == "tasks/send":
            return await handle_task_send(data)
        elif method == "tasks/sendSubscribe":
            return await handle_task_send_subscribe(data, request)
        elif method == "tasks/sendInput":
            return await handle_task_send_input(data)
        elif method == "tasks/cancel":
            return await handle_task_cancel(data)
        else:
            return {
                "jsonrpc": "2.0",
                "id": data.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }
    except Exception as e:
        logger.exception(f"Error handling JSON-RPC request: {e}")
        return {
            "jsonrpc": "2.0",
            "id": data.get("id", "unknown"),
            "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
        }

async def handle_task_send(data: Dict[str, Any]):
    """Handle tasks/send method - Create a new task and process it"""
    request_id = data.get("id", "")
    params = data.get("params", {})
    
    # Extract task info
    task_id = params.get("id", str(uuid.uuid4()))
    session_id = params.get("sessionId", str(uuid.uuid4()))
    message = params.get("message", {})
    
    # Extract message text
    text = ""
    if message.get("parts"):
        for part in message.get("parts", []):
            if part.get("type") == "text":
                text += part.get("text", "")
    
    # Handle artifacts from Agent 1
    artifacts = params.get("artifacts", [])
    if artifacts:
        for artifact in artifacts:
            for part in artifact.get("parts", []):
                if part.get("type") == "text":
                    text += part.get("text", "")
    
    logger.info(f"Creating new task {task_id} with text: {text[:50]}...")
    
    # Create task
    tasks[task_id] = {
        "id": task_id,
        "sessionId": session_id,
        "message": message,
        "status": {
            "state": TaskState.SUBMITTED,
            "message": "Task submitted",
            "progress": 0.0
        },
        "artifacts": []
    }
    
    # Start processing task asynchronously
    asyncio.create_task(finalize_data(task_id, text))
    
    # Return immediate response
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "id": task_id,
            "status": tasks[task_id]["status"],
            "artifacts": tasks[task_id].get("artifacts", [])
        }
    }

async def handle_task_send_subscribe(data: Dict[str, Any], request: Request):
    """
    Handle tasks/sendSubscribe method - Create a new task and subscribe to updates
    Returns a Server-Sent Events (SSE) stream
    """
    request_id = data.get("id", "")
    params = data.get("params", {})
    
    # Extract task info
    task_id = params.get("id", str(uuid.uuid4()))
    session_id = params.get("sessionId", str(uuid.uuid4()))
    message = params.get("message", {})
    
    # Extract message text
    text = ""
    if message.get("parts"):
        for part in message.get("parts", []):
            if part.get("type") == "text":
                text += part.get("text", "")
    
    # Handle artifacts from Agent 1
    artifacts = params.get("artifacts", [])
    if artifacts:
        for artifact in artifacts:
            for part in artifact.get("parts", []):
                if part.get("type") == "text":
                    text += part.get("text", "")
    
    logger.info(f"Creating new task {task_id} with text: {text[:50]}... (subscribed)")
    
    # Create task
    tasks[task_id] = {
        "id": task_id,
        "sessionId": session_id,
        "message": message,
        "status": {
            "state": TaskState.SUBMITTED,
            "message": "Task submitted",
            "progress": 0.0
        },
        "artifacts": []
    }
    
    # Start processing task asynchronously
    asyncio.create_task(finalize_data(task_id, text))
    
    # Set up SSE event generator
    async def event_generator():
        # Send initial response
        yield {
            "data": json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "id": task_id,
                    "status": tasks[task_id]["status"],
                    "artifacts": tasks[task_id].get("artifacts", [])
                }
            })
        }
        
        # Track last state so we only send updates on changes
        last_status = tasks[task_id]["status"].copy()
        last_artifacts = []
        
        # Send updates until task is complete or canceled
        while tasks[task_id]["status"]["state"] not in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED]:
            await asyncio.sleep(0.5)
            
            # Check for status changes
            current_status = tasks[task_id]["status"]
            current_artifacts = tasks[task_id].get("artifacts", [])
            
            # If status changed, send an update
            if (current_status != last_status or 
                len(current_artifacts) != len(last_artifacts)):
                
                yield {
                    "data": json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "id": task_id,
                            "status": current_status,
                            "artifacts": current_artifacts
                        }
                    })
                }
                
                # Update last status
                last_status = current_status.copy()
                last_artifacts = current_artifacts.copy()
                
        # Send final update
        yield {
            "data": json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "id": task_id,
                    "status": tasks[task_id]["status"],
                    "artifacts": tasks[task_id].get("artifacts", [])
                }
            })
        }
    
    # Return SSE response
    return EventSourceResponse(event_generator())

async def handle_task_send_input(data: Dict[str, Any]):
    """Handle tasks/sendInput method - Not applicable for Agent 2"""
    request_id = data.get("id", "")
    params = data.get("params", {})
    
    # Extract task info
    task_id = params.get("id", "")
    
    # Verify task exists
    if task_id not in tasks:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": f"Task not found: {task_id}"}
        }
    
    # Agent 2 doesn't request input in this demo
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": f"Agent 2 doesn't support input requests"}
    }

async def handle_task_cancel(data: Dict[str, Any]):
    """Handle tasks/cancel method - Cancel an in-progress task"""
    request_id = data.get("id", "")
    params = data.get("params", {})
    
    # Extract task info
    task_id = params.get("id", "")
    
    # Verify task exists
    if task_id not in tasks:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": f"Task not found: {task_id}"}
        }
    
    logger.info(f"Canceling task {task_id}")
    
    # Update task status to canceled
    tasks[task_id]["status"] = {
        "state": TaskState.CANCELED,
        "message": "Task canceled by user",
        "progress": tasks[task_id]["status"].get("progress", 0)
    }
    
    # Return response
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "id": task_id,
            "status": tasks[task_id]["status"],
            "artifacts": tasks[task_id].get("artifacts", [])
        }
    }

# MCP Server with SSE Endpoints
@app.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """
    MCP compatible SSE endpoint for streaming task updates.
    This is for integration with MCP clients like Claude Desktop.
    """
    async def event_generator():
        # For demo, just stream updates for all tasks
        task_states = {}
        
        while True:
            # Check for updates on all tasks
            for task_id, task in tasks.items():
                if task_id not in task_states or task_states[task_id] != task["status"]:
                    task_states[task_id] = task["status"].copy()
                    
                    # Send update
                    yield {
                        "data": json.dumps({
                            "task_id": task_id,
                            "status": task["status"],
                            "artifacts": task.get("artifacts", []),
                            "chain_position": {"current": 2, "total": 2}
                        })
                    }
            
            # Small delay
            await asyncio.sleep(0.5)
    
    return EventSourceResponse(event_generator())

# Main function to run the server
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)