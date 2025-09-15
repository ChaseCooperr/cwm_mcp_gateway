#!/usr/bin/env python3
"""
JSON to PostgreSQL Converter for ConnectWise API

This script converts the large manage.json file containing ConnectWise API definitions
into a PostgreSQL database for efficient querying and lookup.

Usage:
    python json_to_postgres.py <path_to_manage.json> [database_url]

Environment Variables:
    DATABASE_URL - PostgreSQL connection string (default: postgresql://user:password@localhost:5432/connectwise_api)
    POSTGRES_HOST - PostgreSQL host (default: localhost)
    POSTGRES_PORT - PostgreSQL port (default: 5432)
    POSTGRES_DB - PostgreSQL database name (default: connectwise_api)
    POSTGRES_USER - PostgreSQL username (default: postgres)
    POSTGRES_PASSWORD - PostgreSQL password (default: password)
"""

import json
import sys
import os
import time
import re
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert
from schema import Base, Endpoint, Parameter, RequestBody, ResponseBody

# Global variable to store loaded API data
API_DATA = None
PATH_DATA = None
SCHEMA_DATA = None

def get_database_url() -> str:
    """Get database URL from environment variables or defaults"""
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/connectwise_api')

    if database_url:
        return database_url
    else:
        host = os.getenv('POSTGRES_HOST', 'localhost')
        port = os.getenv('POSTGRES_PORT', '5432')
        database = os.getenv('POSTGRES_DB', 'connectwise_api')
        user = os.getenv('POSTGRES_USER', 'postgres')
        password = os.getenv('POSTGRES_PASSWORD', 'password')

        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

def get_database_config() -> dict:
    """Get database configuration from environment variables or defaults (for backward compatibility)"""
    database_url = get_database_url()
    parsed = urlparse(database_url)

    return {
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/') or 'connectwise_api',
        'user': parsed.username or 'postgres',
        'password': parsed.password or 'password'
    }

def get_batch_config() -> dict:
    """Get batch processing configuration from environment variables or defaults"""
    return {
        'batch_size': int(os.getenv('BATCH_SIZE', 100))
    }

def extract_keywords(path: str, method: str, summary: str, description: str, operation_id: str, tags: list) -> str:
    """Extract keywords from endpoint data for better searchability (kept for backward compatibility)"""
    keywords = set()

    # Extract from path segments (remove common REST patterns)
    path_segments = [segment for segment in path.split('/') if segment and not segment.startswith('{')]
    keywords.update(path_segments)

    # Add method
    keywords.add(method.upper())

    # Extract from tags
    keywords.update(tags)

    # Extract meaningful words from summary, description and operation_id
    text_content = f"{summary} {description} {operation_id}"

    # Split camel case strings and extract words
    camel_split = re.sub(r'([a-z])([A-Z])', r'\1 \2', text_content)
    text_content = camel_split.lower()

    # Remove common words and extract meaningful terms
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text_content)
    meaningful_words = [word for word in words if word not in {
        'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
        'this', 'that', 'these', 'those', 'is', 'are', 'was', 'were', 'be', 'been',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should',
        'may', 'might', 'must', 'can', 'could', 'from', 'into', 'through', 'during',
        'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off', 'over',
        'under', 'again', 'further', 'then', 'once'
    }]
    keywords.update(meaningful_words[:10])  # Limit to top 10 meaningful words
    # Remove empty strings and convert to lowercase
    keywords = {kw.lower().strip() for kw in keywords if kw and kw.strip()}

    return ','.join(sorted(keywords))

def prepare_search_text(path: str, method: str, summary: str, description: str, operation_id: str, tags: list) -> tuple:
    """Prepare text components for weighted tsvector generation"""
    # Clean and prepare path for search (remove {} placeholders and split into words)
    path_clean = re.sub(r'\{[^}]*\}', ' ', path)  # Replace {id} with space
    path_words = ' '.join([segment for segment in path_clean.split('/') if segment and segment.strip()])

    # Combine method with path for context
    path_method = f"{method.upper()} {path_words}"

    # Clean and prepare summary (highest weight)
    summary_clean = summary.strip() if summary else ''

    # Clean and prepare description
    description_clean = description.strip() if description else ''
    if not description_clean and operation_id:
        # If no description, use operation_id with camelCase splitting
        description_clean = re.sub(r'([a-z])([A-Z])', r'\1 \2', operation_id)

    # Prepare tags
    tags_clean = ' '.join(tags) if tags else ''

    return path_method, summary_clean, description_clean, tags_clean

