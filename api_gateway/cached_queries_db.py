#!/usr/bin/env python3
"""
Cached Queries API Database Module

This module provides functionality to store and retrieve successful API calls
from a 'cached queries' database for quicker access to commonly used queries.
Uses PostgreSQL with SQLAlchemy ORM for performance and scalability.
"""
import os
import logging
import time
from typing import Dict, List, Any, Optional, Generator
from sqlalchemy import create_engine, func, or_, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, OperationalError
from contextlib import contextmanager
from urllib.parse import urlparse
from api_gateway.schema import Base, SavedQuery

# Set up logging
logger = logging.getLogger("api_gateway.cached_queries")

CACHED_QUERIES_DATABASE_URL = None
SYSTEM_DATABASE_URL = None

class CachedQueriesDB:
    """Class to handle the cached queries API database operations using PostgreSQL and SQLAlchemy ORM."""
    def __init__(self, database_url: str, **engine_kwargs):
        global CACHED_QUERIES_DATABASE_URL,SYSTEM_DATABASE_URL
        """
        Initialize the PostgreSQL database connection and create tables if needed.

        Args:
            database_url: PostgreSQL database URL (e.g., postgresql://user:pass@host:port/dbname)
            **engine_kwargs: Additional arguments for SQLAlchemy engine
        """
        self.engine = None
        self.SessionLocal = None

        # Default engine configuration for PostgreSQL
        default_engine_kwargs = {
            'pool_size': 10,
            'max_overflow': 20,
            'pool_pre_ping': True,
            'pool_recycle': 300,
            'echo': False  # Set to True for SQL debugging
        }
        default_engine_kwargs.update(engine_kwargs)

        setupConfig()
        print("TEST")
        print(CACHED_QUERIES_DATABASE_URL)  
        print(SYSTEM_DATABASE_URL)  
        
        # Connect and initialize
        self.initialize_db()
        self.connect(**default_engine_kwargs)
        self.create_tables()
       

    def connect(self, **engine_kwargs) -> None:
        """Establish a connection to the PostgreSQL database."""
        try:
            self.engine = create_engine(CACHED_QUERIES_DATABASE_URL, **engine_kwargs)
            self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

            # Test the connection
            with self.get_session() as session:
                session.execute(func.now())

            logger.info("Connected to cached queries PostgreSQL DB")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def close(self) -> None:
        """Close the database connection and dispose of the engine."""
        if self.engine:
            self.engine.dispose()
            self.engine = None
            self.SessionLocal = None
            logger.info("Cached queries PostgreSQL DB connection closed")

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get a database session with automatic cleanup."""
        if not self.SessionLocal:
            raise RuntimeError("Database connection not initialized")

        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()

    def initialize_db(self) -> None:
        """Check if database exists, if not create new database called cached_queries."""
        try:
            # Parse the database URL to get connection info
            parsed_url = urlparse(CACHED_QUERIES_DATABASE_URL)

            # Extract database name from URL
            db_name = parsed_url.path.lstrip('/')
            
            # Create engine to connect to system database
            system_engine = create_engine(SYSTEM_DATABASE_URL, isolation_level='AUTOCOMMIT')

            try:
                with system_engine.connect() as conn:
                    # Check if database exists
                    result = conn.execute(
                        text("SELECT datname FROM pg_database WHERE datname = :db_name ;"),
                        {"db_name": db_name}
                    )
                    if not result.fetchone():
                        # Database doesn't exist, create cachedQueries database
                        conn.execute(text("CREATE DATABASE cached_queries"))
                        logger.info("Created new database: cached_queries")
                    else:
                        logger.info(f"Database {db_name} already exists")
            finally:
                system_engine.dispose()

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def create_tables(self) -> None:
        """Create the necessary tables if they don't exist."""
        try:
            # Create all tables defined in the Base metadata
            Base.metadata.create_all(bind=self.engine, tables=[SavedQuery.__table__])
            logger.info("Cached queries PostgreSQL DB tables initialized")
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {e}")
            raise

    def save_query(
        self, description: str, 
        path: str, method: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None) -> int:
        """
        Save a successful API query to the database.
        Args:
            description: User-friendly description of the query
            path: API endpoint path
            method: HTTP method
            params: Query parameters (will be stored as JSON)
            data: Request body data (will be stored as JSON)
        Returns:
            ID of the saved query
        """
        try:
            with self.get_session() as session:
                # Check if this query already exists
                existing_query = session.query(SavedQuery).filter(
                    SavedQuery.path == path,
                    SavedQuery.method == method
                ).first()

                # Update existing query
                current_timestamp = int(time.time())
                if existing_query:
                    existing_query.usage_count = SavedQuery.usage_count + 1
                    existing_query.timestamp = current_timestamp
                    existing_query.params = params
                    existing_query.data = data
                    existing_query.description = description  # Update description too
                    session.flush()  # Flush to get updated values
                    query_id = existing_query.id
                    logger.info(f"Updated existing query: {path} {method}")
                    return query_id

                # Create new query
                new_query = SavedQuery(
                    description=description,
                    path=path,
                    method=method,
                    params=params,
                    data=data,
                    timestamp=current_timestamp,
                    usage_count=1
                )

                session.add(new_query)
                session.flush()  # Flush to get the ID
                query_id = new_query.id
                logger.info(f"Saved new query: {path} {method} with ID {query_id}")
                return query_id

        except IntegrityError as e:
            logger.error(f"Integrity error saving query: {e}")
            raise
        except SQLAlchemyError as e:
            logger.error(f"Database error saving query: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error saving query: {e}")
            raise

    def find_query(self, path: str, method: str) -> Optional[Dict[str, Any]]:
        """
        Find a query by path and method.

        Args:
            path: API endpoint path
            method: HTTP method

        Returns:
            Query details as dictionary or None if not found
        """
        try:
            with self.get_session() as session:
                query = session.query(SavedQuery).filter(
                    SavedQuery.path == path,
                    SavedQuery.method == method
                ).first()

                if not query:
                    return None

                # Convert to dictionary
                result = {
                    'id': query.id,
                    'description': query.description,
                    'path': query.path,
                    'method': query.method,
                    'params': query.params,  # Already JSON, no parsing needed
                    'data': query.data,      # Already JSON, no parsing needed
                    'timestamp': query.timestamp,
                    'usage_count': query.usage_count
                }

                return result

        except SQLAlchemyError as e:
            logger.error(f"Database error finding query: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error finding query: {e}")
            raise

    def search_queries(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Search for saved queries matching the search term.

        Args:
            search_term: Term to search for in descriptions and paths

        Returns:
            List of matching queries as dictionaries
        """
        try:
            with self.get_session() as session:
                # Use ILIKE for case-insensitive search (PostgreSQL specific)
                search_pattern = f"%{search_term}%"
                queries = session.query(SavedQuery).filter(
                    or_(
                        SavedQuery.description.ilike(search_pattern),
                        SavedQuery.path.ilike(search_pattern)
                    )
                ).order_by(
                    SavedQuery.usage_count.desc(),
                    SavedQuery.timestamp.desc()
                ).all()

                results = []
                for query in queries:
                    result = {
                        'id': query.id,
                        'description': query.description,
                        'path': query.path,
                        'method': query.method,
                        'params': query.params,
                        'data': query.data,
                        'timestamp': query.timestamp,
                        'usage_count': query.usage_count
                    }
                    results.append(result)
                return results
        except SQLAlchemyError as e:
            logger.error(f"Database error searching queries: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error searching queries: {e}")
            raise

    def get_all_queries(self) -> List[Dict[str, Any]]:
        """
        Get all saved queries.

        Returns:
            List of all saved queries as dictionaries
        """
        try:
            with self.get_session() as session:
                queries = session.query(SavedQuery).order_by(
                    SavedQuery.usage_count.desc(),
                    SavedQuery.timestamp.desc()
                ).all()

                results = []
                for query in queries:
                    result = {
                        'id': query.id,
                        'description': query.description,
                        'path': query.path,
                        'method': query.method,
                        'params': query.params,
                        'data': query.data,
                        'timestamp': query.timestamp,
                        'usage_count': query.usage_count
                    }
                    results.append(result)

                return results

        except SQLAlchemyError as e:
            logger.error(f"Database error getting all queries: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting all queries: {e}")
            raise

    def increment_usage(self, query_id: int) -> None:
        """
        Increment the usage count for a query.

        Args:
            query_id: ID of the query
        """
        try:
            with self.get_session() as session:
                # Atomic update
                result = session.query(SavedQuery).filter(
                    SavedQuery.id == query_id
                ).update({
                    SavedQuery.usage_count: SavedQuery.usage_count + 1,
                    SavedQuery.timestamp: int(time.time())
                })
                if result == 0:
                    logger.warning(f"No query found with ID {query_id} to increment usage")
                else:
                    logger.debug(f"Incremented usage count for query ID {query_id}")

        except SQLAlchemyError as e:
            logger.error(f"Database error incrementing usage: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error incrementing usage: {e}")
            raise

    def delete_query(self, query_id: int) -> bool:
        """
        Delete a saved query.
        Args:
            query_id: ID of the query to delete
        Returns:
            True if successful, False if query not found
        """
        try:
            with self.get_session() as session:
                result = session.query(SavedQuery).filter(
                    SavedQuery.id == query_id
                ).delete()
                if result:
                    logger.info(f"Deleted query with ID {query_id}")
                else:
                    logger.warning(f"No query found with ID {query_id} to delete")

        except SQLAlchemyError as e:
            logger.error(f"Database error deleting query: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error deleting query: {e}")
            raise

    def clear_all(self) -> int:
        """
        Clear all saved queries.

        Returns:
            Number of queries deleted
        """
        try:
            with self.get_session() as session:
                count = session.query(SavedQuery).delete()
                logger.info(f"Cleared {count} queries from cached queries")
                return count

        except SQLAlchemyError as e:
            logger.error(f"Database error clearing all queries: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error clearing all queries: {e}")
            raise


def setupConfig():
    global CACHED_QUERIES_DATABASE_URL, SYSTEM_DATABASE_URL

    db_host = os.environ.get('CACHED_QUERIES_DB_HOST', 'localhost')
    db_port = os.environ.get('CACHED_QUERIES_DB_PORT', '5432')
    db_name = os.environ.get('CACHED_QUERIES_DB_NAME', 'cached_queries')
    db_user = os.environ.get('CACHED_QUERIES_DB_USER', 'postgres')
    db_password = os.environ.get('password', '')

    if all([db_host, db_port, db_name, db_user]):
        CACHED_QUERIES_DATABASE_URL = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        SYSTEM_DATABASE_URL         = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres"
    else:
        logger.error(f"Missing environment variables")



# Example usage
if __name__ == "__main__":
    import os
    try:
        db = CachedQueriesDB()
        # Save a test query
        query_id = db.save_query(
            "Get all open tickets",
            "/service/tickets",
            "GET",
            {"conditions": "status/name='Open'"}
        )

        # print(f"Saved query with ID: {query_id}")

        # Retrieve all queries

        # db.delete_query(3)
        queries = db.search_queries('')


        for query in queries:
            print(f"ID: {query['id']}")
            print(f"Description: {query['description']}")
            print(f"Path: {query['path']} {query['method']}")
            print(f"Parameters: {query['params']}")
            print(f"Usage Count: {query['usage_count']}")
            print()

        db.close()
        

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure PostgreSQL is running and the database exists.")