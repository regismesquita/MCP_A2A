# A2A MCP Server

A Model Context Protocol (MCP) server that provides Claude Desktop access to A2A protocol agents with real-time progress visibility.
> Developer friendly more than production ready, you can hack it to adapt to your needs and expand on whatever you need.
> But probably not the best idea to deploy into prod as-is.

## Architecture Overview

This project creates a bridge between Claude Desktop and agents implementing the A2A (Agent-to-Agent) protocol, with real-time streaming of execution progress.

```ascii
┌───────────────────┐      ┌────────────────────────┐      ┌────────────────────┐
│                   │      │                        │      │                    │
│  Claude Desktop   │◄────►│  A2A MCP Server Bridge │◄────►│  A2A Protocol      │
│  (MCP Client)     │      │  (This Project)        │      │  Agents            │
│                   │      │                        │      │                    │
└───────────────────┘      └────────────────────────┘      └────────────────────┘
                             ▲
                             │ Streaming progress updates 
                             ▼
                           ┌────────────────────────┐
                           │                        │
                           │  Agent Registry        │
                           │  (In-memory state)     │
                           │                        │
                           └────────────────────────┘
```

## Real-Time Progress Visibility

Unlike simpler implementations, this server provides streaming progress updates during agent execution:

```ascii
┌─ Progress Streaming Implementation ───────────────────────────────────────┐
│                                                                           │
│  Claude   ◄───┐                                                           │
│  Desktop      │                                                           │
│               │ SSE/                                                      │
│               │ Streaming HTTP                                            │
│  A2A MCP      │                                                           │
│  Server  ─────┘                  ┌──────────────────┐                     │
│    │                             │ UpdateThrottler  │                     │
│    │                             │ - Rate limiting  │                     │
│    ▼                             │ - Batching       │                     │
│  Agent  ───────────────────────► │ - Prioritization │                     │
│                                  └──────────────────┘                     │
│                                          │                                │
│                                          ▼                                │
│                                  ┌──────────────────┐                     │
│                                  │ Progress Updates │                     │
│                                  │ - Status changes │                     │
│                                  │ - % completion   │                     │
│                                  │ - Chain position │                     │
│                                  │ - Agent messages │                     │
│                                  └──────────────────┘                     │
└───────────────────────────────────────────────────────────────────────────┘
```

## Multi-Agent Workflow

The server can orchestrate interactions between multiple agents in a chain while providing real-time visibility:

```ascii
┌─ Multi-Agent Workflow ──────────────────────────────────────────────────┐
│                                                                         │
│  ┌───────────┐                                                          │
│  │           │                                                          │
│  │  Claude   │◄────────── User Input: "Process this text"               │
│  │  Desktop  │                                                          │
│  │           │                                                          │
│  └─────┬─────┘                                                          │
│        │                                                                │
│        │ MCP Protocol + SSE                                             │
│        ▼                                                                │
│  ┌─────────────┐    ┌─ Progress Updates ───┐    ┌─ Request Tracking ─┐  │
│  │             │    │ "Working: 10%"       │    │ request_id: uuid   │  │
│  │  A2A MCP    │◄───┤ "Working: 20%"       │◄───┤ status: working    │  │
│  │  Server     │    │ "Working: 30%"       │    │ agent: processor   │  │
│  │             │    │ "Need more input..." │    │ position: 1/2      │  │
│  └─────┬───────┘    └─────────────────────┘    └──────────────────┬──┘  │
│        │                                                          │     │
│        │ JSON-RPC + SSE                                           │     │
│        ▼                                                          │     │
│  ┌─────────────┐                                                  │     │
│  │ Agent 1:    │                                                  │     │
│  │ Processor   │──────────── Progress Updates ─────────────────────     │
│  └─────┬───────┘                                                        │
│        │                                                                │
│        │ Intermediate Results                                           │
│        ▼                                                                │
│  ┌─────────────┐    ┌─ Progress Updates ───┐    ┌─ Request Tracking ─┐  │
│  │             │    │ "Working: 20%"       │    │ request_id: uuid   │  │
│  │  A2A MCP    │◄───┤ "Working: 40%"       │◄───┤ status: working    │  │
│  │  Server     │    │ "Working: 60%"       │    │ agent: finalizer   │  │
│  │             │    │ "Working: 80%"       │    │ position: 2/2      │  │
│  └─────┬───────┘    └─────────────────────┘    └──────────────────┬──┘  │
│        │                                                          │     │
│        │ JSON-RPC + SSE                                           │     │
│        ▼                                                          │     │
│  ┌─────────────┐                                                  │     │
│  │ Agent 2:    │                                                  │     │
│  │ Finalizer   │──────────── Progress Updates ─────────────────────     │
│  └─────┬───────┘                                                        │
│        │                                                                │
│        │ Final Results                                                  │
│        ▼                                                                │
│  ┌─────────────┐                                                        │
│  │             │                                                        │
│  │  A2A MCP    │                                                        │
│  │  Server     │                                                        │
│  │             │                                                        │
│  └─────┬───────┘                                                        │
│        │                                                                │
│        │ Combined Results + Progress History                            │
│        ▼                                                                │
│  ┌───────────┐                                                          │
│  │           │                                                          │
│  │  Claude   │───────────► Final Response with execution details        │
│  │  Desktop  │                                                          │
│  │           │                                                          │
│  └───────────┘                                                          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Features

- **Agent Registry**: Register and manage A2A protocol servers
- **Agent Discovery**: List available agents with their capabilities
- **Progress Visibility**: Real-time status updates during agent execution
- **Multi-Agent Chains**: View progress across agent chains with position information
- **User Interaction**: Support for input requests and task cancellation
- **Execution Logging**: Save and export detailed logs of agent executions

## Technical Architecture

```ascii
┌─ A2A MCP Server Architecture ────────────────────────────────────────────┐
│                                                                          │
│  ┌─ Core Components ───────────────────┐  ┌─ Communication ─────────────┐│
│  │                                     │  │                             ││
│  │  ● ServerState                      │  │  ● JSON-RPC over HTTP       ││
│  │    - registry (name → url)          │  │  ● SSE for real-time        ││
│  │    - cache (name → AgentCard)       │  │    streaming                ││
│  │    - active_requests                │  │  ● Connection upgrades      ││
│  │    - execution_logs                 │  │  ● Throttling mechanism     ││
│  │                                     │  │                             ││
│  └─────────────────────────────────────┘  └─────────────────────────────┘│
│                                                                          │
│  ┌─ MCP Tools ──────────────────────────────────────────────────────────┐│
│  │                                                                      ││
│  │  1. a2a_server_registry    4. cancel_request                         ││
│  │  2. list_agents            5. send_input                             ││
│  │  3. call_agent             6. export_logs                            ││
│  │                            7. list_requests                          ││
│  │                                                                      ││
│  └──────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  ┌─ Performance ────────────────────────┐  ┌─ Error Handling ───────────┐│
│  │                                      │  │                            ││
│  │  ● Update throttling                 │  │  ● Graceful degradation    ││
│  │  ● Batch processing                  │  │  ● Detailed logging        ││
│  │  ● Connection timeouts               │  │  ● Error state tracking    ││
│  │  ● Heartbeat mechanism               │  │  ● Client notifications    ││
│  │                                      │  │                            ││
│  └──────────────────────────────────────┘  └────────────────────────────┘│
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

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

