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
from a2a_mcp_server.server import main_cli as server_main


def main():
    """Main entry point for the package."""
    parser = argparse.ArgumentParser(description="A2A MCP Server")
    parser.add_argument("command", choices=["server"], 
                       help="Which component to run")
    
    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)
        
    args = parser.parse_args()
    
    if args.command == "server":
        server_main()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
