#!/usr/bin/env python3
"""
A2A MCP Server - MCP server for A2A integration
Provides tools for A2A agent registry and communication

Tools:
- a2a_server_registry: Add or remove A2A server URLs
- list_agents: List available agents with their agent cards
- call_agent: Call an agent with a prompt
"""

import asyncio
import logging
import json
import os
import sys
import time
import uuid
from collections import deque
from typing import Dict, List, Optional, Union, Literal, Any, Tuple, Deque, TypeVar, Generic, Callable

# Set up logging to stderr with more detailed format
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for more detailed logs
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Define a generic type for update data
T = TypeVar('T')

class UpdateThrottler(Generic[T]):
    """
    Utility class for throttling and batching rapid updates.
    
    This class implements a timestamp-based rate limiting mechanism 
    with additional support for batching similar updates.
    """
    
    def __init__(self, 
                min_interval: float = 1.0, 
                batch_size: int = 5,
                max_queue_size: int = 50):
        """
        Initialize the throttler.
        
        Args:
            min_interval: Minimum time between updates in seconds
            batch_size: Maximum number of updates to batch together
            max_queue_size: Maximum size of the update queue
        """
        self.min_interval = min_interval
        self.batch_size = batch_size
        self.max_queue_size = max_queue_size
        self.last_update_time: Dict[str, float] = {}
        self.update_queues: Dict[str, Deque[T]] = {}
        
    def should_send(self, key: str, current_time: Optional[float] = None) -> bool:
        """
        Check if an update should be sent based on the time since the last update.
        
        Args:
            key: Unique identifier for the update stream
            current_time: Current timestamp (defaults to time.time())
            
        Returns:
            bool: True if sufficient time has passed, False otherwise
        """
        if current_time is None:
            current_time = time.time()
            
        last_time = self.last_update_time.get(key, 0)
        return (current_time - last_time) >= self.min_interval
    
    def add_update(self, key: str, update: T, 
                 is_critical: bool = False,
                 merge_similar: Optional[Callable[[T, T], bool]] = None) -> Tuple[bool, Optional[List[T]]]:
        """
        Add an update to the queue and determine if updates should be sent.
        
        Args:
            key: Unique identifier for the update stream
            update: The update data to add
            is_critical: Whether this update is critical and should bypass throttling
            merge_similar: Optional function to determine if updates are similar and can be merged
            
        Returns:
            Tuple[bool, Optional[List[T]]]: 
                - First element: True if updates should be sent
                - Second element: List of batched updates to send, or None if no updates
        """
        current_time = time.time()
        
        # Initialize queue if needed
        if key not in self.update_queues:
            self.update_queues[key] = deque(maxlen=self.max_queue_size)
            
        queue = self.update_queues[key]
        
        # Handle merging similar updates if needed
        if merge_similar and queue:
            # Check if the new update is similar to the last one in the queue
            if merge_similar(queue[-1], update):
                # Replace the last update with the new one
                queue.pop()
                queue.append(update)
                
                # Only send immediately if it's critical
                if is_critical:
                    self.last_update_time[key] = current_time
                    return True, list(queue)
                    
                return self.should_send(key, current_time), None
        
        # Add the update to the queue
        queue.append(update)
        
        # Determine if we should send updates now
        should_send = is_critical or self.should_send(key, current_time)
        
        # If we should send, prepare the batch
        if should_send or len(queue) >= self.batch_size:
            self.last_update_time[key] = current_time
            batch = list(queue)
            queue.clear()
            return True, batch
            
        return False, None
        
    def mark_sent(self, key: str, current_time: Optional[float] = None) -> None:
        """
        Mark updates as sent for a given key.
        
        Args:
            key: Unique identifier for the update stream
            current_time: Current timestamp (defaults to time.time())
        """
        if current_time is None:
            current_time = time.time()
            
        self.last_update_time[key] = current_time
        
    def get_pending_updates(self, key: str) -> List[T]:
        """
        Get all pending updates for a given key without marking them as sent.
        
        Args:
            key: Unique identifier for the update stream
            
        Returns:
            List[T]: List of pending updates
        """
        queue = self.update_queues.get(key, deque())
        return list(queue)
        
    def clear(self, key: str) -> None:
        """
        Clear all pending updates for a given key.
        
        Args:
            key: Unique identifier for the update stream
        """
        if key in self.update_queues:
            self.update_queues[key].clear()
            
    def clear_all(self) -> None:
        """Clear all pending updates for all keys."""
        for queue in self.update_queues.values():
            queue.clear()

# MCP imports
from fastmcp import FastMCP, Context

# A2A imports
from a2a_min import A2aMinClient
from a2a_min.base.client.card_resolver import A2ACardResolver
from a2a_min.base.types import AgentCard, Message, TextPart, TaskStatus, TaskSendParams

