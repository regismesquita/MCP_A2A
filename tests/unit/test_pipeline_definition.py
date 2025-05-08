"""Unit tests for the PipelineDefinition class."""

import pytest
import jsonschema
from unittest.mock import MagicMock, patch
import sys
import os

# Debug imports
import sys

print("Available modules:", [name for name in sys.modules if "pipeline" in name])

# Import module directly
# Import from the pipeline package (not the redundant pipeline.py file)
from a2a_mcp_server.pipeline import PipelineDefinition, ErrorPolicy

# Import our test fixtures
from tests.fixtures.pipeline_templates import (
    create_simple_pipeline,
    create_complex_pipeline,
    create_error_policy_pipeline,
    create_invalid_pipeline,
    create_circular_pipeline,
)


def test_pipeline_definition_validation(valid_pipeline_def):
    """Test validation of pipeline definition."""
    # Valid pipeline should pass validation
    pipeline = PipelineDefinition(valid_pipeline_def)
    assert pipeline.definition["name"] == "Test Pipeline"

    # Invalid pipeline should fail validation
    with pytest.raises(Exception):
        PipelineDefinition(create_invalid_pipeline())


@pytest.mark.parametrize(
    "pipeline_structure,expected_order",
    [
        # Simple linear pipeline: A -> B -> C
        (
            {
                "name": "Linear Pipeline",
                "nodes": [
                    {"id": "A", "agent_name": "agent1"},
                    {
                        "id": "B", 
                        "agent_name": "agent2",
                        "inputs": {"data": {"source_node": "A", "source_artifact": "output"}}
                    },
                    {
                        "id": "C", 
                        "agent_name": "agent3",
                        "inputs": {"data": {"source_node": "B", "source_artifact": "output"}}
                    }
                ],
                "final_outputs": ["C"]
            },
            ["A", "B", "C"]
        ),
        # Branching: A -> B, A -> C (B, C are alphabetically ordered at same level)
        (
            {
                "name": "Branching Pipeline",
                "nodes": [
                    {"id": "A", "agent_name": "agent1"},
                    {
                        "id": "B", 
                        "agent_name": "agent2",
                        "inputs": {"data": {"source_node": "A", "source_artifact": "output"}}
                    },
                    {
                        "id": "C", 
                        "agent_name": "agent3",
                        "inputs": {"data": {"source_node": "A", "source_artifact": "output"}}
                    }
                ],
                "final_outputs": ["B", "C"]
            },
            ["A", "B", "C"]  # B, C in alphabetical order at same level
        ),
        # Converging: A -> C, B -> C
        (
            {
                "name": "Converging Pipeline",
                "nodes": [
                    {"id": "A", "agent_name": "agent1"},
                    {"id": "B", "agent_name": "agent2"},
                    {
                        "id": "C", 
                        "agent_name": "agent3",
                        "inputs": {
                            "data1": {"source_node": "A", "source_artifact": "output"},
                            "data2": {"source_node": "B", "source_artifact": "output"}
                        }
                    }
                ],
                "final_outputs": ["C"]
            },
            ["A", "B", "C"]  # A, B in alphabetical order at same level
        ),
        # Mixed: A -> B, B -> C, B -> D, C -> E, D -> E
        (
            {
                "name": "Mixed Pipeline",
                "nodes": [
                    {"id": "A", "agent_name": "agent1"},
                    {
                        "id": "B",
                        "agent_name": "agent2",
                        "inputs": {"data": {"source_node": "A", "source_artifact": "output"}}
                    },
                    {
                        "id": "C",
                        "agent_name": "agent3",
                        "inputs": {"data": {"source_node": "B", "source_artifact": "output"}}
                    },
                    {
                        "id": "D",
                        "agent_name": "agent4",
                        "inputs": {"data": {"source_node": "B", "source_artifact": "output"}}
                    },
                    {
                        "id": "E",
                        "agent_name": "agent5",
                        "inputs": {
                            "data1": {"source_node": "C", "source_artifact": "output"},
                            "data2": {"source_node": "D", "source_artifact": "output"}
                        }
                    }
                ],
                "final_outputs": ["E"]
            },
            ["A", "B", "C", "D", "E"]  # C, D in alphabetical order at same level
        ),
        # Pipeline with no dependencies (nodes X, Y, Z)
        (
            {
                "name": "No Dependencies Pipeline",
                "nodes": [
                    {"id": "X", "agent_name": "agent1"},
                    {"id": "Y", "agent_name": "agent2"},
                    {"id": "Z", "agent_name": "agent3"}
                ],
                "final_outputs": ["X", "Y", "Z"]
            },
            ["X", "Y", "Z"]  # X, Y, Z in alphabetical order (no dependencies)
        )
    ]
)
def test_get_execution_order(pipeline_structure, expected_order):
    """Test getting execution order based on dependencies with various pipeline structures."""
    # Create pipeline from the test case
    pipeline = PipelineDefinition(pipeline_structure)
    
    # Get execution order
    execution_order = pipeline.get_execution_order()
    
    # Verify execution order matches expected
    assert execution_order == expected_order, f"Expected {expected_order}, got {execution_order}"
    
    # Additionally verify dependencies are correctly identified for each node
    for i, node_id in enumerate(execution_order):
        node_deps = pipeline.get_dependencies(node_id)
        
        # Check each node's dependencies are before it in the execution order
        for dep in node_deps:
            assert execution_order.index(dep) < i, f"Dependency {dep} of {node_id} should come before it in execution order"


