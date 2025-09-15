#!/usr/bin/env python3
"""
JSON to SQLite Converter for ConnectWise API

This script converts the large manage.json file containing ConnectWise API definitions
into a SQLite database for efficient querying and lookup.

Usage:
    python json_to_sqlite.py <path_to_manage.json> <output_sqlite_db>
"""

import json
import sqlite3
import sys
import os
import time
from typing import Dict, List, Any, Optional, Union

# Global variable to store loaded API data
API_DATA    = None
PATH_DATA   = None
SCHEMA_DATA = None

def create_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    # Create table for API endpoints
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            method TEXT NOT NULL,
            request_bodies_id INTEGER,
            response_bodies_id INTEGER,
            description TEXT,
            category TEXT,
            summary TEXT,
            tags TEXT,
            FOREIGN KEY (request_bodies_id) REFERENCES request_bodies(id),
            FOREIGN KEY (response_bodies_id) REFERENCES response_bodies(id),
            UNIQUE(path, method)
        )''')
    # Create table for endpoint parameters
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            location TEXT NOT NULL,  -- path, query, body
            required INTEGER,        -- 0 or 1
            type TEXT,
            description TEXT,
            FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
        )''')
    # Create table for request bodies
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS request_bodies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema TEXT,              -- JSON schema for the body
            example TEXT             -- JSON example if available
        )''')
    # Create table for response bodies
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS response_bodies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status_code TEXT,
            description TEXT,
            schema TEXT,              -- JSON schema for the response
            example TEXT             -- JSON example if available
        )''')
    conn.commit()

def process_json_file(json_path: str, db_path: str) -> None:
    start_time  = time.time()

    # connect to database
    conn = sqlite3.connect(db_path)
    create_tables(conn)
    cursor = conn.cursor()
    
    with open(json_path, 'r') as f:
        try:
            API_DATA = json.load(f)
            print(f"JSON parsed successfully")
        except json.JSONDecodeError as e:
            print(f"Error parsing {json_path}: {e}")
            conn.close()
            return
    
    # verify paths exist
    if 'paths' not in API_DATA:
        print("ERROR : invalid JSON file - Unable to locate Open API endpoints")
        conn.close()
        return
    
    
    total_paths = len(paths)
    processed = 0

    # gather components 
    PATH_DATA   = API_DATA['paths']
    SCHEMA_DATA = API_DATA['components']['schemas']
    total_paths = len(PATH_DATA)
    processed = 0

    # Process each path and its methods
    for path, path_data in PATH_DATA.items():
        for method, method_data in path_data.items():
            if method in ['get', 'post', 'put', 'patch', 'delete']:
                # Extract endpoint data
                summary = method_data.get('summary', '')
                tags = ','.join(method_data.get('tags', []))
                
                # Determine category from tags or path
                category = method_data.get('tags', [''])[0] if method_data.get('tags') else path.split('/')[1] if len(path.split('/')) > 1 else 'unknown'
                
                # Insert endpoint data
                cursor.execute('''
                    INSERT OR REPLACE INTO endpoints (path, method, description, category, summary, tags)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (path, method, description, category, summary, tags))
                
                # get new endpoint ID
                endpoint_id = cursor.lastrowid
                
                # Process parameters
                parameters = method_data.get('parameters', [])
                for param in parameters:
                    name        = param.get('name', '')
                    location    = param.get('in', '')  # path, query, header, etc.
                    required    = 1 if param.get('required', False) else 0
                    param_type  = param.get('schema', {}).get('type', '') if 'schema' in param else param.get('type', '')
                    param_description = param.get('description', '')
                    
                    cursor.execute('''
                        INSERT INTO parameters (endpoint_id, name, location, required, type, description)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (endpoint_id, name, location, required, param_type, param_description))
                
                # Process request body if present
                if 'requestBody' in method_data:
                    request_body    = method_data.get('requestBody', {})
                    content         = request_body.get('content', {})
                    content_type    = next(iter(content)) if content else ''
                    schema          = content.get(content_type, {}).get('schema', {}) if content_type else '{}'
                    
                    request_body        = {}

                    if schema.get('type', '') == 'array':
                        # build array request body
                        request_body['type']    = 'array'
                        request_body['items']   = []
                        if '$ref' in schema.get('items', ''):
                            component_ref_path  = schema.get('items', '').get('$ref', '')
                            if component_ref_path != '':
                                # add component reference schema
                                request_body['items'].append(resolve_components(component_ref_path))

                    elif schema.get('$ref', '') != '':
                        request_body = resolve_components(schema['$ref'])

                    # example = json.dumps(content.get(content_type, {}).get('example', {})) if content_type else '{}'
                    
                    cursor.execute('''
                        INSERT INTO request_bodies (endpoint_id, schema, example)
                        VALUES (?, ?, ?)
                    ''', (endpoint_id, schema, example))
                
                # Process responses
                responses = method_data.get('responses', {})
                for status_code, response_data in responses.items():
                    description = response_data.get('description', '')
                    content = response_data.get('content', {})
                    content_type = next(iter(content)) if content else ''
                    
                    schema = json.dumps(content.get(content_type, {}).get('schema', {})) if content_type else '{}'
                    example = json.dumps(content.get(content_type, {}).get('example', {})) if content_type else '{}'
                    
                    cursor.execute('''
                    INSERT INTO response_bodies (endpoint_id, status_code, description, schema, example)
                    VALUES (?, ?, ?, ?, ?)
                    ''', (endpoint_id, status_code, description, schema, example))
    
    # Commit periodically to avoid large transactions
    # Final commit
    conn.commit()
    conn.close()
    
    elapsed_time = time.time() - start_time
    print(f"Processing completed in {elapsed_time:.2f} seconds.")
    print(f"Database created at: {db_path}")


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
    split_reference_path    = ref_path.replace("#/", "").split("/")
    name   = [part for part in split_reference_path if part not in ["components", "schemas"]]
    schema = SCHEMA_DATA.get(name, {})

    if not schema:
        return {
        "type": "parsing-error",
        "description": f"Unresolved reference: {ref_path}"
    }  
    schema = process_json_with_refs(json.dumps(schema, get_schema )) 
    return schema

def resolve_components(ref_path):
    request_body    = get_schema(ref_path)
    return request_body


def main():
    if len(sys.argv) < 3:
        print("Usage: python json_to_sqlite.py <path_to_manage.json> <output_sqlite_db>")
        sys.exit(1)
        
    json_path = sys.argv[1]
    db_path = sys.argv[2]
    
    if not os.path.exists(json_path):
        print(f"Error: JSON file does not exist at path: {json_path}")
        sys.exit(1)
    
    process_json_file(json_path, db_path)

if __name__ == "__main__":
    main()
