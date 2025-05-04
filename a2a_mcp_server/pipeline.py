"""
Pipeline orchestration for A2A MCP Server.
"""

import asyncio
import json
import logging
import time
import uuid
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Tuple, Union

import jsonschema

logger = logging.getLogger(__name__)

# Define error policy types
class ErrorPolicy(str, Enum):
    FAIL_FAST = "fail_fast"
    RETRY = "retry"
    CONTINUE = "continue"

# Define node status types
class NodeStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

# Define pipeline status types
class PipelineStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

# Pipeline definition schema
PIPELINE_SCHEMA = {
    "type": "object",
    "required": ["name", "nodes", "final_outputs"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "version": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "agent_name"],
                "properties": {
                    "id": {"type": "string"},
                    "agent_name": {"type": "string"},
                    "inputs": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["source_node", "source_artifact"],
                            "properties": {
                                "source_node": {"type": "string"},
                                "source_artifact": {"type": "string"}
                            }
                        }
                    },
                    "error_policy": {"type": "string", "enum": ["fail_fast", "retry", "continue"]},
                    "timeout": {"type": "number", "minimum": 1}
                }
            }
        },
        "final_outputs": {
            "type": "array",
            "items": {"type": "string"}
        },
        "resource_limits": {
            "type": "object",
            "properties": {
                "max_concurrent_nodes": {"type": "number", "minimum": 1},
                "timeout": {"type": "number", "minimum": 1}
            }
        }
    }
}

