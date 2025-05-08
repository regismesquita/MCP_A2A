#!/usr/bin/env python3
"""
Entry point for the A2A MCP Server when run as a module (`python -m a2a_mcp_server`).
This script defers all argument parsing and server execution to main_cli in server.py.
"""

from a2a_mcp_server.server import main_cli

if __name__ == "__main__":
    main_cli()
