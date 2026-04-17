from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "gpt-4.1-mini"
AI_REVIEWER_NAME = "AI Academic Reviewer"

HARD_FAIL_SCORE_THRESHOLD = 2
AVERAGE_REJECT_THRESHOLD = 2.8
AVERAGE_FRONTIER_THRESHOLD = 3.4

CORE_CRITERIA = [
    "Clarity of Research Problem",
    "Novelty & Contribution",
    "Methodology Soundness",
    "Feasibility",
    "Evaluation Plan",
    "Impact",
    "Risk & Limitations",
]
HARD_FAIL_CRITERIA = {"Methodology Soundness", "Feasibility", "Evaluation Plan"}


@dataclass
class AIReviewError(Exception):
    message: str
    status_code: int = 503

    def __str__(self) -> str:
        return self.message


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_ai_review_enabled() -> bool:
    return _env_bool("AI_REVIEW_ENABLED", True)


def is_mock_mode() -> bool:
    return _env_bool("AI_REVIEW_MOCK", False)


def get_review_model() -> str:
    return os.getenv("OPENAI_REVIEW_MODEL", DEFAULT_MODEL)


def get_openai_client():
    if not is_ai_review_enabled():
        raise AIReviewError("AI review is disabled by AI_REVIEW_ENABLED", status_code=503)

    if is_mock_mode():
        return None

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIReviewError("AI review is enabled but OPENAI_API_KEY is not configured", status_code=503)

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - only when dependency missing in non-mock runtime
        raise AIReviewError(
            "openai package is not installed; install official OpenAI SDK or enable AI_REVIEW_MOCK=1",
            status_code=503,
        ) from exc

    return OpenAI(api_key=api_key)


def build_review_prompt(proposal_text: str) -> str:
    return f"""
You are an expert academic reviewer evaluating research proposals for funding and scientific merit.
Assess the proposal and return STRICT JSON only.

Scoring rubric:
- 1 (very weak) to 5 (excellent)
- Evaluate exactly these criteria in this order:
  1. Clarity of Research Problem
  2. Novelty & Contribution
  3. Methodology Soundness
  4. Feasibility
  5. Evaluation Plan
  6. Impact
  7. Risk & Limitations

Output JSON schema:
{{
  "summary": "string",
  "scores": [
    {{"criterion": "Clarity of Research Problem", "score": 1, "justification": "string"}},
    {{"criterion": "Novelty & Contribution", "score": 1, "justification": "string"}},
    {{"criterion": "Methodology Soundness", "score": 1, "justification": "string"}},
    {{"criterion": "Feasibility", "score": 1, "justification": "string"}},
    {{"criterion": "Evaluation Plan", "score": 1, "justification": "string"}},
    {{"criterion": "Impact", "score": 1, "justification": "string"}},
    {{"criterion": "Risk & Limitations", "score": 1, "justification": "string"}}
  ],
  "strengths": ["string"],
  "weaknesses": ["string"],
  "suggestions": ["string"],
  "final_verdict": "Accept",
  "verdict_justification": "string",
  "average_score": 0.0
}}

Allowed final_verdict values:
- Accept
- Weak Accept
- Borderline
- Reject

Proposal text:
<PROPOSAL_TEXT>
{proposal_text}
</PROPOSAL_TEXT>
""".strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        fragment = text[first : last + 1]
        try:
            loaded = json.loads(fragment)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass

    raise AIReviewError("AI reviewer returned non-JSON content that could not be parsed", status_code=502)


def _normalize_scores(scores: Any) -> list[dict[str, Any]]:
    scores_by_criterion: dict[str, dict[str, Any]] = {}
    if isinstance(scores, list):
        for item in scores:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", "")).strip()
            if not criterion:
                continue
            raw_score = item.get("score", 1)
            try:
                numeric = int(round(float(raw_score)))
            except (TypeError, ValueError):
                numeric = 1
            numeric = max(1, min(5, numeric))
            scores_by_criterion[criterion] = {
                "criterion": criterion,
                "score": numeric,
                "justification": str(item.get("justification", "No justification provided")).strip() or "No justification provided",
            }

    normalized: list[dict[str, Any]] = []
    for criterion in CORE_CRITERIA:
        normalized.append(
            scores_by_criterion.get(
                criterion,
                {
                    "criterion": criterion,
                    "score": 1,
                    "justification": "Missing from model output; defaulted to 1.",
                },
            )
        )
    return normalized


def _normalize_str_list(value: Any, fallback: str) -> list[str]:
    if not isinstance(value, list):
        return [fallback]
    cleaned = [str(x).strip() for x in value if str(x).strip()]
    return cleaned or [fallback]


def parse_ai_review(raw_response_text: str, review_model: str) -> dict[str, Any]:
    data = _extract_json_object(raw_response_text)
    scores = _normalize_scores(data.get("scores"))
    average_score = round(sum(item["score"] for item in scores) / len(scores), 2)

    verdict = str(data.get("final_verdict", "Reject")).strip() or "Reject"
    if verdict not in {"Accept", "Weak Accept", "Borderline", "Reject"}:
        verdict = "Reject"

    return {
        "summary": str(data.get("summary", "No summary provided")).strip() or "No summary provided",
        "scores": scores,
        "strengths": _normalize_str_list(data.get("strengths"), "No clear strengths identified."),
        "weaknesses": _normalize_str_list(data.get("weaknesses"), "Key weaknesses were not clearly stated."),
        "suggestions": _normalize_str_list(data.get("suggestions"), "Provide concrete improvements to methodology and evaluation."),
        "final_verdict": verdict,
        "verdict_justification": str(data.get("verdict_justification", "No verdict justification provided")).strip()
        or "No verdict justification provided",
        "average_score": average_score,
        "review_model": review_model,
        "review_source": "openai",
        "prompt_version": PROMPT_VERSION,
        "raw_response_text": raw_response_text,
    }


