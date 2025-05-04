# A2A Agent Demo for MCP Server Progress Visibility

This directory contains two demo A2A protocol agents that work with the MCP server to demonstrate agent progress visibility features. The agents work with the MCP server in both STDIO and HTTP transport modes.

## Overview

The demo consists of:

1. **Agent 1 (Processor)**: The first agent in the chain, which processes input, reports progress, occasionally requests additional input, and passes intermediate results to Agent 2.

2. **Agent 2 (Finalizer)**: The second agent in the chain, which receives intermediate results from Agent 1, processes them, and produces the final output.

Both agents implement the A2A protocol with real-time progress updates, allowing the MCP server to track and display progress information.

```ascii
┌─ Agent Communication Flow ──────────────────────────────────────────────────┐
│                                                                             │
│  ┌───────────────┐          ┌─────────────────┐          ┌─────────────────┐
│  │               │          │                 │          │                 │
│  │  MCP Client   │◄────────►│  MCP Server     │◄────────►│  Agent 1        │
│  │  (Claude or   │  Tool    │  (FastMCP 2.0)  │  A2A     │  (Processor)    │
│  │   other)      │  Calls   │                 │  Protocol│                 │
│  │               │          │                 │          │                 │
│  └───────────────┘          └─────────────────┘          └────────┬────────┘
│                                      ▲                             │
│                                      │                             │
│                                      │                   Intermediate Results
│                            Progress  │                             │
│                            Updates   │                             │
│                                      │                             ▼
│                                      │                    ┌─────────────────┐
│                                      │                    │                 │
│                                      └────────────────────┤  Agent 2        │
│                                                           │  (Finalizer)    │
│                                                           │                 │
│                                                           └─────────────────┘
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Features Demonstrated

- Real-time progress updates with percentages
- Multi-agent chain position reporting
- Additional input requests
- Task cancellation support
- Throttled status updates
- Structured artifacts for passing data between agents
- Progress reporting compatible with FastMCP 2.0 Context-based architecture

```ascii
┌─ Progress & Feature Visualization ───────────────────────────────────────────┐
│                                                                              │
│  ┌─ Agent 1 (Processor) ─────────────────┐   ┌─ Agent 2 (Finalizer) ────────┐
│  │                                        │   │                              │
│  │  ⏳ Working (10%) - Initial analysis   │   │                              │
│  │  ⏳ Working (20%) - Extracting data    │   │                              │
│  │  ⏳ Working (30%) - Analyzing content  │   │                              │
│  │  ❓ Input Required - Need more info?   │◀──┤                              │
│  │                     ↑                  │   │                              │
│  │  User Input ────────┘                  │   │                              │
│  │                                        │   │                              │
│  │  ⏳ Working (40%) - Processing input   │   │                              │
│  │  ⏳ Working (60%) - Identifying data   │   │                              │
│  │  ⏳ Working (80%) - Creating results   │   │  ⏳ Working (20%) - Starting │
│  │  ✅ Completed (100%) [Chain Pos: 1/2] ─┼──►│  ⏳ Working (40%) - Analyzing│
│  │                                        │   │  ⏳ Working (60%) - Formatting│
│  │           Structured Artifacts         │   │  ⏳ Working (80%) - Polishing│
│  │           ─────────────────────────────┼──►│  ✅ Completed (100%)        │
│  │                                        │   │     [Chain Pos: 2/2]        │
│  └────────────────────────────────────────┘   └──────────────────────────────┘
│                                                                              │
│               │                                          │                   │
│               ▼                                          ▼                   │
│      ┌──────────────────────┐                 ┌──────────────────────────┐  │
│      │  UpdateThrottler     │                 │  Final Results with      │  │
│      │  Rate-limited        │                 │  Detailed Progress       │  │
│      │  Status Updates      │                 │  History                 │  │
│      └──────────────────────┘                 └──────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Setup

1. **Install dependencies**:
   ```
   pip install -r requirements.txt
   ```

2. **Start the agents**:
   
   In two separate terminal windows:
   
   ```bash
   # Terminal 1 - Start Agent 1
   cd agents/agent_1
   python agent.py
   
   # Terminal 2 - Start Agent 2
   cd agents/agent_2
   python agent.py
   ```

3. **Start the MCP server**:

   ```bash
   # Start the MCP server in your preferred transport mode
   python -m a2a_mcp_server server --transport stdio  # For Claude Desktop or other STDIO clients
   # OR
   python -m a2a_mcp_server server --transport http   # For HTTP-based clients
   ```

