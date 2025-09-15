#!/usr/bin/env python3
"""
Build Database Script for ConnectWise API Gateway

This script builds the SQLite database for the ConnectWise API Gateway MCP server.
It should be run once before starting the server, or whenever the API definition changes.

Usage:
    python build_database.py <path_to_manage.json>
"""

import os
import sys
import subprocess
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("build_database")

def build_database(json_path):
    """Build the PostgresSQL database from the JSON file."""
    # Directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Path to the converter script
    converter_script = os.path.join(script_dir, "api_gateway", "json_to_postgres.py")
    
    # Output database path
    # db_path = os.path.join(script_dir, "api_gateway", "connectwise_api.db") /
    
    # Check if the JSON file exists
    if not os.path.exists(json_path):
        logger.error(f"Error: JSON file not found at {json_path}")
        return False
    
    # Check if the converter script exists
    if not os.path.exists(converter_script):
        logger.error(f"Error: Converter script not found at {converter_script}")
        return False
    
    # Run the converter script
    try:
        logger.info(f"Building database from {json_path}...")
        subprocess.run([sys.executable, converter_script, json_path], check=True)
        logger.info(f"Database built successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error building database: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <path_to_manage.json>")
        print("\nExample:")
        print(f"  python {os.path.basename(__file__)} C:\\path\\to\\manage.json")
        sys.exit(1)
    
    json_path = sys.argv[1]
    if build_database(json_path):
        print("\nDatabase built successfully!")
        print("You can now run the API Gateway MCP server.")
    else:
        print("\nFailed to build the database. Please check the logs.")
        sys.exit(1)

if __name__ == "__main__":
    main()