def create_database_if_not_exists(database_url: str) -> None:
    """Create database if it doesn't exist"""
    try:
        parsed = urlparse(database_url)
        database_name = parsed.path.lstrip('/')

        # Create connection to postgres database
        temp_url = database_url.replace(f'/{database_name}', '/postgres')
        temp_engine = create_engine(temp_url, isolation_level='AUTOCOMMIT')

        with temp_engine.connect() as conn:
            # Check if database exists
            result = conn.execute(text("SELECT 1 FROM pg_catalog.pg_database WHERE datname = :db_name"),
                                {"db_name": database_name})
            exists = result.fetchone()

            if not exists:
                conn.execute(text(f'CREATE DATABASE "{database_name}"'))
                print(f"Created database: {database_name}")

        temp_engine.dispose()
    except SQLAlchemyError as e:
        print(f"Warning: Could not create database: {e}")

def check_and_add_search_vector_column(engine) -> bool:
    """Check if search_vector column exists and add it if missing"""
    try:
        with engine.connect() as conn:
            # Check if search_vector column exists
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'endpoints' AND column_name = 'search_vector'
            """))
            column_exists = result.fetchone() is not None

            if not column_exists:
                print("Adding search_vector column to endpoints table...")
                # Add the search_vector column
                conn.execute(text("ALTER TABLE endpoints ADD COLUMN search_vector TSVECTOR"))

                # Create the GIN index
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_endpoints_search_vector_gin ON endpoints USING GIN (search_vector)"))

                print("✓ search_vector column and index added successfully")
            else:
                print("✓ search_vector column already exists")

            conn.commit()
            return True
    except Exception as e:
        print(f"Warning: Could not add search_vector column: {e}")
        return False

def create_tables(engine) -> None:
    """Create tables using SQLAlchemy metadata"""
    Base.metadata.create_all(engine)

    # Check if full-text search should be disabled via environment variable
    disable_fulltext = os.getenv('DISABLE_FULLTEXT_SEARCH', 'false').lower() == 'true'
    if disable_fulltext:
        print("Full-text search disabled via DISABLE_FULLTEXT_SEARCH environment variable")
        return

    # Check and add search_vector column if needed
    search_vector_available = check_and_add_search_vector_column(engine)

    if not search_vector_available:
        print("Skipping full-text search setup due to column migration issues")
        return

    with engine.connect() as conn:
        try:
            # Create the trigger function for automatic search vector updates
            conn.execute(text("""
                CREATE OR REPLACE FUNCTION update_search_vector() RETURNS TRIGGER AS $$
                BEGIN
                    NEW.search_vector :=
                        setweight(to_tsvector('english', COALESCE(NEW.summary, '')), 'A') ||
                        setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'B') ||
                        setweight(to_tsvector('english', COALESCE(NEW.tags, '')), 'C') ||
                        setweight(to_tsvector('english', COALESCE(NEW.path, '') || ' ' || COALESCE(NEW.method, '')), 'D');
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """))

            # Create the trigger
            conn.execute(text("""
                DROP TRIGGER IF EXISTS trigger_update_search_vector ON endpoints;
                CREATE TRIGGER trigger_update_search_vector
                    BEFORE INSERT OR UPDATE ON endpoints
                    FOR EACH ROW EXECUTE FUNCTION update_search_vector();
            """))

            # Keep the old keywords index for backward compatibility
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_endpoints_keywords_gin ON endpoints USING GIN (to_tsvector('english', keywords))"))

            # Update existing records to populate search_vector
            print("Updating existing records with search vectors...")
            conn.execute(text("""
                UPDATE endpoints SET
                search_vector = setweight(to_tsvector('english', COALESCE(summary, '')), 'A') ||
                              setweight(to_tsvector('english', COALESCE(description, '')), 'B') ||
                              setweight(to_tsvector('english', COALESCE(tags, '')), 'C') ||
                              setweight(to_tsvector('english', COALESCE(path, '') || ' ' || COALESCE(method, '')), 'D')
                WHERE search_vector IS NULL
            """))

            print("✓ Full-text search triggers and indexes created successfully")
            conn.commit()
        except Exception as gin_error:
            try:
                conn.rollback()
                # Fallback to a simple B-tree index for basic keyword searches
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_endpoints_keywords_btree ON endpoints (keywords)"))
                conn.commit()
                print(f"Warning: Could not create full-text search features: {gin_error}")
            except Exception as btree_error:
                try:
                    conn.rollback()
                    print(f"Warning: Could not create any search indexes: {btree_error}")
                except:
                    pass

def process_json_file(json_path: str, database_url: str = None) -> None:
    global SCHEMA_DATA

    start_time = time.time()

    # Get database URL
    if database_url:
        db_url = database_url
    else:
        db_url = get_database_url()

    # Get batch configuration
    batch_config = get_batch_config()

    # Create database if it doesn't exist
    create_database_if_not_exists(db_url)

    # Create SQLAlchemy engine and session

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    db_config = get_database_config()  # For display purposes
    try:
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        db_config = get_database_config()  # For display purposes
        print(f"Connected to PostgreSQL database: {db_config['database']}")
    except SQLAlchemyError as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return

    create_tables(engine)

    # Load JSON file
    with open(json_path, 'r') as f:
        try:
            API_DATA = json.load(f)
            print(f"JSON parsed successfully")
        except json.JSONDecodeError as e:
            print(f"Error parsing {json_path}: {e}")
            engine.dispose()
            return

    # Verify paths exist
    if 'paths' not in API_DATA:
        print("ERROR: invalid JSON file - Unable to locate Open API endpoints")
        engine.dispose()
        return

    # Gather components
    PATH_DATA   = API_DATA['paths']
    SCHEMA_DATA = API_DATA['components']['schemas']

    total_paths = len(PATH_DATA)
    processed = 0
    batch_size = batch_config['batch_size']

    # Process each path and its methods with session management
    session = Session()

    try:
        for path, path_data in PATH_DATA.items():
                for method, method_data in path_data.items():
                    if method in ['get', 'post', 'put', 'patch', 'delete']:
                        # Extract endpoint data
                        summary = method_data.get('summary', '')
                        description = method_data.get('description', '')
                        tags = ','.join(method_data.get('tags', []))
                        operation_id = method_data.get('operationId', '')

                        # Split camelCase operation_id for better readability - assign desc if not avail
                        if description == '':
                            description = re.sub(r'([a-z])([A-Z])', r'\1 \2', operation_id)

                        # Determine category from tags or path
                        category = method_data.get('tags', [''])[0] if method_data.get('tags') else path.split('/')[1] if len(path.split('/')) > 1 else 'unknown'

                        # Extract keywords for searchability
                        tag_list = method_data.get('tags', [])
                        keywords = extract_keywords(path, method, summary, description, operation_id, tag_list)

                        # Use SQLAlchemy upsert (merge or ON CONFLICT)
                        endpoint_stmt = insert(Endpoint).values(
                            path=path,
                            method=method,
                            description=description,
                            category=category,
                            summary=summary,
                            tags=tags,
                            keywords=keywords
                        )
                        endpoint_stmt = endpoint_stmt.on_conflict_do_update(
                            index_elements=['path', 'method'],
                            set_=dict(
                                description=endpoint_stmt.excluded.description,
                                category=endpoint_stmt.excluded.category,
                                summary=endpoint_stmt.excluded.summary,
                                tags=endpoint_stmt.excluded.tags,
                                keywords=endpoint_stmt.excluded.keywords
                            )
                        )
                        endpoint_stmt = endpoint_stmt.returning(Endpoint.id)

                        result = session.execute(endpoint_stmt)
                        endpoint_id = result.fetchone()[0]

                        if not endpoint_id:
                            print(f"Warning: Could not get endpoint_id for {path} {method}")
                            continue

                        # Process parameters
                        parameters = method_data.get('parameters', [])
                        for param in parameters:
                            name = param.get('name', '')
                            location = param.get('in', '')  # path, query, header, etc.
                            required = param.get('required', False)
                            param_type = param.get('schema', {}).get('type', '') if 'schema' in param else param.get('type', '')
                            param_description = param.get('description', '')

                            parameter_obj = Parameter(
                                endpoint_id=endpoint_id,
                                name=name,
                                location=location,
                                required=required,
                                type=param_type,
                                description=param_description
                            )
                            session.add(parameter_obj)

                        # Process request body if present
                        if 'requestBody' in method_data:
                            request_body_data = method_data.get('requestBody', {})
                            content = request_body_data.get('content', {})
                            content_type = next(iter(content)) if content else ''
                            schema = content.get(content_type, {}).get('schema', {}) if content_type else {}

                            if schema.get('type', '') == 'array':
                                # Build array request body
                                request_body = {
                                    'type': 'array',
                                    'items': []
                                }
                                if '$ref' in schema.get('items', {}):
                                    component_ref_path = schema.get('items', {}).get('$ref', '')
                                    if component_ref_path != '':
                                        # Add component reference schema
                                        request_body['items'].append(resolve_components(component_ref_path))
                            elif schema.get('$ref', '') != '':
                                request_body = resolve_components(schema['$ref'])
                            else:
                                request_body = schema

                            schema_json = json.dumps(request_body) if request_body else '{}'
                            example_json = json.dumps(content.get(content_type, {}).get('example', {})) if content_type else '{}'

                            request_body_obj = RequestBody(
                                endpoint_id=endpoint_id,
                                schema=schema_json,
                                example=example_json
                            )
                            session.add(request_body_obj)

                        # Process responses
                        responses = method_data.get('responses', {})
                        for status_code, response_data in responses.items():
                            description     = response_data.get('description', '')
                            content         = response_data.get('content', {})
                            content_type    = next(iter(content)) if content else ''
                            response_schema = content.get(content_type, {}).get('schema', {}) if content_type else {}
                            # Build array response schema
                            if response_schema.get('type', '') == 'array':
                                processed_schema = {
                                    'type': 'array',
                                    'items': []
                                }
                                if '$ref' in response_schema.get('items', {}):
                                    component_ref_path = response_schema.get('items', {}).get('$ref', '')
                                    if component_ref_path != '':
                                        # Add component reference schema
                                        processed_schema['items'].append(resolve_components(component_ref_path))
                            elif response_schema.get('$ref', '') != '':
                                processed_schema = resolve_components(response_schema['$ref'])
                            else:
                                processed_schema = response_schema

                            schema = json.dumps(processed_schema) if processed_schema else '{}'
                            example = json.dumps(content.get(content_type, {}).get('example', {})) if content_type else '{}'

                            response_body_obj = ResponseBody(
                                endpoint_id=endpoint_id,
                                status_code=status_code,
                                description=description,
                                schema=schema,
                                example=example
                            )
                            session.add(response_body_obj)

                processed += 1
                if processed % batch_size == 0:
                    print(f"Processed {processed}/{total_paths} paths...")
                    try:
                        session.commit()  # Periodic commits for large datasets
                        print(f"Successfully committed batch at {processed} paths")
                    except SQLAlchemyError as e:
                        print(f"Warning: Failed to commit batch at {processed} paths: {e}")
                        try:
                            session.rollback()
                            print("Transaction rolled back successfully")
                        except SQLAlchemyError as rollback_error:
                            print(f"Error during rollback: {rollback_error}")

        # Final commit
        try:
            session.commit()
            print("Final commit successful")
        except SQLAlchemyError as e:
            print(f"Error during final commit: {e}")
            try:
                session.rollback()
                print("Final transaction rolled back")
            except SQLAlchemyError as rollback_error:
                print(f"Error during final rollback: {rollback_error}")

    except Exception as e:
        print(f"Unexpected error during processing: {e}")
        session.rollback()
    finally:
        session.close()
        engine.dispose()

    elapsed_time = time.time() - start_time
    print(f"Processing completed in {elapsed_time:.2f} seconds.")
    print(f"Database updated: {db_config['database']} on {db_config['host']}:{db_config['port']}")

# iterate through json structure - replace $ref variables with their components
def process_json_with_refs(json_string, ref_processor):
    obj = json.loads(json_string)
    def traverse(current, parent, key):
        if not isinstance(current, dict):
            if isinstance(current, list):
                for i, item in enumerate(current):
                    traverse(item, current, i)
            return

        for child_key, child_value in list(current.items()):
            if child_key == '$ref' and parent is not None and key is not None:
                parent[key] = ref_processor(current)
                return
            traverse(child_value, current, child_key)
    traverse(obj, None, None)
    return obj

def get_schema(ref_path):
    global SCHEMA_DATA

    if '$ref' in ref_path:
        ref_path = ref_path['$ref']

    split_reference_path = ref_path.replace("#/", "").split("/")
    name = [part for part in split_reference_path if part not in ["components", "schemas"]]
    schema_name = name[0] if name else ""
    schema = SCHEMA_DATA[schema_name]
    if not schema:
        return {
            "type": "parsing-error",
            "description": f"Unresolved reference: {ref_path}"
        }
    return schema

def resolve_components(ref_path):
    """Resolve component references iteratively until no $ref patterns remain"""
    request_body = get_schema(ref_path)

    # Process references up to 3 times - this should handle most nested cases
    for _ in range(3):
        json_string = json.dumps(request_body)
        new_schema = process_json_with_refs(json_string, get_schema)

        # Simple check: if no $ref strings remain in the JSON, we're done
        if '$ref' not in json.dumps(new_schema):
            return new_schema

        request_body = new_schema

    # Return after 3 iterations regardless
    return request_body

def main():
    if len(sys.argv) < 2:
        print("Usage: python json_to_postgres.py <path_to_manage.json> [database_url]")
        print("\nEnvironment Variables:")
        print("  DATABASE_URL - Full PostgreSQL connection string")
        print("  POSTGRES_HOST - PostgreSQL host (default: localhost)")
        print("  POSTGRES_PORT - PostgreSQL port (default: 5432)")
        print("  POSTGRES_DB - Database name (default: connectwise_api)")
        print("  POSTGRES_USER - Username (default: postgres)")
        print("  POSTGRES_PASSWORD - Password (default: password)")
        print("  BATCH_SIZE - Number of paths to process before committing (default: 100)")
        sys.exit(1)

    json_path = sys.argv[1]
    database_url = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(json_path):
        print(f"Error: JSON file does not exist at path: {json_path}")
        sys.exit(1)

    if database_url:
        process_json_file(json_path, database_url)
    else:
        process_json_file(json_path)

    try:
       print("YAY")
    except KeyboardInterrupt:
        print("\nProcessing interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()