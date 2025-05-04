#!/usr/bin/env python3
"""
A2A MCP Server - Main entry point

This module provides a command-line interface to:
1. Run the server

Usage:
  python -m a2a_mcp_server server  # Start the MCP server
"""

import sys
import argparse
import os
from a2a_mcp_server.server import main_cli as server_main


def main():
    """Main entry point for the package."""
    # Check if being run as an MCP server in Claude Desktop
    # In this case, run the server directly without requiring arguments
    if len(sys.argv) == 1:
        # Running as MCP server in Claude Desktop
        server_main()
        return

    # Otherwise, handle as normal CLI
    parser = argparse.ArgumentParser(description="A2A MCP Server")
    parser.add_argument("command", choices=["server"], 
                       help="Which component to run")
    
    try:
        args = parser.parse_args()
        
        if args.command == "server":
            server_main()
        else:
            parser.print_help()
    except SystemExit:
        # Handle SystemExit from argparse in a way that doesn't print to stdout
        # This ensures MCP communication isn't disrupted
        if len(sys.argv) < 2:
            # If no arguments, assume we want to run the server
            server_main()
        else:
            # Otherwise, re-raise the exception
            raise


if __name__ == "__main__":
    main()
