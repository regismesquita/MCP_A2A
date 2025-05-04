# A2A MCP Server

A simple MCP server that provides Claude Desktop access to A2A protocol agents.

## Overview

This project was created to connect Claude Desktop with A2A (Agent-to-Agent) protocol agents, particularly those built with pcingola/a2a_min and fast-agent. It was developed as a personal project to assist with agent development and for fun.

## Tools

The server provides three MCP tools:

1. **a2a_server_registry** - Register or remove A2A servers
   ```
   {
     "action": "add", 
     "name": "security_audit", 
     "url": "http://localhost:8000"
   }
   ```

2. **list_agents** - List all registered agents with their capabilities
   ```
   {}
   ```

3. **call_agent** - Send a prompt to an agent and get its response
   ```
   {
     "agent_name": "security_audit",
     "prompt": "regismesquita/DevControlMCP"
   }
   ```

## Workflow

The typical workflow is:

1. Register an A2A server with `a2a_server_registry`
2. List available agents with `list_agents` 
3. Call an agent with `call_agent` whenever needed

## Local Testing

This server has been tested locally with a security audit agent built on fast-agent and a2a_min. It successfully enables Claude Desktop to communicate with A2A agents.

## Purpose

This bridge allows Claude Desktop to interact with any A2A-compatible agent, extending Claude's capabilities through the MCP tools interface.

## Screenshot

<img width="515" alt="image" src="https://github.com/user-attachments/assets/e52a5920-781d-4455-aac1-a547b265ce1f" />

