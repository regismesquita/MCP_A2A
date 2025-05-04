#!/usr/bin/env python3
"""
Demo client for testing A2A agents with the MCP server.
Shows how to connect to agents directly and monitor progress.
"""

import asyncio
import json
import os
import sys
import uuid

import httpx

async def call_agent_1_with_sse(text):
    """Call Agent 1 with SSE streaming to monitor progress"""
    print("\n=== Calling Agent 1 with streaming ===\n")
    
    # Agent 1 URL
    agent1_url = "http://localhost:8001"
    
    # Prepare request
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    
    data = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tasks/sendSubscribe",
        "params": {
            "id": task_id,
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            }
        }
    }
    
    # Use httpx for SSE streaming
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Initialize request
        url = f"{agent1_url}"
        headers = {"Content-Type": "application/json"}
        
        async with client.stream("POST", url, json=data, headers=headers) as response:
            # Check response status
            if response.status_code != 200:
                print(f"Error: {response.status_code}")
                print(await response.text())
                return
            
            # Process SSE events
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                # Process complete events
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    if event.startswith("data: "):
                        event_data = json.loads(event[6:])
                        result = event_data.get("result", {})
                        status = result.get("status", {})
                        
                        # Print progress
                        state = status.get("state", "unknown")
                        message = status.get("message", "")
                        progress = status.get("progress", 0)
                        
                        print(f"Agent 1 - State: {state}, Progress: {progress*100:.0f}%, Message: {message}")
                        
                        # Check if we need to provide input
                        if state == "input-required":
                            print("\nAgent 1 requires additional input. Please provide: ")
                            additional_input = input("> ")
                            await send_input_to_agent_1(task_id, session_id, additional_input)
                        
                        # Check if task is complete
                        if state in ["completed", "failed", "canceled"]:
                            artifacts = result.get("artifacts", [])
                            if artifacts:
                                print("\nAgent 1 Artifacts:")
                                for artifact in artifacts:
                                    print(f"- {artifact.get('name', 'unnamed')}")
                                    for part in artifact.get('parts', []):
                                        if part.get('type') == 'text':
                                            print(f"  Content: {part.get('text', '')}")
                            
                            # If completed, extract result for return
                            if state == "completed":
                                print("\nAgent 1 completed successfully!")
                                return result
                            else:
                                print(f"\nAgent 1 ended with state: {state}")
                                return None

async def send_input_to_agent_1(task_id, session_id, input_text):
    """Send additional input to Agent 1"""
    print(f"Sending input to Agent 1: {input_text}")
    
    # Agent 1 URL
    agent1_url = "http://localhost:8001"
    
    # Prepare request
    request_id = str(uuid.uuid4())
    
    data = {
        "jsonrpc": "2.0",
        "id": request_id,
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
    
    # Send request
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            agent1_url,
            json=data,
            headers={"Content-Type": "application/json"}
        )
        
        # Check response
        if response.status_code != 200:
            print(f"Error sending input: {response.status_code}")
            print(response.text)
            return False
        
        return True

async def monitor_agent_2(task_id):
    """Monitor Agent 2's progress via SSE"""
    print("\n=== Monitoring Agent 2 ===\n")
    
    # Agent 2 URL
    agent2_url = "http://localhost:8002/mcp/sse"
    
    # Use httpx for SSE streaming
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("GET", agent2_url) as response:
            # Check response status
            if response.status_code != 200:
                print(f"Error: {response.status_code}")
                print(await response.text())
                return
            
            # Process SSE events
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                # Process complete events
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    if event.startswith("data: "):
                        event_data = json.loads(event[6:])
                        
                        # Only process events for our task
                        if event_data.get("task_id", "").startswith(task_id):
                            status = event_data.get("status", {})
                            
                            # Print progress
                            state = status.get("state", "unknown")
                            message = status.get("message", "")
                            progress = status.get("progress", 0)
                            
                            print(f"Agent 2 - State: {state}, Progress: {progress*100:.0f}%, Message: {message}")
                            
                            # Check if task is complete
                            if state in ["completed", "failed", "canceled"]:
                                artifacts = event_data.get("artifacts", [])
                                if artifacts:
                                    print("\nAgent 2 Artifacts:")
                                    for artifact in artifacts:
                                        print(f"- {artifact.get('name', 'unnamed')}")
                                        for part in artifact.get('parts', []):
                                            if part.get('type') == 'text':
                                                print(f"  Content: {part.get('text', '')}")
                                
                                # If completed, we're done
                                print(f"\nAgent 2 ended with state: {state}")
                                return

async def main():
    # Get input text from command line or use default
    if len(sys.argv) > 1:
        input_text = " ".join(sys.argv[1:])
    else:
        input_text = "Hello, please process this text and show me the agent chain in action!"
    
    print("\n=== Direct Agent Connection (Legacy Mode) ===")
    print("Note: With the new pipeline orchestration, direct connections between agents are no longer needed.")
    print("Instead, use the MCP server's pipeline tools to define and execute agent workflows.\n")
    
    # Call Agent 1 directly (no longer forwards to Agent 2)
    result = await call_agent_1_with_sse(input_text)
    
    # Show pipeline example
    print("\n=== Pipeline Orchestration Example ===")
    print("To use the pipeline orchestration, connect to the MCP server and use these MCP tools:\n")
    print("1. Define a pipeline:")
    print("""   {
     "name": "Process and Finalize",
     "description": "Two-step pipeline with processor and finalizer agents",
     "version": "1.0.0",
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
   }""")
    
    print("\n2. Save this as a template:")
    print("   save_pipeline_template(template_id='process_and_finalize', pipeline_definition=...)")
    
    print("\n3. Execute the pipeline:")
    print("   execute_pipeline_from_template(template_id='process_and_finalize', input_text='Your text here')")
    
    print("\n4. Get pipeline status:")
    print("   get_pipeline_status(pipeline_id='...')")
    
    print("\n5. Send input if required:")
    print("   send_pipeline_input(pipeline_id='...', node_id='process', input_text='Additional info')")
    
    print("\n6. Monitor or cancel the pipeline:")
    print("   list_pipelines()")
    print("   cancel_pipeline(pipeline_id='...')")

if __name__ == "__main__":
    asyncio.run(main())