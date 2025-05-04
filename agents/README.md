# A2A Agent Demo for MCP Server Progress Visibility

This directory contains two demo A2A protocol agents that work with the MCP server to demonstrate agent progress visibility features.

## Overview

The demo consists of:

1. **Agent 1 (Processor)**: The first agent in the chain, which processes input, reports progress, occasionally requests additional input, and passes intermediate results to Agent 2.

2. **Agent 2 (Finalizer)**: The second agent in the chain, which receives intermediate results from Agent 1, processes them, and produces the final output.

Both agents implement the A2A protocol with Server-Sent Events (SSE) for real-time progress updates, allowing the MCP server to track and display progress information.

## Features Demonstrated

- Real-time progress updates with percentages
- Multi-agent chain position reporting
- Additional input requests
- Task cancellation support
- Throttled status updates
- Structured artifacts for passing data between agents

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

3. **Register agents with the MCP server**:
   
   Use the `a2a_server_registry` tool in Claude Desktop:
   
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

### Through MCP Server

To test through the MCP server, use the `call_agent` tool in Claude Desktop:

```json
{
  "agent_name": "processor",
  "prompt": "Your input text here"
}
```

This will connect to Agent 1 through the MCP server and display progress updates in Claude Desktop using the agent progress visibility feature.

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
- MCP-compatible SSE endpoint for progress monitoring
- Agent cards at /.well-known/agent.json