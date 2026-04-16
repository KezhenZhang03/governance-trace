#!/usr/bin/env python3
"""Automated demo runner for Governance Trace MVP.

Starts an in-process server, executes a proposal->decision->timeline->impact flow,
and prints JSON responses so hackathon presenters can show the full chain quickly.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_server

BASE = "http://127.0.0.1:8777"


def request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def show(title: str, status: int, data: dict | list) -> None:
    print(f"\n=== {title} (HTTP {status}) ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    server = create_server(port=8777)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)

    try:
        status, summary = request("GET", "/governance/summary")
        show("Initial summary", status, summary)

        create_payload = {
            "summary": "Automated live flow demo proposal",
            "source_of_proposal": "manual",
            "target_knowledge_ids": ["kb_eval_rubric_01"],
            "target_module_tag": "durham-ai-module",
            "rationale": "Auto-demo chain for hackathon walkthrough",
            "evidence_refs": ["manual://auto-demo"],
            "proposed_action": "promote_to_canonical",
            "proposed_by": "auto-demo-script",
        }
        status, created = request("POST", "/governance/proposals", create_payload)
        show("Create proposal", status, created)
        proposal_id = created["proposal_id"]

        decision_payload = {
            "decision_status": "approved",
            "reviewer": "Auto Reviewer",
            "decision_reason": "Automated acceptance to showcase canonical update",
            "affected_assets": ["lecture:auto-demo", "rubric:auto-demo-v1"],
        }
        status, decision = request("POST", f"/governance/decisions/{proposal_id}", decision_payload)
        show("Submit decision", status, decision)

        status, detail = request("GET", f"/governance/proposals/{proposal_id}")
        show("Proposal detail", status, detail)

        status, timeline = request("GET", f"/governance/timeline/{proposal_id}")
        show("Timeline", status, timeline)

        status, impact = request("GET", f"/governance/impact/{proposal_id}")
        show("Impact", status, impact)

        status, summary = request("GET", "/governance/summary")
        show("Final summary", status, summary)

        print("\n✅ Automated demo flow finished successfully.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
