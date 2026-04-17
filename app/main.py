from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app import ai_review

DB_PATH = Path(__file__).resolve().parent.parent / "governance_trace.db"
STATIC_INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"
DB_LOCK = threading.Lock()

AI_REVIEWER_NAME = ai_review.AI_REVIEWER_NAME

VALID_PROPOSAL_STATUSES = {"proposed", "under_review", "approved", "frontier", "rejected", "superseded"}
VALID_DECISION_STATUSES = {"approved", "frontier", "rejected"}
VALID_SOURCES = {"analytics", "audit", "external_source", "manual"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    with DB_LOCK:
        conn = _conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id TEXT PRIMARY KEY,
                    proposal_type TEXT NOT NULL,
                    source_of_proposal TEXT NOT NULL,
                    target_knowledge_ids TEXT NOT NULL,
                    target_module_tag TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    rationale TEXT,
                    evidence_refs TEXT NOT NULL,
                    proposed_action TEXT NOT NULL,
                    proposed_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    current_status TEXT NOT NULL,
                    proposal_text TEXT
                );

                CREATE TABLE IF NOT EXISTS decision_traces (
                    trace_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    resulting_knowledge_versions TEXT NOT NULL,
                    affected_assets TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id)
                );

                CREATE TABLE IF NOT EXISTS knowledge_units (
                    knowledge_id TEXT PRIMARY KEY,
                    module_tag TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    version_number TEXT NOT NULL,
                    source_provenance TEXT NOT NULL,
                    is_canonical INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ai_reviews (
                    review_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    trace_id TEXT,
                    review_source TEXT NOT NULL,
                    review_model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    score_table TEXT NOT NULL,
                    strengths TEXT NOT NULL,
                    weaknesses TEXT NOT NULL,
                    suggestions TEXT NOT NULL,
                    final_verdict TEXT NOT NULL,
                    verdict_justification TEXT NOT NULL,
                    average_score REAL NOT NULL,
                    raw_response_text TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id),
                    FOREIGN KEY (trace_id) REFERENCES decision_traces(trace_id)
                );
                """
            )
            _ensure_column(conn, "proposals", "proposal_text", "proposal_text TEXT")
            conn.commit()
        finally:
            conn.close()


def _compose_proposal_text(summary: str, rationale: str | None, evidence_refs: list[str], target_knowledge_ids: list[str]) -> str:
    rationale_text = rationale or "No rationale provided"
    evidence_text = ", ".join(evidence_refs) if evidence_refs else "No evidence refs"
    knowledge_text = ", ".join(target_knowledge_ids) if target_knowledge_ids else "No target knowledge ids"
    return (
        f"Summary: {summary}\n"
        f"Rationale: {rationale_text}\n"
        f"Evidence refs: {evidence_text}\n"
        f"Target knowledge IDs: {knowledge_text}"
    )


def seed_data() -> None:
    with DB_LOCK:
        conn = _conn()
        try:
            count = conn.execute("SELECT COUNT(*) c FROM proposals").fetchone()["c"]
            if count > 0:
                return

            conn.executemany(
                "INSERT INTO knowledge_units VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("kb_math_prompting_01", "durham-ai-module", "approved", "v2", "curriculum_committee", 1),
                    ("kb_eval_rubric_01", "durham-ai-module", "frontier", "v1", "analytics_signal", 0),
                    ("kb_safety_policy_01", "durham-ai-module", "approved", "v3", "audit", 1),
                ],
            )

            p1, p2, p3, p4 = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
            t1, t2, t3 = str(uuid4()), str(uuid4()), str(uuid4())

            s1 = "Promote revised prompt-scaffolding guidance for Year 9 practical."
            r1 = "Analytics shows improved completion and lower hallucination rates."
            e1 = ["ana://run-241", "report://durham/q1-prompting"]
            k1 = ["kb_math_prompting_01"]

            s2 = "Introduce external benchmark rubric for creative AI writing assessment."
            r2 = "Promising, but local cohort calibration is incomplete."
            e2 = ["ext://benchmark-2026", "audit://rubric-gap-note"]
            k2 = ["kb_eval_rubric_01"]

            s3 = "Archive strict ban on iterative prompting for homework assistants."
            r3 = "Audit discovered negative learning impacts in two schools."
            e3 = ["audit://safety-homework-2026-03"]
            k3 = ["kb_safety_policy_01"]

            s4 = "Live flow demo: align prompt rubric language between lecture and practical tracks."
            r4 = "Teacher feedback indicates terminology mismatch causing grading inconsistency."
            e4 = ["manual://teacher-feedback-17"]
            k4 = ["kb_math_prompting_01", "kb_eval_rubric_01"]

            proposals = [
                (
                    p1,
                    "promote",
                    "analytics",
                    json.dumps(k1),
                    "durham-ai-module",
                    s1,
                    r1,
                    json.dumps(e1),
                    "promote_to_canonical",
                    "analytics-bot",
                    "2026-04-01T09:00:00+00:00",
                    "approved",
                    _compose_proposal_text(s1, r1, e1, k1),
                ),
                (
                    p2,
                    "defer",
                    "external_source",
                    json.dumps(k2),
                    "durham-ai-module",
                    s2,
                    r2,
                    json.dumps(e2),
                    "keep_frontier",
                    "policy-analyst",
                    "2026-04-03T10:10:00+00:00",
                    "frontier",
                    _compose_proposal_text(s2, r2, e2, k2),
                ),
                (
                    p3,
                    "archive",
                    "audit",
                    json.dumps(k3),
                    "durham-ai-module",
                    s3,
                    r3,
                    json.dumps(e3),
                    "archive",
                    "audit-team",
                    "2026-04-05T11:45:00+00:00",
                    "rejected",
                    _compose_proposal_text(s3, r3, e3, k3),
                ),
                (
                    p4,
                    "revise",
                    "manual",
                    json.dumps(k4),
                    "durham-ai-module",
                    s4,
                    r4,
                    json.dumps(e4),
                    "revise",
                    "hackathon-demo",
                    "2026-04-10T08:30:00+00:00",
                    "proposed",
                    _compose_proposal_text(s4, r4, e4, k4),
                ),
            ]
            traces = [
                (
                    t1,
                    p1,
                    "approved",
                    "Dr. A. Carter",
                    "Evidence passed confidence threshold and aligns with curriculum outcomes.",
                    json.dumps(["kb_math_prompting_01:v3"]),
                    json.dumps(["lecture:prompting-week2", "practical:lab-A", "rubric:critical-thinking-v2"]),
                    "2026-04-02T14:30:00+00:00",
                ),
                (
                    t2,
                    p2,
                    "frontier",
                    "Prof. L. Singh",
                    "Needs one more cycle of local validation before canonical promotion.",
                    json.dumps([]),
                    json.dumps(["view:frontier-dashboard", "practical:pilot-writing-lab"]),
                    "2026-04-04T16:00:00+00:00",
                ),
                (
                    t3,
                    p3,
                    "rejected",
                    "Ms. R. Moreno",
                    "Insufficient evidence for removal; keep current safeguards.",
                    json.dumps([]),
                    json.dumps([]),
                    "2026-04-06T13:20:00+00:00",
                ),
            ]
            conn.executemany("INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", proposals)
            conn.executemany("INSERT INTO decision_traces VALUES (?, ?, ?, ?, ?, ?, ?, ?)", traces)
            conn.commit()
        finally:
            conn.close()


def _parse_json_field(row: sqlite3.Row, key: str) -> Any:
    return json.loads(row[key]) if row and row[key] else []


def _proposal_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "proposal_id": row["proposal_id"],
        "summary": row["summary"],
        "source_of_proposal": row["source_of_proposal"],
        "current_status": row["current_status"],
        "target_module_tag": row["target_module_tag"],
        "created_at": row["created_at"],
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON payload")


def _apply_approved_knowledge_updates(conn: sqlite3.Connection, module_tag: str, knowledge_ids: list[str]) -> list[str]:
    versions: list[str] = []
    for kid in knowledge_ids:
        row = conn.execute("SELECT * FROM knowledge_units WHERE knowledge_id = ?", (kid,)).fetchone()
        if row is None:
            version = "v1"
            conn.execute(
                "INSERT INTO knowledge_units VALUES (?, ?, ?, ?, ?, ?)",
                (kid, module_tag, "approved", version, "governance_approved", 1),
            )
        else:
            current = row["version_number"]
            num = int(current[1:]) if current.startswith("v") and current[1:].isdigit() else 1
            version = f"v{num + 1}"
            conn.execute(
                "UPDATE knowledge_units SET approval_status = ?, version_number = ?, source_provenance = ?, is_canonical = 1 WHERE knowledge_id = ?",
                ("approved", version, "governance_approved", kid),
            )
        versions.append(f"{kid}:{version}")
    return versions


def _latest_decision(conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM decision_traces WHERE proposal_id = ? ORDER BY decided_at DESC LIMIT 1", (proposal_id,)
    ).fetchone()


def _latest_ai_review_row(conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ai_reviews WHERE proposal_id = ? ORDER BY created_at DESC LIMIT 1", (proposal_id,)
    ).fetchone()


def _serialize_ai_review(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "summary": row["summary"],
        "scores": json.loads(row["score_table"]),
        "strengths": json.loads(row["strengths"]),
        "weaknesses": json.loads(row["weaknesses"]),
        "suggestions": json.loads(row["suggestions"]),
        "final_verdict": row["final_verdict"],
        "verdict_justification": row["verdict_justification"],
        "average_score": row["average_score"],
        "review_model": row["review_model"],
        "created_at": row["created_at"],
    }


def _insert_decision_trace(
    conn: sqlite3.Connection,
    proposal_id: str,
    decision_status: str,
    reviewer: str,
    decision_reason: str,
    proposal_row: sqlite3.Row,
    affected_assets: list[str],
) -> tuple[str, str, list[str]]:
    decided_at = now_iso()
    resulting_versions: list[str] = []
    if decision_status == "approved":
        resulting_versions = _apply_approved_knowledge_updates(
            conn,
            proposal_row["target_module_tag"],
            json.loads(proposal_row["target_knowledge_ids"]),
        )

    trace_id = str(uuid4())
    conn.execute(
        "INSERT INTO decision_traces VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trace_id,
            proposal_id,
            decision_status,
            reviewer,
            decision_reason,
            json.dumps(resulting_versions),
            json.dumps(affected_assets),
            decided_at,
        ),
    )
    conn.execute(
        "UPDATE proposals SET current_status = ? WHERE proposal_id = ?",
        (decision_status, proposal_id),
    )
    return trace_id, decided_at, resulting_versions


def _execute_ai_screen(conn: sqlite3.Connection, proposal_row: sqlite3.Row) -> dict[str, Any]:
    proposal_id = proposal_row["proposal_id"]
    proposal_text = proposal_row["proposal_text"] or _compose_proposal_text(
        proposal_row["summary"],
        proposal_row["rationale"],
        _parse_json_field(proposal_row, "evidence_refs"),
        _parse_json_field(proposal_row, "target_knowledge_ids"),
    )

    review = ai_review.run_ai_review(proposal_text)
    mapped = ai_review.classify_ai_review(review)
    status = mapped["status"]
    reason = ai_review.build_decision_reason(review, status)
    trace_id, decided_at, _ = _insert_decision_trace(
        conn=conn,
        proposal_id=proposal_id,
        decision_status=status,
        reviewer=AI_REVIEWER_NAME,
        decision_reason=reason,
        proposal_row=proposal_row,
        affected_assets=["view:ai-governance-review"],
    )

    review_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO ai_reviews (
          review_id, proposal_id, trace_id, review_source, review_model, prompt_version,
          summary, score_table, strengths, weaknesses, suggestions,
          final_verdict, verdict_justification, average_score, raw_response_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            proposal_id,
            trace_id,
            review["review_source"],
            review["review_model"],
            review["prompt_version"],
            review["summary"],
            json.dumps(review["scores"]),
            json.dumps(review["strengths"]),
            json.dumps(review["weaknesses"]),
            json.dumps(review["suggestions"]),
            review["final_verdict"],
            review["verdict_justification"],
            review["average_score"],
            review.get("raw_response_text", ""),
            now_iso(),
        ),
    )
    conn.commit()

    return {
        "proposal_id": proposal_id,
        "trace_id": trace_id,
        "updated_status": status,
        "decided_at": decided_at,
        "ai_review_summary": review["summary"],
        "ai_final_verdict": review["final_verdict"],
        "ai_average_score": review["average_score"],
    }


class GovernanceHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            html = STATIC_INDEX.read_text(encoding="utf-8")
            payload = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/governance/proposals":
            module = query.get("module", [None])[0]
            status = query.get("status", [None])[0]
            clauses, params = [], []
            if module:
                clauses.append("target_module_tag = ?")
                params.append(module)
            if status:
                clauses.append("current_status = ?")
                params.append(status)
            sql = "SELECT * FROM proposals"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC"
            with DB_LOCK:
                conn = _conn()
                rows = conn.execute(sql, params).fetchall()
                conn.close()
            return _json_response(self, 200, [_proposal_summary(r) for r in rows])

        if path.startswith("/governance/proposals/"):
            proposal_id = path.split("/")[-1]
            with DB_LOCK:
                conn = _conn()
                proposal = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if proposal is None:
                    conn.close()
                    return _json_response(self, 404, {"detail": f"Proposal {proposal_id} not found"})
                latest = _latest_decision(conn, proposal_id)
                latest_review = _latest_ai_review_row(conn, proposal_id)
                conn.close()

            latest_decision = None
            if latest:
                latest_decision = {
                    "trace_id": latest["trace_id"],
                    "decision_status": latest["decision_status"],
                    "reviewer": latest["reviewer"],
                    "decision_reason": latest["decision_reason"],
                    "decided_at": latest["decided_at"],
                }
            payload = {
                "proposal_id": proposal["proposal_id"],
                "proposal_type": proposal["proposal_type"],
                "source_of_proposal": proposal["source_of_proposal"],
                "target_knowledge_ids": _parse_json_field(proposal, "target_knowledge_ids"),
                "target_module_tag": proposal["target_module_tag"],
                "summary": proposal["summary"],
                "rationale": proposal["rationale"],
                "evidence_refs": _parse_json_field(proposal, "evidence_refs"),
                "proposal_text": proposal["proposal_text"],
                "proposed_action": proposal["proposed_action"],
                "proposed_by": proposal["proposed_by"],
                "created_at": proposal["created_at"],
                "current_status": proposal["current_status"],
                "latest_decision": latest_decision,
                "latest_ai_review": _serialize_ai_review(latest_review),
            }
            return _json_response(self, 200, payload)

        if path.startswith("/governance/timeline/"):
            proposal_id = path.split("/")[-1]
            with DB_LOCK:
                conn = _conn()
                proposal = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if proposal is None:
                    conn.close()
                    return _json_response(self, 404, {"detail": f"Proposal {proposal_id} not found"})
                traces = conn.execute(
                    "SELECT * FROM decision_traces WHERE proposal_id = ? ORDER BY decided_at ASC", (proposal_id,)
                ).fetchall()
                conn.close()

            events = [
                {
                    "event_id": f"proposal-{proposal_id}",
                    "event_type": "proposal_created",
                    "proposal_id": proposal_id,
                    "trace_id": None,
                    "knowledge_ids": _parse_json_field(proposal, "target_knowledge_ids"),
                    "status": "proposed",
                    "actor": proposal["proposed_by"],
                    "reason": proposal["rationale"],
                    "happened_at": proposal["created_at"],
                }
            ]
            for t in traces:
                actor = AI_REVIEWER_NAME if t["reviewer"] == AI_REVIEWER_NAME else t["reviewer"]
                events.append(
                    {
                        "event_id": f"decision-{t['trace_id']}",
                        "event_type": "decision",
                        "proposal_id": proposal_id,
                        "trace_id": t["trace_id"],
                        "knowledge_ids": _parse_json_field(proposal, "target_knowledge_ids"),
                        "status": t["decision_status"],
                        "actor": actor,
                        "reason": t["decision_reason"],
                        "happened_at": t["decided_at"],
                    }
                )
            return _json_response(self, 200, events)

        if path.startswith("/governance/impact/"):
            proposal_id = path.split("/")[-1]
            with DB_LOCK:
                conn = _conn()
                proposal = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if proposal is None:
                    conn.close()
                    return _json_response(self, 404, {"detail": f"Proposal {proposal_id} not found"})
                latest = _latest_decision(conn, proposal_id)
                conn.close()
            status = proposal["current_status"]
            outcome = {
                "approved": "Moved to canonical knowledge boundary",
                "frontier": "Retained as frontier knowledge (non-canonical)",
                "rejected": "Rejected; no canonical updates",
            }.get(status, "Pending governance decision")
            return _json_response(
                self,
                200,
                {
                    "proposal_id": proposal_id,
                    "affected_assets": _parse_json_field(latest, "affected_assets") if latest else [],
                    "affected_knowledge_units": _parse_json_field(proposal, "target_knowledge_ids"),
                    "resulting_knowledge_versions": _parse_json_field(latest, "resulting_knowledge_versions") if latest else [],
                    "canonical_outcome": outcome,
                },
            )

        if path == "/governance/summary":
            with DB_LOCK:
                conn = _conn()
                counts = {
                    s: conn.execute("SELECT COUNT(*) c FROM proposals WHERE current_status = ?", (s,)).fetchone()["c"]
                    for s in ["approved", "frontier", "rejected"]
                }
                pending = conn.execute(
                    "SELECT COUNT(*) c FROM proposals WHERE current_status IN ('proposed', 'under_review')"
                ).fetchone()["c"]
                latest = conn.execute("SELECT * FROM decision_traces ORDER BY decided_at DESC LIMIT 1").fetchone()
                conn.close()
            recent = None
            if latest:
                recent = {
                    "trace_id": latest["trace_id"],
                    "decision_status": latest["decision_status"],
                    "reviewer": latest["reviewer"],
                    "decision_reason": latest["decision_reason"],
                    "decided_at": latest["decided_at"],
                }
            return _json_response(
                self,
                200,
                {
                    "approved": counts["approved"],
                    "frontier": counts["frontier"],
                    "rejected": counts["rejected"],
                    "pending": pending,
                    "recent_decision": recent,
                    "pending_count": pending,
                },
            )

        return _json_response(self, 404, {"detail": "Not found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = _read_json_body(self)
        except ValueError as exc:
            return _json_response(self, 400, {"detail": str(exc)})

        if path == "/governance/proposals":
            summary = payload.get("summary", "").strip()
            source = payload.get("source_of_proposal")
            if not summary:
                return _json_response(self, 400, {"detail": "summary cannot be empty"})
            if source not in VALID_SOURCES:
                return _json_response(self, 422, {"detail": "source_of_proposal is invalid"})

            target_knowledge_ids = payload.get("target_knowledge_ids", [])
            evidence_refs = payload.get("evidence_refs", [])
            rationale = payload.get("rationale")
            proposal_text = payload.get("proposal_text")
            if not proposal_text:
                proposal_text = _compose_proposal_text(summary, rationale, evidence_refs, target_knowledge_ids)

            auto_screen = bool(payload.get("auto_screen", False))
            proposal_id = str(uuid4())
            created_at = payload.get("created_at") or now_iso()
            row = (
                proposal_id,
                payload.get("proposal_type", "revise"),
                source,
                json.dumps(target_knowledge_ids),
                payload.get("target_module_tag", "durham-ai-module"),
                summary,
                rationale,
                json.dumps(evidence_refs),
                payload.get("proposed_action", "revise"),
                payload.get("proposed_by", "unknown"),
                created_at,
                "proposed",
                proposal_text,
            )
            with DB_LOCK:
                conn = _conn()
                conn.execute("INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
                result: dict[str, Any] = {
                    "proposal_id": proposal_id,
                    "current_status": "proposed",
                    "ai_screening_triggered": auto_screen,
                    "screening_status": "skipped",
                }
                if auto_screen:
                    proposal_row = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                    try:
                        screening = _execute_ai_screen(conn, proposal_row)
                        latest_trace = conn.execute(
                            "SELECT * FROM decision_traces WHERE trace_id = ?", (screening["trace_id"],)
                        ).fetchone()
                        result.update(
                            {
                                "current_status": screening["updated_status"],
                                "screening_status": "completed",
                                "latest_decision": {
                                    "trace_id": latest_trace["trace_id"],
                                    "decision_status": latest_trace["decision_status"],
                                    "reviewer": latest_trace["reviewer"],
                                    "decision_reason": latest_trace["decision_reason"],
                                    "decided_at": latest_trace["decided_at"],
                                },
                            }
                        )
                    except ai_review.AIReviewError as exc:
                        conn.commit()
                        result.update({"screening_status": "failed", "screening_error": str(exc)})
                else:
                    conn.commit()
                conn.close()

            return _json_response(self, 201, result)

        if path.startswith("/governance/ai-screen/"):
            proposal_id = path.split("/")[-1]
            with DB_LOCK:
                conn = _conn()
                proposal = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if proposal is None:
                    conn.close()
                    return _json_response(self, 404, {"detail": f"Proposal {proposal_id} not found"})
                if proposal["current_status"] in {"approved", "frontier", "rejected"}:
                    conn.close()
                    return _json_response(
                        self,
                        409,
                        {"detail": f"Proposal already finalized as {proposal['current_status']}"},
                    )
                try:
                    result = _execute_ai_screen(conn, proposal)
                except ai_review.AIReviewError as exc:
                    conn.close()
                    return _json_response(self, exc.status_code, {"detail": str(exc)})
                conn.close()
            return _json_response(self, 200, result)

        if path.startswith("/governance/decisions/"):
            proposal_id = path.split("/")[-1]
            decision_status = payload.get("decision_status")
            reviewer = payload.get("reviewer")
            reason = payload.get("decision_reason")
            if decision_status not in VALID_DECISION_STATUSES:
                return _json_response(self, 422, {"detail": "decision_status must be approved/frontier/rejected"})
            if not reviewer or not reason:
                return _json_response(self, 422, {"detail": "reviewer and decision_reason are required"})

            with DB_LOCK:
                conn = _conn()
                proposal = conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if proposal is None:
                    conn.close()
                    return _json_response(self, 404, {"detail": f"Proposal {proposal_id} not found"})
                if proposal["current_status"] in {"approved", "frontier", "rejected"}:
                    conn.close()
                    return _json_response(
                        self,
                        409,
                        {"detail": f"Proposal already finalized as {proposal['current_status']}"},
                    )

                trace_id, decided_at, _ = _insert_decision_trace(
                    conn=conn,
                    proposal_id=proposal_id,
                    decision_status=decision_status,
                    reviewer=reviewer,
                    decision_reason=reason,
                    proposal_row=proposal,
                    affected_assets=payload.get("affected_assets", []),
                )
                conn.commit()
                conn.close()
            return _json_response(
                self,
                200,
                {"trace_id": trace_id, "proposal_id": proposal_id, "updated_status": decision_status, "decided_at": decided_at},
            )

        return _json_response(self, 404, {"detail": "Not found"})


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    init_db()
    seed_data()
    return ThreadingHTTPServer((host, port), GovernanceHandler)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_server(host=host, port=port)
    print(f"Governance Trace MVP running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
