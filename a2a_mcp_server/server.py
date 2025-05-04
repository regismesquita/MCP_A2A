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
from mcp.server.fastmcp import FastMCP

# A2A imports
from a2a_min import A2aMinClient
from a2a_min.base.types import AgentCard, Message, TextPart

# Create a class to store persistent state
class ServerState:
    def __init__(self):
        self.registry = {}  # name -> url
        self.cache = {}  # name -> AgentCard
        self.active_requests = {}  # request_id -> status info
        self.execution_logs = {}  # request_id -> execution log
        self.last_update_time = {}  # request_id -> timestamp
        
        # Create a throttler for updates with 1-second interval, batch size of 5
        self.update_throttler = UpdateThrottler[Dict[str, Any]](
            min_interval=1.0,
            batch_size=5,
            max_queue_size=100
        )
    
    def track_request(self, request_id: str, agent_name: str, task_id: str, 
                     session_id: str) -> None:
        """
        Add a new request to active_requests.
        
        Args:
            request_id: The unique request ID
            agent_name: The name of the agent being called
            task_id: The A2A task ID
            session_id: The A2A session ID
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
            "updates": []
        }
        self.last_update_time[request_id] = time.time()
        self.execution_logs[request_id] = []
        logger.debug(f"Tracking new request: {request_id}")
    
    def update_request_status(self, request_id: str, status: str, 
                             progress: Optional[float] = None, 
                             message: Optional[str] = None,
                             chain_position: Optional[Dict[str, int]] = None) -> bool:
        """
        Update the status of an active request.
        
        Args:
            request_id: The unique request ID
            status: The new status (submitted, working, input-required, completed, failed, canceled)
            progress: Optional progress value between 0 and 1
            message: Optional status message
            chain_position: Optional dict with current and total position in agent chain
            
        Returns:
            bool: True if update should be sent (respects throttling), False otherwise
        """
        if request_id not in self.active_requests:
            logger.warning(f"Attempted to update unknown request: {request_id}")
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
    
    def complete_request(self, request_id: str, status: str = "completed", 
                        message: Optional[str] = None) -> Dict[str, Any]:
        """
        Mark a request as completed and return its final state.
        
        Args:
            request_id: The unique request ID
            status: Final status (completed, failed, or canceled)
            message: Optional final status message
            
        Returns:
            Dict with final request information
        """
        if request_id not in self.active_requests:
            logger.warning(f"Attempted to complete unknown request: {request_id}")
            return {}
        
        # Get the current request info
        request = self.active_requests[request_id]
        
        # Update with final state
        self.update_request_status(
            request_id=request_id,
            status=status,
            progress=1.0 if status == "completed" else request["progress"],
            message=message or f"Request {status}"
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
    """Fetch agent card from an A2A server."""
    try:
        logger.debug(f"Fetching agent card from {url}")
        
        # Extract the base URL
        if url.endswith('/'):
            base_url = url
        else:
            base_url = url + '/'
        
        # Directly fetch the agent card from the well-known URL
        import httpx
        well_known_url = f"{base_url}.well-known/agent.json"
        logger.debug(f"Requesting from {well_known_url}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(well_known_url)
            logger.debug(f"Response status: {response.status_code}")
            if response.status_code == 200:
                card_data = response.json()
                logger.debug(f"Received card data: {card_data}")
                # Convert to AgentCard
                card = AgentCard(**card_data)
                return card
            else:
                logger.error(f"Failed to fetch agent card: HTTP {response.status_code}")
                logger.debug(f"Response content: {response.text}")
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


# Create the FastMCP server with support for both Streamable HTTP and SSE transports
mcp = FastMCP(
    name="A2A MCP Server",
    streaming_enabled=True,  # Enable streaming support
    streaming_endpoint="/mcp",  # Single endpoint for bi-directional communication
    connection_timeout=600.0,  # 10 minutes timeout for long-running operations
    auto_reconnect=True,  # Enable automatic reconnection for dropped connections
    max_reconnect_attempts=5,  # Maximum reconnection attempts
    reconnect_delay=2.0  # Delay between reconnection attempts in seconds
)

# Register tools
@mcp.tool()
async def a2a_server_registry(action: Literal["add", "remove"], name: str, url: Optional[str] = None) -> Dict[str, Any]:
    """
    Add or remove an A2A server URL.
    
    Args:
        action: Either "add" or "remove"
        name: Name of the server
        url: URL of the server (required for "add" action)
        
    Returns:
        Dict with status and message
    """
    try:
        logger.debug(f"a2a_server_registry called with action={action}, name={name}, url={url}")
        logger.debug(f"Registry before: {state.registry}")
        
        if action == "add":
            if not url:
                logger.warning("URL is required for add action")
                return {"status": "error", "message": "URL is required for add action"}
            
            # Add to registry
            state.registry[name] = url
            logger.info(f"Added A2A server: {name} -> {url}")
            logger.debug(f"Registry after: {state.registry}")
            
            # Update the cache asynchronously
            asyncio.create_task(update_agent_cache())
            
            return {
                "status": "success", 
                "message": f"Added A2A server: {name}",
                "registry": state.registry
            }
        
        elif action == "remove":
            if name in state.registry:
                # Remove from registry
                del state.registry[name]
                
                # Remove from cache if present
                if name in state.cache:
                    del state.cache[name]
                
                logger.info(f"Removed A2A server: {name}")
                logger.debug(f"Registry after: {state.registry}")
                
                return {
                    "status": "success", 
                    "message": f"Removed A2A server: {name}",
                    "registry": state.registry
                }
            else:
                logger.warning(f"Server {name} not found in registry")
                return {
                    "status": "error", 
                    "message": f"Server {name} not found in registry"
                }
        
        logger.error(f"Invalid action: {action}")
        return {"status": "error", "message": "Invalid action. Use 'add' or 'remove'"}
    except Exception as e:
        logger.exception(f"Error in a2a_server_registry: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def list_agents() -> Dict[str, Any]:
    """
    List available agents with their agent cards.
    
    Returns:
        Dict with agents and their cards
    """
    try:
        logger.debug("list_agents called")
        logger.debug(f"Current registry: {state.registry}")
        
        # Directly fetch agent cards
        import httpx
        agents = {}
        
        for name, url in state.registry.items():
            try:
                logger.debug(f"Fetching card for {name} from {url}")
                
                # Prepare URL
                if url.endswith('/'):
                    base_url = url
                else:
                    base_url = url + '/'
                    
                well_known_url = f"{base_url}.well-known/agent.json"
                logger.debug(f"Requesting from {well_known_url}")
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(well_known_url)
                    logger.debug(f"Response status: {response.status_code}")
                    
                    if response.status_code == 200:
                        card_data = response.json()
                        logger.debug(f"Received card data: {card_data}")
                        agents[name] = card_data
                    else:
                        logger.error(f"Failed to fetch agent card for {name}: HTTP {response.status_code}")
            except Exception as e:
                logger.exception(f"Error fetching card for {name}: {e}")
        
        # Update the cache with the fetched cards
        state.cache = {}
        for name, card_data in agents.items():
            try:
                state.cache[name] = AgentCard(**card_data)
            except Exception as e:
                logger.exception(f"Error converting card data for {name}: {e}")
        
        logger.info(f"Listing {len(agents)} agents")
        return {
            "agent_count": len(agents),
            "agents": agents
        }
    except Exception as e:
        logger.exception(f"Error in list_agents: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

@mcp.tool()
async def call_agent(agent_name: str, prompt: str) -> Dict[str, Any]:
    """
    Call an agent with a prompt and stream progress updates.
    
    Args:
        agent_name: Name of the agent to call
        prompt: Prompt to send to the agent
        
    Returns:
        Dict with initial response and status (streaming updates follow via connection upgrade)
    """
    try:
        logger.debug(f"call_agent called with agent_name={agent_name}, prompt={prompt[:50]}...")
        logger.debug(f"Current registry: {state.registry}")
        
        # Verify the agent exists
        if agent_name not in state.registry:
            logger.warning(f"Agent '{agent_name}' not found in registry")
            return {
                "status": "error",
                "message": f"Agent '{agent_name}' not found in registry"
            }
        
        # Get the URL for the agent
        url = state.registry[agent_name]
        logger.debug(f"Using URL: {url}")
        
        try:
            # Create a client and send message directly via HTTP
            import httpx
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
            
            # Update initial status
            state.update_request_status(
                request_id=mcp_request_id,
                status="submitted",
                message=f"Sending prompt to {agent_name}"
            )
            
            # Prepare the JSON-RPC request using tasks/sendSubscribe for streaming updates
            json_rpc_request = {
                "jsonrpc": "2.0",
                "id": mcp_request_id,
                "method": "tasks/sendSubscribe",  # Use streaming method
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
            
            # Create a background task to handle the streaming response
            asyncio.create_task(
                process_streaming_response(
                    url=base_url,
                    agent_name=agent_name,
                    mcp_request_id=mcp_request_id,
                    json_rpc_request=json_rpc_request
                )
            )
            
            # Respond immediately with a connection upgrade request
            logger.debug(f"Returning connection upgrade request for {mcp_request_id}")
            return {
                "status": "upgrading_connection",
                "message": f"Connecting to {agent_name} agent...",
                "request_id": mcp_request_id,
                "agent_name": agent_name,
                "needs_upgrade": True,  # Signal to MCP that we need a connection upgrade
                "streaming": True,      # Indicate that this is a streaming response
                "final": False          # Indicate that more updates will follow
            }
        
        except Exception as e:
            logger.exception(f"Error calling agent {agent_name}: {e}")
            return {
                "status": "error",
                "message": f"Error calling agent: {str(e)}"
            }
    except Exception as e:
        logger.exception(f"Error in call_agent: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}


async def process_streaming_response(
    url: str, 
    agent_name: str, 
    mcp_request_id: str, 
    json_rpc_request: Dict[str, Any]
) -> None:
    """
    Process streaming response from an A2A agent.
    
    Args:
        url: The base URL of the agent
        agent_name: The name of the agent
        mcp_request_id: The MCP request ID
        json_rpc_request: The JSON-RPC request to send
    """
    logger.debug(f"Starting streaming response processing for {mcp_request_id}")
    
    try:
        import httpx
        import json
        
        # Update status to working
        state.update_request_status(
            request_id=mcp_request_id,
            status="working",
            message=f"Connected to {agent_name}, waiting for response..."
        )
        
        # Send the request and process streaming responses
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream(
                "POST",
                url,
                json=json_rpc_request,
                headers={"Content-Type": "application/json"},
                timeout=600.0  # 10 minutes timeout
            ) as response:
                logger.debug(f"Streaming response started with status code: {response.status_code}")
                
                if response.status_code != 200:
                    error_text = await response.text()
                    logger.error(f"Error response: HTTP {response.status_code}: {error_text}")
                    state.update_request_status(
                        request_id=mcp_request_id,
                        status="failed",
                        message=f"HTTP error {response.status_code}: {error_text[:100]}..."
                    )
                    return
                
                # Process the streaming response
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    
                    # Try to extract complete JSON objects from the buffer
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not line.strip():
                            continue
                        
                        try:
                            update = json.loads(line)
                            await process_agent_update(mcp_request_id, agent_name, update)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Error decoding JSON from chunk: {line[:100]}... - {e}")
                
                # Process any remaining data in the buffer
                if buffer.strip():
                    try:
                        update = json.loads(buffer)
                        await process_agent_update(mcp_request_id, agent_name, update)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Error decoding JSON from final chunk: {buffer[:100]}... - {e}")
                
                # Mark as completed if we haven't already
                request_info = state.get_request_info(mcp_request_id)
                if request_info and request_info.get("status") not in ["completed", "failed", "canceled"]:
                    state.update_request_status(
                        request_id=mcp_request_id,
                        status="completed",
                        progress=1.0,
                        message=f"Completed response from {agent_name}"
                    )
    
    except Exception as e:
        logger.exception(f"Error processing streaming response for {mcp_request_id}: {e}")
        # Update status to failed
        state.update_request_status(
            request_id=mcp_request_id,
            status="failed",
            message=f"Error: {str(e)}"
        )


async def process_agent_update(mcp_request_id: str, agent_name: str, update: Dict[str, Any]) -> None:
    """
    Process an update from an A2A agent.
    
    Args:
        mcp_request_id: The MCP request ID
        agent_name: The name of the agent
        update: The update data from the agent
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
    
    # Update request status
    should_send = state.update_request_status(
        request_id=mcp_request_id,
        status=mapped_state,
        progress=progress,
        message=status_message
    )
    
    logger.debug(f"Updated request status for {mcp_request_id}: {mapped_state}, should_send={should_send}")


