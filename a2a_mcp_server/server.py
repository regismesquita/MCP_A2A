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
import uuid
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
from a2a_min.base.client.card_resolver import A2ACardResolver
from a2a_min.base.types import AgentCard, Message, TextPart, TaskSendParams

# Create a class to store persistent state
class ServerState:
    def __init__(self):
        self.registry = {}  # name -> url
        self.cache = {}  # name -> AgentCard

# Create a single instance to be used throughout the app
state = ServerState()


async def fetch_agent_card(url: str) -> Optional[AgentCard]:
    """Fetch agent card from an A2A server using A2ACardResolver."""
    try:
        logger.debug(f"Fetching agent card from {url}")
        
        # Use the A2ACardResolver from a2a_min to get the agent card
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
        
        # Use the A2ACardResolver to fetch agent cards
        agents = {}
        
        for name, url in state.registry.items():
            try:
                logger.debug(f"Fetching card for {name} from {url}")
                card = await fetch_agent_card(url)
                
                if card:
                    # Store the card data in a format suitable for JSON response
                    agents[name] = card.model_dump()
                    # Update the cache with the fetched card
                    state.cache[name] = card
                else:
                    logger.error(f"Failed to fetch agent card for {name}")
            except Exception as e:
                logger.exception(f"Error fetching card for {name}: {e}")
        
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
            # Get or create agent card if needed
            if agent_name not in state.cache:
                logger.debug(f"Agent card not in cache, fetching for {agent_name}")
                card = await fetch_agent_card(url)
                if card:
                    state.cache[agent_name] = card
                else:
                    logger.error(f"Failed to fetch agent card for {agent_name}")
                    return {
                        "status": "error",
                        "message": f"Failed to fetch agent card for {agent_name}"
                    }
            
            # Create a client using A2aMinClient
            try:
                # Connect to the A2A server
                logger.debug(f"Connecting to A2A server at {url}")
                client = A2aMinClient.connect(url)
                
                # Prepare message and send
                logger.debug(f"Sending message to agent {agent_name}: {prompt[:50]}...")
                task_id = str(uuid.uuid4())
                session_id = str(uuid.uuid4())
                
                # Send the message and get response
                task = await client.send_message(
                    message=prompt,
                    task_id=task_id,
                    session_id=session_id
                )
                
                # Process and return the response
                logger.debug(f"Received task response: {task}")
                
                response_data = {
                    "status": "success",
                    "task_id": task.id,
                    "state": task.status.state if hasattr(task, 'status') else "unknown",
                    "artifacts": []
                }
                
                # Extract artifacts
                artifacts = task.artifacts if hasattr(task, 'artifacts') else []
                if artifacts:
                    logger.debug(f"Received {len(artifacts)} artifacts")
                    for artifact in artifacts:
                        artifact_data = {
                            "name": artifact.name,
                            "content": []
                        }
                        
                        for part in artifact.parts:
                            part_type = part.type
                            if part_type == "text":
                                artifact_data["content"].append({
                                    "type": "text", 
                                    "text": part.text
                                })
                            elif part_type == "file":
                                file_data = part.file
                                artifact_data["content"].append({
                                    "type": "file", 
                                    "name": file_data.name,
                                    "mime_type": file_data.mimeType
                                })
                            elif part_type == "data":
                                artifact_data["content"].append({
                                    "type": "data", 
                                    "data": part.data
                                })
                        
                        response_data["artifacts"].append(artifact_data)
                
                logger.info(f"Successfully called agent {agent_name}")
                return response_data
                
            except Exception as e:
                logger.exception(f"Error using A2aMinClient: {e}")
                return {
                    "status": "error",
                    "message": f"Error calling agent with A2aMinClient: {str(e)}"
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