def classify_ai_review(review: dict[str, Any]) -> dict[str, Any]:
    scores = review["scores"]
    score_map = {entry["criterion"]: entry["score"] for entry in scores}
    low_hard_fail = [
        criterion
        for criterion in HARD_FAIL_CRITERIA
        if score_map.get(criterion, 1) <= HARD_FAIL_SCORE_THRESHOLD
    ]

    average = float(review["average_score"])
    verdict = review["final_verdict"]

    if low_hard_fail or average < AVERAGE_REJECT_THRESHOLD:
        mapped = "rejected"
    elif verdict == "Reject":
        mapped = "rejected"
    elif verdict == "Borderline":
        mapped = "frontier"
    elif verdict in {"Weak Accept", "Accept"}:
        mapped = "approved"
    else:
        mapped = "rejected"

    if AVERAGE_REJECT_THRESHOLD <= average < AVERAGE_FRONTIER_THRESHOLD and mapped == "approved":
        mapped = "frontier"

    return {
        "status": mapped,
        "average_score": average,
        "hard_fail_criteria": low_hard_fail,
    }


def build_decision_reason(review: dict[str, Any], mapped_status: str) -> str:
    score_map = {entry["criterion"]: entry["score"] for entry in review["scores"]}
    low_dims = [item for item in review["scores"] if item["score"] <= 2]
    high_dims = [item for item in review["scores"] if item["score"] >= 4]

    low_text = "; ".join(f"{d['criterion']}={d['score']}" for d in low_dims)
    high_text = "; ".join(f"{d['criterion']}={d['score']}" for d in high_dims)
    avg = review["average_score"]

    if mapped_status == "rejected":
        focus = low_text or (
            "average score below threshold"
            if avg < AVERAGE_REJECT_THRESHOLD
            else f"hard fail dimensions: Methodology Soundness={score_map.get('Methodology Soundness')}, "
            f"Feasibility={score_map.get('Feasibility')}, Evaluation Plan={score_map.get('Evaluation Plan')}"
        )
        return (
            f"AI review rejected this proposal. {review['verdict_justification']} "
            f"Critical deficiencies detected in: {focus}. "
            f"Average score={avg}."
        )

    if mapped_status == "frontier":
        return (
            f"AI review marked this proposal as frontier. {review['verdict_justification']} "
            f"It shows potential but needs stronger evidence/method validation before canonical promotion. "
            f"Average score={avg}; low-scoring dimensions: {low_text or 'none severe but maturity is insufficient'}"
        )

    return (
        f"AI review approved this proposal for canonical promotion. {review['verdict_justification']} "
        f"High-confidence areas: {high_text or 'balanced performance across criteria'}. "
        f"Average score={avg}."
    )


def _mock_review_from_text(proposal_text: str, review_model: str) -> dict[str, Any]:
    lowered = proposal_text.lower()
    if "reject" in lowered or "low quality" in lowered:
        scores = [2, 2, 1, 2, 1, 2, 2]
        verdict = "Reject"
        verdict_justification = "Method and evaluation design are not credible enough for funding."
        summary = "The proposal lacks methodological rigor and a convincing validation strategy."
    elif "borderline" in lowered:
        scores = [3, 3, 3, 3, 3, 3, 3]
        verdict = "Borderline"
        verdict_justification = "The concept is promising but requires deeper validation and risk controls."
        summary = "The proposal is acceptable in intent but underdeveloped in execution detail."
    else:
        scores = [4, 4, 4, 4, 4, 4, 4]
        verdict = "Accept"
        verdict_justification = "The proposal is technically sound, feasible, and evaluation-ready."
        summary = "The proposal demonstrates clear novelty, sound methods, and measurable impact."

    score_table = [
        {"criterion": c, "score": s, "justification": f"Mock assessment for {c}."}
        for c, s in zip(CORE_CRITERIA, scores)
    ]
    avg = round(sum(scores) / len(scores), 2)
    payload = {
        "summary": summary,
        "scores": score_table,
        "strengths": ["Clear statement of intent", "Actionable scope"],
        "weaknesses": ["Needs stronger empirical grounding"] if verdict != "Accept" else ["Minor clarity edits needed"],
        "suggestions": ["Add stronger baselines", "Expand evaluation protocol"],
        "final_verdict": verdict,
        "verdict_justification": verdict_justification,
        "average_score": avg,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    return parse_ai_review(raw, review_model)


def run_ai_review(proposal_text: str) -> dict[str, Any]:
    if not is_ai_review_enabled():
        raise AIReviewError("AI review is disabled by AI_REVIEW_ENABLED", status_code=503)

    model = get_review_model()
    if is_mock_mode():
        return _mock_review_from_text(proposal_text, model)

    client = get_openai_client()
    prompt = build_review_prompt(proposal_text)

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
    )

    raw_text = getattr(response, "output_text", "")
    if not raw_text:
        raw_text = str(response)

    return parse_ai_review(raw_text, model)