async def handle_streaming_connection(request_id: str, connection) -> None:
    """
    Handle a streaming connection for a long-running task.
    
    Args:
        request_id: The unique request ID
        connection: The streaming connection object
    """
    logger.debug(f"Handling streaming connection for request_id: {request_id}")
    
    # Get the request info
    request_info = state.get_request_info(request_id)
    if not request_info:
        logger.warning(f"No active request found for {request_id}")
        await connection.send_json({
            "status": "error",
            "message": "No active request found",
            "final": True
        })
        return
    
    # Send initial status
    await connection.send_json({
        "status": "connected",
        "request_id": request_id,
        "message": "Connection established for streaming updates",
        "request_info": request_info,
        "final": False
    })
    
    # Set up times for heartbeat and checking for updates
    last_heartbeat_time = time.time()
    last_update_check = time.time()
    
    try:
        # Main connection loop
        while request_id in state.active_requests:
            # Get the latest request info
            current_info = state.get_request_info(request_id)
            current_time = time.time()
            
            # Process any batched updates from the throttler
            pending_updates = state.update_throttler.get_pending_updates(request_id)
            if pending_updates and (current_time - last_update_check) >= 0.5:
                last_update_check = current_time
                last_heartbeat_time = current_time  # Reset heartbeat timer
                
                # Send all pending updates as a batch
                for update in pending_updates:
                    is_final = update["status"] in ["completed", "failed", "canceled"]
                    
                    await connection.send_json({
                        "status": update["status"],
                        "request_id": request_id,
                        "message": update["message"],
                        "timestamp": update["timestamp"],
                        "progress": update["progress"],
                        "chain_position": update["chain_position"],
                        "final": is_final
                    })
                    
                    # If this is a final update, we're done
                    if is_final:
                        logger.debug(f"Sent final update for request {request_id}")
                        state.update_throttler.clear(request_id)
                        return
                
                # Clear the updates since we've sent them
                state.update_throttler.clear(request_id)
            
            # Send heartbeat every 30 seconds if no updates
            if (current_time - last_heartbeat_time) >= 30.0:
                await connection.send_json({
                    "status": "heartbeat",
                    "request_id": request_id,
                    "timestamp": current_time,
                    "final": False
                })
                last_heartbeat_time = current_time
            
            # Check for request completion or cancellation
            if current_info.get("status") in ["completed", "failed", "canceled"]:
                # Send final update
                await connection.send_json({
                    "status": current_info.get("status"),
                    "request_id": request_id,
                    "message": current_info.get("last_message"),
                    "request_info": current_info,
                    "final": True
                })
                logger.debug(f"Sent final update for request {request_id}")
                break
            
            # Small delay to prevent busy waiting
            await asyncio.sleep(0.1)
    
    except Exception as e:
        logger.exception(f"Error in streaming connection for {request_id}: {e}")
        try:
            # Try to send error message
            await connection.send_json({
                "status": "error",
                "message": f"Connection error: {str(e)}",
                "final": True
            })
        except:
            pass
    
    finally:
        # Update request status if it's still active and not in a final state
        if request_id in state.active_requests:
            request_info = state.get_request_info(request_id)
            if request_info and request_info.get("status") not in ["completed", "failed", "canceled"]:
                state.complete_request(
                    request_id=request_id,
                    status="canceled",
                    message="Connection closed"
                )
                logger.debug(f"Marked request {request_id} as canceled due to connection closure")
                
        # Clear any pending updates
        state.update_throttler.clear(request_id)


