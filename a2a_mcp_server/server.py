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

# A2A imports - using python-a2a for better compatibility
from python_a2a import A2AClient, AgentNetwork
from python_a2a.models.agent import AgentCard
from python_a2a.models.message import Message
from python_a2a.models.content import TextContent
from python_a2a.models.task import Task

# Create a class to store persistent state
class ServerState:
    def __init__(self):
        # Use AgentNetwork for better management and discovery capabilities
        # Initialize with just the name, agents will be added later
        self.agent_network = AgentNetwork(name="A2A MCP Server Network")
        # For backward compatibility
        self.registry = {}  # name -> url
        self.cache = {}  # name -> AgentCard

# Create a single instance to be used throughout the app
state = ServerState()


async def fetch_agent_card(url: str) -> Optional[AgentCard]:
    """Fetch agent card from an A2A server using the python-a2a client library.
    
    The python-a2a library has robust handling for agent card discovery with multiple fallback mechanisms:
    - Tries multiple endpoints (/agent.json and /a2a/agent.json)
    - Handles different response types (JSON and HTML)
    - Follows redirects when needed
    """
    try:
        logger.debug(f"Fetching agent card from {url}")
        
        # Create a client that will automatically fetch the agent card
        # The A2AClient constructor handles all the agent card resolution
        # including trying multiple endpoints and following redirects
        client = A2AClient(url)
        
        # Extract the agent card from the client
        if client.agent_card:
            logger.debug(f"Successfully fetched agent card: {client.agent_card}")
            return client.agent_card
        else:
            logger.error(f"Failed to fetch agent card from {url}")
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
            
            # Add to registry for backward compatibility
            state.registry[name] = url
            
            # Add to agent network - this will automatically fetch the agent card
            # The methods are synchronous in python-a2a
            state.agent_network.add(name, url)
            
            logger.info(f"Added A2A server: {name} -> {url}")
            logger.debug(f"Registry after: {state.registry}")
            
            # Get the freshly fetched agent card and update our cache too
            agent = state.agent_network.get_agent(name)
            if agent and agent.agent_card:
                state.cache[name] = agent.agent_card
            
            return {
                "status": "success", 
                "message": f"Added A2A server: {name}",
                "registry": state.registry
            }
        
        elif action == "remove":
            if name in state.registry:
                # Remove from registry for backward compatibility
                del state.registry[name]
                
                # Remove from cache if present
                if name in state.cache:
                    del state.cache[name]
                
                # Remove from agent network
                if name in state.agent_network.agents:
                    state.agent_network.remove(name)
                
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
        
        # Use AgentNetwork to get all agents
        agents = {}
        
        # Get agent information from the AgentNetwork
        # The method is synchronous in python-a2a
        agent_list = state.agent_network.list_agents()
        logger.debug(f"Got agent list from network: {agent_list}")
        
        for agent_info in agent_list:
            try:
                name = agent_info['name']
                # Store the agent info in a format suitable for JSON response
                agents[name] = agent_info
                
                # Also update our registry and cache for backward compatibility
                if 'url' in agent_info:
                    state.registry[name] = agent_info['url']
                if 'agent_card' in agent_info:
                    state.cache[name] = agent_info['agent_card']
                    
            except Exception as e:
                logger.exception(f"Error processing agent info: {e}")
        
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
        
        # Verify the agent exists in the network
        if agent_name not in state.agent_network.agents:
            logger.warning(f"Agent '{agent_name}' not found in agent network")
            return {
                "status": "error",
                "message": f"Agent '{agent_name}' not found in agent network"
            }
        
        try:
            # Get the agent directly from the network
            logger.debug(f"Getting agent {agent_name} from the network")
            client = state.agent_network.get_agent(agent_name)
            
            if not client:
                logger.error(f"Failed to get agent client for {agent_name}")
                return {
                    "status": "error",
                    "message": f"Failed to get agent client for {agent_name}"
                }
                
            # The client is already connected and ready to use
            logger.debug(f"Successfully retrieved agent client for {agent_name}")
            
            try:
                
                # Prepare message and send
                logger.debug(f"Sending message to agent {agent_name}: {prompt[:50]}...")
                
                # Send the message and get response
                # The python-a2a client's ask method is not async
                logger.debug(f"Using python-a2a client to ask: {prompt}")
                response = client.ask(prompt)
                
                # Process and return the response
                logger.debug(f"Received response: {response}")
                
                # The python-a2a client's ask() method returns a simple string response
                # This makes handling much simpler
                response_data = {
                    "status": "success",
                    "response": response
                }
                
                logger.info(f"Successfully called agent {agent_name}")
                return response_data
                
            except Exception as e:
                logger.exception(f"Error using A2AClient: {e}")
                return {
                    "status": "error",
                    "message": f"Error calling agent with A2AClient: {str(e)}"
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