# Create a class to store persistent state
class ServerState:
    def __init__(self):
        self.registry = {}  # name -> url
        self.cache = {}  # name -> AgentCard
        self.active_requests = {}  # request_id -> status info
        self.execution_logs = {}  # request_id -> execution log
        self.last_update_time = {}  # request_id -> timestamp
        
        # Add pipeline storage
        self.pipeline_templates = {}  # template_id -> pipeline definition
        self.pipelines = {}  # pipeline_id -> pipeline state
        
        # Create a throttler for updates with 1-second interval, batch size of 5
        self.update_throttler = UpdateThrottler[Dict[str, Any]](
            min_interval=1.0,
            batch_size=5,
            max_queue_size=100
        )
        
        # Create pipeline execution engine (initialized later to avoid circular imports)
        self.pipeline_engine = None
    
    def track_request(self, request_id: str, agent_name: str, task_id: str, 
                     session_id: str, pipeline_id: Optional[str] = None,
                     node_id: Optional[str] = None) -> None:
        """
        Add a new request to active_requests.
        
        Args:
            request_id: The unique request ID
            agent_name: The name of the agent being called
            task_id: The A2A task ID
            session_id: The A2A session ID
            pipeline_id: Optional pipeline ID if part of a pipeline
            node_id: Optional node ID if part of a pipeline
        """
        self.active_requests[request_id] = {
            "agent_name": agent_name,
            "task_id": task_id,
            "session_id": session_id,
            "start_time": time.time(),
            "status": "submitted",
            "progress": 0.0,
            "chain_position": {"current": 1, "total": 1},
            "last_message": "Request submitted",
            "updates": [],
            "pipeline_id": pipeline_id,
            "node_id": node_id,
            "artifacts": []
        }
        self.last_update_time[request_id] = time.time()
        self.execution_logs[request_id] = []
        logger.debug(f"Tracking new request: {request_id}")
    
    async def update_request_status(self, request_id: str, status: str, 
                              progress: Optional[float] = None, 
                              message: Optional[str] = None,
                              chain_position: Optional[Dict[str, int]] = None,
                              ctx: Optional[Context] = None) -> bool:
        """
        Update the status of an active request.
        
        Args:
            request_id: The unique request ID
            status: The new status (submitted, working, input-required, completed, failed, canceled)
            progress: Optional progress value between 0 and 1
            message: Optional status message
            chain_position: Optional dict with current and total position in agent chain
            ctx: Optional Context object for progress reporting
            
        Returns:
            bool: True if update should be sent (respects throttling), False otherwise
        """
        if request_id not in self.active_requests:
            logger.warning(f"Attempted to update unknown request: {request_id}")
            if ctx:
                await ctx.error(f"Attempted to update unknown request: {request_id}")
            return False
        
        request = self.active_requests[request_id]
        current_time = time.time()
        
        # Determine if this is a critical update
        is_status_change = status != request["status"]
        is_final = status in ["completed", "failed", "canceled"]
        is_critical = is_status_change or is_final
        
        # Update the request info
        request["status"] = status
        if progress is not None:
            request["progress"] = progress
        if message is not None:
            request["last_message"] = message
        if chain_position is not None:
            request["chain_position"] = chain_position
        
        # Create update object
        update = {
            "timestamp": current_time,
            "status": status,
            "progress": request["progress"],
            "message": request["last_message"],
            "chain_position": request["chain_position"]
        }
        
        # Add to update history
        request["updates"].append(update)
        
        # Add to execution log
        log_entry = {
            "timestamp": current_time,
            "status": status, 
            "message": message or request["last_message"],
            "chain_position": request["chain_position"]
        }
        self.execution_logs[request_id].append(log_entry)
        
        # Define a function to determine if updates are similar
        def are_updates_similar(prev: Dict[str, Any], curr: Dict[str, Any]) -> bool:
            """Determine if two updates are similar enough to be merged."""
            # Always treat different statuses as different updates
            if prev["status"] != curr["status"]:
                return False
                
            # For the same status, check if they're working status updates
            if prev["status"] == "working":
                # Merge updates that are within 10% progress of each other
                if abs(prev["progress"] - curr["progress"]) < 0.1:
                    return True
                    
            return False
        
        # Use the throttler to decide if we should send an update
        should_send, batched_updates = self.update_throttler.add_update(
            key=request_id,
            update=update,
            is_critical=is_critical,
            merge_similar=are_updates_similar
        )
        
        # If we have a Context and should send an update, send it using the Context
        if ctx and should_send and batched_updates:
            # Send all batched updates
            for update in batched_updates:
                if is_final and update["status"] in ["completed", "failed", "canceled"]:
                    # Send final status via context
                    if update["status"] == "completed":
                        await ctx.complete(update["message"])
                    elif update["status"] == "failed":
                        await ctx.error(update["message"])
                    else:  # canceled
                        await ctx.report_progress(
                            current=update["progress"],
                            total=1.0,
                            message=f"Canceled: {update['message']}"
                        )
                else:
                    # Send progress update via context
                    await ctx.report_progress(
                        current=update["progress"],
                        total=1.0,
                        message=update["message"]
                    )
        
        # Remember the last time we checked, regardless of whether we sent
        self.last_update_time[request_id] = current_time
            
        return should_send
    
    def get_request_info(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about an active request.
        
        Args:
            request_id: The unique request ID
            
        Returns:
            Dict with request information or None if not found
        """
        return self.active_requests.get(request_id)
    
    async def complete_request(self, request_id: str, status: str = "completed", 
                         message: Optional[str] = None, ctx: Optional[Context] = None) -> Dict[str, Any]:
        """
        Mark a request as completed and return its final state.
        
        Args:
            request_id: The unique request ID
            status: Final status (completed, failed, or canceled)
            message: Optional final status message
            ctx: Optional Context object for progress reporting
            
        Returns:
            Dict with final request information
        """
        if request_id not in self.active_requests:
            logger.warning(f"Attempted to complete unknown request: {request_id}")
            if ctx:
                await ctx.error(f"Attempted to complete unknown request: {request_id}")
            return {}
        
        # Get the current request info
        request = self.active_requests[request_id]
        
        # Update with final state
        await self.update_request_status(
            request_id=request_id,
            status=status,
            progress=1.0 if status == "completed" else request["progress"],
            message=message or f"Request {status}",
            ctx=ctx
        )
        
        # Get the final state
        final_state = self.active_requests.pop(request_id, {})
        self.last_update_time.pop(request_id, None)
        
        return final_state
    
    def get_execution_log(self, request_id: str) -> List[Dict[str, Any]]:
        """
        Get the execution log for a request.
        
        Args:
            request_id: The unique request ID
            
        Returns:
            List of log entries
        """
        return self.execution_logs.get(request_id, [])
        
    async def call_agent_for_pipeline(self, agent_name: str, prompt: str, pipeline_id: str, 
                                     node_id: str, request_id: str, task_id: str, 
                                     session_id: str, ctx) -> Dict[str, Any]:
        """
        Call an agent as part of a pipeline.
        
        Args:
            agent_name: Name of the agent to call
            prompt: Prompt to send to the agent
            pipeline_id: The pipeline ID
            node_id: The node ID within the pipeline
            request_id: The request ID
            task_id: The A2A task ID
            session_id: The A2A session ID
            ctx: The context for progress reporting
            
        Returns:
            Dict with status information
        """
        try:
            logger.debug(f"call_agent_for_pipeline called with agent_name={agent_name}, prompt={prompt[:50]}...")
            logger.debug(f"Current registry: {self.registry}")
            
            # Verify the agent exists
            if agent_name not in self.registry:
                error_msg = f"Agent '{agent_name}' not found in registry"
                logger.warning(error_msg)
                await ctx.error(error_msg)
                return {"status": "error", "message": error_msg}
            
            # Get the URL for the agent
            url = self.registry[agent_name]
            logger.debug(f"Using URL: {url}")
            
            try:
                # Prepare the URL
                if url.endswith('/'):
                    base_url = url
                else:
                    base_url = url + '/'
                
                # Track request with pipeline info
                self.track_request(
                    request_id=request_id,
                    agent_name=agent_name,
                    task_id=task_id,
                    session_id=session_id,
                    pipeline_id=pipeline_id,
                    node_id=node_id
                )
                
                # Update initial status with context
                await self.update_request_status(
                    request_id=request_id,
                    status="submitted",
                    message=f"Sending prompt to {agent_name}",
                    ctx=ctx
                )
                
                # Prepare the JSON-RPC request using tasks/sendSubscribe for streaming updates
                json_rpc_request = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tasks/sendSubscribe",
                    "params": {
                        "id": task_id,
                        "sessionId": session_id,
                        "message": {
                            "role": "user",
                            "parts": [
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    }
                }
                
                logger.debug(f"Sending JSON-RPC request: {json.dumps(json_rpc_request)}")
                
                # Update status to working
                await self.update_request_status(
                    request_id=request_id,
                    status="working",
                    message=f"Connected to {agent_name}, waiting for response...",
                    ctx=ctx
                )
                
                # Use A2aMinClient for pipeline agent communication
                try:
                    # Create A2A min client
                    client = A2aMinClient.connect(url)
                    
                    # Create message for pipeline input
                    message = Message(
                        role="user",
                        parts=[TextPart(text=prompt)]
                    )
                    
                    logger.debug(f"Using A2aMinClient to send streaming message to {agent_name} for pipeline")
                    
                    # Update status to working
                    await self.update_request_status(
                        request_id=request_id,
                        status="working",
                        message=f"Connected to {agent_name}, waiting for response...",
                        ctx=ctx
                    )
                    
                    # Send message with streaming for pipeline
                    try:
                        # Send message with streaming
                        streaming_task = await client.send_message_streaming(
                            message=message,
                            task_id=task_id,
                            session_id=session_id
                        )
                        
                        # Process streaming updates
                        async for update in streaming_task.stream_updates():
                            logger.debug(f"Received pipeline streaming update: {update}")
                            
                            # Process the update using our existing mechanism
                            # We need to convert the a2a_min update to our expected format
                            converted_update = {
                                "jsonrpc": "2.0", 
                                "id": request_id,
                                "result": {
                                    "id": task_id,
                                    "status": {
                                        "state": update.status.state if update.status else "working",
                                        "message": update.status.message if update.status else "",
                                        "progress": update.status.progress if update.status else 0.0
                                    },
                                    "artifacts": [artifact.model_dump() if hasattr(artifact, 'model_dump') else vars(artifact) 
                                                for artifact in (update.artifacts or [])]
                                }
                            }
                            
                            # Process the converted update
                            await process_agent_update(request_id, agent_name, converted_update, ctx)
                            
                            # Store artifacts
                            if update.artifacts:
                                self.active_requests[request_id]["artifacts"] = [
                                    artifact.model_dump() if hasattr(artifact, 'model_dump') else vars(artifact) 
                                    for artifact in update.artifacts
                                ]
                        
                        # Mark as completed if we haven't already
                        request_info = self.get_request_info(request_id)
                        if request_info and request_info.get("status") not in ["completed", "failed", "canceled"]:
                            await self.update_request_status(
                                request_id=request_id,
                                status="completed",
                                progress=1.0,
                                message=f"Completed response from {agent_name}",
                                ctx=ctx
                            )
                    
                    except Exception as e:
                        logger.exception(f"Error in pipeline streaming task: {e}")
                        
                        # Update status to failed
                        await self.update_request_status(
                            request_id=request_id,
                            status="failed",
                            message=f"Error: {str(e)}",
                            ctx=ctx
                        )
                        
                        return {
                            "status": "error",
                            "message": f"Error in pipeline streaming task: {str(e)}",
                            "request_id": request_id
                        }
                    
                    # Extract artifacts from the completed request
                    request_info = self.get_request_info(request_id)
                    artifacts = request_info.get("artifacts", []) if request_info else []
                    
                    # Return the final status
                    return {
                        "status": "success",
                        "message": "Task completed",
                        "request_id": request_id,
                        "artifacts": artifacts
                    }
                    
                except Exception as e:
                    logger.exception(f"Error processing streaming response for {request_id}: {e}")
                    
                    # Update status to failed
                    await self.update_request_status(
                        request_id=request_id,
                        status="failed",
                        message=f"Error: {str(e)}",
                        ctx=ctx
                    )
                    
                    return {
                        "status": "error",
                        "message": f"Error processing response: {str(e)}",
                        "request_id": request_id
                    }
                
            except Exception as e:
                logger.exception(f"Error calling agent {agent_name}: {e}")
                await ctx.error(f"Error calling agent: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Error calling agent: {str(e)}"
                }
        except Exception as e:
            logger.exception(f"Error in call_agent_for_pipeline: {str(e)}")
            await ctx.error(f"Error: {str(e)}")
            return {"status": "error", "message": f"Error: {str(e)}"}
    
    def export_execution_log(self, request_id: str, 
                            format_type: Literal["text", "json"] = "text") -> str:
        """
        Export the execution log for a request.
        
        Args:
            request_id: The unique request ID
            format_type: Output format (text or json)
            
        Returns:
            String with formatted log
        """
        log_entries = self.get_execution_log(request_id)
        
        if not log_entries:
            return "No log entries found"
        
        if format_type == "json":
            return json.dumps(log_entries, indent=2)
        
        # Text format
        formatted_log = []
        formatted_log.append(f"Execution Log for Request: {request_id}")
        formatted_log.append("=" * 50)
        
        for entry in log_entries:
            timestamp = entry.get("timestamp", 0)
            status = entry.get("status", "unknown")
            message = entry.get("message", "")
            position = entry.get("chain_position", {})
            
            # Format the time
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
            
            # Format the position
            position_str = f"[Agent {position.get('current', '?')}/{position.get('total', '?')}]"
            
            # Format the status with emoji
            status_emoji = {
                "submitted": "🔄",
                "working": "⏳",
                "input-required": "❓",
                "completed": "✅",
                "failed": "❌",
                "canceled": "⏹️"
            }.get(status, "⚪")
            
            log_line = f"{time_str} {status_emoji} {position_str} {status.upper()}: {message}"
            formatted_log.append(log_line)
        
        return "\n".join(formatted_log)

# Create a single instance to be used throughout the app
state = ServerState()


async def fetch_agent_card(url: str) -> Optional[AgentCard]:
    """Fetch agent card from an A2A server using A2ACardResolver."""
    try:
        logger.debug(f"Fetching agent card from {url}")
        
        # Use the A2ACardResolver to get the agent card
        card_resolver = A2ACardResolver(url)
        try:
            card = card_resolver.get_agent_card()
            logger.debug(f"Received agent card: {card}")
            return card
        except Exception as e:
            logger.error(f"Failed to fetch agent card: {str(e)}")
            return None
                
    except Exception as e:
        logger.exception(f"Error fetching agent card from {url}: {e}")
        return None


async def update_agent_cache() -> None:
    """Update the cache of agent cards."""
    logger.info("Updating agent cache...")
    new_cache = {}
    for name, url in state.registry.items():
        card = await fetch_agent_card(url)
        if card:
            new_cache[name] = card
            logger.info(f"Added agent {name} to cache: {card.name}")
    
    # Update the state cache
    state.cache = new_cache
    logger.info(f"Agent cache updated with {len(state.cache)} agents")


# Create the FastMCP server with FastMCP 2.0 style
mcp = FastMCP(
    name="A2A MCP Server",
    settings=dict(
        connection_timeout=600.0,  # 10 minutes timeout for long-running operations
        auto_reconnect=True,  # Enable automatic reconnection for dropped connections
        max_reconnect_attempts=5,  # Maximum reconnection attempts
        reconnect_delay=2.0  # Delay between reconnection attempts in seconds
    )
)

# Register tools
@mcp.tool()
async def execute_pipeline(pipeline_definition: Dict[str, Any], input_text: str, ctx: Context) -> Dict[str, Any]:
    """
    Execute a pipeline from a JSON definition.
    
    Args:
        pipeline_definition: The pipeline definition in JSON format
        input_text: The input text to the pipeline
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with pipeline execution information
    """
    try:
        logger.debug(f"execute_pipeline called with definition {json.dumps(pipeline_definition)[:200]}...")
        
        await ctx.report_progress(current=0.1, total=1.0, message="Validating pipeline definition")
        
        # Import the required classes
        from a2a_mcp_server.pipeline import PipelineDefinition
        
        try:
            # Validate the pipeline definition
            pipeline_def = PipelineDefinition(pipeline_definition)
            logger.info(f"Validated pipeline: {pipeline_def}")
        except (jsonschema.exceptions.ValidationError, ValueError) as e:
            error_msg = f"Invalid pipeline definition: {str(e)}"
            logger.error(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        await ctx.report_progress(current=0.2, total=1.0, message=f"Starting pipeline execution: {pipeline_def.definition['name']}")
        
        # Execute the pipeline
        pipeline_id = await state.pipeline_engine.execute_pipeline(pipeline_def, input_text, ctx)
        
        logger.info(f"Started pipeline execution: {pipeline_id}")
        
        return {
            "status": "success",
            "message": f"Pipeline {pipeline_def.definition['name']} started",
            "pipeline_id": pipeline_id
        }
    except Exception as e:
        logger.exception(f"Error in execute_pipeline: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def get_pipeline_status(pipeline_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Get the status of a pipeline execution.
    
    Args:
        pipeline_id: The ID of the pipeline execution
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with pipeline status information
    """
    try:
        logger.debug(f"get_pipeline_status called for pipeline_id={pipeline_id}")
        
        await ctx.report_progress(current=0.5, total=1.0, message=f"Retrieving status for pipeline {pipeline_id}")
        
        # Check if the pipeline exists
        if pipeline_id not in state.pipelines:
            error_msg = f"Pipeline {pipeline_id} not found"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the pipeline state
        pipeline_state = state.pipelines[pipeline_id]
        
        await ctx.report_progress(current=1.0, total=1.0, message=f"Retrieved status for pipeline {pipeline_id}")
        
        # Convert to a dictionary for the response
        pipeline_info = pipeline_state.to_dict()
        
        return {
            "status": "success",
            "message": f"Pipeline status retrieved",
            "pipeline": pipeline_info
        }
    except Exception as e:
        logger.exception(f"Error in get_pipeline_status: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def a2a_server_registry(action: Literal["add", "remove"], name: str, ctx: Context, url: Optional[str] = None) -> Dict[str, Any]:
    """
    Add or remove an A2A server URL.
    
    Args:
        action: Either "add" or "remove"
        name: Name of the server
        url: URL of the server (required for "add" action)
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with status and message
    """
    try:
        logger.debug(f"a2a_server_registry called with action={action}, name={name}, url={url}")
        logger.debug(f"Registry before: {state.registry}")
        
        if action == "add":
            if not url:
                error_msg = "URL is required for add action"
                logger.warning(error_msg)
                await ctx.error(error_msg)
                return {"status": "error", "message": error_msg}
            
            await ctx.report_progress(current=0.2, total=1.0, message=f"Adding {name} to registry")
            
            # Add to registry
            state.registry[name] = url
            logger.info(f"Added A2A server: {name} -> {url}")
            logger.debug(f"Registry after: {state.registry}")
            
            await ctx.report_progress(current=0.5, total=1.0, message=f"Updating agent cache")
            
            # Update the cache asynchronously
            asyncio.create_task(update_agent_cache())
            
            await ctx.report_progress(current=1.0, total=1.0, message=f"Added A2A server: {name}")
            
            return {
                "status": "success", 
                "message": f"Added A2A server: {name}",
                "registry": state.registry
            }
        
        elif action == "remove":
            if name in state.registry:
                await ctx.report_progress(current=0.5, total=1.0, message=f"Removing {name} from registry")
                
                # Remove from registry
                del state.registry[name]
                
                # Remove from cache if present
                if name in state.cache:
                    del state.cache[name]
                
                logger.info(f"Removed A2A server: {name}")
                logger.debug(f"Registry after: {state.registry}")
                
                await ctx.report_progress(current=1.0, total=1.0, message=f"Removed A2A server: {name}")
                
                return {
                    "status": "success", 
                    "message": f"Removed A2A server: {name}",
                    "registry": state.registry
                }
            else:
                error_msg = f"Server {name} not found in registry"
                logger.warning(error_msg)
                await ctx.error(error_msg)
                return {
                    "status": "error", 
                    "message": error_msg
                }
        
        error_msg = f"Invalid action: {action}. Use 'add' or 'remove'"
        logger.error(f"Invalid action: {action}")
        await ctx.error(error_msg)
        return {"status": "error", "message": error_msg}
    except Exception as e:
        logger.exception(f"Error in a2a_server_registry: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def list_agents(ctx: Context) -> Dict[str, Any]:
    """
    List available agents with their agent cards.
    
    Args:
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with agents and their cards
    """
    try:
        logger.debug("list_agents called")
        logger.debug(f"Current registry: {state.registry}")
        
        await ctx.report_progress(current=0.1, total=1.0, message="Retrieving list of registered agents")
        
        # If no agents are registered
        if not state.registry:
            await ctx.report_progress(current=1.0, total=1.0, message="No agents are registered")
            return {
                "agent_count": 0,
                "agents": {},
                "message": "No agents are registered. Use a2a_server_registry to add an agent."
            }
        
        # Fetch agent cards using A2ACardResolver
        agents = {}
        
        total_agents = len(state.registry)
        current_agent = 0
        
        for name, url in state.registry.items():
            current_agent += 1
            progress = 0.1 + (0.8 * (current_agent / total_agents))
            
            await ctx.report_progress(
                current=progress, 
                total=1.0, 
                message=f"Fetching card for {name} ({current_agent}/{total_agents})"
            )
            
            try:
                logger.debug(f"Fetching card for {name} from {url}")
                
                # Use A2ACardResolver to fetch the card
                card_resolver = A2ACardResolver(url)
                try:
                    card = card_resolver.get_agent_card()
                    if card:
                        # Convert to dictionary for JSON response
                        agents[name] = card.model_dump() if hasattr(card, 'model_dump') else vars(card)
                        logger.debug(f"Received card for {name}: {card}")
                    else:
                        logger.error(f"Failed to fetch agent card for {name}: No card returned")
                except Exception as e:
                    logger.error(f"Failed to fetch agent card for {name}: {str(e)}")
            except Exception as e:
                logger.exception(f"Error fetching card for {name}: {e}")
        
        # Update the cache with the fetched cards
        await ctx.report_progress(current=0.9, total=1.0, message="Updating agent cache")
        
        state.cache = {}
        for name, card_data in agents.items():
            try:
                state.cache[name] = AgentCard(**card_data)
            except Exception as e:
                logger.exception(f"Error converting card data for {name}: {e}")
        
        logger.info(f"Listing {len(agents)} agents")
        
        await ctx.report_progress(current=1.0, total=1.0, message=f"Found {len(agents)} agents")
        
        return {
            "agent_count": len(agents),
            "agents": agents
        }
    except Exception as e:
        logger.exception(f"Error in list_agents: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def call_agent(agent_name: str, prompt: str, ctx: Context) -> Dict[str, Any]:
    """
    Call an agent with a prompt and stream progress updates.
    
    Args:
        agent_name: Name of the agent to call
        prompt: Prompt to send to the agent
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with status information
    """
    try:
        logger.debug(f"call_agent called with agent_name={agent_name}, prompt={prompt[:50]}...")
        logger.debug(f"Current registry: {state.registry}")
        
        # Verify the agent exists
        if agent_name not in state.registry:
            error_msg = f"Agent '{agent_name}' not found in registry"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the URL for the agent
        url = state.registry[agent_name]
        logger.debug(f"Using URL: {url}")
        
        try:
            import json
            import uuid
            
            # Prepare the URL
            if url.endswith('/'):
                base_url = url
            else:
                base_url = url + '/'
            
            # Generate unique IDs
            mcp_request_id = str(uuid.uuid4())
            task_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            
            # Start tracking this request
            state.track_request(
                request_id=mcp_request_id,
                agent_name=agent_name,
                task_id=task_id,
                session_id=session_id
            )
            
            # Update initial status with context
            await state.update_request_status(
                request_id=mcp_request_id,
                status="submitted",
                message=f"Sending prompt to {agent_name}",
                ctx=ctx
            )
            
            # Prepare the JSON-RPC request using tasks/sendSubscribe for streaming updates
            json_rpc_request = {
                "jsonrpc": "2.0",
                "id": mcp_request_id,
                "method": "tasks/sendSubscribe",
                "params": {
                    "id": task_id,
                    "sessionId": session_id,
                    "message": {
                        "role": "user",
                        "parts": [
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                }
            }
            
            logger.debug(f"Sending JSON-RPC request: {json.dumps(json_rpc_request)}")
            
            # Update status to working
            await state.update_request_status(
                request_id=mcp_request_id,
                status="working",
                message=f"Connected to {agent_name}, waiting for response...",
                ctx=ctx
            )
            
            # Use A2aMinClient to send the message with streaming
            try:
                # Create A2A min client
                client = A2aMinClient.connect(url)
                
                # Create text message
                message = Message(
                    role="user",
                    parts=[TextPart(text=prompt)]
                )
                
                # Send message with streaming
                logger.debug(f"Using A2aMinClient to send streaming message to {agent_name}")
                
                # Update status to working
                await state.update_request_status(
                    request_id=mcp_request_id,
                    status="working",
                    message=f"Connected to {agent_name}, waiting for response...",
                    ctx=ctx
                )
                
                # Use the streaming API
                try:
                    # Send message with streaming
                    streaming_task = await client.send_message_streaming(
                        message=message,
                        task_id=task_id,
                        session_id=session_id
                    )
                    
                    # Process streaming updates
                    async for update in streaming_task.stream_updates():
                        logger.debug(f"Received streaming update: {update}")
                        
                        # Process the update using our existing mechanism
                        # We need to convert the a2a_min update to our expected format
                        converted_update = {
                            "jsonrpc": "2.0", 
                            "id": mcp_request_id,
                            "result": {
                                "id": task_id,
                                "status": {
                                    "state": update.status.state if update.status else "working",
                                    "message": update.status.message if update.status else "",
                                    "progress": update.status.progress if update.status else 0.0
                                },
                                "artifacts": [artifact.model_dump() if hasattr(artifact, 'model_dump') else vars(artifact) 
                                             for artifact in (update.artifacts or [])]
                            }
                        }
                        
                        # Process the converted update
                        await process_agent_update(mcp_request_id, agent_name, converted_update, ctx)
                    
                    # Mark as completed if we haven't already
                    request_info = state.get_request_info(mcp_request_id)
                    if request_info and request_info.get("status") not in ["completed", "failed", "canceled"]:
                        await state.update_request_status(
                            request_id=mcp_request_id,
                            status="completed",
                            progress=1.0,
                            message=f"Completed response from {agent_name}",
                            ctx=ctx
                        )
                        
                except Exception as e:
                    logger.exception(f"Error in streaming task: {e}")
                    
                    # Update status to failed
                    await state.update_request_status(
                        request_id=mcp_request_id,
                        status="failed",
                        message=f"Error: {str(e)}",
                        ctx=ctx
                    )
                    
                    return {
                        "status": "error",
                        "message": f"Error in streaming task: {str(e)}",
                        "request_id": mcp_request_id
                    }
                
                # Return the final status
                return {
                    "status": "success",
                    "message": "Task completed",
                    "request_id": mcp_request_id
                }
                
            except Exception as e:
                logger.exception(f"Error processing streaming response for {mcp_request_id}: {e}")
                
                # Update status to failed
                await state.update_request_status(
                    request_id=mcp_request_id,
                    status="failed",
                    message=f"Error: {str(e)}",
                    ctx=ctx
                )
                
                return {
                    "status": "error",
                    "message": f"Error processing response: {str(e)}",
                    "request_id": mcp_request_id
                }
            
        except Exception as e:
            logger.exception(f"Error calling agent {agent_name}: {e}")
            await ctx.error(f"Error calling agent: {str(e)}")
            return {
                "status": "error",
                "message": f"Error calling agent: {str(e)}"
            }
    except Exception as e:
        logger.exception(f"Error in call_agent: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}


# Streaming response processing is now handled directly in call_agent with Context


async def process_agent_update(mcp_request_id: str, agent_name: str, update: Dict[str, Any], ctx: Context) -> None:
    """
    Process an update from an A2A agent.
    
    Args:
        mcp_request_id: The MCP request ID
        agent_name: The name of the agent
        update: The update data from the agent
        ctx: The FastMCP context for progress reporting
    """
    logger.debug(f"Processing agent update for {mcp_request_id}: {json.dumps(update)[:200]}...")
    
    # Check if this is a valid JSON-RPC response
    if "jsonrpc" not in update or "result" not in update:
        logger.warning(f"Invalid JSON-RPC response: {json.dumps(update)[:100]}...")
        return
    
    # Extract the task data from the result
    task_data = update.get("result", {})
    status_data = task_data.get("status", {})
    
    # Extract task state
    task_state = status_data.get("state", "unknown")
    
    # Extract progress information
    progress = status_data.get("progress", 0.0)
    
    # Extract status message
    status_message = status_data.get("message", "")
    
    # Map A2A states to our states
    state_mapping = {
        "submitted": "submitted",
        "working": "working",
        "input-required": "input-required",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled"
    }
    
    mapped_state = state_mapping.get(task_state, "working")
    
    # Handle input-required state
    input_request = None
    if mapped_state == "input-required":
        # Check if there's an input request
        input_request = task_data.get("inputRequest", {})
        prompt = input_request.get("prompt", "")
        
        if prompt:
            status_message = f"{agent_name} needs additional input: {prompt}"
        else:
            status_message = f"{agent_name} needs additional input"
    
    # Determine appropriate message if none was provided
    if not status_message:
        status_message = {
            "submitted": f"Request submitted to {agent_name}",
            "working": f"{agent_name} is processing your request...",
            "input-required": f"{agent_name} needs additional input",
            "completed": f"{agent_name} has completed the task",
            "failed": f"{agent_name} encountered an error",
            "canceled": f"Request to {agent_name} has been canceled"
        }.get(mapped_state, f"{agent_name} is responding...")
    
    # Extract artifacts for completed tasks
    artifacts = []
    if mapped_state == "completed" and "artifacts" in task_data:
        artifacts = task_data.get("artifacts", [])
        if artifacts:
            artifact_names = [a.get("name", "unnamed") for a in artifacts]
            status_message = f"{agent_name} completed with {len(artifacts)} artifact(s): {', '.join(artifact_names)}"
    
    # Get chain position info (current agent in chain)
    chain_position = {"current": 1, "total": 1}
    
    # Update request status with context
    should_send = await state.update_request_status(
        request_id=mcp_request_id,
        status=mapped_state,
        progress=progress,
        message=status_message,
        chain_position=chain_position,
        ctx=ctx
    )
    
    logger.debug(f"Updated request status for {mcp_request_id}: {mapped_state}, should_send={should_send}")


# FastMCP 2.0 handles connections via Context, so we don't need separate handler functions


# Define new tool for sending additional input to an agent
@mcp.tool()
async def send_input(request_id: str, input_text: str, ctx: Context) -> Dict[str, Any]:
    """
    Send additional input to an agent that has requested it.
    
    Args:
        request_id: The unique request ID for the in-progress task
        input_text: The input text to send to the agent
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with response information
    """
    try:
        logger.debug(f"send_input called for request_id={request_id}, input={input_text[:50]}...")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Verifying request {request_id}")
        
        # Verify the request exists
        request_info = state.get_request_info(request_id)
        if not request_info:
            error_msg = f"No active request found with ID: {request_id}"
            logger.warning(f"No active request found for {request_id}")
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Verify the request is in the input-required state
        current_status = request_info.get("status", "unknown")
        if current_status != "input-required":
            error_msg = f"Request {request_id} is not waiting for input (status: {current_status})"
            logger.warning(f"Request {request_id} is not in input-required state (status: {current_status})")
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the agent information
        agent_name = request_info.get("agent_name", "unknown")
        task_id = request_info.get("task_id", "unknown")
        session_id = request_info.get("session_id", "unknown")
        
        await ctx.report_progress(current=0.2, total=1.0, message=f"Preparing to send input to {agent_name}")
        
        # Get the URL for the agent
        if agent_name not in state.registry:
            error_msg = f"Agent '{agent_name}' not found in registry"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        url = state.registry[agent_name]
        
        # Prepare the URL
        if url.endswith('/'):
            base_url = url
        else:
            base_url = url + '/'
        
        # Prepare the JSON-RPC request to send input
        json_rpc_request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/sendInput",
            "params": {
                "id": task_id,
                "sessionId": session_id,
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "type": "text",
                            "text": input_text
                        }
                    ]
                }
            }
        }
        
        logger.debug(f"Sending input request: {json.dumps(json_rpc_request)}")
        
        # Update status to working
        await state.update_request_status(
            request_id=request_id,
            status="working",
            message=f"Sent additional input to {agent_name}, waiting for response...",
            ctx=ctx
        )
        
        await ctx.report_progress(current=0.5, total=1.0, message=f"Sending input to {agent_name}")
        
        # Use A2aMinClient to send input
        try:
            # Create A2A min client
            client = A2aMinClient.connect(url)
            
            # Create message for input
            message = Message(
                role="user",
                parts=[TextPart(text=input_text)]
            )
            
            logger.debug(f"Using A2aMinClient to send input to {agent_name}")
            
            # Send input
            task = await client.send_input(
                task_id=task_id,
                session_id=session_id,
                message=message
            )
            
            logger.debug(f"Received input response: {task}")
            
            # Get status from the task
            status = task.status if hasattr(task, 'status') else None
            task_state = status.state if status else "working"
            
            await ctx.report_progress(current=1.0, total=1.0, message=f"Successfully sent input to {agent_name}")
            
            # Convert task to expected format for process_agent_update
            converted_update = {
                "jsonrpc": "2.0", 
                "id": request_id,
                "result": {
                    "id": task_id,
                    "status": {
                        "state": status.state if status else "working",
                        "message": status.message if status else "",
                        "progress": status.progress if status else 0.0
                    },
                    "artifacts": [artifact.model_dump() if hasattr(artifact, 'model_dump') else vars(artifact) 
                                 for artifact in (task.artifacts if hasattr(task, 'artifacts') else [])]
                }
            }
            
            # Process the update
            await process_agent_update(request_id, agent_name, converted_update, ctx)
            
            return {
                "status": "success",
                "message": f"Successfully sent input to {agent_name}",
                "task_state": task_state,
                "request_id": request_id
            }
            
        except Exception as e:
            logger.exception(f"Error sending input: {e}")
            error_msg = f"Error sending input: {str(e)}"
            
            # Update status back to input-required since the input failed
            await state.update_request_status(
                request_id=request_id,
                status="input-required",
                message=f"Error sending input to {agent_name}: {str(e)}",
                ctx=ctx
            )
            
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    except Exception as e:
        logger.exception(f"Error in send_input: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define new tool for cancellation
@mcp.tool()
async def cancel_request(request_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Cancel an in-progress agent request.
    
    Args:
        request_id: The unique request ID to cancel
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with status information
    """
    try:
        logger.debug(f"cancel_request called for request_id={request_id}")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Verifying request {request_id}")
        
        # Verify the request exists
        request_info = state.get_request_info(request_id)
        if not request_info:
            error_msg = f"No active request found with ID: {request_id}"
            logger.warning(f"No active request found for {request_id}")
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the agent information
        agent_name = request_info.get("agent_name", "unknown")
        task_id = request_info.get("task_id", "unknown")
        session_id = request_info.get("session_id", "unknown")
        
        await ctx.report_progress(current=0.3, total=1.0, message=f"Preparing to cancel request to {agent_name}")
        
        # Get the URL for the agent
        if agent_name not in state.registry:
            error_msg = f"Agent '{agent_name}' not found in registry"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        url = state.registry[agent_name]
        logger.debug(f"Using URL for cancel: {url}")
        
        # Prepare the URL
        if url.endswith('/'):
            base_url = url
        else:
            base_url = url + '/'
        
        # Prepare the JSON-RPC cancel request
        json_rpc_request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/cancel",
            "params": {
                "id": task_id,
                "sessionId": session_id
            }
        }
        
        logger.debug(f"Sending cancel request: {json.dumps(json_rpc_request)}")
        
        await ctx.report_progress(current=0.5, total=1.0, message=f"Sending cancellation request to {agent_name}")
        
        # Use A2aMinClient to cancel task
        try:
            # Create A2A min client
            client = A2aMinClient.connect(url)
            
            logger.debug(f"Using A2aMinClient to cancel task for {agent_name}")
            
            # Cancel the task
            result = await client.cancel_task(
                task_id=task_id,
                session_id=session_id
            )
            
            logger.debug(f"Received cancel response: {result}")
            
            # Update our local state
            await state.update_request_status(
                request_id=request_id,
                status="canceled",
                message=f"Request to {agent_name} was canceled by the user",
                ctx=ctx
            )
            
            # Mark as completed in Context
            await ctx.report_progress(current=1.0, total=1.0, message=f"Successfully canceled request {request_id}")
            
            return {
                "status": "success",
                "message": f"Successfully canceled request {request_id}"
            }
            
        except Exception as e:
            logger.exception(f"Error canceling task: {e}")
            error_msg = f"Error canceling request: {str(e)}"
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    except Exception as e:
        logger.exception(f"Error in cancel_request: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define tool for exporting execution logs
@mcp.tool()
async def export_logs(request_id: str, ctx: Context, format_type: Literal["text", "json"] = "text", file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Export execution logs for a request, optionally saving to a file.
    
    Args:
        request_id: The unique request ID
        format_type: Output format (text or json)
        file_path: Optional file path to save the log to
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with the log data or file path
    """
    try:
        logger.debug(f"export_logs called for request_id={request_id}, format={format_type}")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Retrieving logs for request {request_id}")
        
        # Check if the request exists or existed
        log_entries = state.get_execution_log(request_id)
        if not log_entries:
            error_msg = f"No log entries found for request ID: {request_id}"
            logger.warning(f"No log entries found for {request_id}")
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        await ctx.report_progress(current=0.4, total=1.0, message=f"Formatting {len(log_entries)} log entries as {format_type}")
        
        # Format the log
        formatted_log = state.export_execution_log(request_id, format_type)
        
        # If a file path is provided, save the log to file
        if file_path:
            await ctx.report_progress(current=0.7, total=1.0, message=f"Saving log to {file_path}")
            
            try:
                # Ensure the directory exists
                os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
                
                # Write the log to file
                with open(file_path, 'w') as f:
                    f.write(formatted_log)
                
                await ctx.report_progress(current=1.0, total=1.0, message=f"Log exported to {file_path}")
                return {
                    "status": "success",
                    "message": f"Log exported to {file_path}",
                    "file_path": file_path
                }
            except Exception as e:
                logger.exception(f"Error writing log to file {file_path}: {e}")
                await ctx.error(f"Error writing log to file: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Error writing log to file: {str(e)}",
                    "log": formatted_log  # Return the log anyway
                }
        
        # Return the formatted log
        await ctx.report_progress(current=1.0, total=1.0, message=f"Exported {len(log_entries)} log entries")
        return {
            "status": "success",
            "log": formatted_log,
            "format": format_type,
            "entry_count": len(log_entries)
        }
    
    except Exception as e:
        logger.exception(f"Error in export_logs: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define tool for listing active requests
@mcp.tool()
async def save_pipeline_template(template_id: str, pipeline_definition: Dict[str, Any], 
                                ctx: Context) -> Dict[str, Any]:
    """
    Save a pipeline definition as a template for reuse.
    
    Args:
        template_id: Unique identifier for the template
        pipeline_definition: The pipeline definition in JSON format
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with template information
    """
    try:
        logger.debug(f"save_pipeline_template called with template_id={template_id}")
        
        await ctx.report_progress(current=0.1, total=1.0, message="Validating pipeline definition")
        
        # Import the required classes
        from a2a_mcp_server.pipeline import PipelineDefinition
        
        try:
            # Validate the pipeline definition
            pipeline_def = PipelineDefinition(pipeline_definition)
            logger.info(f"Validated pipeline template: {pipeline_def}")
        except (jsonschema.exceptions.ValidationError, ValueError) as e:
            error_msg = f"Invalid pipeline definition: {str(e)}"
            logger.error(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        await ctx.report_progress(current=0.5, total=1.0, message=f"Saving pipeline template: {template_id}")
        
        # Save the template
        state.pipeline_templates[template_id] = pipeline_definition
        
        await ctx.report_progress(current=1.0, total=1.0, message=f"Pipeline template saved: {template_id}")
        
        return {
            "status": "success",
            "message": f"Pipeline template saved: {template_id}",
            "template_id": template_id
        }
    except Exception as e:
        logger.exception(f"Error in save_pipeline_template: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def list_pipeline_templates(ctx: Context) -> Dict[str, Any]:
    """
    List all available pipeline templates.
    
    Args:
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with template information
    """
    try:
        logger.debug("list_pipeline_templates called")
        
        await ctx.report_progress(current=0.5, total=1.0, message="Retrieving pipeline templates")
        
        # Get template information
        templates = {}
        for template_id, definition in state.pipeline_templates.items():
            templates[template_id] = {
                "name": definition.get("name", "Unnamed Pipeline"),
                "description": definition.get("description", ""),
                "node_count": len(definition.get("nodes", [])),
                "version": definition.get("version", "1.0.0")
            }
        
        await ctx.report_progress(current=1.0, total=1.0, message=f"Found {len(templates)} pipeline templates")
        
        return {
            "status": "success",
            "message": f"Found {len(templates)} pipeline templates",
            "templates": templates
        }
    except Exception as e:
        logger.exception(f"Error in list_pipeline_templates: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def execute_pipeline_from_template(template_id: str, input_text: str, ctx: Context) -> Dict[str, Any]:
    """
    Execute a pipeline from a saved template.
    
    Args:
        template_id: The ID of the template to execute
        input_text: The input text to the pipeline
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with pipeline execution information
    """
    try:
        logger.debug(f"execute_pipeline_from_template called with template_id={template_id}")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Looking for template: {template_id}")
        
        # Check if the template exists
        if template_id not in state.pipeline_templates:
            error_msg = f"Pipeline template {template_id} not found"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the template definition
        pipeline_definition = state.pipeline_templates[template_id]
        
        await ctx.report_progress(current=0.3, total=1.0, message=f"Executing pipeline from template: {template_id}")
        
        # Execute the pipeline using the existing tool
        result = await execute_pipeline(pipeline_definition, input_text, ctx)
        
        return result
    except Exception as e:
        logger.exception(f"Error in execute_pipeline_from_template: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def cancel_pipeline(pipeline_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Cancel an in-progress pipeline execution.
    
    Args:
        pipeline_id: The ID of the pipeline execution to cancel
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with status information
    """
    try:
        logger.debug(f"cancel_pipeline called for pipeline_id={pipeline_id}")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Verifying pipeline: {pipeline_id}")
        
        # Check if the pipeline exists
        if pipeline_id not in state.pipelines:
            error_msg = f"Pipeline {pipeline_id} not found"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the pipeline state
        pipeline_state = state.pipelines[pipeline_id]
        
        await ctx.report_progress(current=0.3, total=1.0, message=f"Canceling pipeline: {pipeline_id}")
        
        # Update pipeline status to canceled
        from a2a_mcp_server.pipeline import PipelineStatus, NodeStatus
        
        pipeline_state.status = PipelineStatus.CANCELED
        pipeline_state.message = "Pipeline canceled by user"
        pipeline_state.end_time = time.time()
        
        # Cancel the current node if it's still in progress
        current_node = pipeline_state.get_current_node()
        if current_node and current_node.status in [NodeStatus.SUBMITTED, NodeStatus.WORKING, NodeStatus.INPUT_REQUIRED]:
            current_node.status = NodeStatus.CANCELED
            current_node.message = "Node canceled by user"
            current_node.end_time = time.time()
            
            # If we have a task_id and session_id, try to cancel the agent task
            if current_node.task_id and current_node.session_id:
                # Try to cancel the agent task
                agent_name = current_node.agent_name
                if agent_name in state.registry:
                    url = state.registry[agent_name]
                    
                    try:
                        # Prepare the URL
                        if url.endswith('/'):
                            base_url = url
                        else:
                            base_url = url + '/'
                        
                        # Prepare the JSON-RPC cancel request
                        json_rpc_request = {
                            "jsonrpc": "2.0",
                            "id": str(uuid.uuid4()),
                            "method": "tasks/cancel",
                            "params": {
                                "id": current_node.task_id,
                                "sessionId": current_node.session_id
                            }
                        }
                        
                        # Send the cancel request
                        await ctx.report_progress(current=0.5, total=1.0, message=f"Canceling agent task for {agent_name}")
                        
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.post(
                                f"{base_url}",
                                json=json_rpc_request,
                                headers={"Content-Type": "application/json"}
                            )
                            
                            logger.debug(f"Cancel response status: {response.status_code}")
                    except Exception as e:
                        logger.exception(f"Error canceling agent task: {e}")
        
        await ctx.report_progress(current=1.0, total=1.0, message=f"Pipeline canceled: {pipeline_id}")
        
        return {
            "status": "success",
            "message": f"Pipeline canceled: {pipeline_id}",
            "pipeline_id": pipeline_id
        }
    except Exception as e:
        logger.exception(f"Error in cancel_pipeline: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def send_pipeline_input(pipeline_id: str, node_id: str, input_text: str, ctx: Context) -> Dict[str, Any]:
    """
    Send input to a pipeline node that is in the input-required state.
    
    Args:
        pipeline_id: The ID of the pipeline execution
        node_id: The ID of the node requiring input
        input_text: The input text to send
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with status information
    """
    try:
        logger.debug(f"send_pipeline_input called for pipeline_id={pipeline_id}, node_id={node_id}")
        
        await ctx.report_progress(current=0.1, total=1.0, message=f"Verifying pipeline: {pipeline_id}")
        
        # Check if the pipeline exists
        if pipeline_id not in state.pipelines:
            error_msg = f"Pipeline {pipeline_id} not found"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the pipeline state
        pipeline_state = state.pipelines[pipeline_id]
        
        # Check if the node exists
        if node_id not in pipeline_state.nodes:
            error_msg = f"Node {node_id} not found in pipeline {pipeline_id}"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Get the node state
        from a2a_mcp_server.pipeline import NodeStatus
        
        node_state = pipeline_state.nodes[node_id]
        
        # Check if the node is in the input-required state
        if node_state.status != NodeStatus.INPUT_REQUIRED:
            error_msg = f"Node {node_id} is not waiting for input (status: {node_state.status})"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Check if we have the task_id and session_id
        if not node_state.task_id or not node_state.session_id:
            error_msg = f"Node {node_id} does not have task_id or session_id"
            logger.error(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        # Send input to the agent
        agent_name = node_state.agent_name
        if agent_name not in state.registry:
            error_msg = f"Agent {agent_name} not found in registry"
            logger.warning(error_msg)
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
        
        url = state.registry[agent_name]
        
        await ctx.report_progress(current=0.3, total=1.0, message=f"Sending input to {agent_name} for node {node_id}")
        
        try:
            # Prepare the URL
            if url.endswith('/'):
                base_url = url
            else:
                base_url = url + '/'
            
            # Prepare the JSON-RPC request
            request_id = str(uuid.uuid4())
            json_rpc_request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tasks/sendInput",
                "params": {
                    "id": node_state.task_id,
                    "sessionId": node_state.session_id,
                    "message": {
                        "role": "user",
                        "parts": [
                            {
                                "type": "text",
                                "text": input_text
                            }
                        ]
                    }
                }
            }
            
            # Update node status to working
            node_state.status = NodeStatus.WORKING
            node_state.message = f"Processing input: {input_text[:50]}..."
            
            # Update pipeline status
            pipeline_state.update_status()
            pipeline_state.update_progress()
            
            await ctx.report_progress(
                current=pipeline_state.progress,
                total=1.0,
                message=pipeline_state.message
            )
            
            # Send the request
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}",
                    json=json_rpc_request,
                    headers={"Content-Type": "application/json"}
                )
                
                logger.debug(f"Input response status: {response.status_code}")
                
                if response.status_code == 200:
                    try:
                        json_response = response.json()
                        logger.debug(f"Received input response: {json.dumps(json_response)}")
                        
                        # Update the node status based on the response
                        task_data = json_response.get("result", {})
                        status_data = task_data.get("status", {})
                        task_state = status_data.get("state", "working")
                        
                        # Update node status
                        node_state.message = status_data.get("message", f"Processing input: {input_text[:50]}...")
                        
                        # Update pipeline status
                        pipeline_state.update_status()
                        pipeline_state.update_progress()
                        
                        await ctx.report_progress(
                            current=pipeline_state.progress,
                            total=1.0,
                            message=pipeline_state.message
                        )
                        
                        return {
                            "status": "success",
                            "message": f"Input sent to node {node_id} in pipeline {pipeline_id}",
                            "node_status": task_state
                        }
                    except Exception as e:
                        error_msg = f"Error processing response: {str(e)}"
                        logger.exception(error_msg)
                        await ctx.error(error_msg)
                        return {"status": "error", "message": error_msg}
                else:
                    error_text = await response.text()
                    error_msg = f"Error sending input: HTTP {response.status_code}: {error_text}"
                    logger.error(error_msg)
                    
                    # Revert node status to input-required
                    node_state.status = NodeStatus.INPUT_REQUIRED
                    node_state.message = f"Error sending input: HTTP {response.status_code}"
                    
                    # Update pipeline status
                    pipeline_state.update_status()
                    pipeline_state.update_progress()
                    
                    await ctx.report_progress(
                        current=pipeline_state.progress,
                        total=1.0,
                        message=pipeline_state.message
                    )
                    
                    await ctx.error(error_msg)
                    return {"status": "error", "message": error_msg}
        except Exception as e:
            error_msg = f"Error sending input to node {node_id}: {str(e)}"
            logger.exception(error_msg)
            
            # Revert node status to input-required
            node_state.status = NodeStatus.INPUT_REQUIRED
            node_state.message = f"Error sending input: {str(e)}"
            
            # Update pipeline status
            pipeline_state.update_status()
            pipeline_state.update_progress()
            
            await ctx.report_progress(
                current=pipeline_state.progress,
                total=1.0,
                message=pipeline_state.message
            )
            
            await ctx.error(error_msg)
            return {"status": "error", "message": error_msg}
    except Exception as e:
        logger.exception(f"Error in send_pipeline_input: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def list_pipelines(ctx: Context) -> Dict[str, Any]:
    """
    List all active and completed pipelines.
    
    Args:
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with pipeline information
    """
    try:
        logger.debug("list_pipelines called")
        
        await ctx.report_progress(current=0.5, total=1.0, message="Retrieving pipelines")
        
        # Get pipeline information
        pipeline_info = {}
        for pipeline_id, pipeline_state in state.pipelines.items():
            pipeline_info[pipeline_id] = {
                "name": pipeline_state.definition.definition.get("name", "Unnamed Pipeline"),
                "status": pipeline_state.status.value if hasattr(pipeline_state.status, "value") else pipeline_state.status,
                "progress": pipeline_state.progress,
                "start_time": pipeline_state.start_time,
                "end_time": pipeline_state.end_time,
                "message": pipeline_state.message,
                "node_count": len(pipeline_state.nodes),
                "completed_nodes": sum(1 for node in pipeline_state.nodes.values() 
                                     if node.status == "completed" or getattr(node.status, "value", "") == "completed")
            }
        
        count_message = f"Found {len(pipeline_info)} pipelines"
        await ctx.report_progress(current=1.0, total=1.0, message=count_message)
        
        return {
            "status": "success",
            "message": count_message,
            "pipeline_count": len(pipeline_info),
            "pipelines": pipeline_info
        }
    except Exception as e:
        logger.exception(f"Error in list_pipelines: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def list_requests(ctx: Context) -> Dict[str, Any]:
    """
    List all active requests.
    
    Args:
        ctx: The FastMCP context for progress reporting
        
    Returns:
        Dict with active requests information
    """
    try:
        logger.debug("list_requests called")
        
        await ctx.report_progress(current=0.3, total=1.0, message="Retrieving active requests")
        
        # Get active request IDs
        active_requests = {}
        for request_id, request_info in state.active_requests.items():
            # Create a simplified view of the request
            active_requests[request_id] = {
                "agent_name": request_info.get("agent_name", "unknown"),
                "status": request_info.get("status", "unknown"),
                "progress": request_info.get("progress", 0.0),
                "start_time": request_info.get("start_time", 0),
                "last_message": request_info.get("last_message", ""),
                "chain_position": request_info.get("chain_position", {"current": 1, "total": 1}),
                "update_count": len(request_info.get("updates", []))
            }
        
        count_message = f"Found {len(active_requests)} active requests"
        await ctx.report_progress(current=1.0, total=1.0, message=count_message)
        
        return {
            "status": "success",
            "request_count": len(active_requests),
            "requests": active_requests
        }
    
    except Exception as e:
        logger.exception(f"Error in list_requests: {str(e)}")
        await ctx.error(f"Error: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# FastMCP 2.0 handles connections via Context, so we don't need to register handlers


def main():
    """Run the MCP server."""
    # Import the pipeline module and initialize the pipeline engine
    from a2a_mcp_server.pipeline import PipelineExecutionEngine
    state.pipeline_engine = PipelineExecutionEngine(state)
    return mcp


def main_cli():
    """CLI entry point for running the server."""
    # Get transport from environment variable or use stdio as default
    import os
    import argparse
    
    parser = argparse.ArgumentParser(description="A2A MCP Server")
    parser.add_argument(
        "--transport", 
        choices=["stdio", "http"], 
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport mode for the server (stdio or http)"
    )
    
    args = parser.parse_args()
    
    # Run the server with specified transport
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main_cli()