#!/bin/bash
set -e

echo "Starting CWM API Gateway MCP Server..."

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to be ready..."
until pg_isready -h ${API_DB_HOST:-postgres} -p ${API_DB_PORT:-5432} -U ${API_DB_USER:-postgres}; do
    echo "PostgreSQL is unavailable - sleeping"
    sleep 2
done

echo "PostgreSQL is ready!"

# Check if manage.json exists and build database if needed
if [ -f "/app/manage.json" ]; then
    echo "Found manage.json - building API database..."
    python build_database.py /app/manage.json

    if [ $? -eq 0 ]; then
        echo "Database built successfully!"
    else
        echo "Warning: Database build failed, but continuing..."
    fi
else
    echo "Warning: manage.json not found - API database may not be populated"
fi

# Execute the main application
echo "Starting API Gateway Server..."
exec "$@"