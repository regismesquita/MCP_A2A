"""Pipeline definition templates for testing."""

from typing import Dict, Any, List


def create_simple_pipeline() -> Dict[str, Any]:
    """Create a simple two-node pipeline definition."""
    return {
        "name": "Simple Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2",
                "agent_name": "agent2",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
        ],
        "final_outputs": ["node2"],
    }


def create_complex_pipeline() -> Dict[str, Any]:
    """Create a complex pipeline with multiple nodes and dependencies."""
    return {
        "name": "Complex Pipeline",
        "nodes": [
            {"id": "source", "agent_name": "data_source"},
            {
                "id": "preprocess",
                "agent_name": "preprocessor",
                "inputs": {
                    "data": {"source_node": "source", "source_artifact": "raw_data"}
                },
            },
            {
                "id": "analyze",
                "agent_name": "analyzer",
                "inputs": {
                    "data": {
                        "source_node": "preprocess",
                        "source_artifact": "processed_data",
                    }
                },
            },
            {
                "id": "visualize",
                "agent_name": "visualizer",
                "inputs": {
                    "data": {
                        "source_node": "analyze",
                        "source_artifact": "analysis_results",
                    }
                },
            },
            {
                "id": "report",
                "agent_name": "reporter",
                "inputs": {
                    "analysis": {
                        "source_node": "analyze",
                        "source_artifact": "analysis_results",
                    },
                    "visuals": {
                        "source_node": "visualize",
                        "source_artifact": "visualizations",
                    },
                },
            },
        ],
        "final_outputs": ["report"],
    }


def create_error_policy_pipeline() -> Dict[str, Any]:
    """Create a pipeline with different error policies."""
    return {
        "name": "Error Policy Pipeline",
        "nodes": [
            {"id": "node1", "agent_name": "agent1"},
            {
                "id": "node2",
                "agent_name": "agent2",
                "error_policy": "fail_fast",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
            {
                "id": "node3",
                "agent_name": "agent3",
                "error_policy": "continue",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
            {
                "id": "node4",
                "agent_name": "agent4",
                "error_policy": "retry",
                "retry_config": {
                    "max_attempts": 3,
                    "base_delay_seconds": 0.1,
                    "backoff_factor": 2.0,
                },
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
        ],
        "final_outputs": ["node2", "node3", "node4"],
    }


def create_invalid_pipeline() -> Dict[str, Any]:
    """Create an invalid pipeline for testing validation."""
    return {
        "name": "Invalid Pipeline",
        # Missing required nodes field
        "final_outputs": ["node1"],
    }


def create_circular_pipeline() -> Dict[str, Any]:
    """Create a pipeline with circular dependencies."""
    return {
        "name": "Circular Pipeline",
        "nodes": [
            {
                "id": "node1",
                "agent_name": "agent1",
                "inputs": {
                    "data": {"source_node": "node3", "source_artifact": "output"}
                },
            },
            {
                "id": "node2",
                "agent_name": "agent2",
                "inputs": {
                    "data": {"source_node": "node1", "source_artifact": "output"}
                },
            },
            {
                "id": "node3",
                "agent_name": "agent3",
                "inputs": {
                    "data": {"source_node": "node2", "source_artifact": "output"}
                },
            },
        ],
        "final_outputs": ["node3"],
    }
