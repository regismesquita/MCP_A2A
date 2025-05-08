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

# Import a2a_min client library instead of httpx
from a2a_min import A2aMinClient
from a2a_min.base.types import Message, TextPart

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
    
    # Use A2aMinClient for streaming
    try:
        # Create message
        message = Message(
            role="user",
            parts=[TextPart(text=text)]
        )
        
        # Create client
        client = A2aMinClient.connect(agent1_url)
        
        # Send message with streaming
        streaming_task = await client.send_message_streaming(
            message=message,
            task_id=task_id,
            session_id=session_id
        )
        
        # Process streaming updates
        result = None
        async for update in streaming_task.stream_updates():
            # Extract status information
            status = update.status
            if status:
                # Print progress
                state = status.state
                message = status.message or ""
                progress = status.progress or 0
                
                print(f"Agent 1 - State: {state}, Progress: {progress*100:.0f}%, Message: {message}")
                
                # Check if we need to provide input
                if state == "input-required":
                    print("\nAgent 1 requires additional input. Please provide: ")
                    additional_input = input("> ")
                    await send_input_to_agent_1(task_id, session_id, additional_input)
                
                # Check if task is complete
                if state in ["completed", "failed", "canceled"]:
                    artifacts = update.artifacts or []
                    if artifacts:
                        print("\nAgent 1 Artifacts:")
                        for artifact in artifacts:
                            print(f"- {artifact.name if hasattr(artifact, 'name') else 'unnamed'}")
                            for part in (artifact.parts if hasattr(artifact, 'parts') else []):
                                if hasattr(part, 'type') and part.type == "text":
                                    print(f"  Content: {part.text if hasattr(part, 'text') else ''}")
                    
                    # If completed, prepare result for return
                    if state == "completed":
                        print("\nAgent 1 completed successfully!")
                        # Convert to dict format similar to before
                        result = {
                            "id": task_id,
                            "status": {
                                "state": state,
                                "message": message,
                                "progress": progress
                            },
                            "artifacts": [
                                vars(artifact) if not hasattr(artifact, 'model_dump') else artifact.model_dump()
                                for artifact in artifacts
                            ]
                        }
                    else:
                        print(f"\nAgent 1 ended with state: {state}")
        
        return result
                    
    except Exception as e:
        print(f"Error during streaming: {str(e)}")
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
    
    # Use A2aMinClient to send input
    try:
        # Create message
        message = Message(
            role="user",
            parts=[TextPart(text=input_text)]
        )
        
        # Create client
        client = A2aMinClient.connect(agent1_url)
        
        # Send input
        result = await client.send_input(
            task_id=task_id,
            session_id=session_id,
            message=message
        )
        
        print(f"Input sent successfully: {result}")
        return True
        
    except Exception as e:
        print(f"Error sending input: {str(e)}")
        return False

async def monitor_agent_2(task_id):
    """Monitor Agent 2's progress via SSE"""
    print("\n=== Monitoring Agent 2 ===\n")
    
    # Agent 2 URL
    agent2_url = "http://localhost:8002/mcp/sse"
    
    # Note: For an actual direct A2A MonitorClient we would use 
    # A2aMinClient's monitoring capabilities, but for this specific endpoint 
    # that's designed for MCP compatibility, we need to use a different approach.
    # Since this is a custom MCP SSE endpoint, we need to handle it specially.

    # Create an agent wrapper to get the A2aMinClient
    client = A2aMinClient.connect(agent2_url)
    
    try:
        # For MCP SSE monitoring, we use a simpler streaming approach
        # This would normally use more a2a_min native capabilities for direct agent monitoring
        
        # Simulate the SSE connection using custom subscription
        async for event in client.subscribe_to_updates(task_id):
            # Process the event - this is a simulation
            status = event.status if hasattr(event, 'status') else {}
            
            # Print progress
            state = status.state if hasattr(status, 'state') else "unknown"
            message = status.message if hasattr(status, 'message') else ""
            progress = status.progress if hasattr(status, 'progress') else 0
            
            print(f"Agent 2 - State: {state}, Progress: {progress*100:.0f}%, Message: {message}")
            
            # Check if task is complete
            if state in ["completed", "failed", "canceled"]:
                artifacts = event.artifacts if hasattr(event, 'artifacts') else []
                if artifacts:
                    print("\nAgent 2 Artifacts:")
                    for artifact in artifacts:
                        print(f"- {artifact.name if hasattr(artifact, 'name') else 'unnamed'}")
                        for part in (artifact.parts if hasattr(artifact, 'parts') else []):
                            if hasattr(part, 'type') and part.type == "text":
                                print(f"  Content: {part.text if hasattr(part, 'text') else ''}")
                
                # If completed, we're done
                print(f"\nAgent 2 ended with state: {state}")
                return
    
    except Exception as e:
        print(f"Error monitoring Agent 2: {str(e)}")
        # For demo purposes, we can simulate the monitoring with a basic approach
        print("Falling back to basic monitoring approach...")
        
        # This is example code - in a real implementation, we would 
        # implement proper A2A protocol monitoring

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