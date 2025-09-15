"""
Database schema definitions for ConnectWise API Gateway

Contains SQLAlchemy table models for storing API endpoint information.
"""

from sqlalchemy import Column, Integer, Text, Boolean, ForeignKey, Index, BigInteger, func
from sqlalchemy.dialects.postgresql import JSON, TSVECTOR
from sqlalchemy.orm import declarative_base, relationship

# SQLAlchemy setup
Base = declarative_base()

class Endpoint(Base):
    __tablename__ = 'endpoints'

    id = Column(Integer, primary_key=True)
    path = Column(Text, nullable=False)
    method = Column(Text, nullable=False)
    description = Column(Text)
    category = Column(Text)
    summary = Column(Text)
    tags = Column(Text)
    keywords = Column(Text)
    search_vector = Column(TSVECTOR, nullable=True)

    # Relationships
    parameters = relationship("Parameter", back_populates="endpoint", cascade="all, delete-orphan")
    request_bodies = relationship("RequestBody", back_populates="endpoint", cascade="all, delete-orphan")
    response_bodies = relationship("ResponseBody", back_populates="endpoint", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_endpoints_path', 'path'),
        Index('idx_endpoints_method', 'method'),
        Index('idx_endpoints_category', 'category'),
        Index('idx_endpoints_path_method', 'path', 'method', unique=True),
        Index('idx_endpoints_search_vector_gin', 'search_vector', postgresql_using='gin'),
    )

class Parameter(Base):
    __tablename__ = 'parameters'

    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey('endpoints.id'), nullable=False)
    name = Column(Text, nullable=False)
    location = Column(Text, nullable=False)  # path, query, body
    required = Column(Boolean, default=False)
    type = Column(Text)
    description = Column(Text)

    # Relationships
    endpoint = relationship("Endpoint", back_populates="parameters")

    # Indexes
    __table_args__ = (
        Index('idx_parameters_endpoint_id', 'endpoint_id'),
    )

class RequestBody(Base):
    __tablename__ = 'request_bodies'

    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey('endpoints.id'), nullable=False)
    schema = Column(Text)  # JSON schema for the body
    example = Column(Text)  # JSON example if available

    # Relationships
    endpoint = relationship("Endpoint", back_populates="request_bodies")

    # Indexes
    __table_args__ = (
        Index('idx_request_bodies_endpoint_id', 'endpoint_id'),
    )

class ResponseBody(Base):
    __tablename__ = 'response_bodies'

    id = Column(Integer, primary_key=True)
    endpoint_id = Column(Integer, ForeignKey('endpoints.id'), nullable=False)
    status_code = Column(Text)
    description = Column(Text)
    schema = Column(Text)  # JSON schema for the response
    example = Column(Text)  # JSON example if available

    # Relationships
    endpoint = relationship("Endpoint", back_populates="response_bodies")

    # Indexes
    __table_args__ = (
        Index('idx_response_bodies_endpoint_id', 'endpoint_id'),
    )

class SavedQuery(Base):
    """Model for Fast Memory saved queries"""
    __tablename__ = 'saved_queries'

    id = Column(Integer, primary_key=True)
    description = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    method = Column(Text, nullable=False)
    params = Column(JSON)  # Native PostgreSQL JSON support
    data = Column(JSON)    # Native PostgreSQL JSON support
    timestamp = Column(BigInteger, nullable=False, default=func.extract('epoch', func.now()))
    usage_count = Column(Integer, default=0, nullable=False)

    # Indexes for performance
    __table_args__ = (
        Index('idx_saved_queries_path_method', 'path', 'method', unique=True),
        Index('idx_saved_queries_timestamp', 'timestamp'),
        Index('idx_saved_queries_usage_count', 'usage_count'),
        Index('idx_saved_queries_search', 'description', 'path'),
    )