def test_execution_order_complex_pipeline():
    """Test execution order with the complex pipeline template."""
    # Test with complex pipeline
    complex_def = create_complex_pipeline()
    complex_pipeline = PipelineDefinition(complex_def)
    execution_order = complex_pipeline.get_execution_order()
    
    # Check all nodes are in the execution order
    for node_id in ["source", "preprocess", "analyze", "visualize", "report"]:
        assert node_id in execution_order
    
    # Check dependency relationships are preserved in execution order
    # These dependency assertions verify the topological sort
    assert execution_order.index("source") < execution_order.index("preprocess")
    assert execution_order.index("preprocess") < execution_order.index("analyze")
    assert execution_order.index("analyze") < execution_order.index("report")
    assert execution_order.index("visualize") < execution_order.index("report")
    
    # Verify all dependencies are correctly identified
    deps = complex_pipeline.get_dependencies("report")
    assert "analyze" in deps
    assert "visualize" in deps
    
    deps = complex_pipeline.get_dependencies("analyze")
    assert "preprocess" in deps
    
    deps = complex_pipeline.get_dependencies("preprocess")
    assert "source" in deps


def test_circular_dependency_detection():
    """Test detection of circular dependencies."""
    circular_def = create_circular_pipeline()

    # First create a valid pipeline
    pipeline = PipelineDefinition(create_simple_pipeline())

    # Then directly test the get_execution_order method with a circular dependency
    with pytest.raises(Exception) as exc_info:
        # Create a new pipeline with circular dependencies and check if get_execution_order raises
        circular_pipeline = PipelineDefinition(circular_def)
        circular_pipeline.get_execution_order()

    # Check exception message contains indication of cycle
    assert "cycle" in str(exc_info.value).lower()


def test_error_policy_handling():
    """Test error policy handling."""
    pipeline_def = create_error_policy_pipeline()
    pipeline = PipelineDefinition(pipeline_def)

    # Test error policy retrieval
    fail_fast = pipeline.get_error_policy("node2")
    continue_policy = pipeline.get_error_policy("node3")
    retry_policy = pipeline.get_error_policy("node4")

    # Check correct policy values
    # We're using string comparison to avoid direct dependency on the actual enum values
    assert str(fail_fast) == str(ErrorPolicy.FAIL_FAST)
    assert str(continue_policy) == str(ErrorPolicy.CONTINUE)
    assert str(retry_policy) == str(ErrorPolicy.RETRY)

    # Default error policy for node without explicit policy
    default_policy = pipeline.get_error_policy("node1")
    assert str(default_policy) == str(ErrorPolicy.FAIL_FAST)  # Usually the default


def test_retry_config():
    """Test retry configuration retrieval."""
    pipeline_def = create_error_policy_pipeline()
    pipeline = PipelineDefinition(pipeline_def)

    # Get node with retry policy
    node4 = pipeline.get_node_by_id("node4")
    assert node4 is not None

    # Get retry config
    retry_config = node4.get("retry_config", {})
    assert retry_config is not None
    assert retry_config["max_attempts"] == 3
    assert retry_config["base_delay_seconds"] == 0.1
    assert retry_config["backoff_factor"] == 2.0

    # Default retry config for nodes without explicit config
    node1 = pipeline.get_node_by_id("node1")
    default_config = node1.get("retry_config", {})
    # Implementation might return a default or empty dict
    assert isinstance(default_config, dict)


def test_get_node():
    """Test retrieving node information."""
    pipeline_def = create_complex_pipeline()
    pipeline = PipelineDefinition(pipeline_def)

    # Get existing node
    node = pipeline.get_node_by_id("analyze")
    assert node is not None
    assert node["id"] == "analyze"
    assert node["agent_name"] == "analyzer"
    assert "inputs" in node

    # Non-existent node should return None
    nonexistent = pipeline.get_node_by_id("nonexistent")
    assert nonexistent is None


