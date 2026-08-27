"""Review-gated conversion of production Agent failures into eval cases."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from app.core.observability_sanitizer import redact_payload, stable_hash
from app.core.time import utc_now
from app.models.agent_eval import AgentEvalCandidate

PENDING_REVIEW = "pending_review"
APPROVED = "approved"
REJECTED = "rejected"
EXPORTED = "exported"


class OnlineEvalService:
    def capture_failure(
        self,
        db: Session,
        *,
        run_id: int | None,
        trace_id: str | None,
        user_id: int | None,
        organization_id: int | None,
        failure_type: str,
        goal: str | None = None,
        source_event_id: int | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> AgentEvalCandidate | None:
        """Persist a deduplicated, redacted candidate. It is not executable yet."""
        if not run_id and not trace_id:
            return None
        raw_key = f"{run_id or 0}:{trace_id or ''}:{failure_type}:{source_event_id or 0}"
        dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        existing = db.query(AgentEvalCandidate).filter_by(dedupe_key=dedupe_key).first()
        if existing:
            return existing
        candidate = AgentEvalCandidate(
            agent_run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            organization_id=organization_id,
            source_event_id=source_event_id,
            failure_type=failure_type,
            status=PENDING_REVIEW,
            goal_hash=stable_hash(goal) if goal else None,
            evidence_json=json.dumps(redact_payload(evidence or {}), ensure_ascii=False, default=str),
            dedupe_key=dedupe_key,
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        return candidate

    def list_candidates(self, db: Session, *, status: str | None = None, limit: int = 100) -> list[AgentEvalCandidate]:
        query = db.query(AgentEvalCandidate)
        if status:
            query = query.filter(AgentEvalCandidate.status == status)
        return query.order_by(AgentEvalCandidate.id.desc()).limit(max(1, min(limit, 500))).all()

    def approve(
        self,
        db: Session,
        *,
        candidate_id: int,
        reviewer_id: int,
        evaluation_input: dict[str, Any],
        expected_outcome: dict[str, Any],
        review_note: str | None = None,
    ) -> AgentEvalCandidate:
        candidate = db.query(AgentEvalCandidate).filter_by(id=candidate_id).first()
        if not candidate:
            raise ValueError("Agent eval candidate not found")
        goal = str(evaluation_input.get("goal") or "").strip()
        if not goal:
            raise ValueError("evaluation_input.goal is required")
        terminal_status = str(expected_outcome.get("terminal_status") or "").strip()
        if terminal_status not in {"completed", "error", "cancelled"}:
            raise ValueError("expected_outcome.terminal_status must be completed, error, or cancelled")
        candidate.status = APPROVED
        candidate.reviewer_id = reviewer_id
        candidate.review_note = (review_note or "").strip() or None
        candidate.evaluation_input_json = json.dumps(evaluation_input, ensure_ascii=False)
        candidate.expected_outcome_json = json.dumps(expected_outcome, ensure_ascii=False)
        candidate.approved_at = utc_now()
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        return candidate

    def reject(
        self, db: Session, *, candidate_id: int, reviewer_id: int, review_note: str | None = None
    ) -> AgentEvalCandidate:
        candidate = db.query(AgentEvalCandidate).filter_by(id=candidate_id).first()
        if not candidate:
            raise ValueError("Agent eval candidate not found")
        candidate.status = REJECTED
        candidate.reviewer_id = reviewer_id
        candidate.review_note = (review_note or "").strip() or None
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        return candidate

    def export_approved(self, db: Session) -> dict[str, Any]:
        rows = (
            db.query(AgentEvalCandidate)
            .filter(AgentEvalCandidate.status.in_((APPROVED, EXPORTED)))
            .order_by(AgentEvalCandidate.id.asc())
            .limit(500)
            .all()
        )
        cases = []
        for row in rows:
            try:
                evaluation_input = json.loads(row.evaluation_input_json or "{}")
                expected_outcome = json.loads(row.expected_outcome_json or "{}")
            except json.JSONDecodeError:
                continue
            cases.append(
                {
                    "id": f"agent_online_{row.id}",
                    "candidate_id": row.id,
                    "trace_id": row.trace_id,
                    "failure_type": row.failure_type,
                    "input": evaluation_input,
                    "expected": expected_outcome,
                }
            )
            if row.status == APPROVED:
                row.status = EXPORTED
                row.exported_at = utc_now()
                db.add(row)
        db.commit()
        return {"schema_version": 1, "generated_at": utc_now().astimezone(UTC).isoformat(), "cases": cases}


online_eval_service = OnlineEvalService()
