#!/usr/bin/env python3
"""
API Database Utility Functions

This module provides utility functions to query the PostgreSQL database containing
the ConnectWise API endpoint information.
"""

import json
import re
from typing import Dict, List, Any, Optional, Union, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager

class APIDatabase:
    """Class to handle queries to the ConnectWise API PostgreSQL database."""

    def __init__(self, database_url: str):
        """Initialize the database connection."""
        self.database_url = database_url
        self.engine = None
        self.SessionLocal = None
        self.connect()

    def connect(self) -> None:
        """Establish a connection to the PostgreSQL database."""
        try:
            self.engine = create_engine(self.database_url)
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        except Exception as e:
            raise Exception(f"Failed to connect to PostgreSQL database: {e}")

    @contextmanager
    def get_session(self):
        """Get a database session with automatic cleanup."""
        if not self.SessionLocal:
            raise RuntimeError("Database connection not initialized")

        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        """Close the database connection."""
        if self.engine:
            self.engine.dispose()
            self.engine = None
            self.SessionLocal = None

    def has_fulltext_search(self) -> bool:
        """Check if full-text search features are available in the database."""
        try:
            with self.get_session() as session:
                # Check if search_vector column exists
                result = session.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'endpoints' AND column_name = 'search_vector'
                """))
                column_exists = result.fetchone() is not None

                if not column_exists:
                    return False

                # Check if there's data in search_vector (not all NULL)
                result = session.execute(text("""
                    SELECT COUNT(*) as total, COUNT(search_vector) as with_vector
                    FROM endpoints
                    LIMIT 1
                """))
                row = result.fetchone()
                return row is not None and row.total > 0 and row.with_vector > 0

        except Exception:
            return False

    def search_endpoints(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for API endpoints matching the query using full-text search.

        Args:
            query: Search string (can match path, description, tags)

        Returns:
            List of matching endpoints ordered by relevance
        """
        # Check if full-text search features are available
        if self.has_fulltext_search():
            try:
                with self.get_session() as session:
                    result = session.execute(text('''
                    SELECT *, ts_rank(search_vector, websearch_to_tsquery('english', :query)) as rank
                    FROM endpoints
                    WHERE search_vector @@ websearch_to_tsquery('english', :query)
                    ORDER BY rank DESC, category, path
                    LIMIT 50
                    '''), {"query": query})

                    results = [dict(row._mapping) for row in result.fetchall()]
                    if results:
                        return results
            except Exception:
                pass

        # Fallback to pattern matching in separate session
        try:
            with self.get_session() as session:
                search_pattern = f"%{query}%"
                result = session.execute(text('''
                SELECT * FROM endpoints
                WHERE path ILIKE :pattern
                OR description ILIKE :pattern
                OR tags ILIKE :pattern
                OR summary ILIKE :pattern
                ORDER BY category, path
                '''), {"pattern": search_pattern})

                return [dict(row._mapping) for row in result.fetchall()]
        except SQLAlchemyError as e:
            raise Exception(f"Database error searching endpoints: {e}")

    def get_endpoint_details(self, endpoint_id: int) -> Dict[str, Any]:
        """
        Get complete details for a specific endpoint.

        Args:
            endpoint_id: The endpoint ID

        Returns:
            Dictionary containing complete endpoint details
        """
        try:
            with self.get_session() as session:
                # Get basic endpoint info
                result = session.execute(text('SELECT * FROM endpoints WHERE id = :id'), {"id": endpoint_id})
                endpoint_row = result.fetchone()

                if not endpoint_row:
                    return {}

                endpoint = dict(endpoint_row._mapping)

                # Get parameters
                params_result = session.execute(text('SELECT * FROM parameters WHERE endpoint_id = :id'), {"id": endpoint_id})
                endpoint['parameters'] = [dict(row._mapping) for row in params_result.fetchall()]

                # Get request body
                request_result = session.execute(text('SELECT * FROM request_bodies WHERE endpoint_id = :id'), {"id": endpoint_id})
                request_body_row = request_result.fetchone()

                if request_body_row:
                    endpoint['request_body'] = dict(request_body_row._mapping)
                    # Parse the JSON schema
                    if endpoint['request_body'].get('schema'):
                        try:
                            endpoint['request_body']['schema'] = json.loads(endpoint['request_body']['schema'])
                        except json.JSONDecodeError:
                            pass

                    # Parse the JSON example
                    if endpoint['request_body'].get('example'):
                        try:
                            endpoint['request_body']['example'] = json.loads(endpoint['request_body']['example'])
                        except json.JSONDecodeError:
                            pass

                # Get response bodies
                responses_result = session.execute(text('SELECT * FROM response_bodies WHERE endpoint_id = :id'), {"id": endpoint_id})
                responses = []
                for row in responses_result.fetchall():
                    response = dict(row._mapping)
                    # Parse the JSON schema
                    if response.get('schema'):
                        try:
                            response['schema'] = json.loads(response['schema'])
                        except json.JSONDecodeError:
                            pass

                    # Parse the JSON example
                    if response.get('example'):
                        try:
                            response['example'] = json.loads(response['example'])
                        except json.JSONDecodeError:
                            pass
                    responses.append(response)

                endpoint['response_bodies'] = responses

                return endpoint

        except SQLAlchemyError as e:
            raise Exception(f"Database error getting endpoint details: {e}")

    def find_endpoint_by_path_method(self, path: str, method: str) -> Optional[Dict[str, Any]]:
        """
        Find an endpoint by its path and HTTP method with free text search capabilities.

        Args:
            path: API endpoint path (supports exact match, wildcards, and free text search)
            method: HTTP method (GET, POST, etc.)

        Returns:
            Endpoint details or None if not found
        """
        try:
            # Normalize the input path for consistent searching
            normalized_path = self._normalize_path(path)

            with self.get_session() as session:
                # Exact match (case-insensitive)
                result = session.execute(text('''
                SELECT id FROM endpoints
                WHERE path ilike :path AND method = :method
                '''), {"path": normalized_path, "method": method.lower()})

                endpoint_row = result.fetchone()
                if endpoint_row:
                    return self.get_endpoint_details(endpoint_row.id)

                # Parameter-agnostic matching
                # Convert both our parameterized path and database paths to use a generic pattern
                # This handles cases where parameter names differ (e.g., {id} vs {parentId})
                parameterized_path = self._convert_to_parameterized_path(normalized_path)
                generic_pattern = self._convert_to_generic_pattern(parameterized_path)
                if generic_pattern != parameterized_path:
                    result = session.execute(text('''
                    SELECT id, path FROM endpoints
                    WHERE method = :method
                    '''), {"method": method.lower()})

                    for row in result.fetchall():
                        db_generic_pattern = self._convert_to_generic_pattern(row.path)
                        if db_generic_pattern == generic_pattern:
                            return self.get_endpoint_details(row.id)

                endpoint_row = result.fetchone()
                if endpoint_row:
                    return self.get_endpoint_details(endpoint_row.id)

                return None

        except SQLAlchemyError as e:
            raise Exception(f"Database error finding endpoint: {e}")

    def get_categories(self) -> List[str]:
        """
        Get all unique endpoint categories.

        Returns:
            List of category names
        """
        try:
            with self.get_session() as session:
                result = session.execute(text('SELECT DISTINCT category FROM endpoints ORDER BY category'))
                return [row.category for row in result.fetchall()]

        except SQLAlchemyError as e:
            raise Exception(f"Database error getting categories: {e}")

    def get_endpoints_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        Get all endpoints for a specific category.

        Args:
            category: Category name

        Returns:
            List of endpoints in the category
        """
        try:
            with self.get_session() as session:
                result = session.execute(text('''
                SELECT * FROM endpoints
                WHERE category = :category
                ORDER BY path
                '''), {"category": category})

                return [dict(row._mapping) for row in result.fetchall()]

        except SQLAlchemyError as e:
            raise Exception(f"Database error getting endpoints by category: {e}")

    def get_parameter_details(self, endpoint_id: int, param_name: str) -> Optional[Dict[str, Any]]:
        """
        Get details for a specific parameter of an endpoint.

        Args:
            endpoint_id: The endpoint ID
            param_name: Name of the parameter

        Returns:
            Parameter details or None if not found
        """
        try:
            with self.get_session() as session:
                result = session.execute(text('''
                SELECT * FROM parameters
                WHERE endpoint_id = :endpoint_id AND name = :param_name
                '''), {"endpoint_id": endpoint_id, "param_name": param_name})

                param_row = result.fetchone()
                return dict(param_row._mapping) if param_row else None

        except SQLAlchemyError as e:
            raise Exception(f"Database error getting parameter details: {e}")

    def search_by_natural_language(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Search for endpoints using natural language queries with PostgreSQL full-text search.

        Args:
            query: Natural language query
            limit: Maximum number of results

        Returns:
            List of matching endpoints ordered by relevance
        """
        print(limit)
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        # Check if full-text search features are available
        if self.has_fulltext_search():
            # Try advanced full-text search first
            try:
                with self.get_session() as session:
                    result = session.execute(text('''
                    SELECT id, path, method, description, category, tags, summary,
                           ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) as rank
                    FROM endpoints
                    WHERE search_vector @@ websearch_to_tsquery('english', :query)
                    ORDER BY rank DESC
                    LIMIT :limit_val
                    '''), {"query": query_lower, "limit_val": limit})

                    results = [dict(row._mapping) for row in result.fetchall()]
                    if results:
                        print("IS websearch_to_tsquery")
                        return results
            except Exception:
                pass

            # Fall back to plainto_tsquery in separate session
            try:
                with self.get_session() as session:
                    result = session.execute(text('''
                    SELECT id, path, method, description, category, tags, summary,
                           ts_rank(search_vector, plainto_tsquery('english', :query)) as rank
                    FROM endpoints
                    WHERE search_vector @@ plainto_tsquery('english', :query)
                    ORDER BY rank DESC
                    LIMIT :limit_val
                    '''), {"query": query_lower, "limit_val": limit})

                    results = [dict(row._mapping) for row in result.fetchall()]
                    if results:
                        print("IS plainto_tsquery")
                        return results
            except Exception:
                pass

        # Fallback to keyword-based search in separate session
        try:
            print("IS keyword")
            keywords = query_lower.split()
            stopwords = {
                'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'with', 'by',
                'about', 'like', 'from', 'of', 'as', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should', 'could', 'can',
                'i', 'you', 'he', 'she', 'it', 'we', 'they', 'this', 'that', 'these', 'those',
                'get', 'find', 'show', 'list', 'give', 'me', 'all', 'some', 'any', 'how', 'what', 'when', 'where', 'why'
            }

            filtered_keywords = [word for word in keywords if word not in stopwords and len(word) >= 3]
            if not filtered_keywords:
                return []

            with self.get_session() as session:
                search_conditions = []
                search_params = {}

                for i, keyword in enumerate(filtered_keywords):
                    search_pattern = f"%{keyword}%"
                    search_conditions.extend([
                        f"path ILIKE :pattern_{i}",
                        f"description ILIKE :pattern_{i}",
                        f"tags ILIKE :pattern_{i}",
                        f"summary ILIKE :pattern_{i}"
                    ])
                    search_params[f"pattern_{i}"] = search_pattern

                where_clause = " OR ".join(search_conditions)
                result = session.execute(text(f'''
                SELECT id, path, method, description, category, tags, summary
                FROM endpoints
                WHERE {where_clause}
                ORDER BY
                    CASE
                        WHEN summary ILIKE :first_pattern THEN 1
                        WHEN description ILIKE :first_pattern THEN 2
                        WHEN path ILIKE :first_pattern THEN 3
                        WHEN tags ILIKE :first_pattern THEN 4
                        ELSE 5
                    END
                LIMIT :limit_val
                '''), {**search_params, "first_pattern": f"%{filtered_keywords[0]}%", "limit_val": limit})

                return [dict(row._mapping) for row in result.fetchall()]

        except SQLAlchemyError as e:
            raise Exception(f"Database error in natural language search: {e}")

    def advanced_search(self, query: str, limit: int = 10, include_highlights: bool = False) -> List[Dict[str, Any]]:
        """
        Advanced search with full-text search, phrase matching, and optional highlighting.

        Args:
            query: Search query (supports phrases in quotes, boolean operators)
            limit: Maximum number of results
            include_highlights: Whether to include highlighted snippets

        Returns:
            List of matching endpoints with optional highlights
        """
        if not query or not query.strip():
            return []

        try:
            with self.get_session() as session:
                # Try full-text search with highlighting
                try:
                    if include_highlights:
                        highlight_query = '''
                        SELECT id, path, method, description, category, tags, summary,
                               ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) as rank,
                               ts_headline('english', COALESCE(summary, ''), websearch_to_tsquery('english', :query)) as summary_highlight,
                               ts_headline('english', COALESCE(description, ''), websearch_to_tsquery('english', :query)) as description_highlight
                        FROM endpoints
                        WHERE search_vector @@ websearch_to_tsquery('english', :query)
                        ORDER BY rank DESC
                        LIMIT :limit_val
                        '''
                    else:
                        highlight_query = '''
                        SELECT id, path, method, description, category, tags, summary,
                               ts_rank_cd(search_vector, websearch_to_tsquery('english', :query)) as rank
                        FROM endpoints
                        WHERE search_vector @@ websearch_to_tsquery('english', :query)
                        ORDER BY rank DESC
                        LIMIT :limit_val
                        '''

                    result = session.execute(text(highlight_query), {"query": query.strip(), "limit_val": limit})
                    results = [dict(row._mapping) for row in result.fetchall()]

                    if results:
                        return results

                except Exception:
                    # Fallback to plainto_tsquery
                    try:
                        result = session.execute(text('''
                        SELECT id, path, method, description, category, tags, summary,
                               ts_rank(search_vector, plainto_tsquery('english', :query)) as rank
                        FROM endpoints
                        WHERE search_vector @@ plainto_tsquery('english', :query)
                        ORDER BY rank DESC
                        LIMIT :limit_val
                        '''), {"query": query.strip(), "limit_val": limit})

                        results = [dict(row._mapping) for row in result.fetchall()]
                        if results:
                            return results
                    except Exception:
                        pass

                # Final fallback to ILIKE search
                search_pattern = f"%{query.strip()}%"
                result = session.execute(text('''
                SELECT id, path, method, description, category, tags, summary
                FROM endpoints
                WHERE summary ILIKE :pattern
                OR description ILIKE :pattern
                OR path ILIKE :pattern
                OR tags ILIKE :pattern
                ORDER BY
                    CASE
                        WHEN summary ILIKE :pattern THEN 1
                        WHEN description ILIKE :pattern THEN 2
                        WHEN path ILIKE :pattern THEN 3
                        ELSE 4
                    END
                LIMIT :limit_val
                '''), {"pattern": search_pattern, "limit_val": limit})

                return [dict(row._mapping) for row in result.fetchall()]

        except SQLAlchemyError as e:
            raise Exception(f"Database error in advanced search: {e}")

    def format_endpoint_for_display(self, endpoint: Dict[str, Any]) -> str:
        """
        Format endpoint details for display to the user.

        Args:
            endpoint: Endpoint dictionary from get_endpoint_details

        Returns:
            Formatted string for display
        """
        if not endpoint:
            return "Endpoint not found."

        output = []

        # Basic info
        method = endpoint.get('method', '').upper()
        path = endpoint.get('path', '')
        summary = endpoint.get('summary', '')
        description = endpoint.get('description', '')
        category = endpoint.get('category', '')

        output.append(f"=== {method} {path} ===")
        if category:
            output.append(f"Category: {category}")
        if summary:
            output.append(f"Summary: {summary}")
        if description:
            output.append(f"Description: {description}")

        # Parameters
        parameters = endpoint.get('parameters', [])
        if parameters:
            output.append("\nParameters:")
            for param in parameters:
                param_info = f"  - {param.get('name', 'Unknown')}"
                if param.get('type'):
                    param_info += f" ({param['type']})"
                if param.get('required'):
                    param_info += " [Required]"
                if param.get('description'):
                    param_info += f": {param['description']}"
                output.append(param_info)

        # Request body
        request_body = endpoint.get('request_body')
        if request_body:
            output.append(f"\nRequest Body:")
            if request_body.get('description'):
                output.append(f"  Description: {request_body['description']}")
            if request_body.get('content_type'):
                output.append(f"  Content Type: {request_body['content_type']}")
            if request_body.get('example'):
                output.append(f"  Example: {json.dumps(request_body['example'], indent=2)}")

        # Response bodies
        response_bodies = endpoint.get('response_bodies', [])
        if response_bodies:
            output.append("\nResponse Bodies:")
            for resp in response_bodies:
                status_code = resp.get('status_code', 'Unknown')
                output.append(f"  {status_code}:")
                if resp.get('description'):
                    output.append(f"    Description: {resp['description']}")
                if resp.get('content_type'):
                    output.append(f"    Content Type: {resp['content_type']}")
                if resp.get('example'):
                    output.append(f"    Example: {json.dumps(resp['example'], indent=2)}")

        return "\n".join(output)

    def _convert_to_parameterized_path(self, path: str) -> str:
        """
        Convert a path with actual values to a parameterized path pattern.

        Examples:
            /service/tickets/123 -> /service/tickets/{id}
            /company/companies/456/contacts/789 -> /company/companies/{id}/contacts/{id}
            /finance/invoices/INV-2023-001 -> /finance/invoices/{id}
            /system/callbacks/12345678-1234-1234-1234-123456789012 -> /system/callbacks/{id}

        Args:
            path: Original path with actual values

        Returns:
            Parameterized path pattern
        """
        if not path or path == '/':
            return path
        # Normalize path (remove trailing slash, ensure leading slash)
        normalized_path = path.rstrip('/')
        if not normalized_path.startswith('/'):
            normalized_path = '/' + normalized_path
        # Split into segments
        segments = normalized_path.split('/')
        # Convert segments that look like parameters
        converted_segments = []
        for segment in segments:
            if not segment:  # Empty segment (leading slash creates empty first segment)
                converted_segments.append(segment)
                continue
            # Check if segment looks like a parameter value
            converted_segment = self._convert_segment_to_parameter(segment)
            converted_segments.append(converted_segment)
        return '/'.join(converted_segments)

    def _is_known_path_segment(self, segment: str) -> bool:
        """
        Check if a segment appears as a literal (non-parameterized) component in known endpoint paths.

        Args:
            segment: Path segment to check

        Returns:
            True if the segment appears in actual endpoint paths, False otherwise
        """
        if not segment:
            return False

        # Use caching to avoid repeated database queries
        if not hasattr(self, '_known_segments_cache'):
            self._known_segments_cache = {}

        if segment in self._known_segments_cache:
            return self._known_segments_cache[segment]

        try:
            with self.get_session() as session:
                # Check if this segment appears as a literal component in any endpoint path
                # We look for paths that contain the segment surrounded by slashes or at the end
                result = session.execute(text('''
                SELECT COUNT(*) as count FROM endpoints
                WHERE path LIKE :segment_pattern
                LIMIT 1
                '''), {
                    "segment_pattern": f"%/{segment}%"
                })

                row = result.fetchone()
                exists = row is not None and row.count > 0

                # Cache the result
                self._known_segments_cache[segment] = exists
                return exists

        except Exception:
            # If database query fails, assume it's not a known segment
            return False

    def _convert_segment_to_parameter(self, segment: str) -> str:
        """
        Convert a single path segment to a parameter if it looks like a value.

        Args:
            segment: Path segment to analyze

        Returns:
            Either the original segment or a parameter pattern
        """
        if not segment:
            return segment

        # First check if this segment appears in known endpoint paths
        # If it does, keep it as-is (don't parameterize known path components)
        if self._is_known_path_segment(segment):
            return segment

        # Numeric IDs (pure numbers)
        if re.match(r'^\d+$', segment):
            return '{id}'
        # UUID patterns
        if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', segment, re.IGNORECASE):
            return '{id}'
        # Alphanumeric IDs with common prefixes (INV-123, TKT-456, etc.)
        if re.match(r'^[A-Z]{2,4}-[\w\-]+$', segment, re.IGNORECASE):
            return '{id}'
        # Long alphanumeric strings that look like IDs (8+ chars, mixed letters/numbers)
        if len(segment) >= 8 and re.match(r'^[a-zA-Z0-9]+$', segment) and re.search(r'\d', segment) and re.search(r'[a-zA-Z]', segment):
            return '{id}'
        # Base64-like strings (common in API tokens/IDs)
        if len(segment) >= 10 and re.match(r'^[A-Za-z0-9+/=_-]+$', segment):
            return '{id}'
        # Keep the original segment if it doesn't look like a parameter
        return segment

    def _convert_to_generic_pattern(self, path: str) -> str:
        """
        Convert a path with any parameter names to a generic pattern.

        Examples:
            /service/tickets/{parentId}/activities -> /service/tickets/{param}/activities
            /service/tickets/{id}/activities -> /service/tickets/{param}/activities
            /company/companies/{companyId}/contacts/{id} -> /company/companies/{param}/contacts/{param}

        Args:
            path: Path with specific parameter names

        Returns:
            Path with generic parameter placeholders
        """
        if not path:
            return path

        # Replace all parameter patterns like {anything} with {param}
        return re.sub(r'\{[^}]+\}', '{param}', path)

    def _normalize_path(self, path: str) -> str:
        """
        Normalize a path for consistent searching.

        Args:
            path: Path to normalize

        Returns:
            Normalized path
        """
        if not path:
            return '/'

        # Remove extra slashes and normalize
        normalized = re.sub(r'/+', '/', path.strip())
        # Ensure leading slash
        if not normalized.startswith('/'):
            normalized = '/' + normalized
        # Remove trailing slash unless it's the root
        if len(normalized) > 1 and normalized.endswith('/'):
            normalized = normalized.rstrip('/')

        return normalized