"""
Pipeline package for A2A MCP Server.

Contains pipeline definitions and utility functions for pipeline orchestration.
"""

from a2a_mcp_server.pipeline.definition import ErrorPolicy, PipelineDefinition

from a2a_mcp_server.pipeline.code_review_pipeline import (
    get_pipeline_definition,
    get_pipeline_definition_json,
    get_simplified_pipeline_definition,
)

# Import pipeline components from engine module
from a2a_mcp_server.pipeline.engine import (
    PipelineExecutionEngine,
    NodeStatus,
    PipelineStatus,
    PipelineState,
    NodeState,
    NodeExecutionContext,
)