async def handle_connection_upgrade(request_id: str) -> None:
    """
    Initiate a connection upgrade for a long-running task.
    
    Args:
        request_id: The unique request ID
    
    Returns:
        Dict with connection upgrade information
    """
    logger.debug(f"Initiating connection upgrade for request_id: {request_id}")
    
    # Get the request info
    request_info = state.get_request_info(request_id)
    if not request_info:
        logger.warning(f"No active request found for {request_id}")
        return {
            "status": "error",
            "message": "No active request found"
        }
    
    # Return connection upgrade information
    return {
        "status": "upgrade_connection",
        "request_id": request_id,
        "message": "Initiating connection upgrade for streaming updates",
        "upgrade_info": {
            "protocol": "streamable_http",
            "endpoint": "/mcp",
            "request_id": request_id
        }
    }


# Define new tool for sending additional input to an agent
@mcp.tool()
async def send_input(request_id: str, input_text: str) -> Dict[str, Any]:
    """
    Send additional input to an agent that has requested it.
    
    Args:
        request_id: The unique request ID for the in-progress task
        input_text: The input text to send to the agent
        
    Returns:
        Dict with response information
    """
    try:
        logger.debug(f"send_input called for request_id={request_id}, input={input_text[:50]}...")
        
        # Verify the request exists
        request_info = state.get_request_info(request_id)
        if not request_info:
            logger.warning(f"No active request found for {request_id}")
            return {
                "status": "error",
                "message": f"No active request found with ID: {request_id}"
            }
        
        # Verify the request is in the input-required state
        current_status = request_info.get("status", "unknown")
        if current_status != "input-required":
            logger.warning(f"Request {request_id} is not in input-required state (status: {current_status})")
            return {
                "status": "error",
                "message": f"Request {request_id} is not waiting for input (status: {current_status})"
            }
        
        # Get the agent information
        agent_name = request_info.get("agent_name", "unknown")
        task_id = request_info.get("task_id", "unknown")
        session_id = request_info.get("session_id", "unknown")
        
        # Get the URL for the agent
        if agent_name not in state.registry:
            logger.warning(f"Agent '{agent_name}' not found in registry")
            return {
                "status": "error",
                "message": f"Agent '{agent_name}' not found in registry"
            }
        
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
        state.update_request_status(
            request_id=request_id,
            status="working",
            message=f"Sent additional input to {agent_name}, waiting for response..."
        )
        
        # Send the input request
        import httpx
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
                    
                    # Extract the task data
                    task_data = json_response.get("result", {})
                    status_data = task_data.get("status", {})
                    task_state = status_data.get("state", "working")
                    
                    return {
                        "status": "success",
                        "message": f"Successfully sent input to {agent_name}",
                        "task_state": task_state,
                        "needs_upgrade": True,  # Signal that we need to continue streaming updates
                        "streaming": True,
                        "final": False,
                        "request_id": request_id
                    }
                except json.JSONDecodeError as e:
                    logger.exception(f"Error decoding JSON response: {e}")
                    return {
                        "status": "error",
                        "message": f"Error decoding response: {str(e)}"
                    }
            else:
                error_text = await response.text()
                logger.error(f"Error sending input: HTTP {response.status_code}: {error_text}")
                
                # Update status back to input-required since the input failed
                state.update_request_status(
                    request_id=request_id,
                    status="input-required",
                    message=f"Error sending input to {agent_name}: HTTP {response.status_code}"
                )
                
                return {
                    "status": "error",
                    "message": f"Error sending input: HTTP {response.status_code}"
                }
    
    except Exception as e:
        logger.exception(f"Error in send_input: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define new tool for cancellation
@mcp.tool()
async def cancel_request(request_id: str) -> Dict[str, Any]:
    """
    Cancel an in-progress agent request.
    
    Args:
        request_id: The unique request ID to cancel
        
    Returns:
        Dict with status information
    """
    try:
        logger.debug(f"cancel_request called for request_id={request_id}")
        
        # Verify the request exists
        request_info = state.get_request_info(request_id)
        if not request_info:
            logger.warning(f"No active request found for {request_id}")
            return {
                "status": "error",
                "message": f"No active request found with ID: {request_id}"
            }
        
        # Get the agent information
        agent_name = request_info.get("agent_name", "unknown")
        task_id = request_info.get("task_id", "unknown")
        session_id = request_info.get("session_id", "unknown")
        
        # Get the URL for the agent
        if agent_name not in state.registry:
            logger.warning(f"Agent '{agent_name}' not found in registry")
            return {
                "status": "error",
                "message": f"Agent '{agent_name}' not found in registry"
            }
        
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
        
        # Send the cancel request
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}",
                json=json_rpc_request,
                headers={"Content-Type": "application/json"}
            )
            
            logger.debug(f"Cancel response status: {response.status_code}")
            
            if response.status_code == 200:
                # Update our local state
                state.update_request_status(
                    request_id=request_id,
                    status="canceled",
                    message=f"Request to {agent_name} was canceled by the user"
                )
                
                # Complete the request
                state.complete_request(
                    request_id=request_id,
                    status="canceled",
                    message=f"Request to {agent_name} was canceled by the user"
                )
                
                return {
                    "status": "success",
                    "message": f"Successfully canceled request {request_id}"
                }
            else:
                error_text = await response.text()
                logger.error(f"Error canceling request: HTTP {response.status_code}: {error_text}")
                return {
                    "status": "error",
                    "message": f"Error canceling request: HTTP {response.status_code}"
                }
    
    except Exception as e:
        logger.exception(f"Error in cancel_request: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define tool for exporting execution logs
@mcp.tool()
async def export_logs(request_id: str, format_type: Literal["text", "json"] = "text", file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Export execution logs for a request, optionally saving to a file.
    
    Args:
        request_id: The unique request ID
        format_type: Output format (text or json)
        file_path: Optional file path to save the log to
        
    Returns:
        Dict with the log data or file path
    """
    try:
        logger.debug(f"export_logs called for request_id={request_id}, format={format_type}")
        
        # Check if the request exists or existed
        log_entries = state.get_execution_log(request_id)
        if not log_entries:
            logger.warning(f"No log entries found for {request_id}")
            return {
                "status": "error",
                "message": f"No log entries found for request ID: {request_id}"
            }
        
        # Format the log
        formatted_log = state.export_execution_log(request_id, format_type)
        
        # If a file path is provided, save the log to file
        if file_path:
            try:
                # Ensure the directory exists
                os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
                
                # Write the log to file
                with open(file_path, 'w') as f:
                    f.write(formatted_log)
                
                return {
                    "status": "success",
                    "message": f"Log exported to {file_path}",
                    "file_path": file_path
                }
            except Exception as e:
                logger.exception(f"Error writing log to file {file_path}: {e}")
                return {
                    "status": "error",
                    "message": f"Error writing log to file: {str(e)}",
                    "log": formatted_log  # Return the log anyway
                }
        
        # Return the formatted log
        return {
            "status": "success",
            "log": formatted_log,
            "format": format_type,
            "entry_count": len(log_entries)
        }
    
    except Exception as e:
        logger.exception(f"Error in export_logs: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Define tool for listing active requests
@mcp.tool()
async def list_requests() -> Dict[str, Any]:
    """
    List all active requests.
    
    Returns:
        Dict with active requests information
    """
    try:
        logger.debug("list_requests called")
        
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
        
        return {
            "status": "success",
            "request_count": len(active_requests),
            "requests": active_requests
        }
    
    except Exception as e:
        logger.exception(f"Error in list_requests: {str(e)}")
        return {"status": "error", "message": f"Error: {str(e)}"}

# Register connection handlers with MCP
mcp.register_connection_handler(handle_streaming_connection)
mcp.register_upgrade_handler(handle_connection_upgrade)


def main():
    """Run the MCP server."""
    return mcp


def main_cli():
    """CLI entry point for running the server."""
    mcp.run()


if __name__ == "__main__":
    main_cli()