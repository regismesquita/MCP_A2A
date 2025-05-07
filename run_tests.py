#!/usr/bin/env python3
"""
DEPRECATED: This script is deprecated in favor of the improved run_tests.sh script.

The run_tests.sh script now directly creates and uses a virtual environment,
which is a more reliable approach than using uvx.

This script remains for backward compatibility but may be removed in the future.
"""

print("WARNING: This script is deprecated in favor of the improved run_tests.sh script.")
print("The run_tests.sh script now directly creates and uses a virtual environment.")
print("Please use ./run_tests.sh instead.")
sys.exit(1)

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
    # Check for uvx command and install uv package if needed
    if not check_uvx():
        print("uv command (uvx) not found. This is required to ensure the correct Python version for tests.")
        install_uvx()  # install_uvx installs the 'uv' package
        
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
