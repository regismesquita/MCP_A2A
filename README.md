# A2A MCP Server

A Model Context Protocol (MCP) server that provides Claude Desktop access to A2A protocol agents with real-time progress visibility.
> Developer friendly more than production ready, you can hack it to adapt to your needs and expand on whatever you need.
> But probably not the best idea to deploy into prod as-is.

## Overview

This project was created to connect Claude Desktop with A2A (Agent-to-Agent) protocol agents, particularly those built with python-a2a, pcingola/a2a_min and fast-agent. It implements real-time agent progress visibility through Streamable HTTP transport and SSE, allowing Claude users to see status updates and intermediate results during agent processing.

## Features

- **Agent Registry**: Register and manage A2A protocol servers
- **Agent Discovery**: List available agents with their capabilities
- **Progress Visibility**: Real-time status updates during agent execution
- **Multi-Agent Chains**: View progress across agent chains with position information
- **User Interaction**: Support for input requests and task cancellation
- **Execution Logging**: Save and export detailed logs of agent executions

## Tools

The server provides several MCP tools:

1. **a2a_server_registry** - Register or remove A2A servers
   ```json
   {
     "action": "add", 
     "name": "processor", 
     "url": "http://localhost:8001"
   }
   ```

2. **list_agents** - List all registered agents with their capabilities
   ```json
   {}
   ```

3. **call_agent** - Send a prompt to an agent and get streaming responses
   ```json
   {
     "agent_name": "processor",
     "prompt": "Process this text with detailed progress visibility"
   }
   ```

4. **cancel_request** - Cancel an in-progress agent task
   ```json
   {
     "request_id": "task-uuid-here"
   }
   ```

5. **send_input** - Send additional input to an agent that requested it
   ```json
   {
     "request_id": "task-uuid-here",
     "input_text": "Here's the additional information you requested"
   }
   ```

6. **export_logs** - Export execution logs of an agent task
   ```json
   {
     "request_id": "task-uuid-here",
     "format_type": "text"
   }
   ```

7. **list_requests** - List all active agent requests
   ```json
   {}
   ```

## Workflow

The typical workflow is:

1. Register A2A servers with `a2a_server_registry`
2. List available agents with `list_agents` 
3. Call an agent with `call_agent` and observe real-time progress updates
4. Optionally respond to input requests or cancel tasks
5. Export execution logs for review

## Demo Agents

The repository includes two demo A2A agents in the `agents` directory to showcase the progress visibility features:

- **Agent 1 (Processor)**: First agent in the chain - processes input and forwards to Agent 2
- **Agent 2 (Finalizer)**: Second agent - finalizes processing of intermediate results

These agents demonstrate:
- Real-time progress reporting
- Agent chain position information
- Input requests
- Task cancellation
- Passing data between agents

See the [agents/README.md](agents/README.md) file for setup and testing instructions.

## Technical Implementation

- Uses Streamable HTTP transport with SSE endpoints for real-time updates
- Supports bi-directional communication with automatic connection upgrades
- Implements throttling mechanism to prevent overwhelming the client
- Tracks task states through the A2A protocol lifecycle
- Provides detailed execution logging and export capabilities

## Local Testing

This server has been tested locally with both the included demo agents and external A2A agents. It successfully enables Claude Desktop to communicate with A2A agents while providing detailed progress visibility.

## Purpose

This bridge allows Claude Desktop to interact with any A2A-compatible agent with real-time progress visibility, extending Claude's capabilities through the MCP tools interface.

## Screenshot

<img width="515" alt="image" src="https://github.com/user-attachments/assets/e52a5920-781d-4455-aac1-a547b265ce1f" />

