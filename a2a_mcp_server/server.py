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
from typing import Dict, List, Optional, Union, Literal, Any

# Set up logging to stderr with more detailed format
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for more detailed logs
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

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


# Create the FastMCP server
mcp = FastMCP("A2A MCP Server")

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
    Call an agent with a prompt.
    
    Args:
        agent_name: Name of the agent to call
        prompt: Prompt to send to the agent
        
    Returns:
        Dict with response from the agent
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
                
            # Prepare the JSON-RPC request
            request_id = str(uuid.uuid4())
            json_rpc_request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tasks/send",
                "params": {
                    "id": str(uuid.uuid4()),
                    "sessionId": str(uuid.uuid4()),
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
            
            # Send the request
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{base_url}",
                    json=json_rpc_request,
                    headers={"Content-Type": "application/json"}
                )
                
                logger.debug(f"Response status: {response.status_code}")
                
                if response.status_code == 200:
                    try:
                        json_response = response.json()
                        logger.debug(f"Received response: {json.dumps(json_response)}")
                        
                        task_data = json_response.get("result", {})
                        
                        # Process and return the response
                        response_data = {
                            "status": "success",
                            "task_id": task_data.get("id", "unknown"),
                            "state": task_data.get("status", {}).get("state", "unknown"),
                            "artifacts": []
                        }
                        
                        # Extract artifacts
                        artifacts = task_data.get("artifacts", [])
                        if artifacts:
                            logger.debug(f"Received {len(artifacts)} artifacts")
                            for artifact in artifacts:
                                artifact_data = {
                                    "name": artifact.get("name", ""),
                                    "content": []
                                }
                                
                                for part in artifact.get("parts", []):
                                    part_type = part.get("type")
                                    if part_type == "text":
                                        artifact_data["content"].append({
                                            "type": "text", 
                                            "text": part.get("text", "")
                                        })
                                    elif part_type == "file":
                                        file_data = part.get("file", {})
                                        artifact_data["content"].append({
                                            "type": "file", 
                                            "name": file_data.get("name", ""),
                                            "mime_type": file_data.get("mimeType", "")
                                        })
                                    elif part_type == "data":
                                        artifact_data["content"].append({
                                            "type": "data", 
                                            "data": part.get("data", {})
                                        })
                                
                                response_data["artifacts"].append(artifact_data)
                        
                        logger.info(f"Successfully called agent {agent_name}")
                        return response_data
                    except json.JSONDecodeError as e:
                        logger.exception(f"Error decoding JSON response: {e}")
                        return {
                            "status": "error",
                            "message": f"Error decoding response: {str(e)}"
                        }
                else:
                    logger.error(f"Error response: HTTP {response.status_code}")
                    return {
                        "status": "error",
                        "message": f"HTTP error {response.status_code}: {response.text}"
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


def main():
    """Run the MCP server."""
    return mcp


def main_cli():
    """CLI entry point for running the server."""
    mcp.run()


if __name__ == "__main__":
    main_cli()