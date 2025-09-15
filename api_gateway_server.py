import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the current directory to the module search path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the main function from the server module
from api_gateway.server import main

if __name__ == "__main__":
    main()