4. **Register agents with the MCP server**:
   
   Use the `a2a_server_registry` tool in your MCP client:
   
   ```json
   {
     "action": "add",
     "name": "processor",
     "url": "http://localhost:8001"
   }
   ```
   
   ```json
   {
     "action": "add",
     "name": "finalizer",
     "url": "http://localhost:8002"
   }
   ```

## Testing

### Direct Testing

For direct testing of the agents without going through the MCP server:

```bash
python client.py "Your input text here"
```

This will call Agent 1, which will process the input and forward the results to Agent 2, displaying progress updates from both agents.

### Testing with MCP Server in STDIO Mode

1. Start the MCP server in STDIO mode:
   ```bash
   python -m a2a_mcp_server server --transport stdio
   ```

2. Connect to the server with an MCP client (like Claude Desktop)

3. Register the agents:
   ```json
   {
     "action": "add",
     "name": "processor",
     "url": "http://localhost:8001"
   }
   ```
   ```json
   {
     "action": "add",
     "name": "finalizer",
     "url": "http://localhost:8002"
   }
   ```

4. Call the agent and observe real-time progress updates:
   ```json
   {
     "agent_name": "processor",
     "prompt": "Your input text here"
   }
   ```

### Testing with MCP Server in HTTP Mode

1. Start the MCP server in HTTP mode:
   ```bash
   python -m a2a_mcp_server server --transport http
   ```

2. Use an HTTP-based MCP client to connect to the server

3. Register and call agents using the same commands as with STDIO mode

The progress updates will display identically in both transport modes thanks to the FastMCP 2.0 Context-based architecture. The UpdateThrottler mechanism ensures consistent progress reporting regardless of transport.

## Agent Implementation Details

### Agent 1: Processor

- Simulates processing that takes approximately 10 seconds
- Reports progress in 10% increments
- Randomly decides whether to request additional input halfway through
- Adds metadata about chain position (1/2)
- Forwards results to Agent 2

### Agent 2: Finalizer

- Simulates processing that takes approximately 4 seconds
- Reports progress in 20% increments
- Adds metadata about chain position (2/2)
- Returns final results

Both agents expose:
- A2A JSON-RPC endpoints (tasks/send, tasks/sendSubscribe, tasks/sendInput, tasks/cancel)
- Progress updates compatible with both STDIO and HTTP transport modes
- Agent cards at /.well-known/agent.json

## Integration with FastMCP 2.0

The MCP server uses FastMCP 2.0's Context-based architecture for progress reporting, which enables:
- Seamless integration with both STDIO and HTTP transport modes
- Unified progress tracking regardless of transport
- Efficient rate limiting and throttling of updates
- Proper handling of critical status transitions

```ascii
┌─ FastMCP 2.0 Context Integration ────────────────────────────────────────────┐
│                                                                              │
│  ┌─ Transport Layer ─────────────────┐   ┌─ Context Layer ──────────────────┐
│  │                                   │   │                                  │
│  │  ┌───────────┐    ┌───────────┐   │   │  ┌──────────────────────────┐   │
│  │  │           │    │           │   │   │  │                          │   │
│  │  │  STDIO    │    │  HTTP     │   │   │  │  Context Object          │   │
│  │  │  Transport│    │  Transport│   │   │  │  - report_progress()     │   │
│  │  │           │    │           │   │   │  │  - complete()            │   │
│  │  └─────┬─────┘    └─────┬─────┘   │   │  │  - error()               │   │
│  │        │                │         │   │  └───────────┬──────────────┘   │
│  └────────┼────────────────┼─────────┘   └─────────────┼──────────────────┘
│           │                │                           │                    │
│           └────────────────┼───────────────────────────┘                    │
│                            │                                                │
│                            ▼                                                │
│  ┌─ A2A Agent Progress Updates ───────────────────────────────────────────┐ │
│  │                                                                         │ │
│  │  ┌─────────────────┐    ┌───────────────────┐    ┌──────────────────┐  │ │
│  │  │                 │    │                   │    │                  │  │ │
│  │  │  Status Change  │───►│  UpdateThrottler  │───►│  Processed       │  │ │
│  │  │  Detection      │    │  - Rate limiting  │    │  Updates         │  │ │
│  │  │                 │    │  - Batching       │    │                  │  │ │
│  │  └─────────────────┘    └───────────────────┘    └──────────────────┘  │ │
│  │                                                                         │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```