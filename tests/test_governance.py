import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ai_review, main

BASE = "http://127.0.0.1:8765"


def _request(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _mock_review(verdict: str, scores: list[int]):
    score_table = [
        {"criterion": c, "score": s, "justification": f"Mock justification for {c}"}
        for c, s in zip(ai_review.CORE_CRITERIA, scores)
    ]
    return {
        "summary": f"Mock review for verdict={verdict}",
        "scores": score_table,
        "strengths": ["Structured objective"],
        "weaknesses": ["Needs stronger baselines"],
        "suggestions": ["Add ablation and robustness checks"],
        "final_verdict": verdict,
        "verdict_justification": "Detailed model-based justification.",
        "average_score": round(sum(scores) / len(scores), 2),
        "review_model": "mock-review-model",
        "review_source": "openai",
        "prompt_version": "v1",
        "raw_response_text": "{}",
    }


def setup_module(module):
    os.environ["AI_REVIEW_MOCK"] = "1"
    os.environ["AI_REVIEW_ENABLED"] = "1"
    db = Path("governance_trace.db")
    if db.exists():
        db.unlink()
    module.server = main.create_server(port=8765)
    module.thread = threading.Thread(target=module.server.serve_forever, daemon=True)
    module.thread.start()
    time.sleep(0.2)


def teardown_module(module):
    module.server.shutdown()
    module.server.server_close()


def _create_proposal(summary: str, auto_screen: bool = False, proposal_text: str | None = None):
    payload = {
        "summary": summary,
        "source_of_proposal": "manual",
        "target_knowledge_ids": ["kb_test_01"],
        "evidence_refs": ["manual://evidence"],
        "rationale": "smoke test rationale",
        "proposed_action": "revise",
        "target_module_tag": "durham-ai-module",
        "proposed_by": "pytest",
        "auto_screen": auto_screen,
    }
    if proposal_text:
        payload["proposal_text"] = proposal_text

    status, body = _request("POST", "/governance/proposals", payload)
    assert status == 201
    return body


def test_create_proposal_success():
    created = _create_proposal("Create proposal success")
    proposal_id = created["proposal_id"]
    status, detail = _request("GET", f"/governance/proposals/{proposal_id}")
    assert status == 200
    assert detail["current_status"] == "proposed"


def test_decision_approved_updates_kb_and_timeline():
    proposal_id = _create_proposal("Approval flow")["proposal_id"]
    status, _ = _request(
        "POST",
        f"/governance/decisions/{proposal_id}",
        {
            "decision_status": "approved",
            "reviewer": "QA Reviewer",
            "decision_reason": "All checks passed",
            "affected_assets": ["lecture:test"],
        },
    )
    assert status == 200

    status, timeline = _request("GET", f"/governance/timeline/{proposal_id}")
    assert status == 200
    assert len(timeline) == 2
    assert timeline[-1]["status"] == "approved"

    status, impact = _request("GET", f"/governance/impact/{proposal_id}")
    assert status == 200
    assert impact["canonical_outcome"].startswith("Moved to canonical")
    assert impact["resulting_knowledge_versions"]


def test_decision_frontier_and_rejected_do_not_canonicalize():
    frontier_proposal = _create_proposal("Frontier flow")["proposal_id"]
    rej_proposal = _create_proposal("Rejected flow")["proposal_id"]

    f_status, _ = _request(
        "POST",
        f"/governance/decisions/{frontier_proposal}",
        {
            "decision_status": "frontier",
            "reviewer": "Frontier Reviewer",
            "decision_reason": "Need more data",
            "affected_assets": ["view:frontier"],
        },
    )
    r_status, _ = _request(
        "POST",
        f"/governance/decisions/{rej_proposal}",
        {
            "decision_status": "rejected",
            "reviewer": "Reject Reviewer",
            "decision_reason": "Evidence weak",
        },
    )
    assert f_status == 200 and r_status == 200

    _, frontier_impact = _request("GET", f"/governance/impact/{frontier_proposal}")
    _, rejected_impact = _request("GET", f"/governance/impact/{rej_proposal}")
    assert frontier_impact["canonical_outcome"].startswith("Retained as frontier")
    assert rejected_impact["canonical_outcome"].startswith("Rejected")
    assert frontier_impact["resulting_knowledge_versions"] == []
    assert rejected_impact["resulting_knowledge_versions"] == []


def test_decision_requires_fields():
    proposal_id = _create_proposal("Missing decision fields")["proposal_id"]
    status, body = _request(
        "POST",
        f"/governance/decisions/{proposal_id}",
        {"decision_status": "approved", "reviewer": "Only reviewer"},
    )
    assert status == 422
    assert "required" in body["detail"]


def test_auto_screen_rejects_low_quality_proposal(monkeypatch):
    monkeypatch.setattr(ai_review, "run_ai_review", lambda text: _mock_review("Reject", [2, 2, 1, 2, 1, 2, 2]))
    created = _create_proposal(
        "Low quality proposal",
        auto_screen=True,
        proposal_text="This is low quality and should reject",
    )
    assert created["screening_status"] == "completed"
    proposal_id = created["proposal_id"]

    _, detail = _request("GET", f"/governance/proposals/{proposal_id}")
    assert detail["current_status"] == "rejected"
    assert detail["latest_decision"]["reviewer"] == ai_review.AI_REVIEWER_NAME
    reason = detail["latest_decision"]["decision_reason"]
    assert reason != "Need more data"
    assert any(keyword in reason for keyword in ["Methodology Soundness", "Feasibility", "Evaluation Plan"])


def test_auto_screen_borderline_becomes_frontier(monkeypatch):
    monkeypatch.setattr(ai_review, "run_ai_review", lambda text: _mock_review("Borderline", [3, 3, 3, 3, 3, 3, 3]))
    created = _create_proposal(
        "Borderline proposal",
        auto_screen=True,
        proposal_text="borderline quality proposal",
    )
    proposal_id = created["proposal_id"]
    _, detail = _request("GET", f"/governance/proposals/{proposal_id}")
    assert detail["current_status"] == "frontier"


def test_auto_screen_accept_approves_and_updates_kb(monkeypatch):
    monkeypatch.setattr(ai_review, "run_ai_review", lambda text: _mock_review("Accept", [4, 4, 4, 4, 4, 4, 4]))
    created = _create_proposal(
        "Accept proposal",
        auto_screen=True,
        proposal_text="strong proposal accept",
    )
    proposal_id = created["proposal_id"]
    _, detail = _request("GET", f"/governance/proposals/{proposal_id}")
    assert detail["current_status"] == "approved"

    _, impact = _request("GET", f"/governance/impact/{proposal_id}")
    assert impact["resulting_knowledge_versions"]
    assert "canonical" in impact["canonical_outcome"].lower()


def test_ai_screen_endpoint_for_existing_proposal(monkeypatch):
    monkeypatch.setattr(ai_review, "run_ai_review", lambda text: _mock_review("Weak Accept", [4, 4, 4, 4, 3, 4, 4]))
    proposal_id = _create_proposal("Manual then AI", auto_screen=False)["proposal_id"]
    status, body = _request("POST", f"/governance/ai-screen/{proposal_id}")
    assert status == 200
    assert body["updated_status"] == "approved"


def test_missing_api_key_returns_readable_error(monkeypatch):
    proposal_id = _create_proposal("Needs real AI call", auto_screen=False)["proposal_id"]
    monkeypatch.setenv("AI_REVIEW_MOCK", "0")
    monkeypatch.setenv("AI_REVIEW_ENABLED", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status, body = _request("POST", f"/governance/ai-screen/{proposal_id}")
    assert status == 503
    assert "OPENAI_API_KEY" in body["detail"]

    monkeypatch.setenv("AI_REVIEW_MOCK", "1")
