-- Create databases for CWM API Gateway MCP Server
-- This script runs automatically when the PostgreSQL container starts

-- Create the cached_queries database
CREATE DATABASE cached_queries;
COMMENT ON DATABASE cached_queries IS 'Database for storing cached API queries';

-- Create the connectwise_api database
CREATE DATABASE connectwise_api;
COMMENT ON DATABASE connectwise_api IS 'Database for storing ConnectWise API metadata';

-- Grant permissions to the default user
GRANT ALL PRIVILEGES ON DATABASE cached_queries TO postgres;
GRANT ALL PRIVILEGES ON DATABASE connectwise_api TO postgres;

-- Connect to cached_queries and create necessary extensions if needed
\c cached_queries;
-- Add any cached_queries specific setup here

-- Connect to connectwise_api and create necessary extensions if needed
\c connectwise_api;
-- Add any connectwise_api specific setup here

-- Return to postgres database
\c postgres;