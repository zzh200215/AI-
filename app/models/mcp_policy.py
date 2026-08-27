from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class MCPPolicyVersion(Base):
    __tablename__ = "mcp_policy_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(64), nullable=False, unique=True, index=True)
    schema_version = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="draft", index=True)
    policy_json = Column(Text, nullable=False)
    checksum = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    activated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
