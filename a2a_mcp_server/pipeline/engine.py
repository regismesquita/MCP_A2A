"""
Pipeline execution engine module.

Contains classes for executing pipelines and managing their state.
"""

import asyncio
import json
import logging
import random
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union, TYPE_CHECKING

from a2a_mcp_server.pipeline.definition import ErrorPolicy, PipelineDefinition
from fastmcp import Context

# For type hinting ServerState to avoid circular import at module level
if TYPE_CHECKING:
    from a2a_mcp_server.server import ServerState
else:
    pass  # No runtime import needed

# Import safe_context_call from utils to avoid circular dependency
from a2a_mcp_server.utils.context import safe_context_call

logger = logging.getLogger(__name__)


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
        self.retry_attempts = 0  # Track number of retry attempts
        self.last_retry_time = None  # Track last retry timestamp

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the node state to a dictionary.

        Returns:
            Dict representation of the node state
        """
        return {
            "node_id": self.node_id,
            "agent_name": self.agent_name,
            "status": self.status.value
            if isinstance(self.status, NodeStatus)
            else self.status,
            "progress": self.progress,
            "message": self.message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "task_id": self.task_id,
            "artifacts": self.artifacts,
            "error": self.error,
            "retry_attempts": self.retry_attempts,
            "last_retry_time": self.last_retry_time,
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
        node.status = (
            NodeStatus(data["status"])
            if isinstance(data["status"], str)
            else data["status"]
        )
        node.progress = data["progress"]
        node.message = data["message"]
        node.start_time = data["start_time"]
        node.end_time = data["end_time"]
        node.task_id = data["task_id"]
        node.artifacts = data["artifacts"]
        node.error = data["error"]
        # Handle backward compatibility with states that don't have retry fields
        node.retry_attempts = data.get("retry_attempts", 0)
        node.last_retry_time = data.get("last_retry_time", None)
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
        old_progress = self.progress
        
        if not self.nodes:
            self.progress = 0.0
            return

        # Handle final states with appropriate progress values
        if self.status == PipelineStatus.COMPLETED:
            # For completed pipelines, always set progress to 1.0
            self.progress = 1.0
            return
        elif self.status == PipelineStatus.FAILED:
            # For failed pipelines, preserve progress but ensure it's not 1.0
            # This helps visually distinguish failed vs. completed on UI
            self.progress = min(self.progress, 0.99)
            return
        elif self.status == PipelineStatus.CANCELED:
            # For canceled pipelines, preserve the progress at cancellation time
            return

        # Calculate progress based on completed nodes and current node progress
        total_nodes = len(self.nodes)
        completed_nodes = sum(
            1 for node in self.nodes.values() if node.status == NodeStatus.COMPLETED
        )

        # If we have a current node in progress, factor in its progress
        current_progress = 0.0
        if self.current_node_index < len(self.execution_order):
            current_node_id = self.execution_order[self.current_node_index]
            if current_node_id in self.nodes:
                current_node = self.nodes[current_node_id]
                if current_node.status in [
                    NodeStatus.SUBMITTED,
                    NodeStatus.WORKING,
                    NodeStatus.INPUT_REQUIRED,
                ]:
                    current_progress = current_node.progress / total_nodes

        # Overall progress is completed nodes plus current node progress
        self.progress = (completed_nodes / total_nodes) + current_progress
        
        # Log significant progress changes to help with debugging
        if abs(self.progress - old_progress) > 0.05:  # Only log when progress changes by >5%
            logger.debug(
                f"Pipeline '{self.pipeline_id}' progress updated: {old_progress:.2f} → {self.progress:.2f}, "
                f"Completed nodes: {completed_nodes}/{total_nodes}"
            )

    def update_status(self) -> None:
        """
        Update the overall pipeline status based on node status.
        """
        previous_status = self.status
        
        # If already in a final state, don't change
        if self.status in [
            PipelineStatus.COMPLETED,
            PipelineStatus.FAILED,
            PipelineStatus.CANCELED,
        ]:
            return

        # Check if any node has failed
        for node in self.nodes.values():
            if node.status == NodeStatus.FAILED:
                self.status = PipelineStatus.FAILED
                self.message = f"Pipeline failed: Node {node.node_id} - {node.message}"
                self.error = node.error
                self.end_time = time.time()
                logger.info(
                    f"Pipeline '{self.pipeline_id}' status changed to {self.status.value} due to node '{node.node_id}' failure. "
                    f"Error: {node.error}"
                )
                return

        # Check if all nodes have completed
        all_completed = all(
            node.status == NodeStatus.COMPLETED for node in self.nodes.values()
        )
        if all_completed:
            self.status = PipelineStatus.COMPLETED
            self.message = "Pipeline completed successfully"
            self.end_time = time.time()
            completion_time = self.end_time - self.start_time if self.start_time else 0
            logger.info(
                f"Pipeline '{self.pipeline_id}' status changed to {self.status.value}. "
                f"All {len(self.nodes)} nodes completed successfully in {completion_time:.2f}s"
            )
            return

        # Check if any node is in input-required state
        for node in self.nodes.values():
            if node.status == NodeStatus.INPUT_REQUIRED:
                self.status = PipelineStatus.INPUT_REQUIRED
                self.message = (
                    f"Pipeline waiting for input: Node {node.node_id} - {node.message}"
                )
                if previous_status != PipelineStatus.INPUT_REQUIRED:
                    logger.info(
                        f"Pipeline '{self.pipeline_id}' status changed to {self.status.value}. "
                        f"Node '{node.node_id}' requires input"
                    )
                return

        # Otherwise, pipeline is working
        self.status = PipelineStatus.WORKING

        # Update message based on current node
        if self.current_node_index < len(self.execution_order):
            current_node_id = self.execution_order[self.current_node_index]
            if current_node_id in self.nodes:
                current_node = self.nodes[current_node_id]
                self.message = (
                    f"Pipeline working: Node {current_node_id} - {current_node.message}"
                )
                
                # Log status transition if changed
                if previous_status != PipelineStatus.WORKING:
                    logger.debug(
                        f"Pipeline '{self.pipeline_id}' status changed to {self.status.value}. "
                        f"Current node: '{current_node_id}', Node status: {current_node.status.value}"
                    )

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
        return {
            dep: self.nodes[dep].status.value
            if isinstance(self.nodes[dep].status, NodeStatus)
            else self.nodes[dep].status
            for dep in dependencies
            if dep in self.nodes
        }

    def are_dependencies_satisfied(self, node_id: str) -> bool:
        """
        Check if all dependencies for a node are satisfied (completed).

        Args:
            node_id: The ID of the node to check dependencies for

        Returns:
            True if all dependencies are completed, False otherwise
        """
        dependency_status = self.get_node_dependencies_status(node_id)
        return all(
            status == NodeStatus.COMPLETED.value
            for status in dependency_status.values()
        )

    def get_artifact_from_node(
        self, node_id: str, artifact_name: str
    ) -> Optional[Dict[str, Any]]:
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
            "status": self.status.value
            if isinstance(self.status, PipelineStatus)
            else self.status,
            "progress": self.progress,
            "message": self.message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "current_node_index": self.current_node_index,
            "execution_order": self.execution_order,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "error": self.error,
        }

    @classmethod
    def from_dict(
        cls, data: Dict[str, Any], pipeline_def: PipelineDefinition
    ) -> "PipelineState":
        """
        Create a pipeline state from a dictionary.

        Args:
            data: Dict containing pipeline state data
            pipeline_def: The pipeline definition

        Returns:
            PipelineState instance
        """
        pipeline = cls(data["pipeline_id"], pipeline_def)
        pipeline.status = (
            PipelineStatus(data["status"])
            if isinstance(data["status"], str)
            else data["status"]
        )
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

    async def report_progress(self, progress_value: float) -> None:
        """
        Report node progress and update pipeline progress.

        Args:
            progress_value: Progress value (0.0 to 1.0)
        """
        # Update node state
        node = self.pipeline_state.nodes[self.node_id]
        node.progress = progress_value
        # We no longer receive a message parameter
        node.status = NodeStatus.WORKING

        # Update pipeline state
        self.pipeline_state.update_status()
        self.pipeline_state.update_progress()

        # Report progress to parent context with error handling
        try:
            # Try report_progress with defensive programming
            if hasattr(self.parent_ctx, "report_progress") and callable(self.parent_ctx.report_progress):
                await self.parent_ctx.report_progress(
                    self.pipeline_state.progress
                )
            else:
                logger.warning(f"Parent context has no report_progress method in NodeExecutionContext.report_progress")
        except Exception as e:
            logger.warning(f"Error in NodeExecutionContext.report_progress: {str(e)}")
            # No further fallback needed

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

        # Report progress to parent context using safe_context_call
        try:
            # First try normal report_progress
            await self.parent_ctx.report_progress(
                self.pipeline_state.progress
            )
        except Exception as e:
            logger.warning(f"Error in NodeExecutionContext.complete when calling report_progress: {str(e)}")
            # We don't need another fallback here since this is itself a fallback

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

        # Report progress to parent context with error handling
        try:
            # Try to call error on parent context
            if hasattr(self.parent_ctx, "error") and callable(self.parent_ctx.error):
                await self.parent_ctx.error(message)
            else:
                # Fallback to report_progress if error method doesn't exist
                await self.parent_ctx.report_progress(
                    self.pipeline_state.progress
                )
        except Exception as e:
            logger.warning(f"Error in NodeExecutionContext.error when calling parent context methods: {str(e)}")
            # No further fallback needed


class PipelineExecutionEngine:
    """
    Engine for executing pipelines.
    """

    def __init__(self, server_state: 'ServerState'):
        """
        Initialize the pipeline execution engine.

        Args:
            server_state: The ServerState instance
        """
        self.server_state = server_state

    async def execute_pipeline(
        self,
        pipeline_def: PipelineDefinition,
        initial_input: Union[str, Dict[str, Any]],
        ctx: Context,
    ) -> str:
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

        # Store in server state with eviction check
        self.server_state.pipelines[pipeline_id] = pipeline_state
        self.server_state.pipeline_timestamps[pipeline_id] = time.time()
        
        # Check if we need to evict old pipelines
        if (hasattr(self.server_state, 'max_pipelines') and 
            len(self.server_state.pipelines) > self.server_state.max_pipelines):
            self.server_state._evict_old_pipelines()

        # Start execution asynchronously
        asyncio.create_task(
            self._execute_pipeline_nodes(pipeline_id, initial_input, ctx)
        )

        # Return the pipeline ID
        return pipeline_id
        
    async def _execute_pipeline_nodes(
        self, pipeline_id: str, initial_input: Union[str, Dict[str, Any]], ctx: Context
    ) -> None:
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

        # inputs_for_nodes will store outputs from completed nodes
        # The key is the node_id that produced the artifacts, value is the list of artifacts
        inputs_for_nodes: Dict[str, List[Dict[str, Any]]] = {}
        
        if isinstance(initial_input, str):
            # Convention: initial string input is an artifact named "initial_code" from a virtual "start" node
            inputs_for_nodes["start_node"] = [{"name": "initial_code", "parts": [{"type": "text", "text": initial_input}]}]
        elif isinstance(initial_input, dict):  # Assuming initial_input is like {"code": {"content": "...", "type": "text"}}
            # Convert this to the artifact format expected by _map_inputs
            processed_initial_inputs = []
            for key, artifact_data in initial_input.items():
                if isinstance(artifact_data, dict) and "content" in artifact_data and "type" in artifact_data:
                    processed_initial_inputs.append({"name": key, "parts": [{"type": artifact_data["type"], "text": artifact_data["content"]}]})
                else:  # pass as is if not matching expected structure
                    processed_initial_inputs.append({"name": key, "parts": [{"type": "text", "text": str(artifact_data)}]})  # fallback
            inputs_for_nodes["start_node"] = processed_initial_inputs


        for i, node_id in enumerate(pipeline_state.execution_order):
            pipeline_state.current_node_index = i
            node_state = pipeline_state.nodes[node_id]

            pipeline_state.update_status()
            pipeline_state.update_progress()
            await self._report_pipeline_progress(pipeline_id, ctx)

            if pipeline_state.status in [PipelineStatus.FAILED, PipelineStatus.CANCELED]:
                logger.info(f"Pipeline {pipeline_id} {pipeline_state.status}, stopping execution")
                return

            if not pipeline_state.are_dependencies_satisfied(node_id):
                error_msg = f"Cannot execute node {node_id}: dependencies not satisfied"
                logger.error(error_msg)
                node_state.status = NodeStatus.FAILED
                node_state.message = error_msg
                node_state.error = error_msg
                pipeline_state.update_status()  # This will set pipeline to FAILED
                pipeline_state.update_progress()
                await self._report_pipeline_progress(pipeline_id, ctx)
                return

            node_mapped_inputs = self._map_inputs(pipeline_state, node_id, inputs_for_nodes, initial_input)
            
            logger.info(f"Executing node {node_id} in pipeline {pipeline_id}")
            node_state.status = NodeStatus.SUBMITTED
            node_state.message = f"Node {node_id} submitted for execution"
            node_state.start_time = time.time()
            pipeline_state.update_status()
            pipeline_state.update_progress()
            await self._report_pipeline_progress(pipeline_id, ctx)

            try:
                node_execution_successful = False
                for attempt in range(pipeline_state.definition.get_retry_config(node_id)["max_attempts"] + 1 if pipeline_state.definition.get_error_policy(node_id) == ErrorPolicy.RETRY else 1):
                    node_state.retry_attempts = attempt
                    result_artifacts = await self._execute_node(pipeline_state, node_id, node_mapped_inputs, ctx)

                    if node_state.status == NodeStatus.FAILED:
                        error_policy = pipeline_state.definition.get_error_policy(node_id)
                        if error_policy == ErrorPolicy.RETRY and attempt < pipeline_state.definition.get_retry_config(node_id)["max_attempts"]:
                            retry_config = pipeline_state.definition.get_retry_config(node_id)
                            delay = min(
                                retry_config["base_delay_seconds"] * (retry_config["backoff_factor"] ** attempt),
                                retry_config["max_delay_seconds"]
                            )
                            jitter = random.uniform(0, 0.1 * delay)
                            actual_delay = delay + jitter
                            
                            node_state.last_retry_time = time.time()
                            node_state.status = NodeStatus.PENDING  # Reset for retry
                            node_state.message = f"Node {node_id} failed, retrying (attempt {attempt + 1}) after {actual_delay:.2f}s"
                            node_state.progress = 0.0
                            pipeline_state.update_status()
                            pipeline_state.update_progress()
                            await self._report_pipeline_progress(pipeline_id, ctx)
                            logger.info(f"Node {node_id} retrying in {actual_delay:.2f}s")
                            await asyncio.sleep(actual_delay)
                            node_state.start_time = time.time()  # Reset start time for retry
                            node_state.status = NodeStatus.SUBMITTED  # Resubmit for retry
                            node_state.message = f"Node {node_id} retrying (attempt {attempt + 1})"
                            await self._report_pipeline_progress(pipeline_id, ctx)
                        else:  # Max retries reached or not a retry policy
                            node_execution_successful = False
                            break  # Exit retry loop
                    else:  # Node completed successfully or needs input
                        node_execution_successful = True
                        if result_artifacts is not None:
                            inputs_for_nodes[node_id] = result_artifacts
                        break  # Exit retry loop
                
                if not node_execution_successful:  # Node ultimately failed
                    # Status already set to FAILED by _execute_node or retry loop
                    pass  # Handled by outer error policy check
                elif node_state.status != NodeStatus.FAILED:  # Node completed or is waiting for input
                    if node_state.status != NodeStatus.INPUT_REQUIRED and node_state.status != NodeStatus.CANCELED:
                        node_state.status = NodeStatus.COMPLETED
                        node_state.end_time = time.time()
                        node_state.progress = 1.0
                        node_state.message = f"Node {node_id} completed successfully"

            except Exception as e:
                logger.exception(f"Unhandled error executing node {node_id}: {str(e)}")
                node_state.status = NodeStatus.FAILED
                node_state.message = f"Node {node_id} failed: {str(e)}"
                node_state.error = str(e)
                node_state.end_time = time.time()

            pipeline_state.update_status()
            pipeline_state.update_progress()
            await self._report_pipeline_progress(pipeline_id, ctx)

            if pipeline_state.status == PipelineStatus.FAILED:
                error_policy = pipeline_state.definition.get_error_policy(node_id)
                if error_policy == ErrorPolicy.FAIL_FAST:
                    logger.info(f"Pipeline {pipeline_id} failed with fail_fast policy on node {node_id}")
                    return
                elif error_policy == ErrorPolicy.CONTINUE:
                    logger.info(f"Pipeline {pipeline_id} continuing despite node {node_id} failure")
                    # Mark node as failed but allow pipeline to continue
                    node_state.status = NodeStatus.FAILED 
                    # Pipeline status will be updated to FAILED at the end if any node failed and policy was continue
                # RETRY is handled within the loop above
            
            if pipeline_state.status == PipelineStatus.CANCELED:
                logger.info(f"Pipeline {pipeline_id} canceled during node {node_id} execution.")
                return
            
            if node_state.status == NodeStatus.INPUT_REQUIRED:
                logger.info(f"Pipeline {pipeline_id} paused, node {node_id} requires input.")
                # The pipeline execution will pause here. It will be resumed by send_pipeline_input.
                return


        # If loop completes and pipeline not failed/canceled, it's completed.
        if pipeline_state.status not in [PipelineStatus.FAILED, PipelineStatus.CANCELED, PipelineStatus.INPUT_REQUIRED]:
            pipeline_state.status = PipelineStatus.COMPLETED
            pipeline_state.progress = 1.0
            pipeline_state.message = "Pipeline completed successfully"
            pipeline_state.end_time = time.time()
        
        await self._report_pipeline_progress(pipeline_id, ctx)

        final_output_artifacts = []
        for output_node_id in pipeline_state.definition.get_final_output_nodes():
            if output_node_id in inputs_for_nodes:
                final_output_artifacts.extend(inputs_for_nodes[output_node_id])
            elif output_node_id in pipeline_state.nodes and pipeline_state.nodes[output_node_id].artifacts:
                final_output_artifacts.extend(pipeline_state.nodes[output_node_id].artifacts)


        if pipeline_state.status == PipelineStatus.COMPLETED:
            await safe_context_call(ctx, "complete", 
                f"Pipeline {pipeline_id} completed successfully. Final artifacts: {json.dumps(final_output_artifacts)}"
            )
        elif pipeline_state.status == PipelineStatus.FAILED:
            # Still use error directly as it has its own error handling in general
            await ctx.error(
                f"Pipeline {pipeline_id} failed. Last message: {pipeline_state.message}"
            )
        # Canceled or Input Required states are handled by their respective calls or by node updates.
        
    def _map_inputs(
        self, pipeline_state: PipelineState, node_id: str, 
        available_node_outputs: Dict[str, List[Dict[str, Any]]],
        initial_pipeline_input: Union[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Map inputs for a node.
        `available_node_outputs` is a dict where key is source_node_id and value is its list of artifacts.
        `initial_pipeline_input` is the raw input given to the pipeline.
        """
        node_def = pipeline_state.definition.get_node_by_id(node_id)
        if not node_def: return {}

        input_mappings = node_def.get("inputs", {})
        mapped_inputs: Dict[str, Any] = {}

        if not input_mappings:  # No explicit input mapping
            if node_id == pipeline_state.execution_order[0]:  # First node
                # If initial_pipeline_input was a string, it's in available_node_outputs["start_node"]
                # If it was a dict, it's also there.
                start_node_outputs = available_node_outputs.get("start_node", [])
                if start_node_outputs:
                    # Convention: first node takes the first artifact from "start_node" as its primary input
                    # Or, if multiple initial artifacts, they are all passed.
                    # For simplicity, let's pass all initial artifacts.
                    # The agent itself will need to know how to handle multiple input artifacts if that's the case.
                    # Typically, for a single string input, there's one artifact.
                    return { "initial_artifacts": start_node_outputs }
                return {}  # No initial input provided in a way it can use
            else:  # Not the first node and no explicit mappings, try to gather all outputs from direct dependencies
                dependencies = pipeline_state.definition.get_dependencies(node_id)
                for dep_id in dependencies:
                    if dep_id in available_node_outputs:
                        # Use a key that indicates the source node
                        mapped_inputs[f"output_from_{dep_id}"] = available_node_outputs[dep_id]
                return mapped_inputs


        for target_input_name, source_spec in input_mappings.items():
            source_node_id = source_spec["source_node"]
            source_artifact_name = source_spec["source_artifact"]

            if source_node_id in available_node_outputs:
                source_artifacts = available_node_outputs[source_node_id]
                found_artifact = None
                for art in source_artifacts:
                    if art.get("name") == source_artifact_name:
                        found_artifact = art
                        break
                if found_artifact:
                    mapped_inputs[target_input_name] = found_artifact
                else:
                    logger.warning(f"Artifact '{source_artifact_name}' not found in outputs of node '{source_node_id}' for node '{node_id}'.")
            else:
                logger.warning(f"Source node '{source_node_id}' outputs not found for node '{node_id}'.")
        return mapped_inputs

    
    async def _execute_node(
        self, pipeline_state: PipelineState, node_id: str, inputs: Dict[str, Any], ctx: Context
    ) -> Optional[List[Dict[str, Any]]]:
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
        prompt_text = self._prepare_prompt_from_inputs(inputs)

        # Generate unique IDs for the request
        mcp_agent_request_id = str(uuid.uuid4())
        a2a_task_id = str(uuid.uuid4())
        a2a_session_id = str(uuid.uuid4())

        # Store the task and session IDs in the node state
        node_state.task_id = a2a_task_id
        node_state.session_id = a2a_session_id

        # Create a node execution context for progress tracking
        node_ctx = NodeExecutionContext(pipeline_state, node_id, ctx)

        # Get the timeout for this node from the pipeline definition
        timeout_seconds = pipeline_state.definition.get_timeout(node_id)
        
        agent_call_result: Optional[Dict[str, Any]] = None
        try:
            logger.info(f"Node '{node_id}': Preparing to call agent {agent_name}. Timeout: {timeout_seconds}s")
            
            # Create a task for call_agent_for_pipeline
            agent_call_coro = self.server_state.call_agent_for_pipeline(
                agent_name=agent_name,
                prompt=prompt_text,
                pipeline_id=pipeline_state.pipeline_id,
                node_id=node_id,
                request_id=mcp_agent_request_id,  # Pass the unique ID for this call
                task_id=a2a_task_id,
                session_id=a2a_session_id,
                ctx=node_ctx,  # Pass the NodeExecutionContext
                force_non_streaming=True,  # Force non-streaming mode to avoid async generator issues
            )

            # Wrap it with a timeout if needed
            try:
                if timeout_seconds:
                    agent_call_result = await asyncio.wait_for(agent_call_coro, timeout=timeout_seconds)
                else:
                    agent_call_result = await agent_call_coro
            except asyncio.TimeoutError:
                # Make sure to catch the timeout here to prevent unhandled exceptions
                error_msg = f"Node '{node_id}' execution timed out after {timeout_seconds}s"
                logger.error(error_msg)
                await node_ctx.error(error_msg)  # Report error through node_ctx
                raise  # Re-raise to be caught by the outer try/except block
            
            # After the call, node_state.status should have been updated by call_agent_for_pipeline via node_ctx
            # agent_call_result contains artifacts and final status message from the agent call.
            # node_state.artifacts should already be populated by call_agent_for_pipeline.
            
            # Log the agent call result for debugging
            if agent_call_result:
                agent_status = agent_call_result.get("status", "unknown")
                artifact_count = len(agent_call_result.get("artifacts", []))
                node_status_from_result = agent_call_result.get("node_status", "unknown")
                
                logger.debug(
                    f"Node '{node_id}' (Pipeline '{pipeline_state.pipeline_id}'): Agent call to '{agent_name}' returned: "
                    f"status={agent_status}, node_status={node_status_from_result}, artifacts={artifact_count}"
                )
            else:
                logger.warning(f"Node '{node_id}': Agent call returned None or empty result")

            # Check node state after agent call
            if node_state.status == NodeStatus.FAILED:
                logger.error(
                    f"Node '{node_id}' (Pipeline '{pipeline_state.pipeline_id}'): Agent call reported failure. "
                    f"Message: {node_state.message}, Error: {node_state.error or 'No error details'}"
                )
                return None  # Error already handled by node_ctx.error
            
            if node_state.status == NodeStatus.INPUT_REQUIRED:
                logger.info(
                    f"Node '{node_id}' (Pipeline '{pipeline_state.pipeline_id}'): Agent call resulted in INPUT_REQUIRED. "
                    f"Message: {node_state.message}"
                )
                # Pipeline will pause. Return current artifacts if any.
                return node_state.artifacts 

            # If not FAILED or INPUT_REQUIRED, and agent_call_result is valid, assume success from agent's perspective
            # node_state.status should be COMPLETED if call_agent_for_pipeline called node_ctx.complete()
            artifact_names = [art.get("name", "unnamed") for art in node_state.artifacts] if node_state.artifacts else []
            logger.info(
                f"Node '{node_id}' (Pipeline '{pipeline_state.pipeline_id}'): Agent call finished with status {node_state.status.value}. "
                f"Artifacts: {len(node_state.artifacts)} [{', '.join(artifact_names)}]"
            )
            return node_state.artifacts

        except asyncio.TimeoutError:
            execution_time = time.time() - (node_state.start_time or time.time())
            error_msg = f"Node '{node_id}' execution timed out after {timeout_seconds}s (ran for {execution_time:.2f}s)"
            
            # Log detailed node state information for debugging timeouts
            node_info = {
                "node_id": node_id,
                "pipeline_id": pipeline_state.pipeline_id,
                "agent_name": agent_name,
                "task_id": a2a_task_id,
                "session_id": a2a_session_id,
                "request_id": mcp_agent_request_id,
                "timeout_seconds": timeout_seconds,
                "execution_time": execution_time,
                "current_status": node_state.status.value if hasattr(node_state.status, 'value') else str(node_state.status),
                "current_progress": node_state.progress,
            }
            
            logger.error(f"TIMEOUT ERROR: {error_msg}")
            logger.error(f"Node state: {node_info}")
            
            await node_ctx.error(error_msg)  # This sets node_state.status to FAILED
            
            # Attempt to cancel the underlying A2A task
            if a2a_task_id:
                try:
                    logger.warning(f"Node '{node_id}': Timeout occurred. Attempting to cancel request with ID {mcp_agent_request_id}")
                    await self.server_state.cancel_request(request_id=mcp_agent_request_id, ctx=ctx)
                    logger.info(f"Node '{node_id}': Successfully sent cancellation request for task {a2a_task_id}")
                except Exception as cancel_e:
                    logger.warning(f"Node '{node_id}': Failed to cancel timed-out task {a2a_task_id}: {str(cancel_e)}")
            
            return None  # Timeout means failure for this node
            
        except Exception as e:
            error_msg = f"Node '{node_id}' (Pipeline '{pipeline_state.pipeline_id}'): Unhandled exception during execution: {str(e)}"
            
            # Log detailed node and error information
            error_type = type(e).__name__
            logger.error(f"ERROR [{error_type}]: {error_msg}")
            logger.exception("Detailed traceback:")
            
            # Log the state of the node and relevant context
            current_state = {
                "node_id": node_id,
                "pipeline_id": pipeline_state.pipeline_id,
                "agent_name": agent_name,
                "current_status": node_state.status.value if hasattr(node_state.status, 'value') else str(node_state.status),
                "current_progress": node_state.progress,
                "error_type": error_type,
            }
            logger.error(f"Node state at error: {current_state}")
            
            await node_ctx.error(error_msg)  # This sets node_state.status to FAILED
            return None

    def _prepare_prompt_from_inputs(self, inputs: Dict[str, Any]) -> str:
        """
        Prepare a prompt from the inputs for the agent.
        Inputs is a dictionary where keys are input names (or 'initial_artifacts' or 'output_from_nodeX')
        and values are either an artifact dictionary or a list of artifact dictionaries.
        """
        prompt_parts = []
        if not inputs:
            logger.debug("Empty inputs provided to _prepare_prompt_from_inputs, returning empty string")
            return ""

        # 'inputs' is expected to be like:
        # {"data_input_name": artifact_dict1, "config_input_name": artifact_dict2}
        # or for the first node, it might be {"initial_artifacts": [artifact_dict_initial]}
        
        # Special handling for initial input to first node
        if "initial_artifacts" in inputs and isinstance(inputs["initial_artifacts"], list):
            logger.debug(f"Processing initial_artifacts for prompt preparation. Count: {len(inputs['initial_artifacts'])}")
            for i, artifact in enumerate(inputs["initial_artifacts"]):
                if not isinstance(artifact, dict):
                    logger.warning(f"Unexpected initial artifact format (not a dict): {type(artifact)}")
                    continue
                    
                if "parts" not in artifact:
                    logger.warning(f"Initial artifact missing 'parts' key: {artifact.keys()}")
                    continue
                
                # Extract text from parts
                for part in artifact.get("parts", []):
                    if isinstance(part, dict) and part.get("type") == "text" and "text" in part:
                        prompt_parts.append(part['text'])  # Just the text for initial simple prompt
                    else:
                        logger.debug(f"Skipping non-text part in initial artifact {i}: {part}")
                        
            if prompt_parts:
                logger.debug(f"Using simplified format for initial artifacts, found {len(prompt_parts)} parts")
                return "\n\n".join(prompt_parts)  # Return early if it was simple initial input

        # General case for mapped inputs from other nodes
        for input_name_mapped, artifact_dict in inputs.items():
            # Case 1: Single artifact dictionary
            if isinstance(artifact_dict, dict) and "parts" in artifact_dict:
                current_artifact_texts = self._extract_text_from_artifact(artifact_dict, input_name_mapped)
                if current_artifact_texts:
                    prompt_parts.append(
                        f"--- Input from '{input_name_mapped}' (Artifact: '{artifact_dict.get('name', 'Unnamed')}') ---\n" +
                        "\n".join(current_artifact_texts) +
                        "\n--- End Input ---"
                    )
            
            # Case 2: List of artifact dictionaries
            elif isinstance(artifact_dict, list):
                logger.debug(f"Processing list of artifacts for input '{input_name_mapped}', count: {len(artifact_dict)}")
                for artifact in artifact_dict:
                    if isinstance(artifact, dict) and "parts" in artifact:
                        current_artifact_texts = self._extract_text_from_artifact(artifact, input_name_mapped)
                        if current_artifact_texts:
                            prompt_parts.append(
                                f"--- Input from '{input_name_mapped}' (Artifact: '{artifact.get('name', 'Unnamed')}') ---\n" +
                                "\n".join(current_artifact_texts) +
                                "\n--- End Input ---"
                            )
                    else:
                        logger.warning(f"Unexpected artifact format in list for '{input_name_mapped}': {type(artifact)}")
            
            # Case 3: Anything else - use as is with warning
            else:
                logger.warning(f"Unexpected input format for '{input_name_mapped}': {type(artifact_dict)}")
                prompt_parts.append(f"--- Input: {input_name_mapped} ---\n{str(artifact_dict)}\n--- End Input ---")
        
        # Final fallback if we couldn't extract anything meaningful
        if not prompt_parts:
            logger.warning(f"No valid text parts found in inputs, using serialized format: {list(inputs.keys())}")
            try:
                return json.dumps(inputs)  # Fallback: serialize the whole input dict
            except TypeError as e:
                logger.warning(f"Failed to serialize inputs with json.dumps: {e}")
                return str(inputs)
        
        return "\n\n".join(prompt_parts)
    
    def _extract_text_from_artifact(self, artifact: Dict[str, Any], input_name: str) -> List[str]:
        """
        Helper method to extract text parts from an artifact.
        Returns a list of text strings from valid text parts.
        
        Args:
            artifact: The artifact dictionary containing parts
            input_name: The name of the input (for logging)
            
        Returns:
            List of extracted text strings
        """
        text_parts = []
        
        if not isinstance(artifact, dict) or "parts" not in artifact:
            logger.warning(f"Invalid artifact for '{input_name}': missing 'parts' key or not a dict")
            return text_parts
            
        artifact_name = artifact.get("name", "Unnamed")
        
        for i, part in enumerate(artifact.get("parts", [])):
            if not isinstance(part, dict):
                logger.debug(f"Non-dict part ({type(part)}) in artifact '{artifact_name}' for '{input_name}'")
                continue
                
            if part.get("type") == "text" and "text" in part:
                text_content = part["text"]
                # Check for empty content
                if not text_content:
                    logger.debug(f"Empty text content in part {i} of artifact '{artifact_name}' for '{input_name}'")
                text_parts.append(text_content)
            else:
                logger.debug(f"Skipping non-text part in artifact '{artifact_name}' for '{input_name}': {part.get('type', 'unknown')}")
                
        return text_parts


    async def _report_pipeline_progress(self, pipeline_id: str, ctx: Context) -> None:
        """
        Report pipeline progress to the FastMCP context.

        Args:
            pipeline_id: The ID of the pipeline execution
            ctx: The FastMCP context
        """
        pipeline_state = self.server_state.pipelines.get(pipeline_id)
        if not pipeline_state:
            # This case should ideally not happen if called correctly
            error_msg = f"Pipeline {pipeline_id} not found for progress reporting."
            logger.error(error_msg)
            await ctx.error(error_msg)
            return

        # Ensure status and progress are up-to-date before reporting
        pipeline_state.update_status()
        pipeline_state.update_progress()

        # Log current state of the pipeline and active node
        current_node = pipeline_state.get_current_node()
        current_node_id = current_node.node_id if current_node else "None"
        current_node_status = current_node.status.value if current_node else "N/A"
        
        logger.debug(
            f"Pipeline '{pipeline_id}' progress report: status={pipeline_state.status.value}, "
            f"progress={pipeline_state.progress:.2f}, current_node={current_node_id}, "
            f"node_status={current_node_status}, message='{pipeline_state.message}'"
        )

        # Report to the FastMCP context with defensive programming
        try:
            if hasattr(ctx, "report_progress") and callable(ctx.report_progress):
                await ctx.report_progress(
                    pipeline_state.progress
                )
            else:
                logger.warning(f"Context has no report_progress method in _report_pipeline_progress for pipeline {pipeline_id}")
        except Exception as e:
            logger.warning(f"Error reporting pipeline progress: {str(e)}")
            # No need for fallback as this is progress reporting