```ascii
┌─ Typical User Workflow ───────────────────────────────────────────────────┐
│                                                                           │
│ ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────────────┐│
│ │ 1. Registration │  │ 2. Discovery    │  │ 3. Agent Execution           ││
│ │                 │  │                 │  │                              ││
│ │ Register A2A    │  │ List available  │  │ Call agent with prompt       ││
│ │ servers with    │  │ agents with     │  │ Observe real-time progress   ││
│ │ a2a_server_     │  │ list_agents     │  │ Respond to input requests    ││
│ │ registry        │  │                 │  │ Cancel tasks if needed       ││
│ └─────────────────┘  └─────────────────┘  └──────────────────────────────┘│
│                                                                           │
│ ┌─────────────────────────────────────────────────────────────────────────┐
│ │ 4. Post-Execution                                                      ││
│ │                                                                        ││
│ │ Export execution logs for review                                       ││
│ │ List active/completed requests                                         ││
│ └────────────────────────────────────────────────────────────────────────┘│
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

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

## User Interface Flow

```ascii
┌─ Claude Desktop Interface ───────────────────────────────────────────────┐
│                                                                          │
│  User: Can you process this document and give me a summary?              │
│                                                                          │
│  Claude: I'll help process your document using our specialized agents.   │
│          I'll start by calling the processor agent:                      │
│                                                                          │
│          [Using call_agent with processor...]                            │
│                                                                          │
│          ⏳ Agent Progress: Working (10%) - Starting initial analysis     │
│          ⏳ Agent Progress: Working (20%) - Extracting key sections       │
│          ⏳ Agent Progress: Working (30%) - Analyzing content structure   │
│          ❓ Agent Progress: Need additional information - Is this a       │
│             technical or business document?                              │
│                                                                          │
│  User: It's a technical document about server architecture.              │
│                                                                          │
│  Claude: Thanks! I'll send that information to the agent:                │
│                                                                          │
│          [Using send_input...]                                           │
│                                                                          │
│          ⏳ Agent Progress: Working (40%) - Analyzing technical content   │
│          ⏳ Agent Progress: Working (60%) - Identifying key concepts      │
│          ⏳ Agent Progress: Working (80%) - Preparing intermediate results│
│          ✅ Agent 1 Complete - Forwarding to finalizer agent (1/2)       │
│                                                                          │
│          ⏳ Agent Progress: Working (20%) - Finalizing summary (2/2)     │
│          ⏳ Agent Progress: Working (40%) - Structuring key points (2/2) │
│          ⏳ Agent Progress: Working (60%) - Polishing language (2/2)     │
│          ⏳ Agent Progress: Working (80%) - Final formatting (2/2)       │
│          ✅ Agent 2 Complete - Summary ready                             │
│                                                                          │
│          Here's your document summary:                                   │
│          [Displays final summary with formatting]                        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

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
