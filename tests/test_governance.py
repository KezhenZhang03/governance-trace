import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main

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


def setup_module(module):
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


def _create_proposal(summary: str):
    status, body = _request(
        "POST",
        "/governance/proposals",
        {
            "summary": summary,
            "source_of_proposal": "manual",
            "target_knowledge_ids": ["kb_test_01"],
            "evidence_refs": ["manual://evidence"],
            "rationale": "smoke test rationale",
            "proposed_action": "revise",
            "target_module_tag": "durham-ai-module",
            "proposed_by": "pytest",
        },
    )
    assert status == 201
    return body["proposal_id"]


def test_create_proposal_success():
    proposal_id = _create_proposal("Create proposal success")
    status, detail = _request("GET", f"/governance/proposals/{proposal_id}")
    assert status == 200
    assert detail["current_status"] == "proposed"


def test_decision_approved_updates_kb_and_timeline():
    proposal_id = _create_proposal("Approval flow")
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
    frontier_proposal = _create_proposal("Frontier flow")
    rej_proposal = _create_proposal("Rejected flow")

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
    proposal_id = _create_proposal("Missing decision fields")
    status, body = _request(
        "POST",
        f"/governance/decisions/{proposal_id}",
        {"decision_status": "approved", "reviewer": "Only reviewer"},
    )
    assert status == 422
    assert "required" in body["detail"]
