# CWM API Gateway MCP - Docker Setup

This document provides instructions for running the ConnectWise API Gateway MCP Server using Docker.

## Quick Start

### 1. Prerequisites
- Docker and Docker Compose installed
- ConnectWise API credentials
- `manage.json` file (ConnectWise API schema)

### 2. Setup Environment
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your actual values
# At minimum, you must set:
# - CONNECTWISE_API_URL
# - CONNECTWISE_COMPANY_ID
# - CONNECTWISE_PUBLIC_KEY
# - CONNECTWISE_PRIVATE_KEY
# - POSTGRES_PASSWORD (change from default)
```

### 3. Start Services
```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Architecture

### Services
- **postgres**: PostgreSQL 15 database server
  - Creates `cached_queries` and `connectwise_api` databases
  - Persistent data storage via Docker volume
  - Health checks ensure readiness

- **api-gateway**: Python MCP server application
  - Waits for PostgreSQL to be ready
  - Automatically builds API database from `manage.json`
  - Component-based database configuration

### Volumes
- `postgres_data`: Persistent PostgreSQL data storage
- `./manage.json`: ConnectWise API schema (read-only)
- `./logs`: Application logs directory

### Networks
- `cwm-network`: Internal bridge network for service communication

## Configuration

### Environment Variables

#### Required (ConnectWise API)
```env
CONNECTWISE_API_URL=https://na.myconnectwise.net/v4_6_release/apis/3.0
CONNECTWISE_COMPANY_ID=your_company_id
CONNECTWISE_PUBLIC_KEY=your_public_key
CONNECTWISE_PRIVATE_KEY=your_private_key
```

#### Optional (Database - uses component-based config)
```env
# PostgreSQL Container
POSTGRES_USER=postgres
POSTGRES_PASSWORD=password
POSTGRES_PORT=5432

# Application Database Connections (automatically set for containers)
CACHED_QUERIES_DB_HOST=postgres
API_DB_HOST=postgres
# ... other DB components use POSTGRES_* values
```

### Database Initialization

The system automatically:
1. Creates PostgreSQL databases: `cached_queries` and `connectwise_api`
2. Waits for PostgreSQL to be ready
3. Builds the API database from `manage.json` (if present)
4. Starts the MCP server

## Development

### Development Mode
Development mode provides:
- Hot-reloading of source code
- Direct database access on port 5432
- Debug logging enabled
- Additional development tools

```bash
# Start in development mode (uses docker-compose.override.yml)
docker-compose up -d

# Access PostgreSQL directly
psql -h localhost -p 5432 -U postgres -d cached_queries
```

### Debugging
```bash
# View application logs
docker-compose logs -f api-gateway

# View database logs
docker-compose logs -f postgres

# Access running container
docker-compose exec api-gateway bash
```

### Manual Database Build
If you need to rebuild the API database:
```bash
# Copy new manage.json to container and rebuild
docker-compose exec api-gateway python build_database.py /app/manage.json
```

## Production Deployment

### Security Considerations
1. Change default passwords in `.env`
2. Use secrets management for sensitive values
3. Consider using non-root database user
4. Enable PostgreSQL SSL if external access needed

### Performance Tuning
1. Adjust PostgreSQL settings in `docker-compose.yml`
2. Configure container resource limits
3. Use external PostgreSQL for scaling

### Example Production Override
Create `docker-compose.prod.yml`:
```yaml
version: '3.8'
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD_FILE}
    volumes:
      - /var/lib/postgresql/data:/var/lib/postgresql/data
  api-gateway:
    restart: always
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Troubleshooting

### Common Issues

#### Database Connection Failed
```bash
# Check PostgreSQL health
docker-compose exec postgres pg_isready -U postgres

# Verify databases exist
docker-compose exec postgres psql -U postgres -l
```

#### Missing manage.json
```bash
# Ensure manage.json is in project root
ls -la manage.json

# Check if mounted in container
docker-compose exec api-gateway ls -la /app/manage.json
```

#### Build Database Failed
```bash
# Check build_database script
docker-compose exec api-gateway python build_database.py /app/manage.json

# Verify API database has tables
docker-compose exec postgres psql -U postgres -d connectwise_api -c "\dt"
```

### Health Checks
```bash
# Check service health
docker-compose ps

# Check container logs for health status
docker-compose logs api-gateway | grep -i health
```

## Commands Reference

```bash
# Build services
docker-compose build

# Start services
docker-compose up -d

# Stop services
docker-compose down

# Remove volumes (WARNING: deletes all data)
docker-compose down -v

# Restart specific service
docker-compose restart api-gateway

# Scale services (if needed)
docker-compose up -d --scale api-gateway=2

# Update services
docker-compose pull
docker-compose up -d
```