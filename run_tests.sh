#!/bin/bash
# Test runner script that uses a Python virtual environment
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
# - Creates a virtual environment if it doesn't exist
# - Activates the virtual environment
# - Installs all test dependencies
# - Runs pytest with the specified arguments
#
# You only need Python installed - everything else is handled automatically

# Make script executable if needed
if [ ! -x "$0" ]; then
  chmod +x "$0"
fi

# Set up environment variables
VENV_DIR=".venv"
PYTHON="python3"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR..."
  $PYTHON -m venv "$VENV_DIR"
  if [ $? -ne 0 ]; then
    echo "Failed to create virtual environment. Please check your Python installation."
    exit 1
  fi
fi

# Activate virtual environment
echo "Activating virtual environment..."
if [ -f "$VENV_DIR/bin/activate" ]; then
  source "$VENV_DIR/bin/activate"
else
  echo "Could not find activation script in $VENV_DIR/bin/"
  exit 1
fi

# Install dependencies
echo "Installing dependencies..."
pip install -e ".[test]"
if [ $? -ne 0 ]; then
  echo "Failed to install dependencies."
  exit 1
fi

# Run tests
echo "Running tests..."
python -m pytest "$@"

# Return the exit code from pytest
exit $?