class PipelineDefinition:
    """
    Represents a pipeline definition with validation and utility methods.
    """
    
    def __init__(self, definition: Dict[str, Any]):
        """
        Initialize a pipeline definition.
        
        Args:
            definition: Dict containing the pipeline definition
        """
        self.definition = definition
        self.validate()
        
    def validate(self) -> None:
        """
        Validate the pipeline definition against the schema.
        
        Raises:
            jsonschema.exceptions.ValidationError: If validation fails
        """
        jsonschema.validate(self.definition, PIPELINE_SCHEMA)
        
        # Additional validation
        self._validate_node_references()
        self._validate_final_outputs()
    
    def _validate_node_references(self) -> None:
        """
        Validate that all node references in inputs exist.
        
        Raises:
            ValueError: If a referenced node doesn't exist
        """
        node_ids = {node["id"] for node in self.definition["nodes"]}
        
        for node in self.definition["nodes"]:
            if "inputs" in node:
                for input_name, input_spec in node["inputs"].items():
                    source_node = input_spec["source_node"]
                    if source_node not in node_ids:
                        raise ValueError(f"Node '{node['id']}' references non-existent source node '{source_node}'")
    
    def _validate_final_outputs(self) -> None:
        """
        Validate that all final outputs reference existing nodes.
        
        Raises:
            ValueError: If a final output references a non-existent node
        """
        node_ids = {node["id"] for node in self.definition["nodes"]}
        
        for output_node in self.definition["final_outputs"]:
            if output_node not in node_ids:
                raise ValueError(f"Final output references non-existent node '{output_node}'")
    
    def get_node_by_id(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a node by its ID.
        
        Args:
            node_id: The ID of the node to retrieve
            
        Returns:
            Dict containing the node definition, or None if not found
        """
        for node in self.definition["nodes"]:
            if node["id"] == node_id:
                return node
        return None
    
    def get_dependencies(self, node_id: str) -> Set[str]:
        """
        Get the dependencies (source nodes) for a node.
        
        Args:
            node_id: The ID of the node to get dependencies for
            
        Returns:
            Set of node IDs that are dependencies of the specified node
        """
        node = self.get_node_by_id(node_id)
        if not node or "inputs" not in node:
            return set()
        
        dependencies = set()
        for input_spec in node["inputs"].values():
            dependencies.add(input_spec["source_node"])
        
        return dependencies
    
    def build_execution_order(self) -> List[str]:
        """
        Build the execution order for the pipeline based on dependencies.
        
        Returns:
            List of node IDs in execution order
        """
        # Get all nodes
        nodes = {node["id"]: node for node in self.definition["nodes"]}
        
        # Track visited and temporary nodes for cycle detection
        visited = set()
        temp = set()
        order = []
        
        def visit(node_id):
            # Check for cycles
            if node_id in temp:
                raise ValueError(f"Cycle detected in pipeline involving node '{node_id}'")
            
            # Skip if already visited
            if node_id in visited:
                return
            
            # Mark as temporary visited for cycle detection
            temp.add(node_id)
            
            # Visit dependencies
            dependencies = self.get_dependencies(node_id)
            for dep in dependencies:
                visit(dep)
            
            # Mark as visited and add to order
            temp.remove(node_id)
            visited.add(node_id)
            order.append(node_id)
        
        # Visit all nodes
        for node_id in nodes:
            if node_id not in visited:
                visit(node_id)
        
        return order
    
    def get_error_policy(self, node_id: str) -> ErrorPolicy:
        """
        Get the error policy for a node.
        
        Args:
            node_id: The ID of the node to get the error policy for
            
        Returns:
            ErrorPolicy enum value
        """
        node = self.get_node_by_id(node_id)
        if not node:
            return ErrorPolicy.FAIL_FAST
        
        policy = node.get("error_policy", "fail_fast")
        return ErrorPolicy(policy)
    
    def get_timeout(self, node_id: str) -> Optional[float]:
        """
        Get the timeout for a node in seconds.
        
        Args:
            node_id: The ID of the node to get the timeout for
            
        Returns:
            Timeout in seconds, or None if not specified
        """
        node = self.get_node_by_id(node_id)
        if not node:
            return None
        
        return node.get("timeout")
    
    def get_input_mappings(self, node_id: str) -> Dict[str, Dict[str, str]]:
        """
        Get the input mappings for a node.
        
        Args:
            node_id: The ID of the node to get input mappings for
            
        Returns:
            Dict of input mappings
        """
        node = self.get_node_by_id(node_id)
        if not node or "inputs" not in node:
            return {}
        
        return node["inputs"]
    
    def __str__(self) -> str:
        """String representation of the pipeline definition."""
        return f"Pipeline: {self.definition['name']} - {len(self.definition['nodes'])} nodes"


class NodeState:
    """
    Represents the state of a pipeline node execution.
    """
    
    def __init__(self, node_id: str, agent_name: str):
        """
        Initialize a node state.
        
        Args:
            node_id: The ID of the node
            agent_name: The name of the agent for this node
        """
        self.node_id = node_id
        self.agent_name = agent_name
        self.status = NodeStatus.PENDING
        self.progress = 0.0
        self.message = f"Node {node_id} pending execution"
        self.start_time = None
        self.end_time = None
        self.task_id = None
        self.session_id = None
        self.artifacts = []
        self.error = None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the node state to a dictionary.
        
        Returns:
            Dict representation of the node state
        """
        return {
            "node_id": self.node_id,
            "agent_name": self.agent_name,
            "status": self.status.value if isinstance(self.status, NodeStatus) else self.status,
            "progress": self.progress,
            "message": self.message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "task_id": self.task_id,
            "artifacts": self.artifacts,
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeState":
        """
        Create a node state from a dictionary.
        
        Args:
            data: Dict containing node state data
            
        Returns:
            NodeState instance
        """
        node = cls(data["node_id"], data["agent_name"])
        node.status = NodeStatus(data["status"]) if isinstance(data["status"], str) else data["status"]
        node.progress = data["progress"]
        node.message = data["message"]
        node.start_time = data["start_time"]
        node.end_time = data["end_time"]
        node.task_id = data["task_id"]
        node.artifacts = data["artifacts"]
        node.error = data["error"]
        return node


class PipelineState:
    """
    Represents the state of a pipeline execution.
    """
    
    def __init__(self, pipeline_id: str, pipeline_def: PipelineDefinition):
        """
        Initialize a pipeline state.
        
        Args:
            pipeline_id: The ID of the pipeline execution
            pipeline_def: The pipeline definition
        """
        self.pipeline_id = pipeline_id
        self.definition = pipeline_def
        self.status = PipelineStatus.SUBMITTED
        self.progress = 0.0
        self.message = f"Pipeline {pipeline_def.definition['name']} submitted"
        self.start_time = time.time()
        self.end_time = None
        self.nodes = {}  # node_id -> NodeState
        self.execution_order = pipeline_def.build_execution_order()
        self.current_node_index = 0
        self.error = None
        
        # Initialize node states
        for node in pipeline_def.definition["nodes"]:
            self.nodes[node["id"]] = NodeState(node["id"], node["agent_name"])
    
    def update_progress(self) -> None:
        """
        Update the overall pipeline progress based on node progress.
        """
        if not self.nodes:
            self.progress = 0.0
            return
        
        if self.status in [PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.CANCELED]:
            # Set appropriate progress for final states
            self.progress = 1.0 if self.status == PipelineStatus.COMPLETED else self.progress
            return
        
        # Calculate progress based on completed nodes and current node progress
        total_nodes = len(self.nodes)
        completed_nodes = sum(1 for node in self.nodes.values() 
                             if node.status == NodeStatus.COMPLETED)
        
        # If we have a current node in progress, factor in its progress
        current_progress = 0.0
        if self.current_node_index < len(self.execution_order):
            current_node_id = self.execution_order[self.current_node_index]
            if current_node_id in self.nodes:
                current_node = self.nodes[current_node_id]
                if current_node.status in [NodeStatus.SUBMITTED, NodeStatus.WORKING, NodeStatus.INPUT_REQUIRED]:
                    current_progress = current_node.progress / total_nodes
        
        # Overall progress is completed nodes plus current node progress
        self.progress = (completed_nodes / total_nodes) + current_progress
    
    def update_status(self) -> None:
        """
        Update the overall pipeline status based on node status.
        """
        # If already in a final state, don't change
        if self.status in [PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.CANCELED]:
            return
        
        # Check if any node has failed
        for node in self.nodes.values():
            if node.status == NodeStatus.FAILED:
                self.status = PipelineStatus.FAILED
                self.message = f"Pipeline failed: Node {node.node_id} - {node.message}"
                self.error = node.error
                self.end_time = time.time()
                return
        
        # Check if all nodes have completed
        all_completed = all(node.status == NodeStatus.COMPLETED for node in self.nodes.values())
        if all_completed:
            self.status = PipelineStatus.COMPLETED
            self.message = f"Pipeline completed successfully"
            self.end_time = time.time()
            return
        
        # Check if any node is in input-required state
        for node in self.nodes.values():
            if node.status == NodeStatus.INPUT_REQUIRED:
                self.status = PipelineStatus.INPUT_REQUIRED
                self.message = f"Pipeline waiting for input: Node {node.node_id} - {node.message}"
                return
        
        # Otherwise, pipeline is working
        self.status = PipelineStatus.WORKING
        
        # Update message based on current node
        if self.current_node_index < len(self.execution_order):
            current_node_id = self.execution_order[self.current_node_index]
            if current_node_id in self.nodes:
                current_node = self.nodes[current_node_id]
                self.message = f"Pipeline working: Node {current_node_id} - {current_node.message}"
    
    def get_current_node(self) -> Optional[NodeState]:
        """
        Get the currently executing node.
        
        Returns:
            NodeState of the current node, or None if no current node
        """
        if self.current_node_index >= len(self.execution_order):
            return None
        
        current_node_id = self.execution_order[self.current_node_index]
        return self.nodes.get(current_node_id)
    
    def advance_to_next_node(self) -> Optional[NodeState]:
        """
        Advance to the next node in the execution order.
        
        Returns:
            NodeState of the next node, or None if no more nodes
        """
        self.current_node_index += 1
        return self.get_current_node()
    
    def get_node_dependencies_status(self, node_id: str) -> Dict[str, str]:
        """
        Get the status of all dependencies for a node.
        
        Args:
            node_id: The ID of the node to get dependency status for
            
        Returns:
            Dict mapping dependency node IDs to their status
        """
        dependencies = self.definition.get_dependencies(node_id)
        return {dep: self.nodes[dep].status.value if isinstance(self.nodes[dep].status, NodeStatus) else self.nodes[dep].status 
                for dep in dependencies if dep in self.nodes}
    
    def are_dependencies_satisfied(self, node_id: str) -> bool:
        """
        Check if all dependencies for a node are satisfied (completed).
        
        Args:
            node_id: The ID of the node to check dependencies for
            
        Returns:
            True if all dependencies are completed, False otherwise
        """
        dependency_status = self.get_node_dependencies_status(node_id)
        return all(status == NodeStatus.COMPLETED.value for status in dependency_status.values())
    
    def get_artifact_from_node(self, node_id: str, artifact_name: str) -> Optional[Dict[str, Any]]:
        """
        Get an artifact from a node.
        
        Args:
            node_id: The ID of the node to get the artifact from
            artifact_name: The name of the artifact to get
            
        Returns:
            The artifact, or None if not found
        """
        if node_id not in self.nodes:
            return None
        
        node = self.nodes[node_id]
        if node.status != NodeStatus.COMPLETED:
            return None
        
        for artifact in node.artifacts:
            if artifact.get("name") == artifact_name:
                return artifact
        
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the pipeline state to a dictionary.
        
        Returns:
            Dict representation of the pipeline state
        """
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.definition.definition["name"],
            "description": self.definition.definition.get("description", ""),
            "status": self.status.value if isinstance(self.status, PipelineStatus) else self.status,
            "progress": self.progress,
            "message": self.message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "current_node_index": self.current_node_index,
            "execution_order": self.execution_order,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], pipeline_def: PipelineDefinition) -> "PipelineState":
        """
        Create a pipeline state from a dictionary.
        
        Args:
            data: Dict containing pipeline state data
            pipeline_def: The pipeline definition
            
        Returns:
            PipelineState instance
        """
        pipeline = cls(data["pipeline_id"], pipeline_def)
        pipeline.status = PipelineStatus(data["status"]) if isinstance(data["status"], str) else data["status"]
        pipeline.progress = data["progress"]
        pipeline.message = data["message"]
        pipeline.start_time = data["start_time"]
        pipeline.end_time = data["end_time"]
        pipeline.current_node_index = data["current_node_index"]
        pipeline.execution_order = data["execution_order"]
        pipeline.error = data["error"]
        
        # Restore node states
        pipeline.nodes = {}
        for node_id, node_data in data["nodes"].items():
            pipeline.nodes[node_id] = NodeState.from_dict(node_data)
        
        return pipeline


class NodeExecutionContext:
    """
    Context wrapper for node execution that reports node progress to the pipeline.
    """
    
    def __init__(self, pipeline_state: PipelineState, node_id: str, parent_ctx):
        """
        Initialize the node execution context.
        
        Args:
            pipeline_state: The pipeline state
            node_id: The ID of the node being executed
            parent_ctx: The parent FastMCP context
        """
        self.pipeline_state = pipeline_state
        self.node_id = node_id
        self.parent_ctx = parent_ctx
    
    async def report_progress(self, current: float, total: float, message: str) -> None:
        """
        Report node progress and update pipeline progress.
        
        Args:
            current: Current progress value
            total: Total progress value
            message: Progress message
        """
        # Update node state
        node = self.pipeline_state.nodes[self.node_id]
        node.progress = current / total if total > 0 else 0
        node.message = message
        node.status = NodeStatus.WORKING
        
        # Update pipeline state
        self.pipeline_state.update_status()
        self.pipeline_state.update_progress()
        
        # Report progress to parent context
        await self.parent_ctx.report_progress(
            current=self.pipeline_state.progress,
            total=1.0,
            message=self.pipeline_state.message
        )
    
    async def complete(self, message: str) -> None:
        """
        Complete node execution.
        
        Args:
            message: Completion message
        """
        # Update node state
        node = self.pipeline_state.nodes[self.node_id]
        node.progress = 1.0
        node.message = message
        node.status = NodeStatus.COMPLETED
        node.end_time = time.time()
        
        # Update pipeline state
        self.pipeline_state.update_status()
        self.pipeline_state.update_progress()
        
        # Report progress to parent context
        await self.parent_ctx.report_progress(
            current=self.pipeline_state.progress,
            total=1.0,
            message=self.pipeline_state.message
        )
    
    async def error(self, message: str) -> None:
        """
        Report node error.
        
        Args:
            message: Error message
        """
        # Update node state
        node = self.pipeline_state.nodes[self.node_id]
        node.message = message
        node.status = NodeStatus.FAILED
        node.error = message
        node.end_time = time.time()
        
        # Update pipeline state
        self.pipeline_state.update_status()
        self.pipeline_state.update_progress()
        
        # Report progress to parent context
        await self.parent_ctx.report_progress(
            current=self.pipeline_state.progress,
            total=1.0,
            message=self.pipeline_state.message
        )


class PipelineExecutionEngine:
    """
    Engine for executing pipelines.
    """
    
    def __init__(self, server_state):
        """
        Initialize the pipeline execution engine.
        
        Args:
            server_state: The ServerState instance
        """
        self.server_state = server_state
    
    async def execute_pipeline(self, pipeline_def: PipelineDefinition, initial_input: Union[str, Dict[str, Any]], ctx) -> str:
        """
        Execute a pipeline.
        
        Args:
            pipeline_def: The pipeline definition
            initial_input: The initial input to the pipeline (string or dict)
            ctx: The FastMCP context
            
        Returns:
            The pipeline execution ID
        """
        # Create a new pipeline execution ID
        pipeline_id = str(uuid.uuid4())
        
        # Create pipeline state
        pipeline_state = PipelineState(pipeline_id, pipeline_def)
        
        # Store in server state
        self.server_state.pipelines[pipeline_id] = pipeline_state
        
        # Start execution asynchronously
        asyncio.create_task(self._execute_pipeline_nodes(pipeline_id, initial_input, ctx))
        
        # Return the pipeline ID
        return pipeline_id
    
    async def _execute_pipeline_nodes(self, pipeline_id: str, initial_input: Union[str, Dict[str, Any]], ctx) -> None:
        """
        Execute the nodes in a pipeline.
        
        Args:
            pipeline_id: The ID of the pipeline execution
            initial_input: The initial input to the pipeline
            ctx: The FastMCP context
        """
        # Get pipeline state
        pipeline_state = self.server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            await ctx.error(f"Pipeline {pipeline_id} not found")
            return
        
        # Initial inputs are stored for the first node
        inputs = {"initial_input": initial_input}
        
        # Execute nodes in order
        for i, node_id in enumerate(pipeline_state.execution_order):
            # Set current node index
            pipeline_state.current_node_index = i
            
            # Get node state
            node_state = pipeline_state.nodes[node_id]
            
            # Update pipeline status
            pipeline_state.update_status()
            pipeline_state.update_progress()
            
            # Check if we should continue
            if pipeline_state.status in [PipelineStatus.FAILED, PipelineStatus.CANCELED]:
                logger.info(f"Pipeline {pipeline_id} {pipeline_state.status}, stopping execution")
                await self._report_pipeline_progress(pipeline_id, ctx)
                return
            
            # Check if dependencies are satisfied
            if not pipeline_state.are_dependencies_satisfied(node_id):
                error_msg = f"Cannot execute node {node_id}: dependencies not satisfied"
                logger.error(error_msg)
                node_state.status = NodeStatus.FAILED
                node_state.message = error_msg
                node_state.error = error_msg
                pipeline_state.update_status()
                pipeline_state.update_progress()
                await self._report_pipeline_progress(pipeline_id, ctx)
                return
            
            # Map inputs from previous nodes
            node_inputs = self._map_inputs(pipeline_state, node_id, inputs)
            
            # Execute the node
            logger.info(f"Executing node {node_id} in pipeline {pipeline_id}")
            await self._report_pipeline_progress(pipeline_id, ctx)
            
            try:
                # Update node status to submitted
                node_state.status = NodeStatus.SUBMITTED
                node_state.message = f"Node {node_id} submitted for execution"
                node_state.start_time = time.time()
                pipeline_state.update_status()
                pipeline_state.update_progress()
                await self._report_pipeline_progress(pipeline_id, ctx)
                
                # Execute the node
                result = await self._execute_node(pipeline_state, node_id, node_inputs, ctx)
                
                # Store artifacts in the inputs for the next node
                if result:
                    inputs[node_id] = result
                
                # Update node status based on result
                if node_state.status != NodeStatus.FAILED:
                    node_state.status = NodeStatus.COMPLETED
                    node_state.end_time = time.time()
                    node_state.progress = 1.0
                    node_state.message = f"Node {node_id} completed successfully"
                
                # Update pipeline status
                pipeline_state.update_status()
                pipeline_state.update_progress()
                await self._report_pipeline_progress(pipeline_id, ctx)
                
                # Check if we should continue
                if pipeline_state.status in [PipelineStatus.FAILED, PipelineStatus.CANCELED]:
                    logger.info(f"Pipeline {pipeline_id} {pipeline_state.status}, stopping execution")
                    return
                
            except Exception as e:
                # Handle node execution error
                logger.exception(f"Error executing node {node_id} in pipeline {pipeline_id}: {str(e)}")
                
                # Update node status
                node_state.status = NodeStatus.FAILED
                node_state.message = f"Node {node_id} failed: {str(e)}"
                node_state.error = str(e)
                node_state.end_time = time.time()
                
                # Update pipeline status
                pipeline_state.update_status()
                pipeline_state.update_progress()
                await self._report_pipeline_progress(pipeline_id, ctx)
                
                # Check error policy
                error_policy = pipeline_state.definition.get_error_policy(node_id)
                if error_policy == ErrorPolicy.FAIL_FAST:
                    logger.info(f"Pipeline {pipeline_id} failed with fail_fast policy, stopping execution")
                    return
                elif error_policy == ErrorPolicy.CONTINUE:
                    logger.info(f"Pipeline {pipeline_id} continuing despite node {node_id} failure")
                    continue
                else:
                    # For now, treat retry the same as fail_fast (will implement retry later)
                    logger.info(f"Pipeline {pipeline_id} failed with retry policy, stopping execution (retry not implemented)")
                    return
        
        # All nodes executed, pipeline is complete
        pipeline_state.status = PipelineStatus.COMPLETED
        pipeline_state.progress = 1.0
        pipeline_state.message = "Pipeline completed successfully"
        pipeline_state.end_time = time.time()
        await self._report_pipeline_progress(pipeline_id, ctx)
        
        # If we have final outputs defined, collect them
        final_outputs = []
        for node_id in pipeline_state.definition.definition["final_outputs"]:
            if node_id in inputs:
                final_outputs.append({
                    "node_id": node_id,
                    "artifacts": inputs[node_id]
                })
        
        # Complete the context with the final outputs
        final_outputs_str = json.dumps(final_outputs, indent=2)
        await ctx.complete(f"Pipeline {pipeline_id} completed successfully with outputs: {final_outputs_str}")
    
    def _map_inputs(self, pipeline_state: PipelineState, node_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map inputs for a node based on input mappings.
        
        Args:
            pipeline_state: The pipeline state
            node_id: The ID of the node to map inputs for
            inputs: Dict of available inputs from previous nodes
            
        Returns:
            Dict of mapped inputs for the node
        """
        # Get input mappings from the pipeline definition
        input_mappings = pipeline_state.definition.get_input_mappings(node_id)
        
        # If no mappings defined, pass the initial input to the first node
        if not input_mappings and node_id == pipeline_state.execution_order[0]:
            return inputs.get("initial_input", {})
        
        # If no mappings defined for non-first nodes, collect artifacts from all upstream nodes
        if not input_mappings:
            # Find all upstream nodes
            upstream_nodes = set()
            for i, exec_node_id in enumerate(pipeline_state.execution_order):
                if exec_node_id == node_id:
                    break
                upstream_nodes.add(exec_node_id)
            
            # Collect artifacts from all upstream nodes
            mapped_inputs = {}
            for upstream_node in upstream_nodes:
                if upstream_node in inputs:
                    mapped_inputs[upstream_node] = inputs[upstream_node]
            
            return mapped_inputs
        
        # Apply input mappings
        mapped_inputs = {}
        for input_name, mapping in input_mappings.items():
            source_node = mapping["source_node"]
            source_artifact = mapping["source_artifact"]
            
            # Skip if source node data not available
            if source_node not in inputs:
                logger.warning(f"Source node {source_node} not in available inputs for node {node_id}")
                continue
            
            # Get the artifact from the source node
            artifact = pipeline_state.get_artifact_from_node(source_node, source_artifact)
            if artifact:
                mapped_inputs[input_name] = artifact
            else:
                logger.warning(f"Artifact {source_artifact} not found in node {source_node}")
        
        return mapped_inputs
    
    async def _execute_node(self, pipeline_state: PipelineState, node_id: str, inputs: Dict[str, Any], ctx) -> Optional[List[Dict[str, Any]]]:
        """
        Execute a single node in the pipeline.
        
        Args:
            pipeline_state: The pipeline state
            node_id: The ID of the node to execute
            inputs: The inputs for the node
            ctx: The FastMCP context
            
        Returns:
            List of artifacts produced by the node, or None if execution failed
        """
        # Get node state
        node_state = pipeline_state.nodes[node_id]
        agent_name = node_state.agent_name
        
        # Check if agent exists
        if agent_name not in self.server_state.registry:
            error_msg = f"Agent {agent_name} not found in registry"
            logger.error(error_msg)
            node_state.status = NodeStatus.FAILED
            node_state.message = error_msg
            node_state.error = error_msg
            return None
        
        # Prepare input for the agent
        # For initial implementation, assume text input and flatten all inputs into a text prompt
        prompt = self._prepare_prompt_from_inputs(inputs)
        
        # Execute the agent
        logger.info(f"Calling agent {agent_name} for node {node_id} in pipeline {pipeline_state.pipeline_id}")
        
        # Generate unique IDs for the request
        request_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        
        # Store the task and session IDs in the node state
        node_state.task_id = task_id
        node_state.session_id = session_id
        
        # Create a node execution context for progress tracking
        node_ctx = NodeExecutionContext(pipeline_state, node_id, ctx)
        
        try:
            # Call the agent using existing server function
            result = await self.server_state.call_agent_for_pipeline(
                agent_name=agent_name,
                prompt=prompt,
                pipeline_id=pipeline_state.pipeline_id,
                node_id=node_id,
                request_id=request_id,
                task_id=task_id,
                session_id=session_id,
                ctx=node_ctx
            )
            
            # Check if the agent call was successful
            if result.get("status") == "error":
                error_msg = result.get("message", "Unknown error")
                logger.error(f"Error calling agent {agent_name} for node {node_id}: {error_msg}")
                node_state.status = NodeStatus.FAILED
                node_state.message = f"Error calling agent: {error_msg}"
                node_state.error = error_msg
                return None
            
            # Get the artifacts produced by the agent
            artifacts = result.get("artifacts", [])
            
            # Store artifacts in the node state
            node_state.artifacts = artifacts
            
            return artifacts
            
        except Exception as e:
            logger.exception(f"Error executing node {node_id} in pipeline {pipeline_state.pipeline_id}: {str(e)}")
            node_state.status = NodeStatus.FAILED
            node_state.message = f"Error executing node: {str(e)}"
            node_state.error = str(e)
            return None
    
    def _prepare_prompt_from_inputs(self, inputs: Dict[str, Any]) -> str:
        """
        Prepare a prompt from the inputs for the agent.
        
        Args:
            inputs: The inputs for the node
            
        Returns:
            Prompt string
        """
        # For initial implementation, concatenate all text parts from all inputs
        prompt_parts = []
        
        # If we have a direct initial input, use it
        if "initial_input" in inputs and isinstance(inputs["initial_input"], str):
            return inputs["initial_input"]
        
        # Otherwise, process all inputs
        for input_name, input_value in inputs.items():
            # Skip initial_input if it's not a string
            if input_name == "initial_input" and not isinstance(input_value, str):
                continue
                
            # If input is a string, add directly
            if isinstance(input_value, str):
                prompt_parts.append(f"{input_name}: {input_value}")
                continue
                
            # If input is a list (e.g., artifacts), extract text parts
            if isinstance(input_value, list):
                for item in input_value:
                    if isinstance(item, dict):
                        # If this is an artifact with parts
                        if "parts" in item:
                            for part in item["parts"]:
                                if part.get("type") == "text" and "text" in part:
                                    prompt_parts.append(part["text"])
        
        # Join all parts with a separator
        return "\n\n".join(prompt_parts)
    
    async def _report_pipeline_progress(self, pipeline_id: str, ctx) -> None:
        """
        Report pipeline progress to the FastMCP context.
        
        Args:
            pipeline_id: The ID of the pipeline execution
            ctx: The FastMCP context
        """
        pipeline_state = self.server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            await ctx.error(f"Pipeline {pipeline_id} not found")
            return
        
        # Report progress
        await ctx.report_progress(
            current=pipeline_state.progress,
            total=1.0,
            message=pipeline_state.message
        )