# A2A Agent Demo for MCP Server Pipelines

This directory contains two demo A2A protocol agents that work with the MCP server to demonstrate agent progress visibility and pipeline orchestration features. The agents work with the MCP server in both STDIO and HTTP transport modes.

## Overview

The demo consists of:

1. **Agent 1 (Processor)**: The first agent in the chain, which processes input, reports progress, occasionally requests additional input, and produces artifacts with processed data.

2. **Agent 2 (Finalizer)**: The second agent in the chain, which processes inputs (typically from Agent 1's output artifacts), and produces the final output.

Both agents implement the A2A protocol with real-time progress updates, allowing the MCP server to track and display progress information. These agents can be used individually or as part of a pipeline orchestration workflow.

```ascii
┌─ Agent Communication Models ───────────────────────────────────────────────────┐
│                                                                                │
│  ┌─ Legacy Direct Model ─────────────────┐  ┌─ Pipeline Orchestration Model ─┐ │
│  │                                        │  │                               │ │
│  │  ┌───────────────┐    ┌─────────────┐ │  │  ┌───────────────┐    ┌──────┐│ │
│  │  │               │    │             │ │  │  │               │    │      ││ │
│  │  │  MCP Client   │◄──►│  MCP Server │ │  │  │  MCP Client   │◄──►│  MCP ││ │
│  │  │  (Claude)     │    │             │ │  │  │  (Claude)     │    │Server││ │
│  │  └───────────────┘    └──────┬──────┘ │  │  └───────────────┘    └──┬───┘│ │
│  │                              │        │  │                           │    │ │
│  │                              │        │  │                           │    │ │
│  │                              ▼        │  │                           ▼    │ │
│  │                       ┌─────────────┐ │  │       ┌─ Pipeline Orchestration ┐ │
│  │                       │             │ │  │       │                        │ │
│  │                       │  Agent 1    │ │  │       │  ┌─────┐    ┌─────┐   │ │
│  │                       │  Processor  │ │  │       │  │Agent│◄──►│Agent│   │ │
│  │                       └──────┬──────┘ │  │       │  │  1  │    │  2  │   │ │
│  │                              │        │  │       │  └─────┘    └─────┘   │ │
│  │                              │        │  │       │    ▲          ▲       │ │
│  │                    Hardcoded │        │  │       │    │          │       │ │
│  │                    Forwarding│        │  │       │ Input/Output Mapping  │ │
│  │                              │        │  │       │                        │ │
│  │                              ▼        │  │       └────────────────────────┘ │
│  │                       ┌─────────────┐ │  │                                  │
│  │                       │             │ │  │                                  │
│  │                       │  Agent 2    │ │  │                                  │
│  │                       │  Finalizer  │ │  │                                  │
│  │                       └─────────────┘ │  │                                  │
│  │                                       │  │                                  │
│  └───────────────────────────────────────┘  └──────────────────────────────────┘
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Features Demonstrated

### Agent Features
- Real-time progress updates with percentages
- Multi-agent chain position reporting
- Additional input requests
- Task cancellation support
- Throttled status updates
- Structured artifacts for passing data between agents
- Progress reporting compatible with FastMCP 2.0 Context-based architecture

### Pipeline Orchestration Features
- JSON-based pipeline definitions
- Dependency management between nodes
- Input/output artifact mapping
- Pipeline state tracking
- Pipeline template creation and reuse
- Pipeline-level progress reporting
- Node-specific input handling within pipelines
- Configurable error policies for nodes

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

This will use the a2a_min client library to call Agent 1 directly and show you progress updates. The client uses SSE streaming to display real-time progress information. Note that the direct agent-to-agent forwarding has been removed in favor of the more flexible pipeline orchestration system.

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

4. There are two ways to use the agents:

   A. Call a single agent directly:
   ```json
   {
     "agent_name": "processor",
     "prompt": "Your input text here"
   }
   ```

   B. Execute a pipeline with both agents:
   ```json
   {
     "pipeline_definition": {
       "name": "Text Processing Pipeline",
       "nodes": [
         {
           "id": "process",
           "agent_name": "processor"
         },
         {
           "id": "finalize",
           "agent_name": "finalizer",
           "inputs": {
             "processed_data": {
               "source_node": "process",
               "source_artifact": "processed_data"
             }
           }
         }
       ],
       "final_outputs": ["finalize"]
     },
     "input_text": "Your input text here"
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
- Adds metadata about chain position
- Produces artifacts with processed data

### Agent 2: Finalizer

- Simulates processing that takes approximately 4 seconds
- Reports progress in 20% increments
- Adds metadata about chain position
- Processes input from artifacts
- Returns final results

### Pipeline Usage

The agents now work with the pipeline orchestration system:

- Define pipelines connecting Agent 1 and Agent 2
- Map artifacts from Agent 1 as inputs to Agent 2
- Monitor progress at both the node and pipeline levels
- Handle input requests during pipeline execution
- Save pipeline definitions as reusable templates

Both agents expose:
- A2A JSON-RPC endpoints (tasks/send, tasks/sendSubscribe, tasks/sendInput, tasks/cancel)
- Progress updates compatible with both STDIO and HTTP transport modes
- Agent cards at /.well-known/agent.json

All A2A protocol communications are handled through the a2a_min client library, which provides a clean, type-safe interface to the A2A protocol.

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