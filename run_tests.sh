#!/bin/bash
# Test runner script that ensures the correct Python version via uvx
#
# Examples:
#   # Run all tests
#   ./run_tests.sh
#
#   # Run a specific test file
#   ./run_tests.sh tests/integration/test_pipeline_execution.py
#
#   # Run with coverage
#   ./run_tests.sh --cov=a2a_mcp_server
#
# This script automatically:
# - Installs uvx if needed
# - Installs all test dependencies
# - Ensures Python 3.12.2+ is used
# - Handles all test setup and execution
#
# You only need Python installed - everything else is handled automatically

# Make script executable if needed
if [ ! -x "$0" ]; then
  chmod +x "$0"
fi

# Run the Python test runner script
python3 run_tests.py "$@"
