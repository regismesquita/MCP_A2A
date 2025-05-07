#!/usr/bin/env python3
"""
Test runner script that handles all test setup and execution.

Examples:
    # Run all tests
    python run_tests.py
    
    # Run a specific test file
    python run_tests.py tests/integration/test_pipeline_execution.py
    
    # Run with coverage
    python run_tests.py --cov=a2a_mcp_server
    
This script will automatically:
1. Check if uvx is installed and install it if needed
2. Install all test dependencies from pyproject.toml
3. Run tests with uvx to ensure the correct Python version (3.12.2+)
4. Pass any arguments to pytest

You only need to install uvx, and this script handles everything else.
"""

import os
import sys
import subprocess
import shutil

def check_uvx():
    """Check if uvx is installed and available."""
    return shutil.which("uvx") is not None

def install_uvx():
    """Install uvx if not already installed."""
    print("Installing uv (fast Python package installer and resolver)...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True)
        print("✓ uv installed successfully.")
    except subprocess.CalledProcessError:
        print("✗ Failed to install uv. Please install it manually: pip install uv")
        sys.exit(1)

def ensure_test_dependencies():
    """Ensure test dependencies are installed via uvx."""
    print("Ensuring test dependencies are installed...")
    try:
        # Use uvx pip to install test dependencies from pyproject.toml
        cmd = ["uvx", "pip", "install", "-e", ".[test]"]
        print(f"Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("✓ Test dependencies installed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install test dependencies: {e}")
        return False
    except FileNotFoundError:
        print("✗ Error: uvx command not found during dependency installation.")
        return False

def run_tests_with_uvx(args):
    """Run tests using uvx to ensure correct Python version."""
    # First ensure test dependencies are installed
    if not ensure_test_dependencies():
        print("Cannot run tests without dependencies installed.")
        return 1
        
    # Now run the tests with the correct Python version
    cmd = ["uvx", "python", "-m", "pytest"] + args
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
        return result.returncode
    except subprocess.CalledProcessError as e:
        print(f"Error running tests: {e}")
        return e.returncode
    except FileNotFoundError:
        print("Error: uvx command not found. Make sure it's installed correctly.")
        return 1

def main():
    """Main entry point."""
    # Check for uvx and install if needed
    if not check_uvx():
        print("uvx not found. This is required to ensure the correct Python version.")
        install_uvx()
        
        # Check again after installation
        if not check_uvx():
            print("uvx installation failed or not in PATH. Please install manually.")
            sys.exit(1)
    
    # Get any pytest arguments from command line
    pytest_args = sys.argv[1:] or ["-v"]
    
    # Run tests with uvx
    exit_code = run_tests_with_uvx(pytest_args)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()