def test_get_input_mapping():
    """Test retrieving input mapping information."""
    pipeline_def = create_complex_pipeline()
    pipeline = PipelineDefinition(pipeline_def)

    # Get node with dependencies first
    analyze_node = pipeline.get_node_by_id("analyze")
    assert analyze_node is not None

    # Get inputs for a node with dependencies
    inputs = analyze_node.get("inputs", {})
    assert inputs is not None
    assert "data" in inputs
    assert inputs["data"]["source_node"] == "preprocess"
    assert inputs["data"]["source_artifact"] == "processed_data"

    # Node without inputs should return empty dict or None
    source_node = pipeline.get_node_by_id("source")
    source_inputs = source_node.get("inputs", {})
    assert len(source_inputs) == 0


def test_validate_schema():
    """Test schema validation for pipeline definitions."""
    # Valid
    valid_def = create_simple_pipeline()
    pipeline = PipelineDefinition(valid_def)
    assert pipeline.definition == valid_def

    # Invalid - missing nodes
    invalid_def = {"name": "Invalid Pipeline", "final_outputs": ["node1"]}
    with pytest.raises(Exception) as excinfo:
        PipelineDefinition(invalid_def)
    assert "nodes" in str(excinfo.value).lower(), "Error should mention missing 'nodes' field"

    # Invalid - missing name
    invalid_def = {
        "nodes": [{"id": "node1", "agent_name": "agent1"}],
        "final_outputs": ["node1"],
    }
    with pytest.raises(Exception) as excinfo:
        PipelineDefinition(invalid_def)
    assert "name" in str(excinfo.value).lower(), "Error should mention missing 'name' field"

    # In the new implementation, final_outputs is not required
    # So we'll skip this test
    """
    # Invalid - missing final_outputs 
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [{"id": "node1", "agent_name": "agent1"}],
    }
    with pytest.raises(Exception):
        PipelineDefinition(invalid_def)
    """

    # Invalid - node missing id
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [{"agent_name": "agent1"}],
        "final_outputs": ["node1"],
    }
    with pytest.raises(Exception) as excinfo:
        PipelineDefinition(invalid_def)
    assert "id" in str(excinfo.value).lower(), "Error should mention missing 'id' field"

    # Invalid - node missing agent_name
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [{"id": "node1"}],
        "final_outputs": ["node1"],
    }
    with pytest.raises(Exception) as excinfo:
        PipelineDefinition(invalid_def)
    assert "agent_name" in str(excinfo.value).lower(), "Error should mention missing 'agent_name' field"
    
    # Invalid - specific test for node with missing agent_name in a list of valid nodes
    invalid_def = {
        "name": "Invalid Pipeline with Missing agent_name",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},  # Valid node
            {"id": "node2"},  # Missing agent_name
            {"id": "node3", "agent_name": "agent3"},  # Valid node
        ],
        "final_outputs": ["node3"],
    }
    with pytest.raises(Exception) as excinfo:
        PipelineDefinition(invalid_def)
    assert "agent_name" in str(excinfo.value).lower(), "Error should mention missing 'agent_name' field"
    assert "node2" in str(excinfo.value), "Error should mention the problematic node 'node2'"


def test_final_outputs_validation():
    """Test validation of final_outputs."""
    # Valid
    valid_def = create_simple_pipeline()
    pipeline = PipelineDefinition(valid_def)
    assert "node2" in pipeline.definition["final_outputs"]

    # Invalid - final_outputs references non-existent node
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [{"id": "node1", "agent_name": "agent1"}],
        "final_outputs": ["node2"],  # node2 doesn't exist
    }
    with pytest.raises(Exception):
        PipelineDefinition(invalid_def)


def test_input_mapping_validation():
    """Test validation of input mappings."""
    # Valid
    valid_def = create_simple_pipeline()
    pipeline = PipelineDefinition(valid_def)

    # Invalid - input references non-existent source node
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2",
                "agent_name": "agent2",
                "inputs": {
                    "data": {"source_node": "nonexistent", "source_artifact": "output"}
                },
            },
        ],
        "final_outputs": ["node2"],
    }
    with pytest.raises(Exception):
        PipelineDefinition(invalid_def)

    # Invalid - input missing required fields
    invalid_def = {
        "name": "Invalid Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2",
                "agent_name": "agent2",
                "inputs": {
                    "data": {"source_node": "node1"}  # Missing source_artifact
                },
            },
        ],
        "final_outputs": ["node2"],
    }
    with pytest.raises(Exception):
        PipelineDefinition(invalid_def)
