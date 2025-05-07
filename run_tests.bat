@echo off
REM Test runner script that ensures the correct Python version via uvx
REM
REM Examples:
REM   # Run all tests
REM   run_tests.bat
REM
REM   # Run a specific test file
REM   run_tests.bat tests/integration/test_pipeline_execution.py
REM
REM   # Run with coverage
REM   run_tests.bat --cov=a2a_mcp_server
REM
REM This script automatically:
REM - Installs uvx if needed
REM - Installs all test dependencies
REM - Ensures Python 3.12.2+ is used
REM - Handles all test setup and execution
REM
REM You only need Python installed - everything else is handled automatically

python run_tests.py %*
