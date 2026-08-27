"""Persistence, activation and audit replay for MCP policy versions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.mcp.policy import policy_checksum, validate_policy_document
from app.models.agent import AgentAuditEvent
from app.models.mcp_policy import MCPPolicyVersion


class MCPPolicyService:
    def list_versions(self, db: Session, limit: int = 100) -> list[MCPPolicyVersion]:
        return db.query(MCPPolicyVersion).order_by(MCPPolicyVersion.id.desc()).limit(max(1, min(limit, 500))).all()

    def save_draft(self, db: Session, *, document: dict[str, Any], actor_id: int | None = None) -> MCPPolicyVersion:
        document = validate_policy_document(document)
        version = str(document["version"])
        row = db.query(MCPPolicyVersion).filter(MCPPolicyVersion.version == version).first()
        if row and row.status == "active":
            raise ValueError("Active MCP policy versions are immutable")
        if row is None:
            row = MCPPolicyVersion(version=version, created_by=actor_id)
        row.schema_version = int(document["schema_version"])
        row.status = "draft"
        row.policy_json = json.dumps(document, ensure_ascii=False, sort_keys=True)
        row.checksum = policy_checksum(document)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def activate(self, db: Session, *, version: str, actor_id: int | None = None) -> MCPPolicyVersion:
        row = db.query(MCPPolicyVersion).filter(MCPPolicyVersion.version == version).first()
        if not row:
            raise ValueError("MCP policy version not found")
        document = validate_policy_document(json.loads(row.policy_json))
        db.query(MCPPolicyVersion).filter(MCPPolicyVersion.status == "active").update({"status": "retired"}, synchronize_session=False)
        row.status = "active"
        row.activated_by = actor_id
        row.activated_at = utc_now()
        row.checksum = policy_checksum(document)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def replay_run(self, db: Session, *, run_id: int) -> dict[str, Any]:
        from app.mcp.policy import policy_engine

        events = (
            db.query(AgentAuditEvent)
            .filter(AgentAuditEvent.run_id == run_id, AgentAuditEvent.event_type == "permission_decision")
            .order_by(AgentAuditEvent.id.asc())
            .all()
        )
        decisions = []
        for event in events:
            try:
                original = json.loads(event.decision_json or "{}")
            except json.JSONDecodeError:
                original = {}
            tool_name = event.tool_name or original.get("tool_name")
            agent_type = original.get("agent_type") or "general_agent"
            current = policy_engine.evaluate(agent_type=agent_type, tool_name=tool_name or "", db=db).to_dict()
            comparable_fields = (
                "allowed",
                "requires_approval",
                "rule_id",
                "data_scope",
                "risk_level",
                "error_code",
            )
            metadata_drift = any(
                field in original and original.get(field) != current.get(field)
                for field in comparable_fields
            )
            version_drift = original.get("policy_version") not in (None, current.get("policy_version"))
            decisions.append({
                "event_id": event.id,
                "tool_name": tool_name,
                "original": original,
                "current": current,
                "drifted": bool(metadata_drift or version_drift),
            })
        return {"run_id": run_id, "decision_count": len(decisions), "drift_count": sum(item["drifted"] for item in decisions), "decisions": decisions}


mcp_policy_service = MCPPolicyService()
