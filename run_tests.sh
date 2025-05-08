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
#   # Skip linting and formatting
#   ./run_tests.sh --no-lint
#
#   # Clean up the project (remove Python cache files, test cache, coverage files)
#   ./run_tests.sh --clean
#
# This script automatically:
# - Creates a virtual environment if it doesn't exist
# - Activates the virtual environment
# - Installs all test dependencies
# - Runs linting with Ruff
# - Runs formatting check with Ruff
# - Runs pytest with the specified arguments
# - Cleans up project files when --clean is specified
#
# You only need Python installed - everything else is handled automatically

# Make script executable if needed
if [ ! -x "$0" ]; then
  chmod +x "$0"
fi

# Set up environment variables
VENV_DIR=".venv"
PYTHON="python3"
LINT_TARGETS="a2a_mcp_server tests"

# Parse arguments to check for --no-lint and --clean flags
skip_lint=false
clean_only=false
pytest_args=()

for arg in "$@"; do
  if [ "$arg" == "--no-lint" ]; then
    skip_lint=true
  elif [ "$arg" == "--clean" ]; then
    clean_only=true
  else
    pytest_args+=("$arg")
  fi
done

# Handle --clean option
if [ "$clean_only" = true ]; then
  echo "Cleaning up project..."
  find . -type f -name "*.py[co]" -delete -o -type d -name "__pycache__" -exec rm -rf {} +
  rm -rf .pytest_cache
  rm -rf htmlcov
  rm -f .coverage*
  echo "Cleanup complete."
  exit 0
fi

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

# Function to run linting and formatting
run_checks() {
  # Run Ruff linting
  echo "Running linting checks with Ruff..."
  ruff check $LINT_TARGETS
  lint_status=$?
  
  if [ $lint_status -ne 0 ]; then
    echo "Linting failed. Please fix the issues before running tests."
    return $lint_status
  fi
  
  # Run Ruff formatting check
  echo "Running formatting checks with Ruff..."
  ruff format --check $LINT_TARGETS
  format_status=$?
  
  if [ $format_status -ne 0 ]; then
    echo "Formatting check failed. Run 'ruff format $LINT_TARGETS' to fix formatting issues."
    return $format_status
  fi
  
  return 0
}

# Run linting and formatting checks if not skipped
if [ "$skip_lint" = false ]; then
  run_checks
  if [ $? -ne 0 ]; then
    exit 1
  fi
fi

# Run tests with simplified coverage reporting
echo "Running tests..."
if [[ "${pytest_args[*]}" == *--cov* ]]; then
  # If coverage is requested, ensure we generate report and xml
  python -m pytest "${pytest_args[@]}" --cov-report=term --cov-report=xml
else
  python -m pytest "${pytest_args[@]}"
fi

# Return the exit code from pytest
exit $?
